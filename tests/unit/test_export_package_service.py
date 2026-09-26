# ============================================================
#  tests/unit/test_export_package_service.py
#  Unit tests for ExportPackageService and atomic ZIP creation
# ============================================================

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock

from application.ports.unit_of_work import IUnitOfWork, IUnitOfWorkFactory
from application.services.export_package_service import (
    AssetIntegrityError,
    DestinationAlreadyExistsError,
    DestinationDirectoryNotFoundError,
    ExportPackageService,
    ExportSourceNotFoundError,
    MarkdownExportResult,
    build_export_manifest,
    derive_document_slug,
    get_deterministic_zip_time,
    validate_archive_entry_path,
)
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    EntityNotFoundError,
    PublicationInProgressError,
)


class MockUoW(IUnitOfWork):
    def __init__(self):
        self.jobs = MagicMock()
        self.document_versions = MagicMock()
        self.publish_intents = MagicMock()
        self.publish_intents.get_by_job_id.return_value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


class MockUoWFactory(IUnitOfWorkFactory):
    def __init__(self, uow: MockUoW):
        self._uow = uow

    def create(self) -> IUnitOfWork:
        return self._uow


def test_derive_document_slug_sanitization():
    """Validates slug derivation from job file_name, job_id fallback, and Windows sanitization."""
    job1 = Job(id=1, file_name="Financial_Report_2026.pdf", file_path="/path/report.pdf")
    assert derive_document_slug(job1, 1) == "financial-report-2026"

    # Spaces and special characters
    job2 = Job(id=2, file_name="Quarterly Summary (Draft) #2.pdf", file_path="/path/q.pdf")
    slug2 = derive_document_slug(job2, 2)
    assert "/" not in slug2 and "\\" not in slug2 and " " not in slug2
    assert "draft" in slug2 and "2" in slug2

    # Windows reserved device names
    job_con = Job(id=3, file_name="CON.pdf", file_path="/path/con.pdf")
    slug_con = derive_document_slug(job_con, 3)
    assert slug_con.startswith("_")

    # None / empty fallback
    assert derive_document_slug(None, 42) == "job_42"
    job_empty = Job(id=5, file_name="", file_path="")
    assert derive_document_slug(job_empty, 5) == "job_5"


def test_validate_archive_entry_path():
    """Ensures all generated ZIP archive entry paths are strictly bounded within root."""
    root = "doc_v1"
    # Valid entries
    validate_archive_entry_path("doc_v1/document.md", root)
    validate_archive_entry_path("doc_v1/manifest.json", root)
    validate_archive_entry_path("doc_v1/assets/crop_1.jpg", root)

    # Invalid entries
    with pytest.raises(ValueError, match="cannot start with separator"):
        validate_archive_entry_path("/doc_v1/document.md", root)
    with pytest.raises(ValueError, match="cannot start with separator"):
        validate_archive_entry_path("\\doc_v1\\document.md", root)
    with pytest.raises(ValueError, match="drive letter"):
        validate_archive_entry_path("C:doc_v1/document.md", root)
    with pytest.raises(ValueError, match="invalid path components"):
        validate_archive_entry_path("doc_v1/../secret", root)
    with pytest.raises(ValueError, match="invalid path components"):
        validate_archive_entry_path("doc_v1//document.md", root)
    with pytest.raises(ValueError, match="escapes root directory"):
        validate_archive_entry_path("other_root/document.md", root)


