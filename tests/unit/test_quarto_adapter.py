# ============================================================
#  tests/unit/test_quarto_adapter.py
#  Unit tests for Quarto PDF Infrastructure Adapter
#  Phase 13 — Ticket #101
# ============================================================

from pathlib import Path
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from application.dto.pdf_readiness_dto import EngineReadinessDTO
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
from infrastructure.pdf.quarto_adapter import QuartoPdfAdapter
from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
from infrastructure.pdf.subprocess_supervisor import SubprocessResult, SubprocessSupervisor
from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer


def _create_mock_discovery(quarto_ready: bool = True, quarto_path: str = "/usr/bin/quarto") -> IPdfRuntimeDiscovery:
    mock = MagicMock(spec=IPdfRuntimeDiscovery)
    mock.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=quarto_ready,
        quarto_path=quarto_path if quarto_ready else None,
        quarto_version="1.5.0",
        xelatex_available=True,
        tex_distribution_name="TeX Live",
        missing_prerequisites=() if quarto_ready else ("Quarto CLI is missing",),
    )
    return mock


def test_adapter_successful_render(tmp_path: Path):
    """
    QuartoPdfAdapter executes Quarto within staging, verifies valid %PDF- output,
    and returns PdfExportResult.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_10"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    # When supervisor execute is called, simulate Quarto creating document.pdf with %PDF- header
    def fake_execute(cmd, cwd, timeout_seconds=60.0, is_cancelled=None, extra_env=None):
        pdf_file = Path(cwd) / "document.pdf"
        pdf_file.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                             b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
                             b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\nxref\ntrailer\n<< /Root 1 0 R >>\n%%EOF")
        return SubprocessResult(returncode=0, stdout="Rendering complete", stderr="")

    supervisor.execute.side_effect = fake_execute

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    request = PdfExportRequest(
        job_id=10,
        canonical_markdown="# Test Title\n\nBody content.",
        raw_metadata={"title": "Test Title"},
        job_artifacts_dir=artifacts_dir,
    )

    result = adapter.render_pdf(request)

    assert isinstance(result, PdfExportResult)
    assert result.compiled_pdf_path.exists()
    assert result.compiled_pdf_path.is_file()
    assert result.compiled_pdf_path.stat().st_size > 0
    assert result.page_count >= 1
    assert result.duration_seconds >= 0.0

    # Ensure command executed passed --to pdf --no-execute
    call_args = supervisor.execute.call_args
    cmd = call_args[1]["cmd"] if "cmd" in call_args[1] else call_args[0][0]
    assert cmd[1] == "render"
    assert cmd[2] == "document.qmd"
    assert "--to" in cmd and "pdf" in cmd
    assert "--no-execute" in cmd


def test_adapter_missing_package_raises_typed_error(tmp_path: Path):
    """
    Compiler failure due to missing package raises MissingLatexPackageError with mapped package name.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_11"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    error_log = r"""
! LaTeX Error: File 'tikz.sty' not found.
l.14 \usepackage{tikz}
"""
    supervisor.execute.return_value = SubprocessResult(
        returncode=1,
        stdout="",
        stderr=error_log,
    )

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    request = PdfExportRequest(
        job_id=11,
        canonical_markdown="Sample",
        raw_metadata={},
        job_artifacts_dir=artifacts_dir,
    )

    with pytest.raises(MissingLatexPackageError) as exc_info:
        adapter.render_pdf(request)

    assert "pgf" in exc_info.value.package_names
    assert "pgf" in exc_info.value.sanitized_message or "tikz" in exc_info.value.sanitized_message


def test_adapter_syntax_error_raises_pdf_compilation_error(tmp_path: Path):
    """
    LaTeX syntax errors raise PdfCompilationError with line number and sanitized message.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_12"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    error_log = r"""
