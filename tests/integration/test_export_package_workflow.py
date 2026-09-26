"""Integration tests for ExportPackageService workflow (TICK-EXP-3).

Validates end-to-end package ZIP export and standalone Markdown export using
real SQLite persistence, real filesystem artifacts, actual archive extraction,
deterministic byte-identical reproducibility, atomic failure rollback,
and Markdown asset closure verification.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from application.services.document_asset_resolver import AssetNotFoundError
from application.services.export_package_service import (
    AssetIntegrityError,
    DestinationAlreadyExistsError,
    ExportPackageService,
)
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    EntityNotFoundError,
)
from core.markdown.asset_rewriter import scan_asset_references
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory


@pytest.fixture
def integration_env(tmp_path: Path):
    """Sets up a real SQLite database, migrations, UoW factory, and job artifacts directory."""
    db_file = tmp_path / "integration_export.db"
    db_manager = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager)
    runner.run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(db_manager)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with uow_factory.create() as uow:
        prompt = uow.prompts.save(
            Prompt(
                id=None,
                name="Default",
                text="Prompt template",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        )
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="Deep Learning Research Paper.pdf",
                file_path="/path/to/Deep Learning Research Paper.pdf",
                prompt_id=prompt.id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()
        job_id = job.id

    job_artifacts_dir = artifacts_dir / f"job_{job_id}"
    job_artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create real asset files
    fig1 = job_artifacts_dir / "figure1.png"
    fig1_bytes = b"FAKE_PNG_BYTES_FIGURE_1"
    fig1.write_bytes(fig1_bytes)

    crop = job_artifacts_dir / "crop_reg123.jpg"
    crop_bytes = b"FAKE_JPEG_BYTES_CROP_REGION"
    crop.write_bytes(crop_bytes)

    space_img = job_artifacts_dir / "my crop.png"
    space_bytes = b"FAKE_PNG_BYTES_SPACED_NAME"
    space_img.write_bytes(space_bytes)

    # 2. Create canonical Markdown content with mixed asset references
    canonical_markdown = (
        "# Deep Learning Research\n\n"
        "Here is the main diagram:\n"
        "![Figure 1](figure1.png)\n\n"
        "Here is a manual visual crop occurrence:\n"
        '![Important Region](crop_reg123.jpg "polpo:region=reg-123;occ=1")\n\n'
        "Here is an angle-bracket enclosed destination:\n"
        "![Spaced Name](<my crop.png>)\n\n"
        "Here is an external web image:\n"
        '![External](https://example.com/logo.png "External Logo")\n'
    )
    doc_path = job_artifacts_dir / f"output_{job_id}_v1.md"
    doc_bytes = canonical_markdown.encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    canonical_sha = hashlib.sha256(doc_bytes).hexdigest()

    fixed_created_at = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    with uow_factory.create() as uow:
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                id=None,
                job_id=job_id,
                version=1,
                output_path=doc_path.as_uri(),
                sha256=canonical_sha,
                created_at=fixed_created_at,
            )
        )
        uow.commit()

    service = ExportPackageService(uow_factory=uow_factory, artifacts_dir=artifacts_dir)

    return {
        "uow_factory": uow_factory,
        "artifacts_dir": artifacts_dir,
        "job_id": job_id,
        "service": service,
        "canonical_markdown": canonical_markdown,
        "canonical_sha": canonical_sha,
        "doc_path": doc_path,
        "fixed_created_at": fixed_created_at,
        "assets": {
            "figure1.png": fig1_bytes,
            "crop_reg123.jpg": crop_bytes,
            "my crop.png": space_bytes,
        },
    }


def test_export_package_workflow_complete(integration_env, tmp_path: Path):
    """Verifies end-to-end package export: ZIP structure, manifest, closure, and tokens."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    canonical_sha: str = integration_env["canonical_sha"]

    dest_zip = tmp_path / "deep_learning_export.zip"
    result = service.export_package(job_id=job_id, destination_path=dest_zip)

    assert result.destination_path == dest_zip
    assert result.root_directory == "deep-learning-research-paper_v1"
    assert result.canonical_version == 1
    assert result.asset_count == 3
    assert result.canonical_sha256 == canonical_sha
    assert result.size_bytes == dest_zip.stat().st_size
    assert dest_zip.is_file()

    # Extract to clean directory
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(dest_zip, "r") as zf:
        zf.extractall(extract_dir)

    root_dir = extract_dir / result.root_directory
    assert root_dir.is_dir()

    # 1. Verify manifest.json
    manifest_file = root_dir / "manifest.json"
    assert manifest_file.is_file()
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    assert manifest_data["schema_version"] == "1.0"
    assert manifest_data["document"]["canonical_sha256"] == canonical_sha
    assert manifest_data["document"]["exported_sha256"] == result.exported_sha256
    assert len(manifest_data["assets"]) == 3

    # Check for absence of host paths in manifest
    manifest_raw = manifest_file.read_text(encoding="utf-8")
    for forbidden in ("/tmp", "/home", "\\Users\\", "C:\\"):
        assert forbidden not in manifest_raw

    # 2. Verify document.md
    doc_file = root_dir / "document.md"
    assert doc_file.is_file()
    doc_text = doc_file.read_text(encoding="utf-8")
    assert hashlib.sha256(doc_text.encode("utf-8")).hexdigest() == result.exported_sha256

    # Verify visual region metadata token is intact
    assert 'polpo:region=reg-123;occ=1' in doc_text
    # Verify external URL was not rewritten
    assert "https://example.com/logo.png" in doc_text

    # 3. Verify reference closure: every local asset referenced in document.md exists in assets/
    refs = scan_asset_references(doc_text)
    local_refs = [r for r in refs if not r.destination.startswith("http")]
    assert len(local_refs) == 3

    for ref in local_refs:
        assert ref.destination.startswith("assets/")
        asset_file = root_dir / ref.destination
        assert asset_file.is_file(), f"Referenced asset {ref.destination} missing in extracted package"

    # Check assets binary content
    assets_dir = root_dir / "assets"
    assert assets_dir.is_dir()
    for filename, expected_bytes in integration_env["assets"].items():
        extracted_asset = assets_dir / filename
        assert extracted_asset.is_file()
        assert extracted_asset.read_bytes() == expected_bytes


