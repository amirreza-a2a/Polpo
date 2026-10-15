# ============================================================
#  tests/unit/test_quarto_pdf_export_service.py
#  Unit tests for Quarto PDF Export Application Service
#  Phase 13 — Ticket #103
# ============================================================

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

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
from application.ports.unit_of_work import IUnitOfWork, IUnitOfWorkFactory
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    DestinationDirectoryNotFoundError,
    ExportSourceNotFoundError,
)
from application.services.quarto_pdf_export_service import QuartoPdfExportService
from core.entities.artifact import resolve_canonical_file_path
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    EntityNotFoundError,
    MissingLatexPackageError,
    PdfExportCancelledError,
    PublicationInProgressError,
)


def _setup_test_environment(tmp_path: Path, content: str = "# Sample\n\nCanonical markdown content."):
    job_id = 42
    version = 2
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    job_artifacts_dir = artifacts_dir / f"job_{job_id}"
    job_artifacts_dir.mkdir(parents=True)

    doc_file = job_artifacts_dir / f"output_{job_id}_v{version}.md"
    raw_bytes = content.encode("utf-8")
    doc_file.write_bytes(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    job = MagicMock(spec=Job)
    job.id = job_id
    job.file_name = "test_document.pdf"

    version_record = DocumentVersionRecord(
        job_id=job_id,
        version=version,
        output_path=str(doc_file),
        sha256=content_hash,
        integrity_status="VALID",
    )

    mock_uow = MagicMock()
    mock_uow.publish_intents.get_by_job_id.return_value = None
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.document_versions.get_by_version.return_value = version_record

    mock_uow_factory = MagicMock(spec=IUnitOfWorkFactory)
    mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

    mock_engine = MagicMock(spec=IPdfExportEngine)
    mock_publisher = MagicMock(spec=IPdfArtifactPublisher)
    mock_discovery = MagicMock(spec=IPdfRuntimeDiscovery)
    mock_discovery.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=True,
        quarto_path="/usr/bin/quarto",
        is_tinytex=False,
    )

    return {
        "job_id": job_id,
        "version": version,
        "doc_file": doc_file,
        "artifacts_dir": artifacts_dir,
        "uow_factory": mock_uow_factory,
        "uow": mock_uow,
        "engine": mock_engine,
        "publisher": mock_publisher,
        "discovery": mock_discovery,
        "version_record": version_record,
    }


def test_export_service_happy_path(tmp_path: Path):
    """
    QuartoPdfExportService orchestrates end-to-end export: resolves document version,
    normalizes metadata, calls export engine, publishes atomically, and returns DTO.
    """
    env = _setup_test_environment(tmp_path)
    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    fake_compiled_pdf = tmp_path / "compiled.pdf"
    fake_compiled_pdf.write_bytes(b"%PDF-1.7 mock content")

    env["engine"].render_pdf.return_value = PdfExportResult(
        compiled_pdf_path=fake_compiled_pdf,
        page_count=4,
        duration_seconds=3.2,
        warnings=(),
    )

    env["publisher"].publish.return_value = PublishResultDTO(
        destination_path=dest_path,
        published_bytes=2048,
        cleanup_warning=None,
    )

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
        discovery=env["discovery"],
    )

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
        overwrite=False,
    )

    result = service.export_pdf(cmd)

    assert isinstance(result, PdfExportResultDTO)
    assert result.destination_path == dest_path
    assert result.canonical_version == env["version"]
    assert result.page_count == 4
    assert result.file_size_bytes == 2048
    assert result.duration_seconds == 3.2
    assert result.cleanup_warning is None

    env["engine"].render_pdf.assert_called_once()
    render_req = env["engine"].render_pdf.call_args[0][0]
    assert isinstance(render_req, PdfExportRequest)
    assert render_req.job_id == env["job_id"]
    assert "format" in render_req.raw_metadata

    env["publisher"].publish.assert_called_once_with(
        source_compiled_pdf=fake_compiled_pdf,
        destination_path=dest_path,
        overwrite=False,
    )


