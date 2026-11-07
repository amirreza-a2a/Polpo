# ============================================================
#  application/services/latex_repair_service.py
#  Application Service for AI-Assisted LaTeX Error Repair
# ============================================================

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from application.dto.latex_repair_dto import LaTeXRepairProposal, LaTeXRepairRequest
from application.ports.ai_executor import IAIExecutionService
from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramRenderResult,
    IDiagramRenderer,
)
from application.ports.math_renderer import (
    IMathRenderer,
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
)
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.api_slot import ApiSlot

logger = logging.getLogger(__name__)

_FORBIDDEN_LATEX_PATTERNS = (
    re.compile(r"\\documentclass\b", re.IGNORECASE),
    re.compile(r"\\begin\{document\}", re.IGNORECASE),
    re.compile(r"\\end\{document\}", re.IGNORECASE),
    re.compile(r"\\usepackage\b", re.IGNORECASE),
    re.compile(r"\\write18\b", re.IGNORECASE),
    re.compile(r"\\immediate\s*\\write", re.IGNORECASE),
    re.compile(r"\\input\s*\|", re.IGNORECASE),
    re.compile(r"\\openout\b", re.IGNORECASE),
    re.compile(r"\\immediate\s*\\openout", re.IGNORECASE),
)

_MAX_SOURCE_BYTES = 50000


def _resolve_repair_prompt_content(custom_path: Optional[str] = None) -> str:
    """Locates and loads the authoritative versioned repair prompt template."""
    candidates: List[Path] = []
    if custom_path:
        candidates.append(Path(custom_path).expanduser().resolve())

    # 1. Project root / prompts / repair / latex_repair_v1.txt
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(repo_root / "prompts" / "repair" / "latex_repair_v1.txt")
    candidates.append(repo_root / "resources" / "prompts" / "repair" / "latex_repair_v1.txt")
    candidates.append(Path.cwd() / "prompts" / "repair" / "latex_repair_v1.txt")

    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")

    raise FileNotFoundError(f"LaTeX repair prompt template not found in: {candidates}")


