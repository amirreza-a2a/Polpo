# ============================================================
#  infrastructure/pdf/quarto_adapter.py
#  Quarto PDF Infrastructure Adapter & Compiler Engine
#  Phase 13 — Ticket #101
# ============================================================

"""
Quarto PDF Infrastructure Adapter.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
Child processes execute under ordinary desktop user OS privileges.
The compilation flag -no-shell-escape strictly blocks arbitrary shell command
execution and subprocess invocation via \\write18. However, -no-shell-escape does NOT
provide operating-system filesystem read sandboxing for native TeX primitives
(such as \\input and \\openin).
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import Callable, Optional, Sequence

from application.ports.pdf_export_port import (
    IPdfExportEngine,
    IPdfRuntimeDiscovery,
    PackageInstallResult,
    PdfExportRequest,
    PdfExportResult,
)
from application.services.document_asset_resolver import DocumentAssetResolver
from core.exceptions.domain_exceptions import (
    MissingLatexPackageError,
    PdfCompilationError,
    PdfCompilationTimeoutError,
    PdfExportCancelledError,
)
from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor
from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer

logger = logging.getLogger(__name__)

_DEFAULT_COMPILATION_TIMEOUT_SECONDS: float = 60.0


class QuartoPdfAdapter(IPdfExportEngine):
    """
    Infrastructure adapter implementing IPdfExportEngine for publication-grade
    PDF document export using Quarto CLI and XeLaTeX.

    Security Trust Boundary Disclosure (Mandatory F-03):
    Child processes execute under ordinary desktop user privileges.
    -no-shell-escape blocks arbitrary command execution via \\write18 but does
    not provide OS-level filesystem read sandboxing for TeX primitives.
    """

    def __init__(
        self,
        discovery: IPdfRuntimeDiscovery,
        supervisor: SubprocessSupervisor,
        staging: StagingWorkspaceManager,
        sanitizer: TexLogSanitizer,
        asset_resolver: Optional[DocumentAssetResolver] = None,
        package_installer: Optional[
            Callable[[Sequence[str], Optional[Callable[[], bool]]], PackageInstallResult]
        ] = None,
        timeout_seconds: float = _DEFAULT_COMPILATION_TIMEOUT_SECONDS,
    ) -> None:
        self._discovery = discovery
        self._supervisor = supervisor
        self._staging = staging
        self._sanitizer = sanitizer
        self._asset_resolver = asset_resolver or DocumentAssetResolver()
        self._package_installer = package_installer
        self._timeout_seconds = timeout_seconds

    def render_pdf(self, request: PdfExportRequest) -> PdfExportResult:
        """
        Renders canonical Markdown to a publication-grade PDF in isolated staging.

        Args:
            request: Typed PDF export request containing source text and metadata.

        Returns:
            PdfExportResult with compiled PDF path, page count, and duration.

        Raises:
            PdfExportCancelledError: If export was cancelled before or during compilation.
            PdfCompilationTimeoutError: If compilation exceeds configured timeout.
            MissingLatexPackageError: If LaTeX packages are missing.
            PdfCompilationError: If compilation fails due to syntax or compiler errors.
        """
        # 1. Runtime readiness pre-flight check
        readiness = self._discovery.probe_readiness()
        if not readiness.quarto_available or not readiness.quarto_path:
            missing_desc = (
                ", ".join(readiness.missing_prerequisites)
                if readiness.missing_prerequisites
                else "Quarto executable not found"
            )
            raise PdfCompilationError(f"Quarto runtime not available: {missing_desc}")

        quarto_path = readiness.quarto_path

        # 2. Cooperative cancellation pre-flight check
        if request.is_cancelled and request.is_cancelled():
            raise PdfExportCancelledError("PDF export was cancelled before rendering started.")

        # 3. Isolated staging workspace execution
        with self._staging.create_session(
            job_artifacts_dir=request.job_artifacts_dir,
            job_id=request.job_id,
            raw_metadata=request.raw_metadata,
            canonical_markdown=request.canonical_markdown,
            asset_resolver=self._asset_resolver,
            is_tinytex=request.is_tinytex,
        ) as session:
            # Audit effective engine options in barrier configuration (Security invariant)
            barrier_content = session.quarto_yml_path.read_text(encoding="utf-8")
            if "-no-shell-escape" not in barrier_content or "xelatex" not in barrier_content:
                raise PdfCompilationError(
                    "Effective engine options security violation: -no-shell-escape or "
                    "xelatex missing from staging barrier configuration."
                )

            # Command assembly
            cmd = [
                quarto_path,
                "render",
                "document.qmd",
                "--to",
                "pdf",
                "--pdf-engine",
                "xelatex",
                "--no-execute",
            ]

            # Subprocess execution
            start_time = time.monotonic()
            sub_res = self._supervisor.execute(
                cmd=cmd,
                cwd=session.staging_dir,
                timeout_seconds=self._timeout_seconds,
                is_cancelled=request.is_cancelled,
            )
            duration = max(0.0, time.monotonic() - start_time)

            # Cancellation & timeout mapping
            if sub_res.cancelled:
                raise PdfExportCancelledError("PDF export was cancelled.")
            if sub_res.timed_out:
                raise PdfCompilationTimeoutError("PDF compilation timed out.")

            # Non-zero exit code mapping & log sanitization
            if sub_res.returncode != 0:
                combined_log = f"{sub_res.stderr}\n{sub_res.stdout}"
                for log_file in session.staging_dir.glob("*.log"):
                    try:
                        combined_log += "\n" + log_file.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

                diagnostic = self._sanitizer.sanitize(combined_log)
                if diagnostic.missing_packages:
                    raise MissingLatexPackageError(
                        package_names=diagnostic.missing_packages,
                        sanitized_message=diagnostic.sanitized_message,
                    )
                raise PdfCompilationError(
                    sanitized_message=diagnostic.sanitized_message,
                    line_number=diagnostic.line_number,
                )

            # Output Verification:
            # 1. File exists
            staging_pdf = session.staging_dir / "document.pdf"
            if not staging_pdf.is_file():
                raise PdfCompilationError("Compiled PDF artifact was not created.")

            # 2. Non-zero size
            if staging_pdf.stat().st_size == 0:
                raise PdfCompilationError("Compiled PDF artifact is empty (0 bytes).")

            # 3. %PDF- magic header
            with open(staging_pdf, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                raise PdfCompilationError(
                    f"Compiled artifact does not begin with '%PDF-' magic header (found {header!r})."
                )

            # Page count determination
            page_count = self._determine_page_count(staging_pdf)

            # Intermediate .tex preservation for F-02 verification
            dest_intermediate_tex: Optional[Path] = None
            staging_tex = session.staging_dir / "document.tex"
            pdf_staging_parent = request.job_artifacts_dir / ".pdf_staging"
            pdf_staging_parent.mkdir(parents=True, exist_ok=True)
            token = secrets.token_hex(8)

            if request.raw_metadata.get("keep-tex") and staging_tex.is_file():
                dest_intermediate_tex = pdf_staging_parent / f"document_{request.job_id}_{token}.tex"
                shutil.copyfile(staging_tex, dest_intermediate_tex)

            # Copy compiled PDF artifact out of staging before session purge
            persisted_pdf_path = pdf_staging_parent / f"compiled_{request.job_id}_{token}.pdf"
            shutil.copyfile(staging_pdf, persisted_pdf_path)

            return PdfExportResult(
                compiled_pdf_path=persisted_pdf_path,
                page_count=page_count,
                duration_seconds=duration,
                warnings=(),
                intermediate_tex_path=dest_intermediate_tex,
            )

    def install_packages(
        self,
        package_names: Sequence[str],
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PackageInstallResult:
        """
        Installs missing LaTeX packages into the active TeX distribution.
        Delegated to TinyTexManager (Ticket #104).
        """
        if self._package_installer is not None:
            return self._package_installer(package_names, is_cancelled)
        raise NotImplementedError(
            "Package recovery is managed by TinyTexManager in Phase 13 Ticket #104."
        )

    @staticmethod
    def _determine_page_count(pdf_path: Path) -> int:
        """Determines page count of compiled PDF with robust fallbacks."""
        try:
            import fitz
            with fitz.open(pdf_path) as doc:
                return max(1, doc.page_count)
        except Exception:
            pass

        try:
            content = pdf_path.read_bytes()
            matches = re.findall(rb"/Type\s*/Page\b", content)
            if matches:
                return max(1, len(matches))
        except Exception:
            pass

        return 1
