# ============================================================
#  infrastructure/pdf/staging_workspace.py
#  Isolated Staging Workspace Manager
#  Phase 13 — Ticket #100
# ============================================================

"""
Isolated Staging Workspace Manager for Quarto PDF Export.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
XeLaTeX executes with ordinary desktop-user OS privileges. The compilation flag
-no-shell-escape strictly prevents external binary invocation via \\write18, but does
not sandbox host filesystem reads via native TeX primitives (\\input, \\openin).
To protect against path traversal and unauthorized filesystem access:
1. Every export session executes inside an ephemeral, dedicated workspace.
2. Parent configuration inheritance is blocked by an application-owned _quarto.yml barrier.
3. Referenced visual region assets are validated against strict job artifact containment boundaries.
4. Ephemeral workspaces are deterministically purged on success, failure, timeout, or cancellation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
from typing import Any, Dict, Iterator, List, Optional, Union

from application.services.document_asset_resolver import (
    AssetAccessError,
    AssetContainmentError,
    DocumentAssetResolver,
)
from application.services.metadata_normalizer import normalize_quarto_pdf_metadata
from infrastructure.paths import get_runtime_resource_path

logger = logging.getLogger(__name__)


class StagingCleanupError(OSError):
    """Raised when removing an ephemeral staging workspace fails on normal exit."""
    pass


# Quarto project barrier configuration to isolate ephemeral export sessions
# from any ambient _quarto.yml settings in ancestor directories.
_QUARTO_PROJECT_BARRIER_YML: str = """project:
  type: default
format:
  pdf:
    from: markdown-multiline_tables-simple_tables
    pdf-engine: xelatex
    pdf-engine-opts:
      - "-no-shell-escape"
      - "-interaction=nonstopmode"
      - "-halt-on-error"
"""


@dataclass(frozen=True)
class StagingSession:
    """
    Immutable value object representing an active staging workspace session.

    Attributes:
        staging_dir: Root path to the ephemeral staging workspace.
        qmd_path: Absolute path to the staged document.qmd ready for Quarto compilation.
        preamble_path: Absolute path to the staged polpo_preamble.tex.
        quarto_yml_path: Absolute path to the barrier _quarto.yml configuration.
        staged_asset_count: Total number of referenced visual assets copied into staging.
    """
    staging_dir: Path
    qmd_path: Path
    preamble_path: Path
    quarto_yml_path: Path
    staged_asset_count: int


def _format_yaml_scalar(val: Any) -> str:
    """Formats scalar values for deterministic YAML serialization without third-party dependencies."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, list) and not val:
        return "[]"
    if isinstance(val, dict) and not val:
        return "{}"
    return json.dumps(str(val), ensure_ascii=False)


