# ============================================================
#  application/ports/pdf_export_port.py
#  Abstract Application Ports for PDF Export Subsystem
#  Phase 13 — Ticket #98
# ============================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from application.dto.pdf_readiness_dto import EngineReadinessDTO


@dataclass(frozen=True)
class PdfExportRequest:
    """
    Request DTO passed to IPdfExportEngine.render_pdf().

    Attributes:
        job_id: Numeric identifier for the document job.
        canonical_markdown: Immutable canonical Markdown source text.
        raw_metadata: Document front matter metadata dictionary.
        job_artifacts_dir: Local path containing job artifacts and visual crops.
        is_tinytex: Whether active TeX environment is TinyTeX.
        is_cancelled: Optional cooperative cancellation check callable.
    """
    job_id: int
    canonical_markdown: str
    raw_metadata: Dict[str, Any]
    job_artifacts_dir: Path
    is_tinytex: bool = False
    is_cancelled: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class PdfExportResult:
    """
    Result DTO returned by IPdfExportEngine.render_pdf().

    Attributes:
        compiled_pdf_path: Absolute path to the compiled PDF artifact.
        page_count: Number of pages in the compiled PDF.
        duration_seconds: Compilation duration in seconds.
        warnings: Tuple of non-fatal warning messages.
        intermediate_tex_path: Optional path to retained intermediate .tex file.
    """
    compiled_pdf_path: Path
    page_count: int
    duration_seconds: float
    warnings: Tuple[str, ...] = ()
    intermediate_tex_path: Optional[Path] = None


@dataclass(frozen=True)
class PackageInstallResult:
    """
    Result DTO returned by IPdfExportEngine.install_packages().

    Attributes:
        success: Whether package installation succeeded.
        installed_packages: Tuple of successfully installed package identifiers.
        failed_packages: Tuple of package identifiers that failed to install.
        error_message: Error or diagnostic message if unsuccessful.
    """
    success: bool
    installed_packages: Tuple[str, ...] = ()
    failed_packages: Tuple[str, ...] = ()
    error_message: str = ""


class IPdfExportEngine(ABC):
    """
    Application port interface for PDF compilation and typesetting engines.

    Security Trust Boundary Disclosure (Mandatory F-03):
    Child processes execute under ordinary desktop user privileges.
    -no-shell-escape blocks arbitrary command execution via \\write18 but does
    not provide OS-level filesystem read sandboxing for TeX primitives.
    """

    @abstractmethod
    def render_pdf(self, request: PdfExportRequest) -> PdfExportResult:
        """
        Renders canonical Markdown to a publication-grade PDF in isolated staging.

        Args:
            request: Typed PDF export request containing source and metadata.

        Returns:
            PdfExportResult with compiled PDF path, page count, and duration.
        """
        raise NotImplementedError

    @abstractmethod
    def install_packages(
        self,
        package_names: Sequence[str],
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PackageInstallResult:
        """
        Installs missing LaTeX packages into the active TeX distribution.

        Args:
            package_names: Sequence of CTAN package identifiers to install.
            is_cancelled: Optional cooperative cancellation callback.

        Returns:
            PackageInstallResult indicating outcome.
        """
        raise NotImplementedError


class IPdfRuntimeDiscovery(ABC):
    """Port interface for discovering external Quarto and TeX runtimes."""

    @abstractmethod
    def probe_readiness(
        self,
        configured_quarto_path: Optional[str] = None,
    ) -> EngineReadinessDTO:
        """
        Probes the host environment for Quarto and XeLaTeX runtimes.

        Args:
            configured_quarto_path: Optional path configured by user in AppSettings.

        Returns:
            EngineReadinessDTO containing availability, versions, and diagnostics.
        """
        raise NotImplementedError
