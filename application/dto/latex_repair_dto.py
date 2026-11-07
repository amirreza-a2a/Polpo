# ============================================================
#  application/dto/latex_repair_dto.py
#  Data Transfer Objects for AI-Assisted LaTeX Error Repair
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Optional, Tuple


@dataclass(frozen=True)
class LaTeXRepairRequest:
    """Request DTO specifying a failing LaTeX fragment to repair via AI.

    Attributes:
        job_id: Numeric identifier of the active document job.
        canonical_version: Immutable document version watermark.
        node_id: Stable identifier of the failing Markdown/presentation node.
        original_source: Raw LaTeX formula or diagram source code.
        renderer_type: Target renderer ('diagram', 'math_inline', 'math_display', 'quarto_pdf').
        sanitized_diagnostics: Sanitized compiler error message.
        draft_revision: Monotonic revision number of unsaved editor buffer.
        source_range: Tuple of (start_line, end_line) in canonical Markdown.
        source_hash: Deterministic SHA-256 digest of original_source.
        log_excerpt: Bounded and sanitized compiler log excerpt.
        error_line: Optional fragment-relative line number of syntax error.
        environment_profile: Rendering preamble/environment profile.
        allowed_packages: Tuple of TeX package names available in environment.
        neighboring_context: Surrounding document context for semantic disambiguation.
    """

    job_id: int
    canonical_version: int
    node_id: str
    original_source: str
    renderer_type: str
    sanitized_diagnostics: str
    draft_revision: int = 0
    source_range: Tuple[int, int] = (0, 0)
    source_hash: str = ""
    log_excerpt: str = ""
    error_line: Optional[int] = None
    environment_profile: str = "default"
    allowed_packages: Tuple[str, ...] = ("amsmath", "amssymb", "tikz", "pgfplots", "circuitikz", "tikz-cd")
    neighboring_context: str = ""

    def __post_init__(self) -> None:
        if not self.source_hash:
            h = hashlib.sha256(self.original_source.encode("utf-8")).hexdigest()
            object.__setattr__(self, "source_hash", h)


@dataclass(frozen=True)
class LaTeXRepairProposal:
    """Validated repair candidate proposed by the AI repair service.

    Attributes:
        request_id: Deterministic correlation identifier for this repair session.
        job_id: Associated document job identifier.
        canonical_version: Associated canonical document version watermark.
        draft_revision: Associated editor draft revision number.
        node_id: Target presentation node identifier.
        original_source: Original failing LaTeX source code.
        original_hash: SHA-256 hash of original_source.
        repaired_source: Corrected LaTeX fragment proposed by the AI.
        repaired_hash: SHA-256 hash of repaired_source.
        change_summary: English summary of changes made.
        preserved_invariants: List of domain invariants verified as preserved.
        validation_passed: True if the candidate successfully compiled with target renderer.
        validation_error: Compilation error message if validation failed.
        rendered_preview_xml: Rendered vector SVG XML text if validation succeeded.
        rendered_preview_width: Rendered width in points.
        rendered_preview_height: Rendered height in points.
        retry_count: Number of follow-up attempts made.
        provider_label: Label of the AI provider slot that produced candidate.
        model_name: Name of AI model that produced candidate.
    """

    request_id: str
    job_id: int
    canonical_version: int
    draft_revision: int
    node_id: str
    original_source: str
    original_hash: str
    repaired_source: str
    repaired_hash: str
    change_summary: str
    preserved_invariants: Tuple[str, ...]
    validation_passed: bool
    validation_error: Optional[str] = None
    rendered_preview_xml: Optional[str] = None
    rendered_preview_width: float = 0.0
    rendered_preview_height: float = 0.0
    retry_count: int = 0
    provider_label: str = ""
    model_name: str = ""

    def to_dict(self) -> dict:
        return {
            "requestId": self.request_id,
            "jobId": self.job_id,
            "canonicalVersion": self.canonical_version,
            "draftRevision": self.draft_revision,
            "nodeId": self.node_id,
            "originalSource": self.original_source,
            "originalHash": self.original_hash,
            "repairedSource": self.repaired_source,
            "repairedHash": self.repaired_hash,
            "changeSummary": self.change_summary,
            "preservedInvariants": list(self.preserved_invariants),
            "validationPassed": self.validation_passed,
            "validationError": self.validation_error or "",
            "renderedPreviewXml": self.rendered_preview_xml or "",
            "renderedPreviewWidth": self.rendered_preview_width,
            "renderedPreviewHeight": self.rendered_preview_height,
            "retryCount": self.retry_count,
            "providerLabel": self.provider_label,
            "modelName": self.model_name,
        }