def _dump_yaml(obj: Any, indent_level: int = 0) -> str:
    """
    Serializes normalized metadata dictionaries into clean, indented YAML front matter.
    Preserves empty sequences (e.g. []), empty mappings ({}), lists of dicts
    (such as include-in-header: [{"file": "polpo_preamble.tex"}]), and multi-line
    strings deterministically.
    """
    lines: List[str] = []
    prefix = "  " * indent_level

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and not v:
                lines.append(f"{prefix}{k}: {{}}")
            elif isinstance(v, list) and not v:
                lines.append(f"{prefix}{k}: []")
            elif isinstance(v, (dict, list)) and v:
                lines.append(f"{prefix}{k}:")
                lines.append(_dump_yaml(v, indent_level + 1))
            elif isinstance(v, str) and "\n" in v:
                lines.append(f"{prefix}{k}: |")
                item_prefix = "  " * (indent_level + 1)
                for line in v.splitlines():
                    lines.append(f"{item_prefix}{line}")
            else:
                lines.append(f"{prefix}{k}: {_format_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first = True
                for k, v in item.items():
                    if first:
                        if isinstance(v, dict) and not v:
                            lines.append(f"{prefix}- {k}: {{}}")
                        elif isinstance(v, list) and not v:
                            lines.append(f"{prefix}- {k}: []")
                        elif isinstance(v, (dict, list)) and v:
                            lines.append(f"{prefix}- {k}:")
                            lines.append(_dump_yaml(v, indent_level + 2))
                        elif isinstance(v, str) and "\n" in v:
                            lines.append(f"{prefix}- {k}: |")
                            for line in v.splitlines():
                                lines.append(f"{prefix}    {line}")
                        else:
                            lines.append(f"{prefix}- {k}: {_format_yaml_scalar(v)}")
                        first = False
                    else:
                        item_prefix = prefix + "  "
                        if isinstance(v, dict) and not v:
                            lines.append(f"{item_prefix}{k}: {{}}")
                        elif isinstance(v, list) and not v:
                            lines.append(f"{item_prefix}{k}: []")
                        elif isinstance(v, (dict, list)) and v:
                            lines.append(f"{item_prefix}{k}:")
                            lines.append(_dump_yaml(v, indent_level + 2))
                        elif isinstance(v, str) and "\n" in v:
                            lines.append(f"{item_prefix}{k}: |")
                            for line in v.splitlines():
                                lines.append(f"{item_prefix}  {line}")
                        else:
                            lines.append(f"{item_prefix}{k}: {_format_yaml_scalar(v)}")
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{prefix}- []")
                else:
                    lines.append(f"{prefix}-")
                    lines.append(_dump_yaml(item, indent_level + 1))
            elif isinstance(item, str) and "\n" in item:
                lines.append(f"{prefix}- |")
                for line in item.splitlines():
                    lines.append(f"{prefix}  {line}")
            else:
                lines.append(f"{prefix}- {_format_yaml_scalar(item)}")

    return "\n".join(lines)


def _strip_existing_front_matter(markdown: str) -> str:
    """Removes any leading YAML front matter block from canonical markdown if present.

    A valid YAML metadata block:
    - Must start on line 1 with '---' followed immediately by non-empty YAML content.
    - Must contain at least one valid top-level YAML key (e.g. 'key:').
    - Must not contain un-indented non-YAML paragraph text.
    - Must be closed by '---' or '...' on its own line.

    If the text begins with a thematic break / horizontal rule rather than a recognized
    YAML metadata block, the content is preserved intact.
    """
    if not (markdown.startswith("---\n") or markdown.startswith("---\r\n")):
        return markdown

    lines = markdown.splitlines(keepends=True)
    if len(lines) < 3:
        return markdown

    # Line 2 cannot be empty (in Pandoc, an empty line after '---' indicates a thematic break)
    if not lines[1].strip():
        return markdown

    closing_index = -1
    has_yaml_key = False
    valid_yaml_block = True

    for i in range(1, len(lines)):
        line_stripped = lines[i].rstrip("\r\n")
        if line_stripped in ("---", "..."):
            closing_index = i
            break

        trimmed = line_stripped.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        if re.match(r"^[a-zA-Z0-9_.-]+:\s*", line_stripped):
            has_yaml_key = True
        elif line_stripped.startswith(" ") or line_stripped.startswith("\t") or line_stripped.startswith("- "):
            continue
        else:
            valid_yaml_block = False
            break

    if closing_index != -1 and has_yaml_key and valid_yaml_block:
        return "".join(lines[closing_index + 1 :])

    return markdown


_ALERT_START_PATTERN = re.compile(
    r"^\s*>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](?:\s+(.*))?$",
    re.IGNORECASE,
)


def _convert_github_alerts_to_callouts(markdown: str) -> str:
    """
    Transforms GitHub-style alert blockquotes (> [!NOTE], > [!IMPORTANT], etc.)
    into native Quarto callout blocks (::: {.callout-...}) for publication-grade typesetting.
    Preserves all code blocks (fenced by ``` or ~~~) untouched.
    """
    if "[!" not in markdown:
        return markdown

    lines = markdown.splitlines(keepends=True)
    result: List[str] = []
    i = 0
    n = len(lines)
    in_code_block = False
    fence = ""

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Check for code block fences
        if stripped.startswith("```") or stripped.startswith("~~~"):
            f = stripped[:3]
            if not in_code_block:
                in_code_block = True
                fence = f
            elif f == fence:
                in_code_block = False
                fence = ""
            result.append(line)
            i += 1
            continue

        if in_code_block:
            result.append(line)
            i += 1
            continue

        m = _ALERT_START_PATTERN.match(line.rstrip("\r\n"))
        if m:
            alert_type = m.group(1).lower()
            title = m.group(2).strip() if m.group(2) else None
            callout_lines: List[str] = []
            if title:
                callout_lines.append(f"## {title}\n")

            i += 1
            while i < n:
                next_line = lines[i]
                stripped_next = next_line.lstrip()
                if stripped_next.startswith(">"):
                    content = stripped_next[1:]
                    if content.startswith(" "):
                        content = content[1:]
                    callout_lines.append(content)
                    i += 1
                else:
                    break

            result.append(f"::: {{.callout-{alert_type}}}\n")
            result.extend(callout_lines)
            if not result[-1].endswith("\n"):
                result.append("\n")
            result.append(":::\n")
            continue

        result.append(line)
        i += 1

    return "".join(result)


def _sanitize_uncropped_bounding_boxes(markdown: str) -> str:
    """
    Detects any remaining bounding box coordinate tags ([[ymin=..., ...]] or [[100, ...]])
    that were not converted to image crops during document ingestion.
    Strips them from the staged export document to prevent raw coordinate leaks,
    and logs a diagnostic warning.
    """
    if "[[" not in markdown:
        return markdown

    from core.entities.bounding_box_parser import BoundingBoxParser

    def _replace_box(match: re.Match[str]) -> str:
        logger.warning(
            "Uncropped bounding box coordinate tag '%s' detected during PDF export; "
            "stripping to prevent layout leak.",
            match.group(0),
        )
        return ""

    return BoundingBoxParser.TAG_PATTERN.sub(_replace_box, markdown)


_FENCE_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_HR_LINE_PATTERN = re.compile(r"^[ \t]*-{3,}[ \t]*$")
_STYLE_MACRO_PATTERN = re.compile(
    r"\\style\s*\{\s*font-family\s*:[^}]*\}\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}"
)
_PERSIAN_CHAR_PATTERN = re.compile(r"[\u0600-\u06FF]")
_TEXT_MACRO_PATTERN = re.compile(r"\\text\s*\{([^{}]*)\}")
_BROKEN_OP_NEG_SPACE_PATTERN = re.compile(
    r"(\\(?:cos|sin|tan|cot|sec|csc|arcsin|arccos|arctan|sinh|cosh|tanh|ln|log|exp|lim|det|min|max|sup|inf))!\\left"
)
_BROKEN_FUNC_NEG_SPACE_PATTERN = re.compile(r"\b([a-zA-Z0-9_]+)!\\left")


def _normalize_persian_text_macro(match: re.Match[str]) -> str:
    inner = match.group(1)
    if not _PERSIAN_CHAR_PATTERN.search(inner):
        return match.group(0)
    cleaned = inner.replace("~", " ").replace(r"\ ", " ")
    return f"\\persiantext{{{cleaned}}}"


def _sanitize_markdown_body(markdown: str) -> str:
    """Sanitizes AI transcription quirks in staged Markdown body outside fenced code blocks."""
    if not markdown:
        return markdown

    lines = markdown.splitlines()
    out_lines: List[str] = []
    in_fence = False
    fence_marker = ""
    pending_blank_after_hr = False

    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            if pending_blank_after_hr:
                if out_lines and out_lines[-1].strip() != "":
                    out_lines.append("")
                pending_blank_after_hr = False
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)
            elif line.lstrip().startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            out_lines.append(line)
            continue

        if in_fence:
            out_lines.append(line)
            continue

        if _HR_LINE_PATTERN.match(line):
            if out_lines and out_lines[-1].strip() != "":
                out_lines.append("")
            out_lines.append("---")
            pending_blank_after_hr = True
            continue

        if pending_blank_after_hr:
            if line.strip() != "":
                out_lines.append("")
            pending_blank_after_hr = False

        prev = None
        curr = line
        while prev != curr:
            prev = curr
            curr = _STYLE_MACRO_PATTERN.sub(r"\1", curr)

        curr = _TEXT_MACRO_PATTERN.sub(_normalize_persian_text_macro, curr)
        curr = _BROKEN_OP_NEG_SPACE_PATTERN.sub(r"\1\\!\\left", curr)
        curr = _BROKEN_FUNC_NEG_SPACE_PATTERN.sub(r"\1\\!\\left", curr)

        out_lines.append(curr)

    result = "\n".join(out_lines)
    if markdown.endswith("\n"):
        result += "\n"
    return result