def test_export_package_byte_identical_reproducibility(integration_env, tmp_path: Path):
    """Repeated export of the same canonical document version produces byte-identical ZIPs."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]

    dest_1 = tmp_path / "export_run_1.zip"
    dest_2 = tmp_path / "export_run_2.zip"

    res1 = service.export_package(job_id=job_id, destination_path=dest_1)
    res2 = service.export_package(job_id=job_id, destination_path=dest_2)

    bytes1 = dest_1.read_bytes()
    bytes2 = dest_2.read_bytes()

    assert bytes1 == bytes2
    assert hashlib.sha256(bytes1).hexdigest() == hashlib.sha256(bytes2).hexdigest()
    assert res1.exported_sha256 == res2.exported_sha256


def test_export_package_atomic_cleanup_on_failure(integration_env, tmp_path: Path):
    """Inducing a failure during export leaves no corrupt output or temporary files."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    doc_path: Path = integration_env["doc_path"]

    # Corrupt the canonical file on disk to trigger CanonicalDocumentIntegrityError
    doc_path.write_bytes(b"# Corrupted Content")

    dest_dir = tmp_path / "failed_exports"
    dest_dir.mkdir(parents=True)
    dest_zip = dest_dir / "should_not_exist.zip"

    with pytest.raises(CanonicalDocumentIntegrityError):
        service.export_package(job_id=job_id, destination_path=dest_zip)

    # Destination archive must not exist
    assert not dest_zip.exists()

    # No leftover .tmp files
    tmp_files = list(dest_dir.glob("*.tmp")) + list(dest_dir.glob(".*.tmp"))
    assert tmp_files == []


