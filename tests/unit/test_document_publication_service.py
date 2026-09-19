# ============================================================
#  tests/unit/test_document_publication_service.py
#  Unit Tests for DocumentPublicationService (Ticket 10E.3a-06)
# ============================================================

import hashlib
from pathlib import Path, PureWindowsPath
import pytest

from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.legacy_document_backfill import resolve_canonical_file_path
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord
from core.entities.job import Job, JobStatus
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    DocumentAlreadyExistsError,
    EntityNotFoundError,
    PublicationInProgressError,
    StaleDocumentVersionError,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork, SQLiteUnitOfWorkFactory


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_pub.db"
    mgr = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager=mgr)
    runner.run_migrations()
    return mgr


@pytest.fixture
def uow_factory(db_manager: SQLiteDatabaseManager) -> SQLiteUnitOfWorkFactory:
    return SQLiteUnitOfWorkFactory(db_manager)


@pytest.fixture
def pub_service(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path) -> DocumentPublicationService:
    artifacts_dir = tmp_path / "artifacts"
    staging_svc = CropArtifactStagingService(base_dir=artifacts_dir)
    return DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=staging_svc,
    )


def _create_job(uow: SQLiteUnitOfWork, output_path: str = None) -> Job:
    cur = uow._conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "doc.pdf",
            "/path/doc.pdf",
            JobStatus.DONE.value,
            output_path,
            "2026-09-19T00:00:00+00:00",
            "2026-09-19T00:00:00+00:00",
        ),
    )
    job_id = cur.lastrowid
    return uow.jobs.get_by_id(job_id)


def test_publish_initial_happy_path(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    content = "# Initial Markdown Document\n\nHello world."
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    record = pub_service.publish_initial(job_id=job_id, markdown_text=content, published_by="PIPELINE_1")

    assert record is not None
    assert record.job_id == job_id
    assert record.version == 1
    assert record.sha256 == expected_sha
    assert record.integrity_status == "VALID"
    assert record.published_by == "PIPELINE_1"

    # Verify disk artifact exists and matches
    expected_file = tmp_path / "artifacts" / f"job_{job_id}" / f"output_{job_id}_v1.md"
    assert expected_file.is_file()
    assert expected_file.read_text(encoding="utf-8") == content

    # Verify SQLite state
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job_id)
        assert updated_job.output_path.startswith("file:///")
        assert updated_job.output_path == expected_file.resolve().as_uri()
        assert uow.publish_intents.get_intent_by_job_id(job_id) is None