class StagingWorkspaceManager:
    """
    Allocates and deterministically purges ephemeral workspaces for Quarto PDF compilation.

    Enforces parent configuration barriers (_quarto.yml), places typesetting templates
    (polpo_preamble.tex), resolves local visual region asset closures with path containment,
    and formats normalized document.qmd inputs.
    """

    def __init__(self, templates_dir: Optional[Union[Path, str]] = None):
        """
        Initializes the workspace manager with a templates directory.
        Defaults to resources/templates at the project root if omitted.
        """
        if templates_dir is not None:
            self.templates_dir = Path(templates_dir).resolve()
        else:
            self.templates_dir = get_runtime_resource_path("resources/templates")

    @contextmanager
    def create_session(
        self,
        job_artifacts_dir: Union[Path, str],
        job_id: int,
        raw_metadata: Dict[str, Any],
        canonical_markdown: str,
        asset_resolver: DocumentAssetResolver,
        is_tinytex: bool = False,
        default_title: str = "",
    ) -> Iterator[StagingSession]:
        """
        Allocates an isolated staging workspace and deterministically purges it on exit.

        Args:
            job_artifacts_dir: Local directory containing the document's artifacts and assets.
            job_id: Numeric identifier of the document job.
            raw_metadata: Author-provided front matter dictionary.
            canonical_markdown: Authoritative Markdown content string.
            asset_resolver: Application service resolving local asset references.
            is_tinytex: Flag indicating if Quarto is using TinyTeX (passed to normalizer).
            default_title: Fallback document title if absent from metadata.

        Yields:
            StagingSession object containing verified staging file paths.

        Raises:
            AssetAccessError: If a referenced asset violates containment or is unreadable.
            FileNotFoundError: If the preamble template cannot be found.
            MalformedMetadataError: If metadata violates allowlist schema validation.
            StagingCleanupError: If removing the staging workspace fails on normal exit.
        """
        # Validate preamble template existence before workspace allocation
        preamble_template = self.templates_dir / "polpo_preamble.tex"
        if not preamble_template.is_file():
            raise FileNotFoundError(
                f"Typesetting preamble template not found: {preamble_template}"
            )

        canonical_artifacts_dir = Path(job_artifacts_dir).resolve()
        pdf_staging_parent = canonical_artifacts_dir / ".pdf_staging"
        pdf_staging_parent.mkdir(parents=True, exist_ok=True)

        token = secrets.token_hex(8)
        staging_dir = pdf_staging_parent / f"{job_id}_{token}"
        staging_dir.mkdir(parents=True, exist_ok=False)

        has_primary_exception = False
        try:
            # 1. Project Barrier Injection
            quarto_yml_path = staging_dir / "_quarto.yml"
            quarto_yml_path.write_text(_QUARTO_PROJECT_BARRIER_YML, encoding="utf-8")

            # 2. Preamble Placement
            preamble_path = staging_dir / "polpo_preamble.tex"
            shutil.copyfile(preamble_template, preamble_path)

            # 3. Visual Region Asset Closure with Path Containment
            resolved_set = asset_resolver.resolve_document_assets(
                job_id=job_id,
                canonical_markdown=canonical_markdown,
                job_artifacts_dir=canonical_artifacts_dir,
            )

            staged_assets_count = 0
            for asset in resolved_set.assets:
                src_path = asset.source_path
                dest_path = (staging_dir / asset.relative_dest_path).resolve()

                # Defense-in-depth: Ensure staged asset destination cannot escape staging workspace
                if not dest_path.is_relative_to(staging_dir.resolve()):
                    raise AssetAccessError(
                        f"Asset destination escapes staging workspace: '{asset.relative_dest_path}'"
                    )

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_path, dest_path)
                staged_assets_count += 1

            # 4. Normalized Staged Document Generation (Ticket #97 F-02 compliance)
            normalized_meta = normalize_quarto_pdf_metadata(
                raw_metadata,
                is_tinytex=is_tinytex,
                default_title=default_title,
            )

            yaml_front_matter = _dump_yaml(normalized_meta)
            body_markdown = _strip_existing_front_matter(resolved_set.resolved_markdown)
            body_markdown = _sanitize_markdown_body(body_markdown)
            body_markdown = _convert_github_alerts_to_callouts(body_markdown)
            body_markdown = _sanitize_uncropped_bounding_boxes(body_markdown)

            qmd_path = staging_dir / "document.qmd"
            qmd_content = f"---\n{yaml_front_matter}\n---\n\n{body_markdown.lstrip('\r\n')}"
            qmd_path.write_text(qmd_content, encoding="utf-8")

            session = StagingSession(
                staging_dir=staging_dir,
                qmd_path=qmd_path,
                preamble_path=preamble_path,
                quarto_yml_path=quarto_yml_path,
                staged_asset_count=staged_assets_count,
            )

            yield session

        except BaseException:
            has_primary_exception = True
            raise
        finally:
            # Deterministic workspace cleanup on normal exit, exception, or cancellation
            if staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir)
                except Exception as e:
                    logger.warning(
                        "Failed to remove staging workspace '%s': %s",
                        staging_dir,
                        e,
                    )
                    if not has_primary_exception:
                        raise StagingCleanupError(
                            f"Failed to remove staging workspace at '{staging_dir}': {e}"
                        ) from e

            try:
                if pdf_staging_parent.exists() and not any(pdf_staging_parent.iterdir()):
                    pdf_staging_parent.rmdir()
            except OSError as e:
                # Retain parent directory silently if another concurrent job session is active
                # or if the directory cannot be removed immediately
                if e.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                    logger.warning(
                        "Failed to remove empty parent staging directory '%s': %s",
                        pdf_staging_parent,
                        e,
                    )