class LaTeXRepairService:
    """Orchestrates AI-assisted repair for failing LaTeX math formulas and technical diagrams.

    Guarantees:
      - Clean Architecture: 100% independent from Qt/PySide6 and vendor-specific AI SDKs.
      - Never mutates the canonical document directly; returns typed, validated proposals.
      - Strictly enforces same-renderer compiler verification before offering candidate.
      - Rejects standalone document wrappers, unrequested packages, and shell execution.
      - Enforces bounded retries (max 2 attempts per user request) to prevent expensive loops.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        ai_executor: IAIExecutionService,
        diagram_renderer: Optional[IDiagramRenderer] = None,
        math_renderer: Optional[IMathRenderer] = None,
        pdf_validator: Optional[Any] = None,
        prompt_path: Optional[str] = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.ai_executor = ai_executor
        self.diagram_renderer = diagram_renderer
        self.math_renderer = math_renderer
        self.pdf_validator = pdf_validator
        self._prompt_path = prompt_path

    def has_active_provider(self) -> bool:
        """Checks if at least one active AI provider slot is configured."""
        try:
            with self.uow_factory.create() as uow:
                if hasattr(uow.apis, "list_all"):
                    all_slots = uow.apis.list_all()
                    return any(getattr(s, "is_active", True) for s in all_slots)
                elif hasattr(uow.apis, "list_by_user"):
                    all_slots = uow.apis.list_by_user(user_id=1, include_public=True)
                    return any(getattr(s, "is_active", True) for s in all_slots)
        except Exception as e:
            logger.warning("Failed to check active AI providers: %s", e)
            return False
        return False

    def parse_and_validate_ai_response(
        self,
        raw_response: str,
        original_source: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Parses and strictly validates the machine-readable JSON repair payload.

        Returns:
            (parsed_dict, None) on success, or (None, rejection_reason) on failure.
        """
        if not raw_response or not raw_response.strip():
            return None, "AI response was empty."

        text = raw_response.strip()

        # Strip optional Markdown code block fences (```json ... ```)
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Extract JSON object substring if surrounded by stray text
        start_brace = text.find("{")
        end_brace = text.rfind("}")
        if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
            text = text[start_brace : end_brace + 1]

        try:
            data = json.loads(text)
        except Exception as e:
            return None, f"Failed to parse JSON response: {e}"

        if not isinstance(data, dict):
            return None, "Response JSON root must be an object."

        if data.get("schema_version") != 1:
            return None, f"Unsupported schema_version: {data.get('schema_version')}; expected 1."

        repaired_source = data.get("repaired_source")
        if not isinstance(repaired_source, str) or not repaired_source.strip():
            return None, "Missing or empty 'repaired_source' field."

        change_summary = data.get("change_summary")
        if not isinstance(change_summary, str) or not change_summary.strip():
            return None, "Missing or empty 'change_summary' field."

        preserved = data.get("preserved_invariants")
        if not isinstance(preserved, (list, tuple)):
            return None, "'preserved_invariants' must be an array of strings."

        # Safety & Isolation Boundaries:
        repaired_clean = repaired_source.strip()

        if len(repaired_clean.encode("utf-8")) > _MAX_SOURCE_BYTES:
            return None, f"Repaired source exceeds maximum allowed size ({_MAX_SOURCE_BYTES} bytes)."

        for pattern in _FORBIDDEN_LATEX_PATTERNS:
            if pattern.search(repaired_clean):
                return None, f"Repaired source contains forbidden LaTeX wrapper or command matching {pattern.pattern}."

        # Prevent entire document rewrite:
        if (repaired_clean.startswith("# ") or repaired_clean.startswith("---\n")) and not (
            original_source.strip().startswith("# ") or original_source.strip().startswith("---\n")
        ):
            return None, "Repaired source attempted full document replacement instead of target fragment."

        return data, None

    def validate_candidate(
        self,
        candidate_source: str,
        renderer_type: str,
        environment_profile: str = "default",
    ) -> Tuple[bool, Optional[str], Optional[str], float, float]:
        """Runs the candidate LaTeX fragment through the identical rendering pipeline.

        Returns:
            Tuple of (passed: bool, error_msg: Optional[str], svg_xml: Optional[str], width: float, height: float)
        """
        r_type = renderer_type.lower()

        # 1. Diagram rendering validation
        if "diagram" in r_type or "tikz" in r_type or "pgf" in r_type:
            if self.diagram_renderer is None:
                return False, "Diagram renderer is not configured.", None, 0.0, 0.0

            req = DiagramRenderRequest(
                source=candidate_source,
                profile=environment_profile or "default",
            )
            try:
                res = self.diagram_renderer.render(req)
                if not res.svg_xml or not res.svg_xml.strip():
                    return False, "Rendered diagram SVG was empty.", None, 0.0, 0.0
                return True, None, res.svg_xml, res.width, res.height
            except DiagramRenderError as e:
                return False, f"Diagram compilation failed: {e.message}", None, 0.0, 0.0
            except Exception as e:
                return False, f"Unexpected diagram renderer failure: {e}", None, 0.0, 0.0

        # 2. MathJax equation validation
        elif "math" in r_type:
            if self.math_renderer is None:
                return False, "Math renderer is not configured.", None, 0.0, 0.0

            is_display = "display" in r_type or r_type == "math_block"
            clean_tex = candidate_source.strip()
            # Strip outer $$ or $ if AI mistakenly wrapped candidate in delimiters
            if clean_tex.startswith("$$") and clean_tex.endswith("$$") and len(clean_tex) >= 4:
                clean_tex = clean_tex[2:-2].strip()
            elif clean_tex.startswith("$") and clean_tex.endswith("$") and len(clean_tex) >= 2:
                clean_tex = clean_tex[1:-1].strip()

            req = MathRenderRequest(tex=clean_tex, display=is_display)
            try:
                res = self.math_renderer.render(req)
                if not res.svg_xml or not res.svg_xml.strip():
                    return False, "Rendered math SVG was empty.", None, 0.0, 0.0
                return True, None, res.svg_xml, 0.0, 0.0
            except MathRenderError as e:
                return False, f"Math rendering failed: {e.message}", None, 0.0, 0.0
            except Exception as e:
                return False, f"Unexpected math renderer failure: {e}", None, 0.0, 0.0

        # 3. Quarto / XeLaTeX PDF validation
        elif "pdf" in r_type or "quarto" in r_type:
            if self.pdf_validator is not None:
                try:
                    passed, err_msg, preview_xml = self.pdf_validator(candidate_source)
                    return passed, err_msg, preview_xml, 0.0, 0.0
                except Exception as e:
                    return False, f"PDF validation failure: {e}", None, 0.0, 0.0

            # If no dedicated pdf_validator is injected, validate through diagram or math pipeline
            # depending on candidate structure
            clean = candidate_source.strip()
            if any(k in clean for k in (r"\begin{tikzpicture}", r"\begin{pgfplots}", r"\begin{circuitikz}", r"\begin{tikzcd}")):
                if self.diagram_renderer is not None:
                    return self.validate_candidate(candidate_source, "diagram", environment_profile)
            elif self.math_renderer is not None:
                return self.validate_candidate(candidate_source, "math_display", environment_profile)
            return False, "PDF validator is not configured and candidate could not be mapped to diagram or math renderer.", None, 0.0, 0.0

        # Fallback / generic
        return False, f"Unsupported renderer_type: '{renderer_type}'.", None, 0.0, 0.0

    def repair_latex(
        self,
        request: LaTeXRepairRequest,
        max_retries: int = 1,
    ) -> LaTeXRepairProposal:
        """Executes AI-assisted repair with same-renderer verification and bounded retries.

        Args:
            request: Typed repair request.
            max_retries: Maximum follow-up attempts if compilation fails (default: 1, max 2 total).

        Returns:
            LaTeXRepairProposal with compilation outcome and rendered preview.
        """
        request_id = str(uuid.uuid4())
        bounded_retries = max(0, min(1, max_retries))

        # 1. Resolve active AI provider chain
        chain: List[ApiSlot] = []
        with self.uow_factory.create() as uow:
            if hasattr(uow.apis, "list_all"):
                all_slots = uow.apis.list_all()
                chain = [s for s in all_slots if getattr(s, "is_active", True)]
            elif hasattr(uow.apis, "list_by_user"):
                all_slots = uow.apis.list_by_user(user_id=1, include_public=True)
                chain = [s for s in all_slots if getattr(s, "is_active", True)]

        if not chain:
            return LaTeXRepairProposal(
                request_id=request_id,
                job_id=request.job_id,
                canonical_version=request.canonical_version,
                draft_revision=request.draft_revision,
                node_id=request.node_id,
                original_source=request.original_source,
                original_hash=request.source_hash,
                repaired_source="",
                repaired_hash="",
                change_summary="",
                preserved_invariants=(),
                validation_passed=False,
                validation_error="No active AI provider slot configured. Please configure an API key.",
                source_range=request.source_range,
            )

        # 2. Load repair prompt template
        template = _resolve_repair_prompt_content(self._prompt_path)

        current_diag = request.sanitized_diagnostics
        current_log = request.log_excerpt
        last_val_err: Optional[str] = None
        attempt = 0
        used_slot: Optional[ApiSlot] = None

        while attempt <= bounded_retries:
            substitutions = {
                "failure_type": request.renderer_type,
                "environment_profile": request.environment_profile,
                "allowed_packages": ", ".join(request.allowed_packages),
                "diagnostic_message": current_diag,
                "log_excerpt": current_log or "(no detailed log available)",
                "original_source": request.original_source,
            }
            prompt_text = re.sub(
                r"\{(failure_type|environment_profile|allowed_packages|diagnostic_message|log_excerpt|original_source)\}",
                lambda m: substitutions.get(m.group(1), m.group(0)),
                template,
            )

            # Re-attempt clarification if this is a follow-up
            if attempt > 0 and last_val_err:
                prompt_text += (
                    f"\n\n### PREVIOUS ATTEMPT COMPILATION FAILURE:\n"
                    f"Your previous repair proposal failed compilation with the following error:\n"
                    f"{last_val_err}\n"
                    f"Please provide an alternative minimal correction resolving this specific error while strictly maintaining all invariants."
                )

            # Execute AI text request (at_page=0 ensures OCR page counters are never incremented)
            raw_content, used_slot = self.ai_executor.execute_text_with_fallback(
                chain=chain,
                prompt=prompt_text,
                at_page=0,
            )

            if not raw_content or not raw_content.strip():
                last_val_err = "AI provider returned no response or timed out."
                attempt += 1
                continue

            # Parse and validate JSON structure and safety rules
            parsed_data, parse_err = self.parse_and_validate_ai_response(
                raw_response=raw_content,
                original_source=request.original_source,
            )

            if parse_err is not None or parsed_data is None:
                last_val_err = f"Invalid response schema: {parse_err}"
                attempt += 1
                continue

            candidate = parsed_data["repaired_source"]
            summary = parsed_data["change_summary"]
            invariants = tuple(parsed_data["preserved_invariants"])

            # 3. Mandatory Same-Renderer Compiler Verification Barrier
            val_passed, val_err, svg_xml, width, height = self.validate_candidate(
                candidate_source=candidate,
                renderer_type=request.renderer_type,
                environment_profile=request.environment_profile,
            )

            if val_passed:
                # Successfully compiled! Return validated proposal.
                candidate_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                return LaTeXRepairProposal(
                    request_id=request_id,
                    job_id=request.job_id,
                    canonical_version=request.canonical_version,
                    draft_revision=request.draft_revision,
                    node_id=request.node_id,
                    original_source=request.original_source,
                    original_hash=request.source_hash,
                    repaired_source=candidate,
                    repaired_hash=candidate_hash,
                    change_summary=summary,
                    preserved_invariants=invariants,
                    validation_passed=True,
                    validation_error=None,
                    rendered_preview_xml=svg_xml,
                    rendered_preview_width=width,
                    rendered_preview_height=height,
                    retry_count=attempt,
                    provider_label=used_slot.label if used_slot else "",
                    model_name=used_slot.selected_model if used_slot else "",
                    source_range=request.source_range,
                )

            # Compiler rejected candidate
            last_val_err = val_err
            current_diag = val_err or "Compiler rejection"
            attempt += 1

        # All bounded attempts failed compiler verification
        return LaTeXRepairProposal(
            request_id=request_id,
            job_id=request.job_id,
            canonical_version=request.canonical_version,
            draft_revision=request.draft_revision,
            node_id=request.node_id,
            original_source=request.original_source,
            original_hash=request.source_hash,
            repaired_source="",
            repaired_hash="",
            change_summary="",
            preserved_invariants=(),
            validation_passed=False,
            validation_error=last_val_err or "Candidate failed compiler verification.",
            retry_count=attempt - 1,
            provider_label=used_slot.label if used_slot else "",
            model_name=used_slot.selected_model if used_slot else "",
            source_range=request.source_range,
        )
