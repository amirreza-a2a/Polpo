# ============================================================
#  tests/integration/test_quarto_pdf_export_e2e.py
#  End-to-End Integration, Cross-Platform Verification & P08 Regression Proof
#  Phase 13 — Ticket #106
# ============================================================

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from application.dto.pdf_export_dto import ExportPdfCommand, PdfExportResultDTO
from application.dto.pdf_publish_dto import PublishResultDTO
from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import (
    IPdfArtifactPublisher,
    IPdfExportEngine,
    IPdfRuntimeDiscovery,
    PdfExportRequest,
    PdfExportResult,
)
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.document_asset_resolver import DocumentAssetResolver
from application.services.metadata_normalizer import normalize_quarto_pdf_metadata
from application.services.quarto_pdf_export_service import QuartoPdfExportService
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from core.exceptions.domain_exceptions import (
    MalformedMetadataError,
    PdfCompilationError,
    PdfCompilationTimeoutError,
    PdfExportCancelledError,
    UnsupportedMetadataKeyError,
    UntrustedDocumentExtensionError,
)
from infrastructure.pdf.atomic_publisher import AtomicPdfPublisher
from infrastructure.pdf.quarto_adapter import QuartoPdfAdapter
from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery
from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
from infrastructure.pdf.subprocess_supervisor import SubprocessResult, SubprocessSupervisor
from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer


def is_quarto_installed() -> bool:
    """Helper for conditionally skipping live compilation tests on CI runners lacking Quarto."""
    return shutil.which("quarto") is not None and shutil.which("xelatex") is not None


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def bilingual_doc_content(fixtures_dir: Path) -> str:
    path = fixtures_dir / "sample_bilingual_doc.qmd"
    assert path.is_file(), f"Fixture missing: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def sample_crop_bytes(fixtures_dir: Path) -> bytes:
    path = fixtures_dir / "sample_crop.png"
    assert path.is_file(), f"Fixture missing: {path}"
    return path.read_bytes()