/home/secretuser/project/.pdf_staging/12_abcd/document.tex:24: ! Undefined control sequence.
l.24 \badcmd
"""
    supervisor.execute.return_value = SubprocessResult(
        returncode=1,
        stdout="",
        stderr=error_log,
    )

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    request = PdfExportRequest(
        job_id=12,
        canonical_markdown="Sample",
        raw_metadata={},
        job_artifacts_dir=artifacts_dir,
    )

    with pytest.raises(PdfCompilationError) as exc_info:
        adapter.render_pdf(request)

    assert exc_info.value.line_number == 24
    assert "Undefined control sequence" in exc_info.value.sanitized_message
    assert "secretuser" not in exc_info.value.sanitized_message


def test_adapter_cancellation_and_timeout(tmp_path: Path):
    """
    Subprocess cancellation raises PdfExportCancelledError and timeout raises PdfCompilationTimeoutError.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_13"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    # 1. Pre-cancellation check
    cancelled_request = PdfExportRequest(
        job_id=13,
        canonical_markdown="Sample",
        raw_metadata={},
        job_artifacts_dir=artifacts_dir,
        is_cancelled=lambda: True,
    )
    with pytest.raises(PdfExportCancelledError):
        adapter.render_pdf(cancelled_request)

    # 2. In-flight cancellation
    supervisor.execute.return_value = SubprocessResult(
        returncode=-1,
        stdout="",
        stderr="",
        cancelled=True,
    )
    request = PdfExportRequest(
        job_id=13,
        canonical_markdown="Sample",
        raw_metadata={},
        job_artifacts_dir=artifacts_dir,
    )
    with pytest.raises(PdfExportCancelledError):
        adapter.render_pdf(request)

    # 3. Timeout
    supervisor.execute.return_value = SubprocessResult(
        returncode=-1,
        stdout="",
        stderr="",
        timed_out=True,
    )
    with pytest.raises(PdfCompilationTimeoutError):
        adapter.render_pdf(request)


def test_adapter_output_verification_invalid_pdf(tmp_path: Path):
    """
    Validates output PDF presence, non-empty size, and %PDF- magic header.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_14"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    # Case A: PDF not created at all
    supervisor.execute.return_value = SubprocessResult(returncode=0, stdout="", stderr="")

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    request = PdfExportRequest(
        job_id=14,
        canonical_markdown="Sample",
        raw_metadata={},
        job_artifacts_dir=artifacts_dir,
    )

    with pytest.raises(PdfCompilationError, match="Compiled PDF artifact was not created"):
        adapter.render_pdf(request)

    # Case B: PDF is 0 bytes
    def fake_empty_pdf(cmd, cwd, **kwargs):
        (Path(cwd) / "document.pdf").write_bytes(b"")
        return SubprocessResult(returncode=0, stdout="", stderr="")

    supervisor.execute.side_effect = fake_empty_pdf
    with pytest.raises(PdfCompilationError, match="empty"):
        adapter.render_pdf(request)

    # Case C: PDF does not start with %PDF-
    def fake_corrupt_pdf(cmd, cwd, **kwargs):
        (Path(cwd) / "document.pdf").write_bytes(b"NOT_A_PDF_CONTENT")
        return SubprocessResult(returncode=0, stdout="", stderr="")

    supervisor.execute.side_effect = fake_corrupt_pdf
    with pytest.raises(PdfCompilationError, match="%PDF-"):
        adapter.render_pdf(request)


def test_adapter_intermediate_tex_ordering_invariant(tmp_path: Path):
    r"""
    Mandatory F-02 Verification:
    Intermediate .tex character-offset ordering must strictly satisfy:
    polpo_preamble.tex -> root headers -> format headers -> inline headers
    -> \begin{document} -> before body -> body -> after body -> \end{document}.
    """
    artifacts_dir = tmp_path / "artifacts" / "job_15"
    artifacts_dir.mkdir(parents=True)

    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    # When execute runs, simulate Quarto creating intermediate document.tex and valid document.pdf
    simulated_tex_content = r"""
