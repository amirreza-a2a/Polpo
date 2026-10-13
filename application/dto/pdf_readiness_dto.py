# ============================================================
#  application/dto/pdf_readiness_dto.py
#  Engine Readiness DTO for PDF Export Subsystem
#  Phase 13 — Ticket #98
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class EngineReadinessDTO:
    """
    Diagnostic data transfer object representing external compiler readiness.

    Attributes:
        quarto_available: Whether Quarto CLI is detected and meets minimum version.
        quarto_version: Detected version string (e.g. '1.4.550').
        quarto_path: Absolute path to the Quarto executable if found.
        xelatex_available: Whether XeLaTeX typesetting engine is available.
        is_tinytex: Whether TeX is managed by TinyTeX.
        tex_distribution_name: Identified TeX distribution ('TinyTeX', 'TeX Live', etc.).
        missing_prerequisites: Tuple of missing tool descriptions.
        diagnostic_instructions: Actionable, platform-appropriate setup instructions.
    """
    quarto_available: bool
    quarto_version: str = ""
    quarto_path: Optional[str] = None
    xelatex_available: bool = False
    is_tinytex: bool = False
    tex_distribution_name: str = ""
    missing_prerequisites: Tuple[str, ...] = ()
    diagnostic_instructions: str = ""

    @property
    def is_export_ready(self) -> bool:
        """True only if both Quarto CLI and XeLaTeX compiler are available."""
        return self.quarto_available and self.xelatex_available
