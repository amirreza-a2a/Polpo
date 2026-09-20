# ============================================================
#  tests/unit/test_publication_crash_recovery.py
#  Crash Recovery & Invariant Tests for Publication Service (Ticket 10E.3a-06)
# ============================================================

import hashlib
import json
import os
from pathlib import Path
import time
import pytest

from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord
from core.entities.job import Job, JobStatus
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork, SQLiteUnitOfWorkFactory


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_recovery.db"
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


def _create_job(uow: SQLiteUnitOfWork, job_id: int = 1) -> Job:
    cur = uow._conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "doc.pdf",
            "/path/doc.pdf",
            JobStatus.DONE.value,
            None,
            "2026-09-19T00:00:00+00:00",
            "2026-09-19T00:00:00+00:00",
        ),
    )
    return uow.jobs.get_by_id(job_id)


def test_crash_recovery_case1_pending_no_file(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        _create_job(uow, job_id=1)
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id="intent-case-1",
            job_id=1,
            base_version=0,
            target_version=1,
            output_filename="output_1_v1.md",
            output_sha256="fake-hash",
            status="PENDING",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()

    assert len(results) == 1
    assert results[0].action == "DELETED_PENDING_INTENT"
    assert results[0].job_id == 1

    with uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id("intent-case-1") is None


def test_crash_recovery_case2_tmp_exists_rollback(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 2
    intent_id = "intent-case-2"
    filename = f"output_{job_id}_v2.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Temporary file exists
    tmp_file = job_dir / f".{filename}.{intent_id}.tmp"
    tmp_file.write_text("in-flight text")

    # Crop artifact was promoted before crash
    crop_data = b"promoted crop data"
    crop_sha = hashlib.sha256(crop_data).hexdigest()
    crop_file = job_dir / "crop_2_reg1_v2.jpg"
    crop_file.write_bytes(crop_data)

    manifest = [{
        "staging_id": "stg-c2",
        "region_id": "reg1",
        "artifact_version": 2,
        "dest_filename": "crop_2_reg1_v2.jpg",
        "sha256": crop_sha,
        "size_bytes": len(crop_data),
    }]

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        # Pre-seed v1
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path=f"/path/output_{job_id}_v1.md",
            sha256="hash1",
        ))
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename=filename,
            output_sha256="target-hash",
            staged_artifacts_manifest=json.dumps(manifest),
            status="FLUSHED",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()

    assert len(results) == 1
    assert results[0].action == "ROLLED_BACK_TMP"

    # Verify tmp file unlinked
    assert not tmp_file.exists()
    # Verify promoted crop reverted
    assert not crop_file.exists()

    # Verify intent deleted
    with uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None


def test_crash_recovery_case3_forward_roll(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 3
    intent_id = "intent-case-3"
    filename = f"output_{job_id}_v2.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    final_content = "# Successfully Renamed Document\n"
    final_sha = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
    final_file = job_dir / filename
    final_file.write_bytes(final_content.encode("utf-8"))

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/path/v1.md",
            sha256="h1",
        ))
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename=filename,
            output_sha256=final_sha,
            status="FLUSHED",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()

    assert len(results) == 1
    assert results[0].action == "FORWARD_ROLLED"

    with uow_factory.create() as uow:
        # Intent deleted
        assert uow.publish_intents.get_by_id(intent_id) is None
        # Document version rolled forward to 2
        latest = uow.document_versions.get_latest(job_id)
        assert latest is not None
        assert latest.version == 2
        assert latest.sha256 == final_sha
        assert latest.integrity_status == "VALID"
        assert latest.published_by == "CRASH_RECOVERY_FORWARD_ROLL"
        assert latest.output_path.startswith("file:///")
        assert latest.output_path == final_file.resolve().as_uri()

        job_db = uow.jobs.get_by_id(job_id)
        assert job_db.output_path.startswith("file:///")
        assert job_db.output_path == final_file.resolve().as_uri()


def test_crash_recovery_case4_checksum_mismatch(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 4
    intent_id = "intent-case-4"
    filename = f"output_{job_id}_v1.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    final_file = job_dir / filename
    final_file.write_text("corrupted content on disk")

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=0,
            target_version=1,
            output_filename=filename,
            output_sha256="expected-different-sha256",
            status="FLUSHED",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()

    assert len(results) == 1
    assert results[0].action == "QUARANTINED_CORRUPT_FINAL"

    # Verify final file is quarantined out of the canonical namespace
    assert not final_file.exists()
    quarantine_file = job_dir / f"{filename}.quarantine"
    assert quarantine_file.is_file()
    assert quarantine_file.read_text(encoding="utf-8") == "corrupted content on disk"

    with uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None
        job = uow.jobs.get_by_id(job_id)
        assert "[INTEGRITY_QUARANTINE]" in job.error_message


def test_crash_recovery_case5_already_completed(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 5
    intent_id = "intent-case-5"
    filename = f"output_{job_id}_v2.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    final_file = job_dir / filename
    final_file.write_text("already complete")
    sha = hashlib.sha256(b"already complete").hexdigest()

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        # Database already updated to target version 2
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=2,
            output_path=f"file://{final_file}",
            sha256=sha,
        ))
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename=filename,
            output_sha256=sha,
            status="FLUSHED",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()

    assert len(results) == 1
    assert results[0].action == "CLEANED_LINGERING_INTENT"

    with uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None