def test_manifest_schema_and_deterministic_serialization():
    """Verifies manifest schema, canonical vs exported hash, and absence of host paths."""
    from application.dto.resolved_asset_dto import ResolvedAsset

    asset1 = ResolvedAsset(
        original_reference="file:///host/artifacts/job_1/b.png",
        source_path=Path("/host/artifacts/job_1/b.png"),
        relative_dest_path="assets/b.png",
        sha256="2222" * 16,
        size_bytes=200,
        mime_type="image/png",
    )
    asset2 = ResolvedAsset(
        original_reference="file:///host/artifacts/job_1/a.jpg",
        source_path=Path("/host/artifacts/job_1/a.jpg"),
        relative_dest_path="assets/a.jpg",
        sha256="1111" * 16,
        size_bytes=100,
        mime_type="image/jpeg",
    )

    manifest_dict = build_export_manifest(
        canonical_version=3,
        canonical_sha256="canonical_hash_value",
        exported_sha256="exported_hash_value",
        exported_size_bytes=512,
        created_at="2026-09-25T12:00:00Z",
        assets=[asset1, asset2],
    )

    # Schema verification
    assert manifest_dict["schema_version"] == "1.0"
    assert manifest_dict["generator"]["name"] == "PolpoT Desktop"
    assert manifest_dict["document"]["canonical_version"] == 3
    assert manifest_dict["document"]["entry_point"] == "document.md"
    assert manifest_dict["document"]["canonical_sha256"] == "canonical_hash_value"
    assert manifest_dict["document"]["exported_sha256"] == "exported_hash_value"
    assert manifest_dict["document"]["exported_size_bytes"] == 512
    assert manifest_dict["document"]["created_at"] == "2026-09-25T12:00:00Z"

    # Assets sorted deterministically by exported_path
    assert len(manifest_dict["assets"]) == 2
    assert manifest_dict["assets"][0]["exported_path"] == "assets/a.jpg"
    assert manifest_dict["assets"][1]["exported_path"] == "assets/b.png"

    # Serialization and no host path leakage
    serialized = json.dumps(manifest_dict, indent=2, sort_keys=True, ensure_ascii=False)
    assert "/host" not in serialized
    assert "original_reference" not in serialized
    assert "source_path" not in serialized


def test_deterministic_zip_timestamp():
    """Derives deterministic ZipInfo date_time from ISO timestamp or fallback."""
    t1 = get_deterministic_zip_time("2026-09-25T14:30:15Z")
    assert t1 == (2026, 9, 25, 14, 30, 15)

    # Missing or invalid timestamp falls back to stable fixed epoch
    t_fallback = get_deterministic_zip_time(None)
    assert t_fallback == (2026, 1, 1, 0, 0, 0)
    assert get_deterministic_zip_time("invalid-date") == (2026, 1, 1, 0, 0, 0)


def test_export_package_destination_already_exists_raises(tmp_path: Path):
    """Refuses to overwrite an existing destination file when overwrite=False."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    dest_file = tmp_path / "export.zip"
    dest_file.write_bytes(b"existing zip")

    uow = MockUoW()
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(DestinationAlreadyExistsError):
        service.export_package(job_id=1, destination_path=dest_file, overwrite=False)


def test_export_package_missing_parent_directory_raises(tmp_path: Path):
    """Raises DestinationDirectoryNotFoundError when target parent folder does not exist."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    non_existent_dest = tmp_path / "missing_folder" / "export.zip"

    uow = MockUoW()
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(DestinationDirectoryNotFoundError):
        service.export_package(job_id=1, destination_path=non_existent_dest)


def test_export_package_quarantined_version_rejected(tmp_path: Path):
    """Rejects export when the canonical document version is in QUARANTINED integrity status."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    dest_file = tmp_path / "export.zip"

    uow = MockUoW()
    quarantined_ver = DocumentVersionRecord(
        job_id=1,
        version=2,
        output_path="file:///dummy/path.md",
        sha256="abc",
        integrity_status="QUARANTINED",
    )
    uow.document_versions.get_latest.return_value = quarantined_ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(CanonicalDocumentIntegrityError, match="QUARANTINED"):
        service.export_package(job_id=1, destination_path=dest_file)


def test_export_package_active_publish_intent_raises(tmp_path: Path):
    """Rejects export when an active publication intent is in progress."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    dest_file = tmp_path / "export.zip"

    uow = MockUoW()
    uow.publish_intents.get_by_job_id.return_value = MagicMock()
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(PublicationInProgressError):
        service.export_package(job_id=1, destination_path=dest_file)


def test_export_package_missing_canonical_version_raises(tmp_path: Path):
    """Raises ExportSourceNotFoundError when no canonical version record exists."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    dest_file = tmp_path / "export.zip"

    uow = MockUoW()
    uow.document_versions.get_latest.return_value = None
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(ExportSourceNotFoundError):
        service.export_package(job_id=1, destination_path=dest_file)


def test_export_package_missing_file_on_disk_raises(tmp_path: Path):
    """Raises ExportSourceNotFoundError when the canonical Markdown file is missing from disk."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    dest_file = tmp_path / "export.zip"

    uow = MockUoW()
    non_existent_path = artifacts_dir / "missing.md"
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=non_existent_path.as_uri(),
        sha256="abc",
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    with pytest.raises(ExportSourceNotFoundError, match="does not exist on disk"):
        service.export_package(job_id=1, destination_path=dest_file)