def test_export_service_version_mismatch(tmp_path: Path):
    """
    Raises CanonicalDocumentIntegrityError when file content SHA-256 differs from version record.
    """
    env = _setup_test_environment(tmp_path)
    # Alter file on disk after version record was created
    env["doc_file"].write_bytes(b"# Corrupted Content")

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
    )

    with pytest.raises(CanonicalDocumentIntegrityError, match="SHA-256 mismatch"):
        service.export_pdf(cmd)

    env["engine"].render_pdf.assert_not_called()


def test_export_service_propagates_compilation_error(tmp_path: Path):
    """
    Compiler errors such as MissingLatexPackageError propagate cleanly through the service.
    """
    env = _setup_test_environment(tmp_path)
    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    env["engine"].render_pdf.side_effect = MissingLatexPackageError(
        package_names=("pgf",),
        sanitized_message="Missing pgf package",
    )

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
    )

    with pytest.raises(MissingLatexPackageError) as exc_info:
        service.export_pdf(cmd)

    assert "pgf" in exc_info.value.package_names


def test_export_service_propagates_destination_exists(tmp_path: Path):
    """
    Destination existing with overwrite=False fails fast with DestinationAlreadyExistsError.
    """
    env = _setup_test_environment(tmp_path)
    dest_path = tmp_path / "export" / "existing.pdf"
    dest_path.parent.mkdir(parents=True)
    dest_path.write_bytes(b"existing content")

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
        overwrite=False,
    )

    with pytest.raises(DestinationAlreadyExistsError):
        service.export_pdf(cmd)

    env["engine"].render_pdf.assert_not_called()


def test_export_service_surfaces_cleanup_warning(tmp_path: Path):
    """
    Non-fatal staging cleanup warnings returned by publisher are surfaced in PdfExportResultDTO.
    """
    env = _setup_test_environment(tmp_path)
    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    fake_compiled_pdf = tmp_path / "compiled.pdf"
    fake_compiled_pdf.write_bytes(b"%PDF-1.7 mock content")

    env["engine"].render_pdf.return_value = PdfExportResult(
        compiled_pdf_path=fake_compiled_pdf,
        page_count=2,
        duration_seconds=1.5,
    )

    env["publisher"].publish.return_value = PublishResultDTO(
        destination_path=dest_path,
        published_bytes=4096,
        cleanup_warning="Failed to delete temporary staging artifact",
    )

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
    )

    result = service.export_pdf(cmd)
    assert result.cleanup_warning == "Failed to delete temporary staging artifact"


def test_export_service_entity_not_found(tmp_path: Path):
    """
    Missing job raises EntityNotFoundError and missing version raises ExportSourceNotFoundError.
    """
    env = _setup_test_environment(tmp_path)
    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    # Job missing
    env["uow"].jobs.get_by_id.return_value = None
    with pytest.raises(EntityNotFoundError):
        service.export_pdf(ExportPdfCommand(job_id=999, version=1, destination_path=dest_path))

    # Version missing
    env["uow"].jobs.get_by_id.return_value = MagicMock(spec=Job)
    env["uow"].document_versions.get_by_version.return_value = None
    with pytest.raises(ExportSourceNotFoundError):
        service.export_pdf(ExportPdfCommand(job_id=env["job_id"], version=99, destination_path=dest_path))


def test_export_service_cancellation(tmp_path: Path):
    """
    Cooperative cancellation raises PdfExportCancelledError before rendering starts.
    """
    env = _setup_test_environment(tmp_path)
    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    cmd = ExportPdfCommand(
        job_id=env["job_id"],
        version=env["version"],
        destination_path=dest_path,
        is_cancelled=lambda: True,
    )

    with pytest.raises(PdfExportCancelledError):
        service.export_pdf(cmd)

    env["engine"].render_pdf.assert_not_called()


