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
