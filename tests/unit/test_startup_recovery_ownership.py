# ============================================================
#  tests/unit/test_startup_recovery_ownership.py
#  Deterministic Ownership-Safe Startup Recovery Tests
# ============================================================

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

from core.entities.job import Job, JobStatus, Pipeline2Job
from application.events import JobStateChangedEvent
from core.exceptions.domain_exceptions import ApplicationInstanceLockedError
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.instance_lock import SQLiteInstanceLock
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from application.services.job_recovery import JobRecoveryService
from interfaces.desktop.composition import DesktopAppContainer
from interfaces.desktop.app import main


class TestStartupRecoveryOwnership(unittest.TestCase):
    """
    Comprehensive test suite verifying multi-process ownership safety,
    inter-process database locking, crash recovery, and exception preservation.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ownership_test.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.vault_path = Path(self.temp_dir.name) / "credentials.enc"
        self.events: List[object] = []
        self._active_containers: List[DesktopAppContainer] = []

    def tearDown(self):
        for container in self._active_containers:
            try:
                container.shutdown()
            except Exception:
                pass
        self._active_containers.clear()
        self.temp_dir.cleanup()

    def _create_container(self) -> DesktopAppContainer:
        container = DesktopAppContainer(
            db_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            vault_path=self.vault_path,
            passphrase="TestPassword123!",
            scheduler_tick_interval=100.0,
        )
        self._active_containers.append(container)
        return container

    # -------------------------------------------------------------------------
    # 1. Second instance cannot perform unsafe recovery while active instance owns DB
    # -------------------------------------------------------------------------
    def test_second_instance_cannot_perform_unsafe_recovery_while_active_instance_owns_database(self):
        """
        Instance A claims a Pipeline 2 job and standard job (both PROCESSING).
        Instance B attempts initialize() against the same database.
        Instance B must fail with ApplicationInstanceLockedError, and Instance A's
        active in-flight jobs must remain in PROCESSING.
        """
        container_a = self._create_container()
        container_a.initialize()

        # Enqueue and claim both a standard job and a Pipeline 2 job
        with container_a.uow_factory.create() as uow:
            p1 = Job(id=None, file_name="p1.pdf", file_path="/p1.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            p2 = Pipeline2Job(id=None, source_job_id=saved_p1.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        with container_a.uow_factory.create() as uow:
            claimed_p1 = uow.jobs.claim_job(saved_p1.id)
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.assertIsNotNone(claimed_p1)
        self.assertIsNotNone(claimed_p2)
        self.assertEqual(claimed_p1.status, JobStatus.PROCESSING)
        self.assertEqual(claimed_p2.status, JobStatus.PROCESSING)

        # Instance B attempts to initialize against the exact same database
        container_b = self._create_container()
        with self.assertRaises(ApplicationInstanceLockedError) as ctx:
            container_b.initialize()

        self.assertIn("Another application instance", str(ctx.exception))
        self.assertIn(str(self.db_path), str(ctx.exception))
        self.assertEqual(ctx.exception.holder_pid, os.getpid())

        # Verify Instance A's active jobs were NOT touched or paused
        with container_a.uow_factory.create() as uow:
            db_p1 = uow.jobs.get_by_id(saved_p1.id)
            db_p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(db_p1.status, JobStatus.PROCESSING)
            self.assertEqual(db_p2.status, JobStatus.PROCESSING)

        container_a.shutdown()

    # -------------------------------------------------------------------------
    # 2. Genuinely interrupted PROCESSING Pipeline 2 job is recovered
    # -------------------------------------------------------------------------
    def test_genuinely_interrupted_pipeline2_job_is_recovered_to_paused(self):
        """
        When an instance terminates leaving a Pipeline 2 job in PROCESSING,
        the subsequent valid startup reconciles it to PAUSED and publishes event.
        """
        # Seed an interrupted Pipeline 2 job directly
        db_manager = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(db_manager).run_migrations()
        uow_factory = SQLiteUnitOfWorkFactory(db_manager)

        with uow_factory.create() as uow:
            p1 = Job(id=None, file_name="crashed.pdf", file_path="/p.pdf", status=JobStatus.DONE)
            saved_p1 = uow.jobs.save(p1)
            p2 = Pipeline2Job(id=None, source_job_id=saved_p1.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        with uow_factory.create() as uow:
            uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        # Simulate subsequent clean application startup
        event_bus = InMemoryEventBus()
        captured_events = []
        event_bus.subscribe(JobStateChangedEvent, captured_events.append)

        container = self._create_container()
        container.event_bus = event_bus
        container.job_recovery_service.event_publisher = event_bus
        container.initialize()

        with container.uow_factory.create() as uow:
            rec_p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(rec_p2.status, JobStatus.PAUSED)
            self.assertEqual(rec_p2.error_message, "Interrupted by application crash or unexpected shutdown")

        self.assertTrue(any(e.job_id == saved_p1.id and e.new_status == JobStatus.PAUSED for e in captured_events))
        container.shutdown()

    # -------------------------------------------------------------------------
    # 3. Genuinely interrupted standard job follows intended recovery policy
    # -------------------------------------------------------------------------
    def test_genuinely_interrupted_standard_job_is_recovered_to_paused(self):
        """
        When an instance terminates leaving a standard job in PROCESSING,
        the subsequent valid startup reconciles it to PAUSED and publishes event.
        """
        db_manager = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(db_manager).run_migrations()
        uow_factory = SQLiteUnitOfWorkFactory(db_manager)

        with uow_factory.create() as uow:
            p1 = Job(id=None, file_name="interrupted_std.pdf", file_path="/p.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            uow.commit()

        with uow_factory.create() as uow:
            uow.jobs.claim_job(saved_p1.id)
            uow.commit()

        event_bus = InMemoryEventBus()
        captured_events = []
        event_bus.subscribe(JobStateChangedEvent, captured_events.append)

        container = self._create_container()
        container.event_bus = event_bus
        container.job_recovery_service.event_publisher = event_bus
        container.initialize()

        with container.uow_factory.create() as uow:
            rec_p1 = uow.jobs.get_by_id(saved_p1.id)
            self.assertEqual(rec_p1.status, JobStatus.PAUSED)
            self.assertEqual(rec_p1.error_message, "Interrupted by application crash or unexpected shutdown")

        self.assertTrue(any(e.job_id == saved_p1.id and e.new_status == JobStatus.PAUSED for e in captured_events))
        container.shutdown()

    # -------------------------------------------------------------------------
    # 4. Recovery is idempotent
    # -------------------------------------------------------------------------
    def test_recovery_is_idempotent(self):
        """
        Executing reconcile_stale_jobs multiple times yields identical final state
        and subsequent invocations return 0 recovered records.
        """
        container = self._create_container()
        container.initialize()

        with container.uow_factory.create() as uow:
            p1 = Job(id=None, file_name="idem.pdf", file_path="/p.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            p2 = Pipeline2Job(id=None, source_job_id=saved_p1.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        with container.uow_factory.create() as uow:
            uow.jobs.claim_job(saved_p1.id)
            uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        first_count = container.job_recovery_service.reconcile_stale_jobs()
        self.assertEqual(first_count, 2)

        second_count = container.job_recovery_service.reconcile_stale_jobs()
        self.assertEqual(second_count, 0)

        third_count = container.job_recovery_service.reconcile_stale_jobs()
        self.assertEqual(third_count, 0)

        container.shutdown()

    # -------------------------------------------------------------------------
    # 5. Slow/long-running job does not become stale merely by elapsed duration
    # -------------------------------------------------------------------------
    def test_slow_or_long_running_job_not_marked_stale_merely_by_elapsed_time(self):
        """
        A job claimed 2 hours ago by an active instance does NOT become stale
        or paused while the owning instance is running.
        """
        container = self._create_container()
        container.initialize()

        # Claim a job and simulate it having run for 2 hours
        with container.uow_factory.create() as uow:
            p1 = Job(id=None, file_name="long_ai_task.pdf", file_path="/p.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            uow.commit()

        with container.uow_factory.create() as uow:
            uow.jobs.claim_job(saved_p1.id)
            past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            uow._conn.execute("UPDATE jobs SET claimed_at = ? WHERE id = ?", (past_iso, saved_p1.id))
            uow.commit()

        # Because container is active, another instance cannot initialize or clobber it
        container_b = self._create_container()
        with self.assertRaises(ApplicationInstanceLockedError):
            container_b.initialize()

        # The active job remains in PROCESSING
        with container.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(saved_p1.id)
            self.assertEqual(job.status, JobStatus.PROCESSING)

        container.shutdown()

    # -------------------------------------------------------------------------
    # 6. Ownership released safely on normal shutdown
    # -------------------------------------------------------------------------
    def test_ownership_is_released_safely_on_normal_shutdown(self):
        """
        When container_a is cleanly shut down, container_b can acquire the lock
        and initialize without error.
        """
        container_a = self._create_container()
        container_a.initialize()
        self.assertTrue(container_a.instance_lock.is_locked)

        container_a.shutdown()
        self.assertFalse(container_a.instance_lock.is_locked)

        container_b = self._create_container()
        container_b.initialize()
        self.assertTrue(container_b.instance_lock.is_locked)
        container_b.shutdown()

    # -------------------------------------------------------------------------
    # 7. Process-owner termination releases lock so next startup recovers work
    # -------------------------------------------------------------------------
    def test_after_process_owner_termination_next_valid_startup_recovers_abandoned_work(self):
        """
        Spawns a real separate Python process that acquires the lock, claims a job,
        and is then killed abruptly (SIGKILL).
        The operating system kernel must release the lock, allowing the parent
        to start up and recover the abandoned job.
        """
        db_manager = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(db_manager).run_migrations()
        uow_factory = SQLiteUnitOfWorkFactory(db_manager)

        with uow_factory.create() as uow:
            p1 = Job(id=None, file_name="crashed_proc.pdf", file_path="/p.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            p2 = Pipeline2Job(id=None, source_job_id=saved_p1.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        child_script = f"""
