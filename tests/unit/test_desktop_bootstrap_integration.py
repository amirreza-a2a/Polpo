# ============================================================
#  tests/unit/test_desktop_bootstrap_integration.py
#  Phase 10E.3a: Desktop Bootstrap Integration (Ticket 10E.3a-07)
# ============================================================

import os
from pathlib import Path

import pytest

from core.entities.job import JobStatus
from interfaces.desktop.composition import DesktopAppContainer
from interfaces.desktop.app import create_app


@pytest.fixture(scope="session", autouse=True)
def setup_offscreen_qpa():
    """Ensure Qt runs headlessly in test environment."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"


def test_bootstrap_order(tmp_path: Path):
    """
    Verifies that DesktopAppContainer.initialize() executes the deterministic
    five-step startup sequence in the strictly approved order:
    1. Schema migrations (applies 004)
    2. Legacy document version backfill
    3. Reconcile crashed publication intents
    4. Reconcile stale processing jobs
    5. Reconcile missed schedules
    """
    container = DesktopAppContainer(
        db_path=tmp_path / "test_bootstrap.db",
        artifacts_dir=tmp_path / "artifacts",
        scheduler_tick_interval=100.0,
    )

    call_order = []

    def make_tracker(name, orig_fn):
        def wrapper(*args, **kwargs):
            call_order.append(name)
            return orig_fn(*args, **kwargs)
        return wrapper

    container.migration_runner.run_migrations = make_tracker(
        "run_migrations", container.migration_runner.run_migrations
    )
    container.document_publication_service.backfill_legacy_document_versions = make_tracker(
        "backfill_legacy_document_versions",
        container.document_publication_service.backfill_legacy_document_versions,
    )
    container.document_publication_service.reconcile_startup_intents = make_tracker(
        "reconcile_startup_intents",
        container.document_publication_service.reconcile_startup_intents,
    )
    container.job_recovery_service.reconcile_stale_jobs = make_tracker(
        "reconcile_stale_jobs",
        container.job_recovery_service.reconcile_stale_jobs,
    )
    container.job_recovery_service.reconcile_missed_schedules = make_tracker(
        "reconcile_missed_schedules",
        container.job_recovery_service.reconcile_missed_schedules,
    )

    assert not container._initialized
    container.initialize()
    assert container._initialized

    expected_order = [
        "run_migrations",
        "backfill_legacy_document_versions",
        "reconcile_startup_intents",
        "reconcile_stale_jobs",
        "reconcile_missed_schedules",
    ]
    assert call_order == expected_order


def test_document_viewer_controller_wired(tmp_path: Path):
    """
    Verifies that DocumentViewerController is constructed with
    region_publication_service=container.visual_region_publication_service
    in create_app.
    """
    _app, _engine, container = create_app(
        argv=["-platform", "offscreen"],
        db_path=tmp_path / "test_app.db",
        artifacts_dir=tmp_path / "artifacts",
        start_background_runtime=False,
    )

    try:
        assert container.document_viewer_controller is not None
        assert container.visual_region_publication_service is not None
        assert container.document_viewer_controller.region_publication_service is container.visual_region_publication_service
    finally:
        container.shutdown()


def test_bootstrap_fresh_database(tmp_path: Path):
    """
    Verifies that DesktopAppContainer.initialize() completes cleanly on a brand new database,
    wiring the publication service and staging service, and applying migration 004.
    """
    db_path = tmp_path / "fresh_polpot.db"
    artifacts_dir = tmp_path / "artifacts"

    container = DesktopAppContainer(
        db_path=db_path,
        artifacts_dir=artifacts_dir,
        scheduler_tick_interval=100.0,
    )

    # Verify services are instantiated
    assert container.crop_staging_service is not None
    assert container.document_publication_service is not None
    assert container.document_publication_service.staging_service is container.crop_staging_service

    # Initialize container
    container.initialize()
    assert container._initialized

    # Verify tables created by migration 004
    with container.uow_factory.create() as uow:
        cur = uow._conn.cursor()
        cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert row[0] >= 4

        # Verify empty publication tables
        cur.execute("SELECT COUNT(*) FROM publish_intents")
        assert cur.fetchone()[0] == 0

        cur.execute("SELECT COUNT(*) FROM document_versions")
        assert cur.fetchone()[0] == 0


def test_bootstrap_existing_database(tmp_path: Path):
    """
    Verifies that DesktopAppContainer.initialize() correctly reconciles an existing database:
    - Backfills valid legacy Markdown files into document_versions (VALID).
    - Quarantines unreadable/missing legacy files (QUARANTINED).
    - Ignores jobs with NULL output_path.
    - Reconciles any lingering publication intents from prior crashes.
    - Reconciles stale processing jobs to PAUSED.
    """
    db_path = tmp_path / "existing_polpot.db"
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate database with legacy data
    from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
    from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner

    mgr = SQLiteDatabaseManager(str(db_path))
    runner = SQLiteMigrationRunner(mgr)
    runner.run_migrations()  # Applies all migrations including 004

    # Create test jobs
    with mgr.create_connection() as conn:
        cur = conn.cursor()

        # Job 1: Valid canonical file
        j1_dir = artifacts_dir / "job_1"
        j1_dir.mkdir(parents=True, exist_ok=True)
        j1_file = j1_dir / "output_1_v1.md"
        j1_file.write_text("# Legacy Canonical Markdown\n", encoding="utf-8")
        cur.execute(
            """
            INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
            VALUES (1, 'doc1.pdf', '/in/doc1.pdf', 'done', ?, '2026-09-19T00:00:00', '2026-09-19T01:00:00')
            """,
            (f"file://{j1_file.resolve().as_posix()}",),
        )

        # Job 2: Missing canonical file
        missing_file = artifacts_dir / "job_2" / "missing_v1.md"
        cur.execute(
            """
            INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
            VALUES (2, 'doc2.pdf', '/in/doc2.pdf', 'done', ?, '2026-09-19T00:00:00', '2026-09-19T01:00:00')
            """,
            (f"file://{missing_file.resolve().as_posix()}",),
        )

        # Job 3: Unfinished job with NULL output_path
        cur.execute(
            """
            INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
            VALUES (3, 'doc3.pdf', '/in/doc3.pdf', 'pending', NULL, '2026-09-19T00:00:00', '2026-09-19T01:00:00')
            """
        )

        # Job 4: Stale job in PROCESSING status
        cur.execute(
            """
            INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
            VALUES (4, 'doc4.pdf', '/in/doc4.pdf', 'processing', NULL, '2026-09-19T00:00:00', '2026-09-19T01:00:00')
            """
        )

        # Lingering crash intent for Job 1 (Case 1: pending with no tmp file)
        cur.execute(
            """
            INSERT INTO publish_intents (
                intent_id, job_id, base_version, target_version, output_filename,
                output_sha256, status, created_at
            ) VALUES ('intent-crash-1', 1, 1, 2, 'output_1_v2.md', 'sha2', 'PENDING', '2026-09-19T01:00:00')
            """
        )
        conn.commit()

    # Now boot DesktopAppContainer and initialize
    container = DesktopAppContainer(
        db_path=db_path,
        artifacts_dir=artifacts_dir,
        scheduler_tick_interval=100.0,
    )
    container.initialize()
    assert container._initialized

    # Verify state after bootstrap reconciliation
    with container.uow_factory.create() as uow:
        # Job 1 was backfilled to version 1
        j1_doc = uow.document_versions.get_latest(1)
        assert j1_doc is not None
        assert j1_doc.version == 1
        assert j1_doc.integrity_status == "VALID"
        assert j1_doc.sha256 is not None

        # Job 1 lingering intent was cleaned up
        assert uow.publish_intents.get_by_id("intent-crash-1") is None

        # Job 2 was quarantined
        j2_doc = uow.document_versions.get_latest(2)
        assert j2_doc is not None
        assert j2_doc.integrity_status == "QUARANTINED"
        assert j2_doc.sha256 is None

        # Job 3 has no document_versions row
        assert uow.document_versions.get_latest(3) is None

        # Job 4 was moved from PROCESSING to PAUSED by job recovery service
        j4 = uow.jobs.get_by_id(4)
        assert j4 is not None
        assert j4.status == JobStatus.PAUSED