def test_export_package_missing_referenced_asset_atomic_cleanup(integration_env, tmp_path: Path):
    """If a referenced asset is deleted before packaging, raises AssetNotFoundError and cleans up."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    artifacts_dir: Path = integration_env["artifacts_dir"]

    # Delete one of the referenced assets
    crop_file = artifacts_dir / f"job_{job_id}" / "crop_reg123.jpg"
    crop_file.unlink()

    dest_dir = tmp_path / "missing_asset_export"
    dest_dir.mkdir(parents=True)
    dest_zip = dest_dir / "export_missing.zip"

    with pytest.raises(AssetNotFoundError):
        service.export_package(job_id=job_id, destination_path=dest_zip)

    assert not dest_zip.exists()
    tmp_files = list(dest_dir.glob("*.tmp")) + list(dest_dir.glob(".*.tmp"))
    assert tmp_files == []


def test_export_markdown_standalone_workflow(integration_env, tmp_path: Path):
    """Verifies standalone markdown export produces identical projection without bundling assets."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    canonical_sha: str = integration_env["canonical_sha"]

    dest_md = tmp_path / "standalone.md"
    result = service.export_markdown(job_id=job_id, destination_path=dest_md)

    assert result.destination_path == dest_md
    assert result.canonical_version == 1
    assert result.asset_count == 3
    assert result.has_local_asset_references is True
    assert result.canonical_sha256 == canonical_sha
    assert dest_md.is_file()

    # Compare with package export's document.md
    dest_zip = tmp_path / "pkg.zip"
    service.export_package(job_id=job_id, destination_path=dest_zip)

    with zipfile.ZipFile(dest_zip, "r") as zf:
        pkg_doc = zf.read("deep-learning-research-paper_v1/document.md").decode("utf-8")

    assert dest_md.read_text(encoding="utf-8") == pkg_doc

    # Verify no asset folder or zip file was generated next to standalone.md
    assert not (tmp_path / "assets").exists()
    assert not (tmp_path / "deep-learning-research-paper_v1").exists()


