# ============================================================
#  tests/unit/test_migration_004_schema.py
#  Tests for Migration 004 Schema & Repository Methods (Ticket 10E.3a-04)
# ============================================================

from pathlib import Path
import sqlite3
import pytest

from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_polpot.db"
    return SQLiteDatabaseManager(str(db_file))


@pytest.fixture
def migrated_db(db_manager: SQLiteDatabaseManager) -> SQLiteDatabaseManager:
    runner = SQLiteMigrationRunner(db_manager=db_manager)
    runner.run_migrations()
    return db_manager


def test_migration_004_upgrades_from_v3_database(tmp_path: Path):
    # Setup a database migrated only up to version 3
    db_file = tmp_path / "v3_database.db"
    mgr = SQLiteDatabaseManager(str(db_file))

    # Run only migrations 001, 002, 003
    v3_migrations_dir = tmp_path / "v3_migrations"
    v3_migrations_dir.mkdir()
    real_migrations_dir = Path("infrastructure/persistence/sqlite/migrations")
    for fname in ["001_initial_schema.sql", "002_visual_regions.sql", "003_artifact_version_watermarks.sql"]:
        (v3_migrations_dir / fname).write_text((real_migrations_dir / fname).read_text(encoding="utf-8"), encoding="utf-8")

    runner_v3 = SQLiteMigrationRunner(db_manager=mgr, migrations_dir=v3_migrations_dir)
    applied_v3 = runner_v3.run_migrations()
    assert applied_v3 == [1, 2, 3]

    with mgr.create_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == 3

    # Now run migrations with the full migrations directory (includes 004)
    full_runner = SQLiteMigrationRunner(db_manager=mgr)
    applied_v4 = full_runner.run_migrations()
    assert applied_v4 == [4]

    with mgr.create_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == 4

        # Verify tables created
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publish_intents'")
        assert cur.fetchone() is not None
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='document_versions'")
        assert cur.fetchone() is not None


def test_migration_004_creates_tables(migrated_db: SQLiteDatabaseManager):
    with migrated_db.create_connection() as conn:
        cur = conn.cursor()
        # Verify schema version 4 recorded
        cur.execute("SELECT MAX(version) FROM schema_version")
        ver = cur.fetchone()[0]
        assert ver >= 4

        # Verify publish_intents table columns
        cur.execute("PRAGMA table_info(publish_intents)")
        intent_cols = {row["name"]: row["type"] for row in cur.fetchall()}
        assert "intent_id" in intent_cols
        assert "job_id" in intent_cols
        assert "base_version" in intent_cols
        assert "target_version" in intent_cols
        assert "output_filename" in intent_cols
        assert "output_sha256" in intent_cols
        assert "staged_artifacts_manifest" in intent_cols
        assert "status" in intent_cols
        assert "created_at" in intent_cols

        # Verify document_versions table columns
        cur.execute("PRAGMA table_info(document_versions)")
        doc_cols = {row["name"]: row["type"] for row in cur.fetchall()}
        assert "id" in doc_cols
        assert "job_id" in doc_cols
        assert "version" in doc_cols
        assert "output_path" in doc_cols
        assert "sha256" in doc_cols
        assert "integrity_status" in doc_cols
        assert "published_by" in doc_cols
        assert "created_at" in doc_cols