def test_export_package_propagates_resolver_asset_errors(tmp_path: Path):
    """Propagates AssetNotFoundError from DocumentAssetResolver when an asset is missing."""
    from application.services.document_asset_resolver import AssetNotFoundError
    import hashlib

    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    doc_file = job_dir / "output_1_v1.md"
    content = "# Doc\n\n![Missing](missing.jpg)\n"
    doc_file.write_text(content, encoding="utf-8")
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    uow = MockUoW()
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=content_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    dest_file = tmp_path / "export.zip"
    with pytest.raises(AssetNotFoundError):
        service.export_package(job_id=1, destination_path=dest_file)

    # Ensure no zip file was left behind
    assert not dest_file.exists()


def test_export_markdown_shares_same_portable_projection(tmp_path: Path):
    """Standalone markdown export outputs the exact same projection as package document.md without bundling assets."""
    import hashlib

    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    img_file = job_dir / "diagram.png"
    img_file.write_bytes(b"png data")

    doc_file = job_dir / "output_1_v1.md"
    content = f"# Doc\n\n![Diagram]({img_file.as_uri()})\n"
    doc_file.write_text(content, encoding="utf-8")
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    uow = MockUoW()
    job = Job(id=1, file_name="Doc.pdf", file_path="")
    uow.jobs.get_by_id.return_value = job
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=content_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    dest_md = tmp_path / "standalone.md"
    res = service.export_markdown(job_id=1, destination_path=dest_md)

    assert isinstance(res, MarkdownExportResult)
    assert res.destination_path == dest_md.resolve()
    assert res.canonical_version == 1
    assert res.asset_count == 1
    assert res.has_local_asset_references is True
    assert dest_md.exists()

    expected_content = "# Doc\n\n![Diagram](assets/diagram.png)\n"
    assert dest_md.read_text(encoding="utf-8") == expected_content


def test_export_package_missing_job_raises_entity_not_found(tmp_path: Path):
    """When a job does not exist in persistence, export raises EntityNotFoundError."""
    uow = MockUoW()
    uow.jobs.get_by_id.return_value = None

    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=tmp_path / "artifacts")
    dest_file = tmp_path / "export.zip"

    with pytest.raises(EntityNotFoundError) as exc_info:
        service.export_package(job_id=999, destination_path=dest_file)

    assert exc_info.value.entity_name == "Job"
    assert exc_info.value.entity_id == 999
    assert not dest_file.exists()


def test_export_package_asset_toctou_integrity_mismatch_raises(tmp_path: Path, monkeypatch):
    """Detects TOCTOU tampering: if an asset is modified between resolution and packaging, raises AssetIntegrityError."""
    import hashlib

    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    img_file = job_dir / "diagram.png"
    img_file.write_bytes(b"original image content")

    doc_file = job_dir / "output_1_v1.md"
    content = "# Doc\n\n![Diagram](diagram.png)\n"
    doc_file.write_bytes(content.encode("utf-8"))
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    uow = MockUoW()
    job = Job(id=1, file_name="Doc.pdf", file_path="")
    uow.jobs.get_by_id.return_value = job
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=content_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    orig_resolve = service.resolver.resolve_document_assets

    def tamper_after_resolve(*args, **kwargs):
        resolved = orig_resolve(*args, **kwargs)
        img_file.write_bytes(b"TAMPERED IMAGE CONTENT")
        return resolved

    monkeypatch.setattr(service.resolver, "resolve_document_assets", tamper_after_resolve)

    dest_file = tmp_path / "export.zip"
    with pytest.raises(AssetIntegrityError) as exc_info:
        service.export_package(job_id=1, destination_path=dest_file)

    assert "Asset content mismatch" in str(exc_info.value)
    assert not dest_file.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_export_package_preserves_canonical_crlf_line_endings(tmp_path: Path):
    """Canonical Markdown containing CRLF survives export without newline normalization."""
    import hashlib
    import zipfile

    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    img_file = job_dir / "diagram.png"
    img_file.write_bytes(b"png bytes")

    doc_file = job_dir / "output_1_v1.md"
    crlf_bytes = b"# CRLF Doc\r\n\r\nIntro text\r\n\r\n![Diagram](diagram.png)\r\n\r\nFooter\r\n"
    doc_file.write_bytes(crlf_bytes)
    crlf_sha = hashlib.sha256(crlf_bytes).hexdigest()

    uow = MockUoW()
    job = Job(id=1, file_name="CRLFDoc.pdf", file_path="")
    uow.jobs.get_by_id.return_value = job
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=crlf_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    dest_zip = tmp_path / "crlf_pkg.zip"
    res_pkg = service.export_package(job_id=1, destination_path=dest_zip)

    assert res_pkg.canonical_sha256 == crlf_sha
    assert dest_zip.is_file()

    with zipfile.ZipFile(dest_zip, "r") as zf:
        extracted_doc_bytes = zf.read("crlfdoc_v1/document.md")

    expected_exported_bytes = b"# CRLF Doc\r\n\r\nIntro text\r\n\r\n![Diagram](assets/diagram.png)\r\n\r\nFooter\r\n"
    assert extracted_doc_bytes == expected_exported_bytes
    assert b"\r\n" in extracted_doc_bytes

    dest_md = tmp_path / "crlf_doc.md"
    res_md = service.export_markdown(job_id=1, destination_path=dest_md)
    assert res_md.canonical_sha256 == crlf_sha
    assert dest_md.read_bytes() == expected_exported_bytes


