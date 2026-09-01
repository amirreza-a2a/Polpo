# ============================================================
#  tests/unit/test_desktop_services_reconciliation.py
# ============================================================

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.ai.types import AIResponse
from application.dto.job_dto import SubmitJobCommand
from application.dto.quick_convert_dto import QuickConvertCommand
from application.events import (
    JobProgressEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.quick_convert import QuickConvertService


class DummyDocumentProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 2

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"fake_jpeg_page_" + str(page_num).encode()

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int):
        return markdown_text, []


class DummyAIExecutor:
    def __init__(self, responses=None, fail_vision=False):
        self.responses = responses or ["Page 1 content", "Page 2 content"]
        self.fail_vision = fail_vision
        self.call_count = 0

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        if self.fail_vision:
            return None, None
        resp = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return resp, chain[0] if chain else None

    def execute_text_with_fallback(self, chain, prompt, input_text=None, at_page=0, on_switch=None):
        return "Refined text", chain[0] if chain else None


class TestDesktopServicesReconciliation(unittest.TestCase):
    """
    Integration and unit tests verifying reconciled desktop application services
    using SQLite UoW, InMemoryEventBus, and immutable local storage.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = DummyDocumentProcessor()
        self.ai_executor = DummyAIExecutor()

        # Seed default prompt and API slot
        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Transcribe to markdown", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.prompts.save(Prompt(id=None, name="P2", text="Refine markdown", prompt_type=PromptType.PIPELINE_2, is_default=True))
            uow.prompts.save(Prompt(id=None, name="QC", text="Quick convert", prompt_type=PromptType.QUICK_CONVERT, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Primary Slot", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.commit()

        self.events_received = []
        self.event_bus.subscribe(object, lambda e: self.events_received.append(e))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_submission_creates_valid_source_artifact(self):
        service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        cmd = SubmitJobCommand(
            user_id=1,
            filename="document.pdf",
            file_bytes=b"%PDF-1.4 dummy binary pdf content",
        )
        dto = service.submit_job(cmd)

        self.assertIsNotNone(dto.id)
        self.assertEqual(dto.total_pages, 2)
        self.assertEqual(dto.status, "pending")

        # Verify job has valid file_path pointing to on-disk artifact
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertIsNotNone(job)
            self.assertTrue(job.file_path.startswith("file://"))

        # Verify JobStateChangedEvent emitted
        state_events = [e for e in self.events_received if isinstance(e, JobStateChangedEvent)]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0].job_id, dto.id)
        self.assertEqual(state_events[0].new_status, JobStatus.PENDING)

    def test_job_execution_full_pipeline_lifecycle(self):
        sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        job_dto = sub_service.submit_job(SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 data"))

        # Claim job atomically
        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(job_dto.id)
            uow.commit()
        self.assertIsNotNone(claimed_job)
        self.assertEqual(claimed_job.status, JobStatus.PROCESSING)

        exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )

        completed_job = exec_service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)
        self.assertEqual(completed_job.processed_pages, 2)
        self.assertTrue(completed_job.output_path.startswith("file://"))

        # Verify events emitted
        progress_events = [e for e in self.events_received if isinstance(e, JobProgressEvent)]
        self.assertEqual(len(progress_events), 2)  # Page 1 and Page 2

        completed_events = [e for e in self.events_received if isinstance(e, JobCompletedEvent)]
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0].job_id, completed_job.id)

    def test_cancel_pending_job_transitions_immediately(self):
        sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        job_dto = sub_service.submit_job(SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 data"))

        exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )

        ok = exec_service.cancel_job(job_dto.id)
        self.assertTrue(ok)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_dto.id)
            self.assertEqual(job.status, JobStatus.CANCELLED)

        cancelled_events = [e for e in self.events_received if isinstance(e, JobCancelledEvent)]
        self.assertEqual(len(cancelled_events), 1)
        self.assertEqual(cancelled_events[0].job_id, job_dto.id)

    def test_cancel_processing_job_cooperatively_halts_worker(self):
        sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        job_dto = sub_service.submit_job(SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 data"))

        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_job(job_dto.id)
            uow.commit()

        exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )

        # Flag cancellation on processing job
        ok = exec_service.cancel_job(claimed.id)
        self.assertTrue(ok)

        # Execute claimed job - worker must observe cancel_requested and exit as CANCELLED
        result_job = exec_service.execute_claimed_job(claimed.id)
        self.assertEqual(result_job.status, JobStatus.CANCELLED)

        # Ensure NO JobCompletedEvent is emitted
        completed_events = [e for e in self.events_received if isinstance(e, JobCompletedEvent)]
        self.assertEqual(len(completed_events), 0)

        cancelled_events = [e for e in self.events_received if isinstance(e, JobCancelledEvent)]
        self.assertEqual(len(cancelled_events), 1)

    def test_ai_exhaustion_pauses_job(self):
        sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        job_dto = sub_service.submit_job(SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 data"))

        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_job(job_dto.id)
            uow.commit()

        failing_ai = DummyAIExecutor(fail_vision=True)
        exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=failing_ai,
            event_publisher=self.event_bus,
        )

        result_job = exec_service.execute_claimed_job(claimed.id)
        self.assertEqual(result_job.status, JobStatus.PAUSED)

        failed_events = [e for e in self.events_received if isinstance(e, JobFailedEvent)]
        self.assertEqual(len(failed_events), 1)
        self.assertTrue(failed_events[0].is_retryable)

    def test_job_recovery_stale_reconciliation_and_resume(self):
        sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        job_dto = sub_service.submit_job(SubmitJobCommand(user_id=1, filename="doc.pdf", file_bytes=b"%PDF-1.4 data"))

        with self.uow_factory.create() as uow:
            uow.jobs.claim_job(job_dto.id)  # Left in PROCESSING state
            uow.commit()

        recovery_service = JobRecoveryService(self.uow_factory, self.event_bus)

        # 1. Startup stale recovery
        recovered_count = recovery_service.reconcile_stale_jobs()
        self.assertEqual(recovered_count, 1)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_dto.id)
            self.assertEqual(job.status, JobStatus.PAUSED)

        # 2. Resume job
        resumed_dto = recovery_service.resume_job(job_dto.id)
        self.assertEqual(resumed_dto.status, "pending")

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)

    def test_quick_convert_service(self):
        qc_service = QuickConvertService(self.uow_factory, self.ai_executor)
        res = qc_service.convert_image(QuickConvertCommand(image_bytes=b"fake_image", user_id=1))
        self.assertIsNotNone(res.markdown_content)
        self.assertEqual(res.pages_consumed, 1)


if __name__ == "__main__":
    unittest.main()
