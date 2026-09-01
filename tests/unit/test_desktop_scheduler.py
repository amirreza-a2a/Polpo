# ============================================================
#  tests/unit/test_desktop_scheduler.py
# ============================================================

import time
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from core.exceptions.domain_exceptions import DomainError
from application.dto.job_dto import SubmitJobCommand
from application.events import (
    JobStateChangedEvent,
    JobCompletedEvent,
    JobCancelledEvent,
    MissedScheduleDetectedEvent,
    ScheduleUpdatedEvent,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.schedule_service import ScheduleService
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.workers.scheduler import DesktopJobScheduler


class DummyDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"jpeg_data"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int):
        return markdown_text, []


class DummyAIExecutor:
    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        return "Transcribed page text", chain[0] if chain else None


class TestDesktopJobScheduler(unittest.TestCase):
    """
    Unit and integration tests for DesktopJobScheduler, ScheduleService,
    and missed-schedule reconciliation policies.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scheduler_test.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = DummyDocProcessor()
        self.ai_executor = DummyAIExecutor()

        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Google", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=2, missed_schedule_policy="prompt"))
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

        self.runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
        )

        self.schedule_service = ScheduleService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
            runtime_wake_fn=self.runtime.wake,
            execution_service=self.execution_service,
        )

        self.scheduler = DesktopJobScheduler(
            uow_factory=self.uow_factory,
            runtime=self.runtime,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            tick_interval=0.1,
        )

    def tearDown(self):
        self.scheduler.shutdown()
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_future_job_not_dispatched_and_due_job_executed(self):
        """A future job (scheduled_at > now) is ignored until its due time passes, then executes."""
        now_utc = datetime.now(timezone.utc)
        future_dt = now_utc + timedelta(seconds=0.4)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="future.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=future_dt)
        )

        completed_jobs = []
        self.event_bus.subscribe(JobCompletedEvent, lambda e: completed_jobs.append(e.job_id))

        self.runtime.start()
        self.scheduler.start()

        # At t = 0.1s, job should NOT be claimed or executing
        time.sleep(0.15)
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertEqual(len(completed_jobs), 0)

        # Wait until due time passes (t = 0.6s)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(completed_jobs) > 0:
                break
            time.sleep(0.05)

        self.assertEqual(len(completed_jobs), 1)
        self.assertEqual(completed_jobs[0], dto.id)

    def test_immediate_job_normal_fifo_dispatch(self):
        """Immediate jobs (scheduled_at=None) dispatch normally alongside scheduled jobs."""
        now_utc = datetime.now(timezone.utc)
        due_past = now_utc - timedelta(seconds=10)

        dto1 = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="immediate.pdf", file_bytes=b"%PDF-1.4 content")
        )
        dto2 = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="due.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=due_past)
        )

        completed_jobs = []
        self.event_bus.subscribe(JobCompletedEvent, lambda e: completed_jobs.append(e.job_id))

        self.runtime.start()
        self.scheduler.start()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(completed_jobs) >= 2:
                break
            time.sleep(0.05)

        self.assertEqual(set(completed_jobs), {dto1.id, dto2.id})

    def test_missed_schedule_policy_run_immediately(self):
        """Under run_immediately, missed schedule sets scheduled_at=None and executes immediately."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=2)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="missed.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=past_dt)
        )

        events = []
        self.event_bus.subscribe(object, lambda e: events.append(e))

        reconciled = self.recovery_service.reconcile_missed_schedules(
            startup_time=now_utc, policy="run_immediately"
        )
        self.assertEqual(len(reconciled), 1)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertIsNone(job.scheduled_at)

        missed_events = [e for e in events if isinstance(e, MissedScheduleDetectedEvent)]
        self.assertEqual(len(missed_events), 1)
        self.assertEqual(missed_events[0].policy, "run_immediately")
        self.assertEqual(missed_events[0].scheduled_at, past_dt)

        updated_events = [e for e in events if isinstance(e, ScheduleUpdatedEvent)]
        self.assertEqual(len(updated_events), 1)
        self.assertIsNone(updated_events[0].scheduled_at)

    def test_missed_schedule_policy_mark_paused(self):
        """Under mark_paused, missed schedule transitions to PAUSED with error message."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=3)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="missed_paused.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=past_dt)
        )

        events = []
        self.event_bus.subscribe(object, lambda e: events.append(e))

        reconciled = self.recovery_service.reconcile_missed_schedules(
            startup_time=now_utc, policy="mark_paused"
        )
        self.assertEqual(len(reconciled), 1)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PAUSED)
            self.assertIn("Missed schedule", job.error_message)
            self.assertEqual(job.scheduled_at, past_dt)

        state_events = [e for e in events if isinstance(e, JobStateChangedEvent)]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0].old_status, JobStatus.PENDING)
        self.assertEqual(state_events[0].new_status, JobStatus.PAUSED)

        missed_events = [e for e in events if isinstance(e, MissedScheduleDetectedEvent)]
        self.assertEqual(len(missed_events), 1)
        self.assertEqual(missed_events[0].policy, "mark_paused")

    def test_missed_schedule_policy_prompt_and_resolution(self):
        """Under prompt, missed schedule emits event and is resolved via ScheduleService."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=1)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="prompt_me.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=past_dt)
        )

        events = []
        self.event_bus.subscribe(object, lambda e: events.append(e))

        reconciled = self.recovery_service.reconcile_missed_schedules(
            startup_time=now_utc, policy="prompt"
        )
        self.assertEqual(len(reconciled), 1)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertEqual(job.scheduled_at, past_dt)

        missed_events = [e for e in events if isinstance(e, MissedScheduleDetectedEvent)]
        self.assertEqual(len(missed_events), 1)
        self.assertEqual(missed_events[0].policy, "prompt")

        # User chooses 'run_now'
        res = self.schedule_service.acknowledge_missed_schedule(dto.id, action="run_now")
        self.assertEqual(res.status, JobStatus.PENDING.value)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertIsNone(job.scheduled_at)

    def test_reschedule_job_use_case(self):
        """ScheduleService.reschedule_job sets a new future execution time."""
        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 content")
        )

        new_future = datetime.now(timezone.utc) + timedelta(hours=5)
        res = self.schedule_service.reschedule_job(dto.id, new_scheduled_at=new_future)
        self.assertEqual(res.id, dto.id)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.scheduled_at, new_future)

    def test_scheduler_lifecycle_pause_resume_shutdown(self):
        """Scheduler pause and resume controls due-job evaluation cleanly."""
        self.scheduler.start()
        self.assertTrue(self.scheduler.is_running)

        self.scheduler.pause()
        self.assertFalse(self.scheduler.is_running)

        self.scheduler.resume()
        self.assertTrue(self.scheduler.is_running)

    def test_missed_schedule_invalid_policy_raises_error(self):
        """Reconciliation with an unknown policy raises DomainError."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=1)

        self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="invalid_policy.pdf", file_bytes=b"%PDF-1.4 data", scheduled_at=past_dt)
        )

        with self.assertRaises(DomainError):
            self.recovery_service.reconcile_missed_schedules(startup_time=now_utc, policy="invalid_policy_name")

    def test_acknowledge_missed_schedule_cancel_delegation(self):
        """Acknowledge missed schedule with action='cancel' delegates to cancellation service."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=1)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="cancel_missed.pdf", file_bytes=b"%PDF-1.4 data", scheduled_at=past_dt)
        )

        events = []
        self.event_bus.subscribe(object, lambda e: events.append(e))

        res = self.schedule_service.acknowledge_missed_schedule(dto.id, action="cancel")
        self.assertEqual(res.status, JobStatus.CANCELLED.value)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.CANCELLED)

        cancel_events = [e for e in events if isinstance(e, JobCancelledEvent)]
        self.assertEqual(len(cancel_events), 1)
        self.assertEqual(cancel_events[0].job_id, dto.id)


if __name__ == "__main__":
    unittest.main()