import sys, time
from pathlib import Path
from interfaces.desktop.composition import DesktopAppContainer

container = DesktopAppContainer(
    db_path={repr(str(self.db_path))},
    artifacts_dir={repr(str(self.artifacts_dir))},
    vault_path={repr(str(self.vault_path))},
    passphrase="TestPassword123!",
    scheduler_tick_interval=100.0,
)
container.initialize()

with container.uow_factory.create() as uow:
    uow.pipeline2_jobs.claim_next_pending()
    uow.commit()

sys.stdout.write("READY\\n")
sys.stdout.flush()
time.sleep(30)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            ready_line = proc.stdout.readline().decode().strip()
            self.assertEqual(ready_line, "READY")

            # Verify parent cannot initialize while child process is alive
            parent_container = self._create_container()
            with self.assertRaises(ApplicationInstanceLockedError):
                parent_container.initialize()

            # Abruptly kill the child process (simulating unhandled SIGKILL / OS crash)
            proc.kill()
            proc.wait()

            # Parent now initializes successfully and recovers the abandoned Pipeline 2 job
            parent_container.initialize()
            with parent_container.uow_factory.create() as uow:
                db_p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
                self.assertEqual(db_p2.status, JobStatus.PAUSED)
                self.assertEqual(db_p2.error_message, "Interrupted by application crash or unexpected shutdown")

            parent_container.shutdown()
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    # -------------------------------------------------------------------------
    # 8. Concurrent recovery attempts cannot perform conflicting transitions
    # -------------------------------------------------------------------------
    def test_concurrent_recovery_attempts_cannot_perform_conflicting_transitions(self):
        """
        Multiple concurrent threads invoking reconcile_stale_jobs() on the same database
        serialize under BEGIN IMMEDIATE transactions; exactly the existing jobs are transitioned once.
        """
        db_manager = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(db_manager).run_migrations()
        uow_factory = SQLiteUnitOfWorkFactory(db_manager)

        num_jobs = 10
        with uow_factory.create() as uow:
            now_dt = datetime.now(timezone.utc)
            for i in range(num_jobs):
                j = Job(
                    id=None,
                    file_name=f"c_{i}.pdf",
                    file_path=f"/c_{i}.pdf",
                    status=JobStatus.PROCESSING,
                    claimed_at=now_dt,
                )
                uow.jobs.save(j)
            uow.commit()

        recovery_service = JobRecoveryService(uow_factory=uow_factory)
        counts: List[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)
        errors: List[Exception] = []

        def worker():
            barrier.wait()
            try:
                c = recovery_service.reconcile_stale_jobs()
                with lock:
                    counts.append(c)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(sum(counts), num_jobs)

        with uow_factory.create() as uow:
            paused_jobs = uow.jobs.list(status=JobStatus.PAUSED, limit=100)
            self.assertEqual(len(paused_jobs), num_jobs)

    # -------------------------------------------------------------------------
    # 9. Repository errors are not hidden by broad compatibility fallback
    # -------------------------------------------------------------------------
    def test_repository_errors_are_not_hidden_by_broad_compatibility_fallback(self):
        """
        If a repository's reconcile_stale_jobs raises a genuine TypeError internally,
        JobRecoveryService must propagate that exception rather than silently retrying
        without older_than and triggering an unsafe full reset.
        """
        mock_uow = MagicMock()
        mock_uow_factory = MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

        # Mock a repo where reconcile_stale_jobs raises an internal TypeError
        def buggy_reconcile(older_than=None):
            raise TypeError("Internal type mismatch in date comparison")

        mock_uow.jobs.reconcile_stale_jobs = buggy_reconcile
        mock_uow.pipeline2_jobs = None

        service = JobRecoveryService(uow_factory=mock_uow_factory)
        threshold = datetime.now(timezone.utc) - timedelta(minutes=10)

        with self.assertRaises(TypeError) as ctx:
            service.reconcile_stale_jobs(older_than=threshold)

        self.assertIn("Internal type mismatch in date comparison", str(ctx.exception))

    def test_legacy_repo_without_older_than_raises_when_filtered_recovery_requested(self):
        """
        If a legacy repository does not support older_than, calling reconcile_stale_jobs
        with a specific threshold raises TypeError rather than performing an unsafe full-table wipe.
        """
        mock_uow = MagicMock()
        mock_uow_factory = MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

        # Legacy repo signature: takes no arguments
        def legacy_reconcile():
            return 5

        mock_uow.jobs.reconcile_stale_jobs = legacy_reconcile
        mock_uow.pipeline2_jobs = None

        service = JobRecoveryService(uow_factory=mock_uow_factory)
        threshold = datetime.now(timezone.utc) - timedelta(minutes=10)

        with self.assertRaises(TypeError) as ctx:
            service.reconcile_stale_jobs(older_than=threshold)

        self.assertIn("does not support 'older_than' filtering", str(ctx.exception))

        # But calling with older_than=None works cleanly for legacy repos
        res = service.reconcile_stale_jobs(older_than=None)
        self.assertEqual(res, 5)

    # -------------------------------------------------------------------------
    # 10. CLI main diagnostic on locked instance
    # -------------------------------------------------------------------------
    def test_main_cli_reports_actionable_diagnostic_on_locked_instance(self):
        """When another instance holds the database lock, main() logs and returns 1."""
        with patch(
            "interfaces.desktop.app.create_app",
            side_effect=ApplicationInstanceLockedError("Another instance is running (PID 99999)"),
        ):
            with patch("sys.stderr.write") as mock_stderr:
                code = main([])
                self.assertEqual(code, 1)
                written = "".join(call.args[0] for call in mock_stderr.call_args_list)
                self.assertIn("Another instance is running (PID 99999)", written)

    # -------------------------------------------------------------------------
    # 11. SQLiteInstanceLock unit behaviors
    # -------------------------------------------------------------------------
    def test_sqlite_instance_lock_unit_lifecycle(self):
        """Tests acquire, is_locked, contention, get_holder_pid, and release."""
        lock1 = SQLiteInstanceLock(self.db_path)
        try:
            self.assertFalse(lock1.is_locked)

            self.assertTrue(lock1.acquire())
            self.assertTrue(lock1.is_locked)

            # Second lock attempt on same file path fails
            lock2 = SQLiteInstanceLock(self.db_path)
            try:
                self.assertFalse(lock2.acquire())
                self.assertFalse(lock2.is_locked)
                self.assertEqual(lock2.get_holder_pid(), os.getpid())
            finally:
                lock2.release()

            # Release lock1
            lock1.release()
            self.assertFalse(lock1.is_locked)

            # Now lock2 can acquire
            self.assertTrue(lock2.acquire())
            self.assertTrue(lock2.is_locked)
            lock2.release()
        finally:
            lock1.release()

    def test_sqlite_instance_lock_windows_simulated_path(self):
        """
        Verifies that on Windows, SQLiteInstanceLock uses _winapi file sharing
        and out-of-band offset locking, allowing readers to inspect the PID
        without file contention.
        """
        import types

        lock_path = Path(self.temp_dir.name) / "sim_win.lock"
        real_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))

        mock_winapi = types.ModuleType("_winapi")
        mock_winapi.GENERIC_READ = 0x80000000
        mock_winapi.GENERIC_WRITE = 0x40000000
        mock_winapi.FILE_SHARE_READ = 1
        mock_winapi.FILE_SHARE_WRITE = 2
        mock_winapi.FILE_SHARE_DELETE = 4
        mock_winapi.OPEN_ALWAYS = 4
        mock_winapi.OPEN_EXISTING = 3
        mock_winapi.FILE_ATTRIBUTE_NORMAL = 128
        mock_winapi.CreateFile = MagicMock(return_value=999)
        mock_winapi.CloseHandle = MagicMock()

        mock_msvcrt = types.ModuleType("msvcrt")
        mock_msvcrt.LK_NBLCK = 2
        mock_msvcrt.LK_UNLCK = 0
        mock_msvcrt.open_osfhandle = MagicMock(return_value=real_fd)
        mock_msvcrt.locking = MagicMock()

        with patch("infrastructure.persistence.sqlite.instance_lock._winapi", mock_winapi), \
             patch("infrastructure.persistence.sqlite.instance_lock.msvcrt", mock_msvcrt):

            lock = SQLiteInstanceLock(self.db_path, lock_path=lock_path)
            try:
                self.assertTrue(lock._acquire_windows())
                self.assertTrue(lock.is_locked)

                mock_winapi.CreateFile.assert_called_once_with(
                    str(lock_path),
                    SQLiteInstanceLock._GENERIC_READ | SQLiteInstanceLock._GENERIC_WRITE,
                    SQLiteInstanceLock._FILE_SHARE_READ | SQLiteInstanceLock._FILE_SHARE_WRITE | SQLiteInstanceLock._FILE_SHARE_DELETE,
                    0,
                    SQLiteInstanceLock._OPEN_ALWAYS,
                    SQLiteInstanceLock._FILE_ATTRIBUTE_NORMAL,
                    0,
                )
                mock_msvcrt.locking.assert_called_with(real_fd, mock_msvcrt.LK_NBLCK, 1)

                self.assertEqual(lock.get_holder_pid(), os.getpid())
            finally:
                lock.release()

            self.assertFalse(lock.is_locked)
            mock_msvcrt.locking.assert_called_with(real_fd, mock_msvcrt.LK_UNLCK, 1)

    # -------------------------------------------------------------------------
    # 12. Root-cause reproduction test
    # -------------------------------------------------------------------------
    def test_root_cause_reproduction_recovery_without_lock_pauses_active_jobs_while_lock_blocks_it(self):
        """
        Reproduces the exact failure scenario from Section 3:
        1. Instance A claims a Pipeline 1 and Pipeline 2 job (both PROCESSING).
        2. If recovery runs without single-instance lock protection, it resets
           active jobs to PAUSED (proving the pre-fix vulnerability).
        3. With the single-instance lock, Instance B's initialization is rejected
           with ApplicationInstanceLockedError before recovery can execute,
           and Instance A's active jobs remain in PROCESSING.
        """
        container_a = self._create_container()
        container_a.initialize()

        with container_a.uow_factory.create() as uow:
            p1 = Job(id=None, file_name="p1_repro.pdf", file_path="/p1.pdf", status=JobStatus.PENDING)
            saved_p1 = uow.jobs.save(p1)
            p2 = Pipeline2Job(id=None, source_job_id=saved_p1.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        with container_a.uow_factory.create() as uow:
            claimed_p1 = uow.jobs.claim_job(saved_p1.id)
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.assertEqual(claimed_p1.status, JobStatus.PROCESSING)
        self.assertEqual(claimed_p2.status, JobStatus.PROCESSING)

        # Vulnerability Demonstration: If recovery is invoked directly without the
        # cross-process lock protection, it resets in-flight jobs to PAUSED.
        vulnerable_recovery = JobRecoveryService(container_a.uow_factory)
        reconciled_count = vulnerable_recovery.reconcile_stale_jobs()
        self.assertEqual(reconciled_count, 2)

        with container_a.uow_factory.create() as uow:
            p1_post_vuln = uow.jobs.get_by_id(saved_p1.id)
            p2_post_vuln = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(p1_post_vuln.status, JobStatus.PAUSED)
            self.assertEqual(p2_post_vuln.status, JobStatus.PAUSED)

        # Re-claim jobs to PROCESSING to test the guarded path
        with container_a.uow_factory.create() as uow:
            uow.jobs.update_status(saved_p1.id, JobStatus.PENDING)
            uow.pipeline2_jobs.update_status(saved_p2.id, JobStatus.PENDING)
            uow.commit()

        with container_a.uow_factory.create() as uow:
            uow.jobs.claim_job(saved_p1.id)
            uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        # Guarded Demonstration: A second container attempting normal startup
        # is rejected by the instance lock BEFORE recovery can mutate state.
        container_b = self._create_container()
        with self.assertRaises(ApplicationInstanceLockedError):
            container_b.initialize()

        with container_a.uow_factory.create() as uow:
            p1_guarded = uow.jobs.get_by_id(saved_p1.id)
            p2_guarded = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(p1_guarded.status, JobStatus.PROCESSING)
            self.assertEqual(p2_guarded.status, JobStatus.PROCESSING)

        container_a.shutdown()

    # -------------------------------------------------------------------------
    # 13. Filesystem permission errors are propagated, not masked
    # -------------------------------------------------------------------------
    def test_permission_error_is_propagated_not_masked_as_instance_locked(self):
        """
        When opening or creating the lock file encounters a PermissionError (e.g. read-only
        filesystem), acquire() must propagate the OSError rather than returning False
        and falsely diagnosing that another instance is running.
        """
        with patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
            lock = SQLiteInstanceLock(self.db_path)
            with self.assertRaises(PermissionError) as ctx:
                lock.acquire()
            self.assertIn("Permission denied", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 14. In-memory SQLite paths bypass disk lock file safely
    # -------------------------------------------------------------------------
    def test_in_memory_sqlite_paths_bypass_lock_file_and_do_not_create_disk_files(self):
        """
        SQLite in-memory paths (:memory:, file:memdb?mode=memory, file::memory:)
        must have lock_path=None and succeed without touching disk.
        """
        paths = [
            ":memory:",
            "file:memdb_shared?mode=memory&cache=shared",
            "file::memory:?cache=shared",
            "",
        ]
        for p in paths:
            lock = SQLiteInstanceLock(p)
            self.assertIsNone(lock.lock_path, f"Expected lock_path=None for path: {p}")
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.is_locked)
            self.assertEqual(lock.get_holder_pid(), os.getpid())
            lock.release()
            self.assertFalse(lock.is_locked)
            self.assertIsNone(lock.get_holder_pid())

    # -------------------------------------------------------------------------
    # 15. Container shutdown releases lock even if subcomponent raises exception
    # -------------------------------------------------------------------------
    def test_container_shutdown_transient_failure_preserves_lock_and_allows_retry(self):
        """
        If a subcomponent (such as scheduler.shutdown) raises a transient exception
        during DesktopAppContainer.shutdown(), _is_shut_down remains False allowing recovery,
        and the lock remains held. Once the transient issue is resolved, retrying shutdown
        succeeds and releases the lock.
        """
        container = self._create_container()
        container.initialize()
        self.assertTrue(container.instance_lock.is_locked)

        def failing_scheduler_shutdown():
            raise RuntimeError("Unexpected transient scheduler shutdown explosion")

        container.scheduler.shutdown = failing_scheduler_shutdown

        with self.assertRaises(RuntimeError) as ctx:
            container.shutdown()
        self.assertIn("Unexpected transient scheduler shutdown explosion", str(ctx.exception))

        # Transient failure must allow shutdown retry, so lock remains held
        self.assertFalse(container._is_shut_down)
        self.assertTrue(container.instance_lock.is_locked)

        # Fix condition and retry shutdown: cleanly marks _is_shut_down and releases lock
        container.scheduler.shutdown = MagicMock()
        container.shutdown()
        self.assertTrue(container._is_shut_down)
        self.assertFalse(container.instance_lock.is_locked)

    # -------------------------------------------------------------------------
    # 16. Container initialize idempotency protects active in-flight jobs
    # -------------------------------------------------------------------------
    def test_container_initialize_is_idempotent_and_does_not_pause_active_jobs_on_second_call(self):
        """
        Invoking container.initialize() a second time on an already initialized container
        is a no-op and does not re-run reconcile_stale_jobs against active in-flight jobs.
        """
        container = self._create_container()
        container.initialize()

        with container.uow_factory.create() as uow:
            job = uow.jobs.save(Job(id=None, file_name="active.pdf", file_path="/a.pdf", status=JobStatus.PENDING))
            uow.commit()

        with container.uow_factory.create() as uow:
            uow.jobs.claim_job(job.id)
            uow.commit()

        with container.uow_factory.create() as uow:
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.PROCESSING)

        # Second call to initialize()
        container.initialize()

        with container.uow_factory.create() as uow:
            # Must still be PROCESSING, NOT PAUSED
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.PROCESSING)

        container.shutdown()

    # -------------------------------------------------------------------------
    # 17. Container initialize rejects already shut down container
    # -------------------------------------------------------------------------
    def test_container_initialize_rejects_already_shutdown_container(self):
        """Calling initialize() on an already shut down DesktopAppContainer raises RuntimeError."""
        container = self._create_container()
        container.initialize()
        container.shutdown()

        with self.assertRaises(RuntimeError) as ctx:
            container.initialize()
        self.assertIn("Cannot initialize a shut down DesktopAppContainer", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 18. get_holder_pid returns None on free lock and handles multiline payloads
    # -------------------------------------------------------------------------
    def test_get_holder_pid_returns_none_on_free_lock_and_handles_shorter_overwrites(self):
        """
        Verifies that get_holder_pid():
        1. Returns None when the lock is free even if a lock file exists on disk.
        2. Accurately parses current JSON line without failing on un-truncated trailing data.
        """
        lock_path = Path(self.temp_dir.name) / "test_probe.lock"

        # 1. Unlocked file containing stale content: get_holder_pid() returns None
        lock_path.write_text(json.dumps({"pid": 99999}) + "\n")
        lock = SQLiteInstanceLock(self.db_path, lock_path=lock_path)
        self.assertIsNone(lock.get_holder_pid(), "Expected None when no process holds the lock")

        # 2. When lock is acquired, holder PID is returned
        self.assertTrue(lock.acquire())
        self.assertEqual(lock.get_holder_pid(), os.getpid())

        # 3. Clean release clears metadata so lock returns None
        lock.release()
        self.assertIsNone(lock.get_holder_pid())

        # 4. Overwrite with multiline/trailing garbage while lock is held
        self.assertTrue(lock.acquire())
        # Simulate trailing text from previous run:
        with open(lock_path, "a") as f:
            f.write("garbage trailing text that would cause JSONDecodeError on full read\n")

        # Other inspector reading the locked file can parse line 1 cleanly
        inspector_lock = SQLiteInstanceLock(self.db_path, lock_path=lock_path)
        self.assertEqual(inspector_lock.get_holder_pid(), os.getpid())
        lock.release()


if __name__ == "__main__":
    unittest.main()