def test_migration_004_unique_constraints(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        # Create a parent job first
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("test.pdf", "/path/test.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid
        uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        # 1. Test UNIQUE(job_id) on active publish_intents
        intent1 = PublishIntentRecord(
            intent_id="intent-1",
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename="output_1_v2.md",
            output_sha256="abc123hash",
            staged_artifacts_manifest="[]",
            status="PENDING",
            created_at="2026-09-19T10:00:00",
        )
        uow.publish_intents.insert_intent(intent1)
        uow.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with SQLiteUnitOfWork(migrated_db) as uow:
            intent2 = PublishIntentRecord(
                intent_id="intent-2",
                job_id=job_id,  # Duplicate active job_id
                base_version=1,
                target_version=2,
                output_filename="output_1_v2.md",
                output_sha256="abc123hash",
                staged_artifacts_manifest="[]",
                status="PENDING",
                created_at="2026-09-19T10:00:01",
            )
            uow.publish_intents.insert_intent(intent2)
            uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        # 2. Test UNIQUE(job_id, version) on document_versions
        v1 = DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/path/v1.md",
            sha256="hash1",
            integrity_status="VALID",
            published_by="TEST",
            created_at="2026-09-19T10:00:00",
        )
        uow.document_versions.insert_document_version(v1)
        uow.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with SQLiteUnitOfWork(migrated_db) as uow:
            v1_duplicate = DocumentVersionRecord(
                job_id=job_id,
                version=1,  # Duplicate version for same job
                output_path="/path/v1_other.md",
                sha256="hash2",
                integrity_status="VALID",
                published_by="TEST",
                created_at="2026-09-19T10:01:00",
            )
            uow.document_versions.insert_document_version(v1_duplicate)
            uow.commit()


def test_migration_004_integrity_status_check(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("test.pdf", "/path/test.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid
        uow.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with SQLiteUnitOfWork(migrated_db) as uow:
            invalid_record = DocumentVersionRecord(
                job_id=job_id,
                version=1,
                output_path="/path/v1.md",
                sha256="hash",
                integrity_status="INVALID_STATUS",  # Violates CHECK constraint
                published_by="TEST",
                created_at="2026-09-19T10:00:00",
            )
            uow.document_versions.insert_document_version(invalid_record)
            uow.commit()


def test_migration_004_nullable_sha256(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("test.pdf", "/path/test.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid
        uow.commit()

    # Nullable sha256 is accepted for QUARANTINED records
    with SQLiteUnitOfWork(migrated_db) as uow:
        quarantined = DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/path/v1.md",
            sha256=None,
            integrity_status="QUARANTINED",
            published_by="LEGACY_BACKFILL",
            created_at="2026-09-19T10:00:00",
        )
        saved = uow.document_versions.insert_document_version(quarantined)
        uow.commit()
        assert saved.sha256 is None
        assert saved.integrity_status == "QUARANTINED"


def test_migration_004_idempotent_on_fresh_db(migrated_db: SQLiteDatabaseManager):
    runner = SQLiteMigrationRunner(db_manager=migrated_db)
    # Second run of migrations is cleanly idempotent (zero new migrations applied)
    applied = runner.run_migrations()
    assert len(applied) == 0


def test_repository_intent_crud(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("test.pdf", "/path/test.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid

        # 1. Insert intent
        intent = PublishIntentRecord(
            intent_id="intent-crud-1",
            job_id=job_id,
            base_version=1,
            target_version=2,
            output_filename="output_v2.md",
            output_sha256="sha-256-val",
            staged_artifacts_manifest="[]",
            status="PENDING",
            created_at="2026-09-19T10:00:00",
        )
        uow.publish_intents.insert_intent(intent)
        uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        # 2. Get intent by job_id (testing both get_by_job_id and get_intent_by_job_id)
        fetched = uow.publish_intents.get_by_job_id(job_id)
        assert fetched is not None
        assert fetched.intent_id == "intent-crud-1"
        assert fetched.status == "PENDING"
        assert uow.publish_intents.get_intent_by_job_id(job_id) == fetched

        # 3. Update intent status to FLUSHED (testing update_intent_status)
        uow.publish_intents.update_intent_status("intent-crud-1", "FLUSHED")
        uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        fetched2 = uow.publish_intents.get_intent_by_job_id(job_id)
        assert fetched2 is not None
        assert fetched2.status == "FLUSHED"

        # 4. Delete intent
        deleted = uow.publish_intents.delete_intent("intent-crud-1")
        assert deleted is True
        uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        assert uow.publish_intents.get_by_job_id(job_id) is None


def test_repository_document_version_crud(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("test.pdf", "/path/test.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid

        # Insert v1
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/path/v1.md",
            sha256="hash1",
            integrity_status="VALID",
            published_by="TEST_P1",
            created_at="2026-09-19T10:00:00",
        ))
        # Insert v2
        uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=2,
            output_path="/path/v2.md",
            sha256="hash2",
            integrity_status="VALID",
            published_by="TEST_P2",
            created_at="2026-09-19T10:05:00",
        ))
        uow.commit()

    with SQLiteUnitOfWork(migrated_db) as uow:
        # Latest version (testing both get_latest and get_latest_document_version)
        latest = uow.document_versions.get_latest(job_id)
        assert latest is not None
        assert latest.version == 2
        assert latest.sha256 == "hash2"
        assert uow.document_versions.get_latest_document_version(job_id) == latest

        # All versions for job
        history = uow.document_versions.get_by_job_id(job_id)
        assert len(history) == 2
        assert history[0].version == 1
        assert history[1].version == 2


def test_repository_auto_timestamp_and_cascade_delete(migrated_db: SQLiteDatabaseManager):
    with SQLiteUnitOfWork(migrated_db) as uow:
        cur = uow._conn.cursor()
        cur.execute(
            "INSERT INTO jobs (file_name, file_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("cascade_test.pdf", "/path/cascade.pdf", "pending", "2026-09-19T00:00:00", "2026-09-19T00:00:00"),
        )
        job_id = cur.lastrowid

        # Insert intent without created_at -> should be auto-populated
        intent = uow.publish_intents.insert_intent(PublishIntentRecord(
            intent_id="intent-auto-time",
            job_id=job_id,
            base_version=0,
            target_version=1,
            output_filename="output_v1.md",
            output_sha256="hash-v1",
        ))
        assert intent.created_at is not None

        # Insert doc version without created_at -> should be auto-populated
        doc_ver = uow.document_versions.insert_document_version(DocumentVersionRecord(
            job_id=job_id,
            version=1,
            output_path="/path/v1.md",
            sha256="hash-v1",
        ))
        assert doc_ver.created_at is not None
        uow.commit()

    # Verify rows exist
    with SQLiteUnitOfWork(migrated_db) as uow:
        assert uow.publish_intents.get_intent_by_job_id(job_id) is not None
        assert uow.document_versions.get_latest_document_version(job_id) is not None

        # Delete the job and verify cascade
        cur = uow._conn.cursor()
        cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        uow.commit()

    # Verify cascaded deletion
    with SQLiteUnitOfWork(migrated_db) as uow:
        assert uow.publish_intents.get_intent_by_job_id(job_id) is None
        assert uow.document_versions.get_latest_document_version(job_id) is None
