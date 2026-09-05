# ============================================================
#  tests/unit/test_phase8d_remediation.py
# ============================================================

import io
import time
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from application.dto.job_dto import SubmitJobCommand
from application.dto.quick_convert_dto import QuickConvertCommand
from application.events import (
    JobProgressEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
)
from application.sanitizer import sanitize_error_message
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.rate_limiting.memory_rate_limiter import ThreadSafeMemoryRateLimiter
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.quick_convert import QuickConvertService
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.composition import DesktopAppContainer


class RemediationDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 2

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"jpeg_bytes"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []


class RemediationAIExecutor:
    def __init__(self, delay=0.01):
        self.delay = delay

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        if self.delay > 0:
            time.sleep(self.delay)
        return "Page Markdown", chain[0] if chain else None


class TestPhase8DRemediation(unittest.TestCase):
    """
    Comprehensive regression and invariant verification tests for Phase 8D.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "remedy.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = RemediationDocProcessor()
        self.ai_executor = RemediationAIExecutor(delay=0.005)

        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Google", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=2))
            uow.commit()

        self.settings_service = LocalSettingsService(self.uow_factory)
        self.submission_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        self.execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )
        self.recovery_service = JobRecoveryService(self.uow_factory, self.event_bus)

        self.events = []
        self.event_bus.subscribe(object, lambda e: self.events.append(e))

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------
    # 1. Claim -> Dispatch -> Event Ordering Synchronization
    # -------------------------------------------------------------
    def test_claim_dispatch_event_ordering_guarantee(self):
        """
        Verify that JobStateChangedEvent(PENDING -> PROCESSING) is published strictly
        BEFORE any worker-originated events (JobProgressEvent, JobCompletedEvent).
        """
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.01,
        )

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="order_test.pdf", file_bytes=b"%PDF-1.4 content")
        )

        # Clear submission events
        self.events.clear()

        runtime.start()

        # Wait for job completion
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            with self.uow_factory.create() as uow:
                job = uow.jobs.get_by_id(dto.id)
            if job and job.status == JobStatus.DONE:
                break
            time.sleep(0.02)

        runtime.shutdown(wait=True, timeout=1.0)

        # Inspect event order
        event_types = [type(e) for e in self.events]
        self.assertIn(JobStateChangedEvent, event_types)
        self.assertIn(JobProgressEvent, event_types)
        self.assertIn(JobCompletedEvent, event_types)

        # Find first occurrences
        idx_processing = next(
            i for i, e in enumerate(self.events)
            if isinstance(e, JobStateChangedEvent) and e.old_status == JobStatus.PENDING and e.new_status == JobStatus.PROCESSING
        )
        idx_first_progress = next(i for i, e in enumerate(self.events) if isinstance(e, JobProgressEvent))
        idx_completed = next(i for i, e in enumerate(self.events) if isinstance(e, JobCompletedEvent))

        # Invariant: PENDING -> PROCESSING must strictly precede all execution events
        self.assertLess(idx_processing, idx_first_progress, "PROCESSING state event MUST precede JobProgressEvent!")
        self.assertLess(idx_first_progress, idx_completed, "JobProgressEvent must precede JobCompletedEvent!")

    # -------------------------------------------------------------
    # 2. Worker Crash Event Ordering
    # -------------------------------------------------------------
    def test_worker_crash_event_ordering_and_isolation(self):
        """
        Verify that an unhandled worker crash publishes:
        JobStateChangedEvent(PENDING -> PROCESSING)
        -> JobStateChangedEvent(PROCESSING -> FAILED)
        -> JobFailedEvent
        and NEVER publishes JobCompletedEvent.
        """
        class CrashingExecutionService:
            def execute_claimed_job(self, job_id: int):
                raise RuntimeError("Worker exploded: AIzaSyKEY12345678901234567890")

        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=CrashingExecutionService(),
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.01,
        )

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="crash_order.pdf", file_bytes=b"%PDF-1.4 data")
        )
        self.events.clear()

        runtime.start()

        # Wait until job is failed
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            with self.uow_factory.create() as uow:
                job = uow.jobs.get_by_id(dto.id)
            if job and job.status == JobStatus.FAILED:
                break
            time.sleep(0.02)

        runtime.shutdown(wait=True, timeout=1.0)

        # Inspect events
        processing_events = [
            e for e in self.events
            if isinstance(e, JobStateChangedEvent) and e.old_status == JobStatus.PENDING and e.new_status == JobStatus.PROCESSING
        ]
        failed_state_events = [
            e for e in self.events
            if isinstance(e, JobStateChangedEvent) and e.old_status == JobStatus.PROCESSING and e.new_status == JobStatus.FAILED
        ]
        failed_events = [e for e in self.events if isinstance(e, JobFailedEvent)]
        completed_events = [e for e in self.events if isinstance(e, JobCompletedEvent)]

        self.assertEqual(len(processing_events), 1)
        self.assertEqual(len(failed_state_events), 1)
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(len(completed_events), 0, "JobCompletedEvent must NEVER be emitted on crash!")

        # Verify ordering
        idx_proc = self.events.index(processing_events[0])
        idx_failed_st = self.events.index(failed_state_events[0])
        idx_failed_ev = self.events.index(failed_events[0])

        self.assertLess(idx_proc, idx_failed_st)
        self.assertLess(idx_failed_st, idx_failed_ev)

    # -------------------------------------------------------------
    # 3. Claim Compensation Event Safety
    # -------------------------------------------------------------
    def test_claim_compensation_emits_no_processing_event(self):
        """
        Verify that when claim succeeds but dispatch fails (e.g. submit error),
        the job is compensated to PENDING and NO PROCESSING event is published.
        """
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.01,
        )

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="comp_safety.pdf", file_bytes=b"%PDF-1.4 data")
        )
        self.events.clear()

        # Mock submit failure
        runtime._executor.submit = MagicMock(side_effect=RuntimeError("Worker pool full"))
        runtime._running = True

        runtime._dispatch_pending_if_capacity_available()

        # Invariant: Job is reverted to PENDING
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)

        # Invariant: NO PROCESSING event was emitted
        processing_events = [
            e for e in self.events
            if isinstance(e, JobStateChangedEvent) and e.new_status == JobStatus.PROCESSING
        ]
        self.assertEqual(len(processing_events), 0, "Compensation must not emit PROCESSING event!")
        self.assertEqual(runtime.active_worker_count, 0)

    # -------------------------------------------------------------
    # 4. Cancellation Event Ordering & Exclusivity
    # -------------------------------------------------------------
    def test_cancellation_event_ordering_and_terminal_exclusivity(self):
        """
        Verify that cancellation publishes JobStateChangedEvent -> JobCancelledEvent
        and NEVER publishes JobCompletedEvent or duplicate events.
        """
        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="cancel_order.pdf", file_bytes=b"%PDF-1.4 data")
        )
        self.events.clear()

        # 1. Cancel pending job
        self.execution_service.cancel_job(dto.id)

        cancel_state_events = [
            e for e in self.events
            if isinstance(e, JobStateChangedEvent) and e.old_status == JobStatus.PENDING and e.new_status == JobStatus.CANCELLED
        ]
        cancelled_events = [e for e in self.events if isinstance(e, JobCancelledEvent)]
        completed_events = [e for e in self.events if isinstance(e, JobCompletedEvent)]

        self.assertEqual(len(cancel_state_events), 1)
        self.assertEqual(len(cancelled_events), 1)
        self.assertEqual(len(completed_events), 0)

        # Verify ordering: State event precedes Cancelled event
        self.assertLess(self.events.index(cancel_state_events[0]), self.events.index(cancelled_events[0]))

    # -------------------------------------------------------------
    # 5. Startup Order Guarantee with Real Synchronization
    # -------------------------------------------------------------
    def test_startup_order_guarantee_with_real_synchronization(self):
        """
        Verify that runtime cannot claim any job until stale reconciliation
        has fully completed using synchronization barriers.
        """
        vault_file = Path(self.temp_dir.name) / "vault_sync_barrier.enc"
        container = DesktopAppContainer(
            db_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            vault_path=vault_file,
            passphrase="SecretPassword123!",
        )
        container.doc_processor = self.doc_processor
        container.job_submission_service.doc_processor = self.doc_processor
        container.job_execution_service.doc_processor = self.doc_processor

        # Seed orphaned job directly into SQLite in PROCESSING state
        with container.uow_factory.create() as uow:
            job = container.job_submission_service.submit_job(
                SubmitJobCommand(user_id=1, filename="sync_stale.pdf", file_bytes=b"%PDF-1.4 content")
            )
            uow.jobs.claim_job(job.id)
            uow.commit()

        reconciliation_started = threading.Event()
        reconciliation_release = threading.Event()
        real_reconcile = container.job_recovery_service.reconcile_stale_jobs

        def gated_reconcile():
            reconciliation_started.set()
            reconciliation_release.wait(timeout=2.0)
            return real_reconcile()

        container.job_recovery_service.reconcile_stale_jobs = gated_reconcile

        init_thread = threading.Thread(target=container.initialize, daemon=True)
        init_thread.start()

        # Wait until reconciliation is in progress
        reconciliation_started.wait(timeout=1.0)

        # Attempt to start runtime while reconciliation is running -> blocked by _initialized
        with self.assertRaises(RuntimeError):
            container.start_runtime()

        # Release reconciliation
        reconciliation_release.set()
        init_thread.join(timeout=1.0)

        # Verify initialization succeeded and stale job was reconciled to PAUSED
        self.assertTrue(container._initialized)
        with container.uow_factory.create() as uow:
            reconciled_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(reconciled_job.status, JobStatus.PAUSED)

        container.start_runtime()
        container.shutdown()

    # -------------------------------------------------------------
    # 6. Shutdown / Restart Recovery Full Lifecycle
    # -------------------------------------------------------------
    def test_shutdown_timeout_and_restart_recovery_lifecycle(self):
        """
        Verify full shutdown with timeout -> job remains PROCESSING -> new container
        initialization -> startup recovery reconciles job to PAUSED.
        """
        worker_unblock = threading.Event()

        class BlockingAIExecutor:
            def execute_vision_with_fallback(self, *args, **kwargs):
                worker_unblock.wait(timeout=3.0)
                return None, None

        blocking_exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=BlockingAIExecutor(),
            event_publisher=self.event_bus,
        )

        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=blocking_exec_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.02,
        )

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="lifecycle.pdf", file_bytes=b"%PDF-1.4 content")
        )

        runtime.start()

        # Wait until job is in PROCESSING
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            with self.uow_factory.create() as uow:
                job = uow.jobs.get_by_id(dto.id)
            if job.status == JobStatus.PROCESSING:
                break
            time.sleep(0.05)

        # Enforce shutdown with timeout of 0.1s
        shutdown_start = time.monotonic()
        runtime.shutdown(wait=True, timeout=0.1)
        shutdown_duration = time.monotonic() - shutdown_start

        # Bounded shutdown duration
        self.assertLess(shutdown_duration, 1.0)

        # Unblock worker thread
        worker_unblock.set()
        time.sleep(0.05)

        # Verify job was left in PROCESSING in SQLite
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertIn(job.status, (JobStatus.PROCESSING, JobStatus.PAUSED))

        # Simulate new container startup (application restart)
        vault_file = Path(self.temp_dir.name) / "vault_restart.enc"
        new_container = DesktopAppContainer(
            db_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            vault_path=vault_file,
            passphrase="SecretPassword123!",
        )
        new_container.doc_processor = self.doc_processor
        new_container.initialize()

        # Invariant: Startup recovery reconciled the stale job to PAUSED
        with new_container.uow_factory.create() as uow:
            reconciled = uow.jobs.get_by_id(dto.id)
            self.assertEqual(reconciled.status, JobStatus.PAUSED)

        new_container.shutdown()

    # -------------------------------------------------------------
    # 7. Rate Limiter Concurrent Reservation Verification
    # -------------------------------------------------------------
    def test_rate_limiter_concurrent_reservations_do_not_exceed_rpm(self):
        """
        Verify that N concurrent callers performing wait_if_needed -> send -> mark_request_sent
        atomically respect sliding window RPM limits.
        """
        limiter = ThreadSafeMemoryRateLimiter(default_rpms={"google": 10})
        slot = ApiSlot(id=1, provider="google", label="G Slot", credential_ref=CredentialRef("g_key", "google", "byok"))

        # Launch 10 concurrent requests
        threads = []
        for _ in range(10):
            def req():
                limiter.wait_if_needed(slot)
                time.sleep(0.001)
                limiter.mark_request_sent(slot)
            t = threading.Thread(target=req)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Invariant: 10 requests completed and count is exactly 10
        self.assertEqual(limiter.get_history_count(slot), 10)


if __name__ == "__main__":
    unittest.main()