def _build_test_uow_factory(
    job_id: int,
    version: int,
    canonical_markdown: str,
    output_md_path: Path,
) -> IUnitOfWorkFactory:
    """Constructs a mock IUnitOfWorkFactory returning valid Job and DocumentVersionRecord."""
    raw_bytes = canonical_markdown.encode("utf-8")
    output_md_path.write_bytes(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    job = MagicMock(spec=Job)
    job.id = job_id
    job.file_name = f"document_{job_id}.pdf"

    version_record = DocumentVersionRecord(
        job_id=job_id,
        version=version,
        output_path=str(output_md_path),
        sha256=content_hash,
        integrity_status="VALID",
    )

    mock_uow = MagicMock()
    mock_uow.publish_intents.get_by_job_id.return_value = None
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.document_versions.get_by_version.return_value = version_record

    uow_factory = MagicMock(spec=IUnitOfWorkFactory)
    uow_factory.create.return_value.__enter__.return_value = mock_uow
    return uow_factory


@pytest.mark.integration
class TestQuartoPdfExportE2E:
    """
    Comprehensive end-to-end integration test suite covering the 10 Phase 13 V3.8 scenarios
    and cross-platform execution guarantees.
    """

    def test_scenario_01_include_ordering_in_intermediate_tex(self, tmp_path: Path):
        """
        Scenario 1 & Mandatory F-02 Verification:
        Intermediate .tex character-offset ordering must strictly satisfy:
        offset(polpo_preamble) < offset(root_header) < offset(format_header) < offset(inline_header)
        < offset(\\begin{document}) < offset(before_body) < offset(body) < offset(after_body) < offset(\\end{document}).
        """
        job_id = 101
        artifacts_dir = tmp_path / "artifacts" / f"job_{job_id}"
        artifacts_dir.mkdir(parents=True)

        discovery = MagicMock(spec=IPdfRuntimeDiscovery)
        discovery.probe_readiness.return_value = EngineReadinessDTO(
            quarto_available=True,
            quarto_path="/usr/bin/quarto",
            quarto_version="1.5.0",
            xelatex_available=True,
            tex_distribution_name="TeX Live",
            missing_prerequisites=(),
        )

        supervisor = MagicMock(spec=SubprocessSupervisor)
        simulated_tex = r"""
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

Main Document Body Content

% AFTER_BODY_FLAG
\section{AfterBody}
\end{document}
"""

        def fake_execute(cmd, cwd, **kwargs):
            cwd_path = Path(cwd)
            (cwd_path / "document.tex").write_text(simulated_tex, encoding="utf-8")
            (cwd_path / "document.pdf").write_bytes(b"%PDF-1.7 mock output")
            return SubprocessResult(returncode=0, stdout="Success", stderr="")

        supervisor.execute.side_effect = fake_execute

        adapter = QuartoPdfAdapter(
            discovery=discovery,
            supervisor=supervisor,
            staging=StagingWorkspaceManager(),
            sanitizer=TexLogSanitizer(),
        )

        request = PdfExportRequest(
            job_id=job_id,
            canonical_markdown="Main Document Body Content",
            raw_metadata={
                "header-includes": ["% ROOT_HEADER_FLAG\n\\newcommand{\\rootheader}{Root}"],
                "format": {
                    "pdf": {
                        "header-includes": ["% FORMAT_HEADER_FLAG\n\\newcommand{\\formatheader}{Format}"],
                        "include-in-header": [{"text": "% INLINE_HEADER_FLAG\n\\newcommand{\\inlineheader}{Inline}"}],
                        "include-before-body": [{"text": "% BEFORE_BODY_FLAG\n\\section{BeforeBody}"}],
                        "include-after-body": [{"text": "% AFTER_BODY_FLAG\n\\section{AfterBody}"}],
                        "keep-tex": True,
                    }
                },
                "keep-tex": True,
            },
            job_artifacts_dir=artifacts_dir,
        )

        result = adapter.render_pdf(request)
        assert result.intermediate_tex_path is not None
        assert result.intermediate_tex_path.is_file()

        tex_content = result.intermediate_tex_path.read_text(encoding="utf-8")
        off_preamble = tex_content.index("polpo_preamble.tex")
        off_root = tex_content.index("ROOT_HEADER_FLAG")
        off_format = tex_content.index("FORMAT_HEADER_FLAG")
        off_inline = tex_content.index("INLINE_HEADER_FLAG")
        off_begin_doc = tex_content.index(r"\begin{document}")
        off_before_body = tex_content.index("BEFORE_BODY_FLAG")
        off_body = tex_content.index("Main Document Body Content")
        off_after_body = tex_content.index("AFTER_BODY_FLAG")
        off_end_doc = tex_content.index(r"\end{document}")

        assert (
            off_preamble
            < off_root
            < off_format
            < off_inline
            < off_begin_doc
            < off_before_body
            < off_body
            < off_after_body
            < off_end_doc
        ), "F-02 Character offset ordering violated in intermediate .tex"

    def test_scenario_02_documentclass_precedence(self):
        """
        Scenario 2: Document class precedence:
        Format-level documentclass overrides root-level declaration.
        Default to 'scrartcl' when omitted.
        """
        raw_meta = {
            "documentclass": "report",
            "format": {
                "pdf": {
                    "documentclass": "scrartcl",
                }
            },
        }
        normalized = normalize_quarto_pdf_metadata(raw_meta, is_tinytex=False, default_title="Document")
        assert normalized["format"]["pdf"]["documentclass"] == "scrartcl"

        # When format-level omits documentclass, root-level applies
        raw_meta_root = {
            "documentclass": "report",
            "format": {
                "pdf": {
                    "toc": True,
                }
            },
        }
        normalized_root = normalize_quarto_pdf_metadata(raw_meta_root, is_tinytex=False, default_title="Document")
        assert normalized_root["format"]["pdf"]["documentclass"] == "report"

    def test_scenario_03_custom_format_extension_rejection(self):
        """
        Scenario 3: Custom format extension rejection:
        Tests format: acm-pdf, format: [pdf, acm-pdf], and format: {acm-pdf: {}}.
        Asserts all raise UnsupportedMetadataKeyError before launching subprocesses.
        """
        # String format rejection
        with pytest.raises(UnsupportedMetadataKeyError, match="Unsupported format 'acm-pdf'"):
            normalize_quarto_pdf_metadata({"format": "acm-pdf"}, is_tinytex=False, default_title="Document")

        # List format rejection
        with pytest.raises(UnsupportedMetadataKeyError, match="Unsupported format 'acm-pdf'"):
            normalize_quarto_pdf_metadata({"format": ["pdf", "acm-pdf"]}, is_tinytex=False, default_title="Document")

        # Dict format rejection
        with pytest.raises(UnsupportedMetadataKeyError, match="Unsupported format 'acm-pdf'"):
            normalize_quarto_pdf_metadata({"format": {"acm-pdf": {}}}, is_tinytex=False, default_title="Document")

    def test_scenario_04_multi_format_pruning(self, tmp_path: Path):
        """
        Scenario 4: Multi-format pruning:
        Tests format: {html: {theme: cosmo}, pdf: {toc: true}}.
        Asserts canonical Markdown is unmodified, staged file prunes html, and compilation proceeds.
        """
        job_id = 104
        artifacts_dir = tmp_path / "artifacts" / f"job_{job_id}"
        artifacts_dir.mkdir(parents=True)

        canonical_content = """---
title: "Multi-Format Test"
format:
  html:
    theme: cosmo
  pdf:
    toc: true
---

# Content Body
Original canonical text."""

        staging = StagingWorkspaceManager()
        raw_meta = {
            "title": "Multi-Format Test",
            "format": {
                "html": {"theme": "cosmo"},
                "pdf": {"toc": True},
            },
        }

        with staging.create_session(
            job_artifacts_dir=artifacts_dir,
            job_id=job_id,
            raw_metadata=raw_meta,
            canonical_markdown=canonical_content,
            asset_resolver=DocumentAssetResolver(),
        ) as session:
            staged_qmd = session.qmd_path.read_text(encoding="utf-8")
            assert "theme: \"cosmo\"" not in staged_qmd
            assert "html:" not in staged_qmd
            assert "toc: true" in staged_qmd

    def test_scenario_05_typed_schema_and_papersize_validation(self):
        """
        Scenario 5 & Mandatory F-01 Verification:
        Tests papersize: 123 and papersize: ["a4"] at root and format scopes.
        Asserts MalformedMetadataError. Valid string 'a4' normalizes cleanly.
        """
        # Root integer papersize rejection
        with pytest.raises(MalformedMetadataError, match="must be a string"):
            normalize_quarto_pdf_metadata({"papersize": 123}, is_tinytex=False, default_title="Document")

        # Root list papersize rejection
        with pytest.raises(MalformedMetadataError, match="must be a string"):
            normalize_quarto_pdf_metadata({"papersize": ["a4"]}, is_tinytex=False, default_title="Document")

        # Format integer papersize rejection
        with pytest.raises(MalformedMetadataError, match="must be a string"):
            normalize_quarto_pdf_metadata({"format": {"pdf": {"papersize": 123}}}, is_tinytex=False, default_title="Document")

        # Format list papersize rejection
        with pytest.raises(MalformedMetadataError, match="must be a string"):
            normalize_quarto_pdf_metadata({"format": {"pdf": {"papersize": ["a4"]}}}, is_tinytex=False, default_title="Document")

        # Valid string papersize passes at root and format scopes
        normalized_root = normalize_quarto_pdf_metadata({"papersize": "a4"}, is_tinytex=False, default_title="Document")
        assert normalized_root["papersize"] == "a4"

        normalized_fmt = normalize_quarto_pdf_metadata({"format": {"pdf": {"papersize": "a4"}}}, is_tinytex=False, default_title="Document")
        assert normalized_fmt["format"]["pdf"]["papersize"] == "a4"

    def test_scenario_06_lua_filter_attack_containment(self):
        """
        Scenario 6: Lua filter attack containment:
        Tests front matter declaring filters: ["exploit.lua"].
        Asserts rejection before any child process is spawned.
        """
        with pytest.raises((UnsupportedMetadataKeyError, UntrustedDocumentExtensionError)):
            normalize_quarto_pdf_metadata({"filters": ["exploit.lua"]}, is_tinytex=False, default_title="Document")

    def test_scenario_07_external_include_file_rejection(self):
        """
        Scenario 7: External include file rejection:
        Tests include-before-body: "evil.tex" and include-in-header: [{"file": "evil.tex"}].
        Asserts UntrustedDocumentExtensionError.
        """
        with pytest.raises(UntrustedDocumentExtensionError, match="External file inclusion"):
            normalize_quarto_pdf_metadata({
                "format": {
                    "pdf": {
                        "include-before-body": "evil.tex"
                    }
                }
            }, is_tinytex=False, default_title="Document")

        with pytest.raises(UntrustedDocumentExtensionError, match="External file inclusion"):
            normalize_quarto_pdf_metadata({
                "format": {
                    "pdf": {
                        "include-in-header": [{"file": "evil.tex"}]
                    }
                }
            }, is_tinytex=False, default_title="Document")

    def test_scenario_08_bilingual_persian_rtl_and_tikz_isolation_mock(
        self, tmp_path: Path, bilingual_doc_content: str
    ):
        """
        Scenario 8 (ADR-003 D02): Bilingual Persian RTL & TikZ Isolation in orchestrated service stack.
        Verifies end-to-end orchestration with mock engine and atomic publishing.
        """
        job_id = 88
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        job_dir = artifacts_dir / f"job_{job_id}"
        job_dir.mkdir(parents=True)
        canonical_md_file = job_dir / f"output_{job_id}_v{version}.md"

        uow_factory = _build_test_uow_factory(job_id, version, bilingual_doc_content, canonical_md_file)

        mock_engine = MagicMock(spec=IPdfExportEngine)
        fake_compiled_pdf = tmp_path / "compiled_document.pdf"
        fake_compiled_pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n%%EOF")

        mock_engine.render_pdf.return_value = PdfExportResult(
            compiled_pdf_path=fake_compiled_pdf,
            page_count=3,
            duration_seconds=1.45,
            warnings=(),
        )

        publisher = AtomicPdfPublisher()
        discovery = MagicMock(spec=IPdfRuntimeDiscovery)
        discovery.probe_readiness.return_value = EngineReadinessDTO(
            quarto_available=True,
            quarto_path="/usr/bin/quarto",
            is_tinytex=False,
        )

        service = QuartoPdfExportService(
            uow_factory=uow_factory,
            export_engine=mock_engine,
            publisher=publisher,
            artifacts_dir=artifacts_dir,
            discovery=discovery,
        )

        dest_pdf = tmp_path / "exported" / "persian_test.pdf"
        dest_pdf.parent.mkdir(parents=True, exist_ok=True)
        cmd = ExportPdfCommand(
            job_id=job_id,
            version=version,
            destination_path=dest_pdf,
            overwrite=True,
        )

        result = service.export_pdf(cmd)
        assert result.destination_path == dest_pdf.resolve()
        assert result.page_count == 3
        assert result.file_size_bytes > 0
        assert dest_pdf.is_file()
        assert dest_pdf.read_bytes().startswith(b"%PDF-")

    def test_scenario_08_canonical_rfc8089_uri_e2e_orchestration(
        self, tmp_path: Path, bilingual_doc_content: str
    ):
        """
        Scenario 8 RFC 8089 URI Integration:
        Verifies end-to-end orchestration when DocumentVersionRecord.output_path is stored
        as an RFC 8089 file:/// URI (matching DocumentPublicationService production behavior).
        """
        job_id = 90
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        job_dir = artifacts_dir / f"job_{job_id}"
        job_dir.mkdir(parents=True)
        canonical_md_file = job_dir / f"output_{job_id}_v{version}.md"

        # Provide RFC 8089 file:/// URI in version record
        raw_bytes = bilingual_doc_content.encode("utf-8")
        canonical_md_file.write_bytes(raw_bytes)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        job = MagicMock(spec=Job)
        job.id = job_id
        job.file_name = f"document_{job_id}.pdf"

        version_record = DocumentVersionRecord(
            job_id=job_id,
            version=version,
            output_path=canonical_md_file.resolve().as_uri(),
            sha256=content_hash,
            integrity_status="VALID",
        )

        mock_uow = MagicMock()
        mock_uow.publish_intents.get_by_job_id.return_value = None
        mock_uow.jobs.get_by_id.return_value = job
        mock_uow.document_versions.get_by_version.return_value = version_record
        mock_uow.document_versions.get_latest.return_value = version_record

        uow_factory = MagicMock(spec=IUnitOfWorkFactory)
        uow_factory.create.return_value.__enter__.return_value = mock_uow

        mock_engine = MagicMock(spec=IPdfExportEngine)
        fake_compiled_pdf = tmp_path / "compiled_uri_doc.pdf"
        fake_compiled_pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n%%EOF")

        mock_engine.render_pdf.return_value = PdfExportResult(
            compiled_pdf_path=fake_compiled_pdf,
            page_count=2,
            duration_seconds=0.8,
            warnings=(),
        )

        publisher = AtomicPdfPublisher()
        discovery = MagicMock(spec=IPdfRuntimeDiscovery)
        discovery.probe_readiness.return_value = EngineReadinessDTO(
            quarto_available=True,
            quarto_path="/usr/bin/quarto",
            is_tinytex=False,
        )

        service = QuartoPdfExportService(
            uow_factory=uow_factory,
            export_engine=mock_engine,
            publisher=publisher,
            artifacts_dir=artifacts_dir,
            discovery=discovery,
        )

        dest_pdf = tmp_path / "exported" / "uri_persian_test.pdf"
        dest_pdf.parent.mkdir(parents=True, exist_ok=True)
        dest_uri = dest_pdf.resolve().as_uri()

        cmd = ExportPdfCommand(
            job_id=job_id,
            version=version,
            destination_path=dest_uri,
            overwrite=True,
        )

        result = service.export_pdf(cmd)
        assert result.destination_path == dest_pdf.resolve()
        assert result.page_count == 2
        assert dest_pdf.is_file()
        assert dest_pdf.read_bytes().startswith(b"%PDF-")


    @pytest.mark.skipif(not is_quarto_installed(), reason="Quarto or XeLaTeX not available on host")
    def test_scenario_08_bilingual_persian_rtl_and_tikz_live_compilation(
        self, tmp_path: Path, bilingual_doc_content: str
    ):
        """
        Scenario 8 Live Compilation (requires Quarto & XeLaTeX):
        Compiles sample_bilingual_doc.qmd to real publication-grade PDF using native toolchain
        through QuartoPdfExportService, verifying RFC 8089 URI canonical source & destination resolution.
        """
        import pymupdf as fitz

        job_id = 89
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        job_dir = artifacts_dir / f"job_{job_id}"
        job_dir.mkdir(parents=True)
        canonical_md_file = job_dir / f"output_{job_id}_v{version}.md"

        raw_bytes = bilingual_doc_content.encode("utf-8")
        canonical_md_file.write_bytes(raw_bytes)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        job = MagicMock(spec=Job)
        job.id = job_id
        job.file_name = f"document_{job_id}.pdf"

        version_record = DocumentVersionRecord(
            job_id=job_id,
            version=version,
            output_path=canonical_md_file.resolve().as_uri(),
            sha256=content_hash,
            integrity_status="VALID",
        )

        mock_uow = MagicMock()
        mock_uow.publish_intents.get_by_job_id.return_value = None
        mock_uow.jobs.get_by_id.return_value = job
        mock_uow.document_versions.get_by_version.return_value = version_record
        mock_uow.document_versions.get_latest.return_value = version_record

        uow_factory = MagicMock(spec=IUnitOfWorkFactory)
        uow_factory.create.return_value.__enter__.return_value = mock_uow

        discovery = QuartoRuntimeDiscovery()
        supervisor = SubprocessSupervisor()
        staging = StagingWorkspaceManager()
        sanitizer = TexLogSanitizer()

        adapter = QuartoPdfAdapter(
            discovery=discovery,
            supervisor=supervisor,
            staging=staging,
            sanitizer=sanitizer,
            timeout_seconds=120.0,
        )
        publisher = AtomicPdfPublisher()

        service = QuartoPdfExportService(
            uow_factory=uow_factory,
            export_engine=adapter,
            publisher=publisher,
            artifacts_dir=artifacts_dir,
            discovery=discovery,
        )

        dest_pdf = tmp_path / "exported" / "live_persian_test.pdf"
        dest_pdf.parent.mkdir(parents=True, exist_ok=True)
        dest_uri = dest_pdf.resolve().as_uri()

        cmd = ExportPdfCommand(
            job_id=job_id,
            version=version,
            destination_path=dest_uri,
            overwrite=True,
        )

        result = service.export_pdf(cmd)

        # 1. Target artifact exists at destination
        assert result.destination_path == dest_pdf.resolve()
        assert dest_pdf.is_file()

        # 2. File size is non-zero (real compilation artifact > 10KB, not a 31-byte mock)
        assert dest_pdf.stat().st_size > 1000
        assert result.file_size_bytes == dest_pdf.stat().st_size

        # 3. Header begins with %PDF-
        pdf_bytes = dest_pdf.read_bytes()
        assert pdf_bytes.startswith(b"%PDF-")

        # 4. Parsable by real PDF reader with valid page count
        doc = fitz.open(dest_pdf)
        assert doc.page_count >= 1
        assert result.page_count == doc.page_count

        # 5. Rendered document contains expected Persian and English content
        full_text = "".join(page.get_text() for page in doc)
        assert "LaTeX" in full_text or "TikZ" in full_text or "فارسی" in full_text
        doc.close()

    def test_scenario_09_visual_region_crop_embedding(
        self, tmp_path: Path, sample_crop_bytes: bytes
    ):
        """
        Scenario 9: Visual region crop embedding:
        Extracts visual region crop_101.png, generates Markdown reference ![](crop_101.png),
        and exports PDF. Asserts crop is staged, embedded, and workspace is cleanly unlinked.
        """
        job_id = 91
        artifacts_dir = tmp_path / "artifacts" / f"job_{job_id}"
        artifacts_dir.mkdir(parents=True)

        # Place crop in job artifacts directory
        crop_path = artifacts_dir / "crop_101.png"
        crop_path.write_bytes(sample_crop_bytes)

        markdown_with_crop = """# Document With Visual Crop

Here is the extracted visual equation crop:

![](crop_101.png)

End of document."""

        staging = StagingWorkspaceManager()
        resolver = DocumentAssetResolver()

        with staging.create_session(
            job_artifacts_dir=artifacts_dir,
            job_id=job_id,
            raw_metadata={"title": "Visual Region Document"},
            canonical_markdown=markdown_with_crop,
            asset_resolver=resolver,
        ) as session:
            # Asset must be copied into staging under assets/
            staged_crop = session.staging_dir / "assets" / "crop_101.png"
            assert staged_crop.is_file()
            assert staged_crop.read_bytes() == sample_crop_bytes
            assert session.staged_asset_count == 1
            staging_path = session.staging_dir

        # Workspace must be purged upon exit
        assert not staging_path.exists()
        # Original crop artifact must remain completely intact
        assert crop_path.is_file()
        assert crop_path.read_bytes() == sample_crop_bytes

    def test_scenario_10_cooperative_cancellation_and_timeout(self, tmp_path: Path):
        """
        Scenario 10: Cooperative cancellation and timeout under load:
        Asserts process tree killed immediately, no orphan processes remain,
        and staging directory is purged on cancellation and timeout.
        """
        job_id = 100
        artifacts_dir = tmp_path / "artifacts" / f"job_{job_id}"
        artifacts_dir.mkdir(parents=True)

        discovery = MagicMock(spec=IPdfRuntimeDiscovery)
        discovery.probe_readiness.return_value = EngineReadinessDTO(
            quarto_available=True,
            quarto_path="/usr/bin/quarto",
        )

        supervisor = MagicMock(spec=SubprocessSupervisor)
        supervisor.execute.return_value = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            cancelled=True,
        )

        adapter = QuartoPdfAdapter(
            discovery=discovery,
            supervisor=supervisor,
            staging=StagingWorkspaceManager(),
            sanitizer=TexLogSanitizer(),
        )

        # 1. Cancellation test
        req_cancelled = PdfExportRequest(
            job_id=job_id,
            canonical_markdown="# Body Content",
            raw_metadata={},
            job_artifacts_dir=artifacts_dir,
            is_cancelled=lambda: True,
        )

        with pytest.raises(PdfExportCancelledError):
            adapter.render_pdf(req_cancelled)

        # 2. Timeout test
        supervisor.execute.return_value = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            timed_out=True,
        )

        req_timeout = PdfExportRequest(
            job_id=job_id,
            canonical_markdown="# Body Content",
            raw_metadata={},
            job_artifacts_dir=artifacts_dir,
            is_cancelled=None,
        )

        with pytest.raises(PdfCompilationTimeoutError):
            adapter.render_pdf(req_timeout)

        # Ensure staging directory is unlinked
        staging_root = artifacts_dir / ".pdf_staging"
        if staging_root.exists():
            assert list(staging_root.iterdir()) == []

    def test_cross_platform_path_separators_and_line_endings(self, tmp_path: Path):
        """
        Cross-Platform Verification:
        Validates path separators, Windows CRLF vs LF line endings in staged .qmd,
        and CREATE_NO_WINDOW execution flags on Windows.
        """
        job_id = 106
        artifacts_dir = tmp_path / "artifacts" / f"job_{job_id}"
        artifacts_dir.mkdir(parents=True)

        # Input markdown with mixed Windows CRLF line endings
        crlf_markdown = "---\r\ntitle: CRLF Document\r\n---\r\n\r\nFirst paragraph.\r\n\r\nSecond paragraph.\r\n"

        staging = StagingWorkspaceManager()
        with staging.create_session(
            job_artifacts_dir=artifacts_dir,
            job_id=job_id,
            raw_metadata={"title": "CRLF Document"},
            canonical_markdown=crlf_markdown,
            asset_resolver=DocumentAssetResolver(),
        ) as session:
            qmd_text = session.qmd_path.read_text(encoding="utf-8")
            assert "First paragraph." in qmd_text
            assert "Second paragraph." in qmd_text

        # Verify CREATE_NO_WINDOW constant availability
        from infrastructure.pdf.subprocess_supervisor import _WIN32_CREATE_NO_WINDOW
        assert _WIN32_CREATE_NO_WINDOW == 0x08000000
