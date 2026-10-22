# ============================================================
#  tests/unit/test_migration_005_schema.py
#  Tests for Migration 005: Pipeline 2 Source Version Lineage (P2-03)
# ============================================================

from pathlib import Path
import sqlite3
import pytest

from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork
from core.entities.job import Pipeline2Job, JobStatus, Job
from core.entities.prompt import Prompt, PromptType


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_polpot_v5.db"
    return SQLiteDatabaseManager(str(db_file))


@pytest.fixture
def migrated_db(db_manager: SQLiteDatabaseManager) -> SQLiteDatabaseManager:
    runner = SQLiteMigrationRunner(db_manager=db_manager)
    runner.run_migrations()
    return db_manager


def test_migration_005_upgrades_from_v4_database(tmp_path: Path):
    """
    Verifies that an existing v4 database cleanly applies migration 005,
    adding the source_version column to pipeline2_jobs while preserving existing rows.
    """
    db_file = tmp_path / "v4_database.db"
    mgr = SQLiteDatabaseManager(str(db_file))

    # Run only migrations 001, 002, 003, 004
    v4_migrations_dir = tmp_path / "v4_migrations"
    v4_migrations_dir.mkdir()
    real_migrations_dir = Path("infrastructure/persistence/sqlite/migrations")
    for fname in [
        "001_initial_schema.sql",
        "002_visual_regions.sql",
        "003_artifact_version_watermarks.sql",
        "004_publish_intents_and_document_versions.sql",
    ]:
        (v4_migrations_dir / fname).write_text((real_migrations_dir / fname).read_text(encoding="utf-8"), encoding="utf-8")

    runner_v4 = SQLiteMigrationRunner(db_manager=mgr, migrations_dir=v4_migrations_dir)
    applied_v4 = runner_v4.run_migrations()
    assert applied_v4 == [1, 2, 3, 4]

    # Insert a job and a pipeline2_job in v4 schema (no source_version column)
    with mgr.create_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == 4

        # Confirm source_version does not exist yet
        cur.execute("PRAGMA table_info(pipeline2_jobs)")
        cols_before = {row["name"] for row in cur.fetchall()}
        assert "source_version" not in cols_before

        cur.execute(
            """
            INSERT INTO jobs (file_name, file_path, status, total_pages, processed_pages, created_at, updated_at)
            VALUES ('doc.pdf', '/path/doc.pdf', 'done', 1, 1, '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')
            """
        )
        job_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO pipeline2_jobs (source_job_id, status, input_path, output_path, created_at, updated_at)
            VALUES (?, 'done', '/path/in.md', '/path/out.md', '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')
            """,
            (job_id,),
        )
        p2_id = cur.lastrowid
        conn.commit()

    # Now upgrade using full migrations directory (applies 005)
    full_runner = SQLiteMigrationRunner(db_manager=mgr)
    applied_v5 = full_runner.run_migrations()
    assert 5 in applied_v5

    with mgr.create_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == 5

        # Confirm source_version column exists
        cur.execute("PRAGMA table_info(pipeline2_jobs)")
        cols_after = {row["name"] for row in cur.fetchall()}
        assert "source_version" in cols_after

        # Confirm pre-existing row has source_version as NULL
        cur.execute("SELECT id, source_job_id, source_version FROM pipeline2_jobs WHERE id = ?", (p2_id,))
        row = cur.fetchone()
        assert row["id"] == p2_id
        assert row["source_job_id"] == job_id
        assert row["source_version"] is None


def test_pipeline2_job_repository_source_version_crud(migrated_db: SQLiteDatabaseManager):
    """
    Verifies that SQLitePipeline2JobRepository properly reads, writes, and queries
    source_version lineage.
    """
    uow = SQLiteUnitOfWork(migrated_db)
    with uow:
        # Create parent job
        parent = Job(
            id=None,
            file_name="paper.pdf",
            file_path="/artifacts/paper.pdf",
            status=JobStatus.DONE,
            total_pages=2,
        )
        saved_parent = uow.jobs.save(parent)

        # Create pipeline2 job with source_version=1
        p2 = Pipeline2Job(
            id=None,
            source_job_id=saved_parent.id,
            status=JobStatus.DONE,
            input_path="/artifacts/v1.md",
            output_path="/artifacts/p2_out.md",
            source_version=1,
        )
        saved_p2 = uow.pipeline2_jobs.save(p2)
        assert saved_p2.id is not None
        assert saved_p2.source_version == 1
        uow.commit()

    # Read back in new transaction
    with uow:
        retrieved = uow.pipeline2_jobs.get_by_id(saved_p2.id)
        assert retrieved is not None
        assert retrieved.source_version == 1

        # Test get_latest_for_source_job
        latest = uow.pipeline2_jobs.get_latest_for_source_job(saved_parent.id)
        assert latest is not None
        assert latest.id == saved_p2.id
        assert latest.source_version == 1

        # Update source_version
        retrieved.source_version = 2
        uow.pipeline2_jobs.save(retrieved)
        uow.commit()

    with uow:
        updated = uow.pipeline2_jobs.get_by_id(saved_p2.id)
        assert updated.source_version == 2