def test_export_package_mid_write_atomic_cleanup(tmp_path: Path, monkeypatch):
    """Failure after temporary ZIP is created and written unlinks the temp file and leaves no destination."""
    import hashlib
    import zipfile

    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    img_file = job_dir / "diagram.png"
    img_file.write_bytes(b"png data")

    doc_file = job_dir / "output_1_v1.md"
    content = "# Doc\n\n![Diagram](diagram.png)\n"
    doc_file.write_bytes(content.encode("utf-8"))
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    uow = MockUoW()
    job = Job(id=1, file_name="Doc.pdf", file_path="")
    uow.jobs.get_by_id.return_value = job
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=content_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    dest_dir = tmp_path / "export_target"
    dest_dir.mkdir(parents=True)
    dest_zip = dest_dir / "target.zip"

    orig_writestr = zipfile.ZipFile.writestr
    call_count = 0

    def fail_mid_write(self, zinfo_or_arcname, data=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            tmp_files = list(dest_dir.glob(".*.tmp"))
            assert len(tmp_files) == 1, "Temporary ZIP file must exist during writing"
            assert tmp_files[0].is_file()
            raise RuntimeError("Simulated crash during ZIP archive entry writing")
        return orig_writestr(self, zinfo_or_arcname, data)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_mid_write)

    with pytest.raises(RuntimeError, match="Simulated crash during ZIP archive entry writing"):
        service.export_package(job_id=1, destination_path=dest_zip)

    assert not dest_zip.exists()
    assert list(dest_dir.glob(".*.tmp")) == []


def test_export_package_overwrite_true_failure_preserves_existing_destination(tmp_path: Path):
    """When overwrite=True and packaging fails, existing destination file remains completely untouched."""
    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_1"
    job_dir.mkdir(parents=True)

    img_file = job_dir / "diagram.png"
    img_file.write_bytes(b"valid image")

    doc_file = job_dir / "output_1_v1.md"
    content = "# Doc\n\n![Diagram](diagram.png)\n"
    doc_file.write_bytes(content.encode("utf-8"))
    corrupt_sha = "deadbeef" * 8

    uow = MockUoW()
    job = Job(id=1, file_name="Doc.pdf", file_path="")
    uow.jobs.get_by_id.return_value = job
    ver = DocumentVersionRecord(
        job_id=1,
        version=1,
        output_path=doc_file.as_uri(),
        sha256=corrupt_sha,
    )
    uow.document_versions.get_latest.return_value = ver
    service = ExportPackageService(uow_factory=MockUoWFactory(uow), artifacts_dir=artifacts_dir)

    dest_zip = tmp_path / "important_existing.zip"
    existing_bytes = b"EXISTING_VALID_ARCHIVE_DATA_MUST_NOT_BE_OVERWRITTEN_ON_FAILURE"
    dest_zip.write_bytes(existing_bytes)

    with pytest.raises(CanonicalDocumentIntegrityError):
        service.export_package(job_id=1, destination_path=dest_zip, overwrite=True)

    assert dest_zip.exists()
    assert dest_zip.read_bytes() == existing_bytes
    assert list(tmp_path.glob(".*.tmp")) == []