def test_export_service_quarantined_document(tmp_path: Path):
    """
    Quarantined canonical document raises CanonicalDocumentIntegrityError.
    """
    env = _setup_test_environment(tmp_path)
    quarantined_record = DocumentVersionRecord(
        job_id=env["job_id"],
        version=env["version"],
        output_path=str(env["doc_file"]),
        sha256="fake",
        integrity_status="QUARANTINED",
    )
    env["uow"].document_versions.get_by_version.return_value = quarantined_record

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    cmd = ExportPdfCommand(job_id=env["job_id"], version=env["version"], destination_path=dest_path)
    with pytest.raises(CanonicalDocumentIntegrityError, match="QUARANTINED"):
        service.export_pdf(cmd)


def test_export_service_publication_in_progress(tmp_path: Path):
    """
    Active publication intent raises PublicationInProgressError.
    """
    env = _setup_test_environment(tmp_path)
    env["uow"].publish_intents.get_by_job_id.return_value = MagicMock()

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    cmd = ExportPdfCommand(job_id=env["job_id"], version=env["version"], destination_path=dest_path)
    with pytest.raises(PublicationInProgressError):
        service.export_pdf(cmd)


def test_export_service_f03_docstring_disclosure():
    """
    Mandatory F-03: Service docstring documents user OS privilege model
    and TeX filesystem read limitations without shell escape.
    """
    doc = QuartoPdfExportService.__doc__ or ""
    assert "F-03" in doc or "Trust Boundary" in doc
    assert "-no-shell-escape" in doc
    assert "privileges" in doc.lower()


def test_export_service_version_default_none_fetches_latest(tmp_path: Path):
    """
    When version is None or <= 0, service resolves latest canonical document version.
    """
    env = _setup_test_environment(tmp_path)
    # Configure get_by_version to return None, get_latest to return valid record
    env["uow"].document_versions.get_by_version.return_value = None
    env["uow"].document_versions.get_latest.return_value = env["version_record"]

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    cmd = ExportPdfCommand(job_id=env["job_id"], version=None, destination_path=dest_path)
    result = service.export_pdf(cmd)

    assert result.canonical_version == env["version"]
    env["uow"].document_versions.get_latest.assert_called_once_with(env["job_id"])


def test_export_service_extract_raw_front_matter_complex_collections():
    """
    Verifies _extract_raw_front_matter correctly parses block lists,
    inline JSON collections, and nested mappings without dropping elements.
    """
    from application.services.quarto_pdf_export_service import _extract_raw_front_matter

    qmd = """---
title: "Document With Lists"
authors: ["Alice", "Bob"]
header-includes:
  - '\\usepackage{booktabs}'
  - '\\usepackage{microtype}'
format:
  pdf:
    documentclass: scrartcl
    toc: true
    include-in-header:
      - text: '\\newcommand{\\customcmd}{val}'
---

# Content
"""
    meta = _extract_raw_front_matter(qmd)
    assert meta["title"] == "Document With Lists"
    assert meta["authors"] == ["Alice", "Bob"]
    assert meta["header-includes"] == ["\\usepackage{booktabs}", "\\usepackage{microtype}"]
    assert isinstance(meta["format"], dict)
    assert meta["format"]["pdf"]["documentclass"] == "scrartcl"
    assert meta["format"]["pdf"]["toc"] is True
    assert meta["format"]["pdf"]["include-in-header"] == [{"text": "\\newcommand{\\customcmd}{val}"}]


