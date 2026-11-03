# ============================================================
#  application/services/prompt_seeding_service.py
# ============================================================

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.prompt import Prompt, PromptType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CuratedPromptDefinition:
    """Descriptor for an authored default prompt template."""

    relative_path: str
    name: str
    prompt_type: PromptType
    is_default: bool


# Curated manifest of authored prompt assets to seed into SQLite on clean installations.
# Contains all templates under prompts/templates/ (8 files) and prompts/pipeline2/unify_markdown.txt (1 file).
CURATED_PROMPTS: Tuple[CuratedPromptDefinition, ...] = (
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/stem_transcription.txt",
        name="Persian STEM Transcription",
        prompt_type=PromptType.PIPELINE_1,
        is_default=True,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/en/stem_handwritten_transcription.txt",
        name="English STEM Handwritten Transcription",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/stem_transcription_with_figure_description.txt",
        name="Persian STEM with Figure Description",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/stem_transcription_with_figure_description_and_netlist.txt",
        name="Persian STEM with Netlist",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/educational_stem_transcription_griffiths.txt",
        name="Educational STEM (Griffiths)",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/electromagnetics_griffiths_editor.txt",
        name="Electromagnetics Editor (Griffiths)",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/stem_transcription_tikz.txt",
        name="Persian STEM with TikZ",
        prompt_type=PromptType.PIPELINE_1,
        is_default=False,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/templates/fa/quick_convert.txt",
        name="Quick Convert",
        prompt_type=PromptType.QUICK_CONVERT,
        is_default=True,
    ),
    CuratedPromptDefinition(
        relative_path="prompts/pipeline2/unify_markdown.txt",
        name="Pipeline 2 Unify Markdown",
        prompt_type=PromptType.PIPELINE_2,
        is_default=True,
    ),
)


# Known SHA-256 digests of previously shipped stock prompts (v0.1.0-rc.1 / rc.2).
# Used by reconcile_stock_prompts() to safely detect unmodified stock templates
# and upgrade them to Quarto raw LaTeX contracts while strictly preserving user customizations.
HISTORICAL_STOCK_PROMPT_HASHES: Dict[str, Set[str]] = {
    "Persian STEM with TikZ": {
        "d49c2991cc50a010edfcf34e1153df5e1b12e154741bc345b177ebc332f38502",  # v0.1.0-rc.1 / rc.2 stock
    },
    "Quick Convert": {
        "df81289cb6f1e8edba6cf41f36892fcc7a665dcdfe26c493c8a848e27fefd67f",  # v0.1.0-rc.1 / rc.2 stock
    },
    "Pipeline 2 Unify Markdown": {
        "7fca58b641cf098748f93d5cad27422657ae8b587599b37b13b1109857478610",  # v0.1.0-rc.1 / rc.2 stock
    },
}