def test_crash_recovery_case6_idempotent_no_intents(pub_service: DocumentPublicationService):
    results = pub_service.reconcile_startup_intents()
    assert results == []


def test_recovery_artifact_preexisting_sha_match(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 7
    intent_id = "intent-match"
    filename = f"output_{job_id}_v2.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Crop destination exists and SHA matches manifest -> treated as already promoted
    crop_data = b"matching crop data"
    crop_sha = hashlib.sha256(crop_data).hexdigest()
    crop_file = job_dir / "crop_7_v2.jpg"
    crop_file.write_bytes(crop_data)

    # Final file also exists and matches -> Case 3 forward roll
    final_content = "markdown content"
    final_sha = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
    final_file = job_dir / filename
    final_file.write_text(final_content, encoding="utf-8")

    manifest = [{
        "staging_id": "stg-7",
        "region_id": "reg7",
        "artifact_version": 2,
        "dest_filename": "crop_7_v2.jpg",
        "sha256": crop_sha,
        "size_bytes": len(crop_data),
    }]

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/v1.md",
            sha256="h1",
        ))
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename=filename,
            output_sha256=final_sha,
            staged_artifacts_manifest=json.dumps(manifest),
            status="FLUSHED",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()
    assert len(results) == 1
    assert results[0].action == "FORWARD_ROLLED"
    # Crop artifact must still exist
    assert crop_file.is_file()


def test_recovery_artifact_preexisting_sha_mismatch(pub_service: DocumentPublicationService, uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    job_id = 8
    intent_id = "intent-mismatch"
    filename = f"output_{job_id}_v2.md"
    job_dir = tmp_path / "artifacts" / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Tmp file exists (crashed during staging)
    tmp_file = job_dir / f".{filename}.{intent_id}.tmp"
    tmp_file.write_text("tmp text")

    # Destination crop exists with DIFFERENT data (pre-existing artifact!)
    existing_crop_data = b"pre-existing un-related artifact"
    crop_file = job_dir / "crop_8_v2.jpg"
    crop_file.write_bytes(existing_crop_data)

    manifest = [{
        "staging_id": "stg-8",
        "region_id": "reg8",
        "artifact_version": 2,
        "dest_filename": "crop_8_v2.jpg",
        "sha256": "different-expected-sha-from-staging",
        "size_bytes": 100,
    }]

    with uow_factory.create() as uow:
        _create_job(uow, job_id=job_id)
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/v1.md",
            sha256="h1",
        ))
        uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id=intent_id,
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename=filename,
            output_sha256="expected-sha",
            staged_artifacts_manifest=json.dumps(manifest),
            status="PENDING",
        ))
        uow.commit()

    results = pub_service.reconcile_startup_intents()
    assert len(results) == 1
    assert results[0].action == "ROLLED_BACK_TMP"

    # Pre-existing file with different SHA must NOT be deleted!
    assert crop_file.is_file()
    assert crop_file.read_bytes() == existing_crop_data


def test_staging_orphan_scan(pub_service: DocumentPublicationService, tmp_path: Path):
    staging_base = tmp_path / "artifacts" / ".staging"
    staging_base.mkdir(parents=True, exist_ok=True)

    # 1. Fresh staging dir (< 24h)
    fresh_dir = staging_base / "fresh-staging-id"
    fresh_dir.mkdir()
    (fresh_dir / "crop.jpg").write_bytes(b"fresh")

    # 2. Old staging dir (> 24h)
    old_dir = staging_base / "old-staging-id"
    old_dir.mkdir()
    (old_dir / "crop.jpg").write_bytes(b"old")
    # Set mtime back by 30 hours
    old_mtime = time.time() - (30 * 3600)
    os.utime(old_dir, (old_mtime, old_mtime))

    pub_service.reconcile_startup_intents()

    # Old dir is pruned; fresh dir is kept
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_staging_directory_under_dot_staging_and_orphan_scanner_discovers_it(
    pub_service: DocumentPublicationService, tmp_path: Path
):
    """
    Ticket 11 Regression Test:
    Stage a crop using CropArtifactStagingService wired via artifacts_dir / ".staging",
    assert that the resulting file path is located within <artifacts_dir>/.staging/<staging_id>/,
    artificially age the staging directory (st_mtime > 25 hours),
    invoke reconcile_startup_intents(), and assert that the orphan directory is removed cleanly.
    """
    artifacts_dir = tmp_path / "artifacts"
    staging_svc = pub_service.staging_service

    staging_id = "orphan-aged-staging-session"
    handle = staging_svc.stage_crop(
        job_id=99,
        staging_id=staging_id,
        region_id="reg-orphan",
        version=1,
        image_bytes=b"staged-orphan-crop-data",
    )

    staged_file = Path(handle.staging_path)
    assert staged_file.is_file()
    # Assert staged file is strictly inside <artifacts_dir>/.staging/<staging_id>/
    expected_staging_dir = (artifacts_dir / ".staging" / staging_id).resolve()
    assert staged_file.parent.resolve() == expected_staging_dir
    assert ".staging" in staged_file.parts

    # Artificially age the staging directory by setting mtime to 30 hours ago
    old_mtime = time.time() - (30 * 3600)
    os.utime(expected_staging_dir, (old_mtime, old_mtime))

    # Run startup reconciliation without active intents
    pub_service.reconcile_startup_intents()

    # The orphan staging directory must be cleanly removed
    assert not expected_staging_dir.exists()