def test_export_service_resolves_canonical_file_uri(tmp_path: Path):
    """
    Regression test: Canonical document versions saved by DocumentPublicationService
    store output_path as an RFC 8089 file:/// URI. QuartoPdfExportService must resolve
    this URI to a filesystem path rather than treating 'file:' as a relative directory.
    Also verifies destination_path provided as file:/// URI resolves correctly.
    """
    env = _setup_test_environment(tmp_path)
    # Set output_path to real RFC 8089 file:/// URI
    canonical_uri = env["doc_file"].resolve().as_uri()
    env["version_record"].output_path = canonical_uri

    fake_compiled_pdf = tmp_path / "compiled.pdf"
    fake_compiled_pdf.write_bytes(b"%PDF-1.7")

    dest_path = tmp_path / "export" / "output_uri_test.pdf"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    env["engine"].render_pdf.return_value = PdfExportResult(
        compiled_pdf_path=fake_compiled_pdf,
        page_count=2,
        duration_seconds=0.5,
        warnings=(),
    )
    env["publisher"].publish.return_value = PublishResultDTO(
        destination_path=dest_path.resolve(),
        published_bytes=512,
        cleanup_warning=None,
    )

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    # Test with destination_path as RFC 8089 URI
    dest_uri = dest_path.resolve().as_uri()
    cmd = ExportPdfCommand(job_id=env["job_id"], version=env["version"], destination_path=dest_uri)
    result = service.export_pdf(cmd)

    assert result.destination_path == dest_path.resolve()
    env["publisher"].publish.assert_called_once_with(
        source_compiled_pdf=fake_compiled_pdf,
        destination_path=dest_path.resolve(),
        overwrite=False,
    )


def test_export_service_suppresses_fallback_title_for_cover_image_or_h1(tmp_path: Path):
    """
    When canonical markdown starts with a cover image or primary heading (H1),
    QuartoPdfExportService must not force a technical slug as fallback title,
    preventing sparse cover pages and orphan blank pages in compiled PDFs.
    """
    env = _setup_test_environment(tmp_path)
    dest_path = tmp_path / "export" / "output.pdf"
    dest_path.parent.mkdir(parents=True)

    fake_compiled = tmp_path / "fake.pdf"
    fake_compiled.write_bytes(b"%PDF-1.7")
    env["engine"].render_pdf.return_value = PdfExportResult(
        compiled_pdf_path=fake_compiled,
        page_count=1,
        duration_seconds=1.0,
        warnings=(),
    )
    env["publisher"].publish.return_value = PublishResultDTO(
        destination_path=dest_path,
        published_bytes=100,
        cleanup_warning=None,
    )

    service = QuartoPdfExportService(
        uow_factory=env["uow_factory"],
        export_engine=env["engine"],
        publisher=env["publisher"],
        artifacts_dir=env["artifacts_dir"],
    )

    # 1. Document starts with cover image
    cover_md = "<!-- Page 1 -->\n![](assets/cover.jpg)\n\n# Document Title\n\nContent here."
    cover_bytes = cover_md.encode("utf-8")
    doc_path = env["artifacts_dir"] / f"job_{env['job_id']}" / f"output_{env['job_id']}_v{env['version']}.md"
    doc_path.write_bytes(cover_bytes)
    with env["uow_factory"].create() as uow:
        version_rec = uow.document_versions.get_by_version.return_value
        version_rec.sha256 = hashlib.sha256(cover_bytes).hexdigest()

    cmd = ExportPdfCommand(job_id=env["job_id"], version=env["version"], destination_path=dest_path)
    service.export_pdf(cmd)

    render_req = env["engine"].render_pdf.call_args[0][0]
    assert "title" not in render_req.raw_metadata

    # 2. Document is plain text without leading image or H1
    env["engine"].render_pdf.reset_mock()
    plain_md = "Just plain body text without any headings or images."
    plain_bytes = plain_md.encode("utf-8")
    doc_path.write_bytes(plain_bytes)
    with env["uow_factory"].create() as uow:
        version_rec = uow.document_versions.get_by_version.return_value
        version_rec.sha256 = hashlib.sha256(plain_bytes).hexdigest()

    service.export_pdf(cmd)
    render_req = env["engine"].render_pdf.call_args[0][0]
    assert render_req.raw_metadata.get("title") == "test-document"