def test_publish_initial_empty_text(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    record = pub_service.publish_initial(job_id=job_id, markdown_text="", published_by="PIPELINE_1")
    expected_empty_sha = hashlib.sha256(b"").hexdigest()

    assert record.version == 1
    assert record.sha256 == expected_empty_sha
    assert record.integrity_status == "VALID"


def test_publish_initial_already_exists(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    pub_service.publish_initial(job_id=job_id, markdown_text="v1 text")

    # Calling publish_initial again must raise DocumentAlreadyExistsError
    with pytest.raises(DocumentAlreadyExistsError):
        pub_service.publish_initial(job_id=job_id, markdown_text="another v1")


def test_publish_initial_quarantined(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        # Pre-seed a quarantined document_version
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/missing/path.md",
            sha256=None,
            integrity_status="QUARANTINED",
            published_by="LEGACY_BACKFILL",
        ))
        uow.commit()

    with pytest.raises(CanonicalDocumentIntegrityError):
        pub_service.publish_initial(job_id=job_id, markdown_text="content")


def test_publish_initial_orphan_pointer(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        # Non-null output_path but no document_versions record
        job = _create_job(uow, output_path="/some/legacy/output.md")
        job_id = job.id
        uow.commit()

    with pytest.raises(CanonicalDocumentIntegrityError):
        pub_service.publish_initial(job_id=job_id, markdown_text="content")


def test_publish_initial_nonexistent_job(pub_service: DocumentPublicationService):
    with pytest.raises(EntityNotFoundError):
        pub_service.publish_initial(job_id=999999, markdown_text="content")


def test_publish_version_happy_path(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    # Initial publication (v1)
    pub_service.publish_initial(job_id=job_id, markdown_text="v1 content")

    # Stage a crop artifact
    staging_svc = pub_service.staging_service
    crop_data = b"\xff\xd8\xff\xe0 fake jpeg data"
    crop_handle = staging_svc.stage_crop(
        job_id=job_id,
        staging_id="stg_test_1",
        region_id="reg_1",
        version=2,
        image_bytes=crop_data,
    )

    # Publish version 2
    v2_content = f"# Version 2 Document\n\n![diagram](file://...)"
    record_v2 = pub_service.publish_version(
        job_id=job_id,
        base_version=1,
        markdown_text=v2_content,
        staged_crops=[crop_handle],
        published_by="EDITOR_SESSION",
    )

    assert record_v2.version == 2
    assert record_v2.published_by == "EDITOR_SESSION"
    assert record_v2.integrity_status == "VALID"

    # Check that crop was promoted to job directory
    dest_crop = tmp_path / "artifacts" / f"job_{job_id}" / crop_handle.dest_filename
    assert dest_crop.is_file()
    assert dest_crop.read_bytes() == crop_data

    # Check that staging directory was cleaned up
    staging_dir = tmp_path / "artifacts" / ".staging" / crop_handle.staging_id
    assert not staging_dir.exists()


def test_publish_version_occ_stale(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    pub_service.publish_initial(job_id=job_id, markdown_text="v1")

    # Trying to publish based on version 0 or 2 when active is 1
    with pytest.raises(StaleDocumentVersionError) as exc_info:
        pub_service.publish_version(job_id=job_id, base_version=0, markdown_text="v2 text")
    assert exc_info.value.base_version == 0
    assert exc_info.value.current_version == 1

    with pytest.raises(StaleDocumentVersionError) as exc_info2:
        pub_service.publish_version(job_id=job_id, base_version=99, markdown_text="v2 text")
    assert exc_info2.value.base_version == 99


def test_publish_version_concurrent_intent(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    pub_service.publish_initial(job_id=job_id, markdown_text="v1")

    # Pre-seed an active intent
    with uow_factory.create() as uow:
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id="active-concurrent-intent",
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename="output_test.md",
            output_sha256="fake-sha",
        ))
        uow.commit()

    # Attempting to publish must raise PublicationInProgressError
    with pytest.raises(PublicationInProgressError):
        pub_service.publish_version(job_id=job_id, base_version=1, markdown_text="v2")


def test_staging_cleanup_after_publish(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    pub_service.publish_initial(job_id=job_id, markdown_text="v1")

    crop_handle = pub_service.staging_service.stage_crop(
        job_id=job_id,
        staging_id="stg_clean",
        region_id="reg_clean",
        version=2,
        image_bytes=b"clean test bytes",
    )

    staging_path = Path(crop_handle.staging_path)
    assert staging_path.is_file()

    pub_service.publish_version(
        job_id=job_id,
        base_version=1,
        markdown_text="v2",
        staged_crops=[crop_handle],
    )

    # After publishing, staging directory is deleted
    assert not staging_path.exists()
    assert not staging_path.parent.exists()


def test_canonical_uri_rfc8089_cross_platform_roundtrip(
    pub_service: DocumentPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    tmp_path: Path,
):
    """
    Assert that output paths generated by publish_initial() and publish_version()
    start with file:/// and round-trip through resolve_canonical_file_path()
    identically across platforms, including drive-letter test vectors.
    """
    with uow_factory.create() as uow:
        job = _create_job(uow)
        job_id = job.id
        uow.commit()

    # 1. Initial publication
    v1_record = pub_service.publish_initial(
        job_id=job_id,
        markdown_text="# Initial Version\n",
        published_by="PIPELINE_1",
    )
    assert v1_record.output_path.startswith("file:///")
    resolved_v1 = resolve_canonical_file_path(v1_record.output_path)
    expected_v1 = tmp_path / "artifacts" / f"job_{job_id}" / f"output_{job_id}_v1.md"
    assert resolved_v1.resolve() == expected_v1.resolve()

    # Verify SQLite jobs.output_path and document_versions.output_path match RFC 8089
    with uow_factory.create() as uow:
        job_db = uow.jobs.get_by_id(job_id)
        assert job_db.output_path.startswith("file:///")
        assert job_db.output_path == v1_record.output_path
        doc_ver = uow.document_versions.get_latest_document_version(job_id)
        assert doc_ver.output_path.startswith("file:///")
        assert doc_ver.output_path == v1_record.output_path

    # 2. Subsequent version publication
    v2_record = pub_service.publish_version(
        job_id=job_id,
        base_version=1,
        markdown_text="# Version 2\n",
        staged_crops=[],
        published_by="EDITOR",
    )
    assert v2_record.output_path.startswith("file:///")
    resolved_v2 = resolve_canonical_file_path(v2_record.output_path)
    expected_v2 = tmp_path / "artifacts" / f"job_{job_id}" / f"output_{job_id}_v2.md"
    assert resolved_v2.resolve() == expected_v2.resolve()

    # Verify SQLite jobs.output_path and document_versions.output_path match RFC 8089 for v2
    with uow_factory.create() as uow:
        job_db_v2 = uow.jobs.get_by_id(job_id)
        assert job_db_v2.output_path.startswith("file:///")
        assert job_db_v2.output_path == v2_record.output_path
        doc_ver_v2 = uow.document_versions.get_latest_document_version(job_id)
        assert doc_ver_v2.output_path.startswith("file:///")
        assert doc_ver_v2.output_path == v2_record.output_path

    # 3. Cross-platform drive-letter test vector (RFC 8089 file:///C:/...)
    win_expected = PureWindowsPath("C:/artifacts/job_1/output_1_v1.md")
    win_uri = win_expected.as_uri()
    assert win_uri.startswith("file:///")
    assert win_uri == "file:///C:/artifacts/job_1/output_1_v1.md"

    resolved_win = resolve_canonical_file_path(win_uri)
    assert resolved_win.as_posix() == win_expected.as_posix()