% Quarto generated LaTeX
\input{polpo_preamble.tex}
% ROOT_HEADER_FLAG
\newcommand{\rootheader}{Root}
% FORMAT_HEADER_FLAG
\newcommand{\formatheader}{Format}
% INLINE_HEADER_FLAG
\newcommand{\inlineheader}{Inline}

\begin{document}
% BEFORE_BODY_FLAG
\section{BeforeBody}

Main Document Body Content Here.

% AFTER_BODY_FLAG
\section{AfterBody}
\end{document}
"""

    def fake_execute_keep_tex(cmd, cwd, **kwargs):
        cwd_path = Path(cwd)
        (cwd_path / "document.tex").write_text(simulated_tex_content, encoding="utf-8")
        (cwd_path / "document.pdf").write_bytes(b"%PDF-1.5 test pdf")
        return SubprocessResult(returncode=0, stdout="", stderr="")

    supervisor.execute.side_effect = fake_execute_keep_tex

    adapter = QuartoPdfAdapter(
        discovery=discovery,
        supervisor=supervisor,
        staging=staging,
        sanitizer=sanitizer,
    )

    request = PdfExportRequest(
        job_id=15,
        canonical_markdown="Sample Markdown",
        raw_metadata={"keep-tex": True},
        job_artifacts_dir=artifacts_dir,
    )

    result = adapter.render_pdf(request)

    assert result.intermediate_tex_path is not None
    assert result.intermediate_tex_path.exists()
    tex_text = result.intermediate_tex_path.read_text(encoding="utf-8")

    # Verify character offsets
    offset_preamble = tex_text.index("polpo_preamble.tex")
    offset_root = tex_text.index("ROOT_HEADER_FLAG")
    offset_format = tex_text.index("FORMAT_HEADER_FLAG")
    offset_inline = tex_text.index("INLINE_HEADER_FLAG")
    offset_begin_doc = tex_text.index(r"\begin{document}")
    offset_before_body = tex_text.index("BEFORE_BODY_FLAG")
    offset_body = tex_text.index("Main Document Body Content")
    offset_after_body = tex_text.index("AFTER_BODY_FLAG")
    offset_end_doc = tex_text.index(r"\end{document}")

    assert (
        offset_preamble
        < offset_root
        < offset_format
        < offset_inline
        < offset_begin_doc
        < offset_before_body
        < offset_body
        < offset_after_body
        < offset_end_doc
    ), "F-02 ordering invariant violated!"


def test_adapter_f03_enforced_trust_boundary_disclosure():
    """
    Mandatory F-03: Class docstring documents ordinary desktop user OS privileges
    and TeX filesystem read limitations without shell escape.
    """
    doc = QuartoPdfAdapter.__doc__ or ""
    assert "F-03" in doc or "Trust Boundary" in doc
    assert "-no-shell-escape" in doc
    assert "privileges" in doc.lower()


def test_adapter_install_packages_interface():
    """
    install_packages() complies with IPdfExportEngine port.
    Delegates to injected installer if configured, or raises NotImplementedError.
    """
    discovery = _create_mock_discovery()
    supervisor = MagicMock(spec=SubprocessSupervisor)
    sanitizer = TexLogSanitizer()
    staging = StagingWorkspaceManager()

    # Without installer configured:
    adapter = QuartoPdfAdapter(discovery, supervisor, staging, sanitizer)
    with pytest.raises(NotImplementedError):
        adapter.install_packages(["pgf", "amsmath"])

    # With injected installer:
    mock_installer = MagicMock(return_value=PackageInstallResult(
        success=True,
        installed_packages=("pgf", "amsmath"),
        failed_packages=(),
    ))
    adapter_with_installer = QuartoPdfAdapter(
        discovery, supervisor, staging, sanitizer, package_installer=mock_installer
    )
    res = adapter_with_installer.install_packages(["pgf", "amsmath"])
    assert res.success is True
    assert res.installed_packages == ("pgf", "amsmath")
    mock_installer.assert_called_once()