class PromptSeedingService:
    """Application service managing atomic, idempotent seeding of curated prompts into SQLite."""

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        resource_resolver: Optional[Callable[[str | Path], Path]] = None,
        curated_prompts: Optional[Sequence[CuratedPromptDefinition]] = None,
    ):
        self.uow_factory = uow_factory
        self.resource_resolver = resource_resolver
        self.curated_prompts = tuple(curated_prompts) if curated_prompts is not None else CURATED_PROMPTS

    def _resolve_path(self, relative_path: str | Path) -> Path:
        if self.resource_resolver is not None:
            return self.resource_resolver(relative_path)
        return Path(relative_path).resolve()

    def seed_default_prompts(self) -> List[Prompt]:
        """Atomically seeds curated prompts into SQLite if and only if the prompts table is empty.

        If the prompts table contains any records (from a prior run, migration, or user creation),
        all records are left completely untouched and an empty list is returned.

        Returns:
            List[Prompt]: Newly seeded prompt entities if seeding occurred, or an empty list if skipped.

        Raises:
            FileNotFoundError: If any required prompt source file cannot be found.
            ValueError: If any required prompt source file is empty or whitespace-only.
        """
        # 1. Pre-validate and read all prompt source files before entering the database transaction
        loaded_contents: List[Tuple[CuratedPromptDefinition, str]] = []
        for defn in self.curated_prompts:
            resolved_path = self._resolve_path(defn.relative_path)
            if not resolved_path.is_file():
                raise FileNotFoundError(
                    f"Required curated prompt template not found at resolved path: {resolved_path}"
                )
            content = resolved_path.read_text(encoding="utf-8")
            if not content.strip():
                raise ValueError(
                    f"Required curated prompt template is empty: {resolved_path}"
                )
            loaded_contents.append((defn, content))

        do_reconcile = False
        seeded_entities: List[Prompt] = []
        with self.uow_factory.create() as uow:
            # Acquire immediate lock to prevent race conditions during concurrent startup
            if hasattr(uow, "begin_immediate"):
                uow.begin_immediate()

            existing_prompts = uow.prompts.list_all()
            if existing_prompts:
                logger.debug(
                    "Prompts table already contains %d records; skipping default prompt seeding.",
                    len(existing_prompts),
                )
                do_reconcile = True
            else:
                default_p1_id: Optional[int] = None
                default_p2_id: Optional[int] = None

                for defn, text in loaded_contents:
                    prompt_entity = Prompt(
                        id=None,
                        name=defn.name,
                        text=text,
                        prompt_type=defn.prompt_type,
                        is_default=defn.is_default,
                    )
                    saved_prompt = uow.prompts.save(prompt_entity)
                    seeded_entities.append(saved_prompt)

                    if defn.is_default:
                        if defn.prompt_type == PromptType.PIPELINE_1 and default_p1_id is None:
                            default_p1_id = saved_prompt.id
                        elif defn.prompt_type == PromptType.PIPELINE_2 and default_p2_id is None:
                            default_p2_id = saved_prompt.id

                # Reconcile app settings defaults if unset
                settings = uow.settings.get()
                settings_modified = False
                if settings.default_prompt_id is None and default_p1_id is not None:
                    settings.default_prompt_id = default_p1_id
                    settings_modified = True
                if settings.default_pipeline2_prompt_id is None and default_p2_id is not None:
                    settings.default_pipeline2_prompt_id = default_p2_id
                    settings_modified = True

                if settings_modified:
                    uow.settings.save(settings)

                uow.commit()
                logger.info("Successfully seeded %d curated prompt templates into SQLite.", len(seeded_entities))

        if do_reconcile:
            self.reconcile_stock_prompts()
            return []

        return seeded_entities

    def reconcile_stock_prompts(self) -> List[Prompt]:
        """Safely updates unmodified stock prompt templates in SQLite to current Quarto-compatible versions.

        Safety Invariants:
        - Never overwrites user-created prompts (name or content unrecognized).
        - Never overwrites a user-customized prompt (content hash differs from known historical stock versions).
        - Updates ONLY records that can be conclusively identified as previously shipped Polpo defaults.
        - Preserves existing prompt IDs, prompt types, and user settings foreign keys.
        - Fully transactional and idempotent.

        Returns:
            List[Prompt]: Prompts that were updated, or an empty list if none required updating.
        """
        loaded_contents: Dict[str, str] = {}
        for defn in self.curated_prompts:
            resolved_path = self._resolve_path(defn.relative_path)
            if resolved_path.is_file():
                content = resolved_path.read_text(encoding="utf-8")
                if content.strip():
                    loaded_contents[defn.name] = content

        updated_prompts: List[Prompt] = []
        with self.uow_factory.create() as uow:
            if hasattr(uow, "begin_immediate"):
                uow.begin_immediate()

            existing_prompts = uow.prompts.list_all()
            if not existing_prompts:
                return []

            for p in existing_prompts:
                if p.name not in loaded_contents:
                    continue

                historical_hashes = HISTORICAL_STOCK_PROMPT_HASHES.get(p.name)
                if not historical_hashes:
                    continue

                normalized_text = p.text.replace("\r\n", "\n")
                current_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

                if current_hash in historical_hashes:
                    new_text = loaded_contents[p.name]
                    new_normalized = new_text.replace("\r\n", "\n")
                    if normalized_text != new_normalized:
                        p.text = new_text
                        saved = uow.prompts.save(p)
                        updated_prompts.append(saved)
                        logger.info(
                            "Reconciled stock prompt template '%s' (ID %s) to Quarto-compatible version.",
                            p.name,
                            p.id,
                        )

            if updated_prompts:
                uow.commit()

        return updated_prompts