def test_export_package_and_markdown_to_directory(integration_env, tmp_path: Path):
    """When destination_path is a directory, automatically derives default filename."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]

    out_dir = tmp_path / "out_folder"
    out_dir.mkdir(parents=True)

    pkg_res = service.export_package(job_id=job_id, destination_path=out_dir)
    assert pkg_res.destination_path == out_dir / "deep-learning-research-paper_v1.zip"
    assert pkg_res.destination_path.is_file()

    md_res = service.export_markdown(job_id=job_id, destination_path=out_dir)
    assert md_res.destination_path == out_dir / "deep-learning-research-paper_v1.md"
    assert md_res.destination_path.is_file()

    # Re-exporting to directory without overwrite raises DestinationAlreadyExistsError
    with pytest.raises(DestinationAlreadyExistsError):
        service.export_package(job_id=job_id, destination_path=out_dir, overwrite=False)


def test_export_package_integration_crlf_preservation(integration_env, tmp_path: Path):
    """End-to-end integration test: CRLF canonical markdown exported and extracted with CRLF preserved."""
    service: ExportPackageService = integration_env["service"]
    uow_factory = integration_env["uow_factory"]
    artifacts_dir: Path = integration_env["artifacts_dir"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="CRLF Document.pdf",
                file_path="/path/to/CRLF Document.pdf",
                status=JobStatus.DONE,
            )
        )
        uow.commit()
        job_id = job.id

    job_artifacts_dir = artifacts_dir / f"job_{job_id}"
    job_artifacts_dir.mkdir(parents=True, exist_ok=True)

    img = job_artifacts_dir / "plot.png"
    img.write_bytes(b"PLOT_IMAGE_BYTES")

    crlf_content = "# Section 1\r\n\r\nIntro text\r\n\r\n![Plot](plot.png)\r\n\r\nSection 2\r\n"
    crlf_bytes = crlf_content.encode("utf-8")
    doc_path = job_artifacts_dir / f"output_{job_id}_v1.md"
    doc_path.write_bytes(crlf_bytes)
    crlf_sha = hashlib.sha256(crlf_bytes).hexdigest()

    with uow_factory.create() as uow:
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                id=None,
                job_id=job_id,
                version=1,
                output_path=doc_path.as_uri(),
                sha256=crlf_sha,
                created_at="2026-09-26T12:00:00+00:00",
            )
        )
        uow.commit()

    dest_zip = tmp_path / "crlf_integration.zip"
    res = service.export_package(job_id=job_id, destination_path=dest_zip)
    assert res.canonical_sha256 == crlf_sha

    extract_dir = tmp_path / "crlf_extract"
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(dest_zip, "r") as zf:
        zf.extractall(extract_dir)

    extracted_doc = extract_dir / res.root_directory / "document.md"
    extracted_bytes = extracted_doc.read_bytes()
    expected_bytes = b"# Section 1\r\n\r\nIntro text\r\n\r\n![Plot](assets/plot.png)\r\n\r\nSection 2\r\n"
    assert extracted_bytes == expected_bytes
    assert b"\r\n" in extracted_bytes


def test_export_package_integration_asset_toctou_mismatch(integration_env, tmp_path: Path, monkeypatch):
    """When asset content is modified on disk after resolution, packaging fails with AssetIntegrityError."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    artifacts_dir: Path = integration_env["artifacts_dir"]
    fig1 = artifacts_dir / f"job_{job_id}" / "figure1.png"

    orig_resolve = service.resolver.resolve_document_assets

    def tamper_asset(*args, **kwargs):
        res = orig_resolve(*args, **kwargs)
        fig1.write_bytes(b"TAMPERED_PNG_BYTES")
        return res

    monkeypatch.setattr(service.resolver, "resolve_document_assets", tamper_asset)

    dest_dir = tmp_path / "toctou_test"
    dest_dir.mkdir(parents=True)
    dest_zip = dest_dir / "toctou.zip"

    with pytest.raises(AssetIntegrityError):
        service.export_package(job_id=job_id, destination_path=dest_zip)

    assert not dest_zip.exists()
    assert list(dest_dir.glob(".*.tmp")) == []


def test_export_package_integration_missing_job_rejected(integration_env, tmp_path: Path):
    """Attempting to export a non-existent job ID raises EntityNotFoundError."""
    service: ExportPackageService = integration_env["service"]
    dest_zip = tmp_path / "nonexistent.zip"

    with pytest.raises(EntityNotFoundError) as exc_info:
        service.export_package(job_id=99999, destination_path=dest_zip)

    assert exc_info.value.entity_name == "Job"
    assert exc_info.value.entity_id == 99999
    assert not dest_zip.exists()


def test_export_package_integration_overwrite_true_failure_preserves_destination(integration_env, tmp_path: Path):
    """When overwrite=True and packaging fails, existing destination file remains completely untouched."""
    service: ExportPackageService = integration_env["service"]
    job_id: int = integration_env["job_id"]
    doc_path: Path = integration_env["doc_path"]

    dest_zip = tmp_path / "existing_archive.zip"
    existing_bytes = b"PRE_EXISTING_VALID_CONTENT_THAT_MUST_SURVIVE_PACKAGING_FAILURE"
    dest_zip.write_bytes(existing_bytes)

    doc_path.write_bytes(b"# Corrupt data")

    with pytest.raises(CanonicalDocumentIntegrityError):
        service.export_package(job_id=job_id, destination_path=dest_zip, overwrite=True)

    assert dest_zip.exists()
    assert dest_zip.read_bytes() == existing_bytes
    assert list(tmp_path.glob(".*.tmp")) == []
