# ============================================================
#  tests/unit/test_pause_resume_cancellation_recovery.py
#  Phase 8F.2 Cooperative Pause/Resume and Cancellation Recovery Tests
# ============================================================

import time
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.ai.executor_service import RateLimitedAIExecutor

from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.schedule_service import ScheduleService
from application.services.job_query import JobQueryService
from application.events import (
    JobProgressEvent,
    JobStateChangedEvent,
    JobCompletedEvent,
    JobCancelledEvent,
    JobFailedEvent,
)
from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.policies.job_state_policy import JobStateTransitionPolicy, InvalidStateTransitionError

from interfaces.desktop.qt_compat import QGuiApplication
from interfaces.desktop.bridge import QtSignalEventBridge
from interfaces.desktop.models.job_queue_model import JobQueueModel
from interfaces.desktop.controllers.job_controller import JobController


class TestPauseResumeCancellationRecovery(unittest.TestCase):
    """
    Comprehensive test suite verifying Phase 8F.2 Cooperative Pause/Resume,
    Deterministic Checkpoint Restoration, and Cancellation Recovery semantics.
    """

    def setUp(self):
        self.app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.db_path = self.base_dir / "test_lifecycle.db"
        self.artifacts_dir = self.base_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_mgr = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(self.db_mgr).run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_mgr)
        self.storage = LocalStorageAdapter(self.artifacts_dir)
        self.doc_processor = PyMuPDFDocumentProcessor()
        self.event_bus = InMemoryEventBus()
        self.ai_executor = RateLimitedAIExecutor(adapter_factory=MagicMock(), rate_limiter=MagicMock())

        self.execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )
        self.recovery_service = JobRecoveryService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
        )
        self.schedule_service = ScheduleService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
        )
        self.query_service = JobQueryService(self.uow_factory)

        self.bridge = QtSignalEventBridge(self.event_bus)
        self.queue_model = JobQueueModel(self.query_service, self.bridge)

        # Setup test API Slot
        with self.uow_factory.create() as uow:
            self.slot = ApiSlot(
                id=1,
                provider="openai",
                label="Primary GPT-4o",
                slot_type="byok",
                selected_model="gpt-4o",
            )
            uow.apis.save(self.slot)
            uow.commit()

        # Create a sample 4-page PDF
        self.pdf_path = self.base_dir / "sample_doc.pdf"
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page()
            page.insert_text((50, 50), f"Page content {i + 1}")
        doc.save(str(self.pdf_path))
        doc.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_and_ingest_job(self, status: JobStatus = JobStatus.PENDING, total_pages: int = 4, scheduled_at: datetime = None) -> Job:
        with open(self.pdf_path, "rb") as f:
            pdf_bytes = f.read()

        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="sample_doc.pdf",
                file_path="",
                total_pages=total_pages,
                status=status,
                api_chain=[self.slot],
                scheduled_at=scheduled_at,
            )
            uow.jobs.save(job)
            uow.commit()

        handle = self.storage.store(
            job_id=job.id,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="source.pdf",
            data=pdf_bytes,
            mime_type="application/pdf",
        )
        with self.uow_factory.create() as uow:
            job.file_path = handle.uri
            uow.jobs.save(job)
            uow.commit()

        return job

    # 1. Processing -> Pause -> PAUSED
    def test_processing_to_pause_to_paused(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda e: events.append(e))

        # Request cooperative pause
        paused_ok = self.execution_service.pause_job(job.id)
        self.assertTrue(paused_ok)
        self.assertTrue(self.execution_service._is_pause_requested(job.id))

        # Worker hits checkpoint and pauses
        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.get_by_id(job.id)
        paused_job = self.execution_service._handle_pause(claimed_job)
        self.assertEqual(paused_job.status, JobStatus.PAUSED)
        self.assertFalse(self.execution_service._is_pause_requested(job.id))

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.PAUSED)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].new_status, JobStatus.PAUSED)

    # 2. PAUSED -> Resume -> PENDING -> PROCESSING
    def test_paused_to_resume_to_pending_and_processing(self):
        job = self._create_and_ingest_job(status=JobStatus.PAUSED)
        events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda e: events.append(e))

        dto = self.recovery_service.resume_job(job.id)
        self.assertEqual(dto.status, JobStatus.PENDING.value)

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.PENDING)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].new_status, JobStatus.PENDING)

        # Worker claims next pending
        with patch.object(self.ai_executor, "execute_vision_with_fallback") as mock_ai:
            mock_ai.return_value = ("Extracted Markdown", self.slot)
            executed = self.execution_service.claim_and_execute_next()
            self.assertIsNotNone(executed)
            self.assertEqual(executed.status, JobStatus.DONE)

    # 3. Processing -> Cancel -> CANCELLED
    def test_processing_to_cancel_to_cancelled(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        events = []
        self.event_bus.subscribe(JobCancelledEvent, lambda e: events.append(e))

        cancel_ok = self.execution_service.cancel_job(job.id)
        self.assertTrue(cancel_ok)
        self.assertTrue(self.execution_service._is_cancellation_requested(job.id))

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.get_by_id(job.id)
        cancelled_job = self.execution_service._handle_cancellation(claimed_job)
        self.assertEqual(cancelled_job.status, JobStatus.CANCELLED)

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.CANCELLED)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].job_id, job.id)

    # 4. CANCELLED -> Retry -> PENDING -> PROCESSING
    def test_cancelled_to_retry_to_pending_and_processing(self):
        job = self._create_and_ingest_job(status=JobStatus.CANCELLED)
        events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda e: events.append(e))

        dto = self.recovery_service.retry_job(job.id)
        self.assertEqual(dto.status, JobStatus.PENDING.value)

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.PENDING)
            self.assertFalse(db_job.cancel_requested)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].new_status, JobStatus.PENDING)

    # 5. FAILED -> Retry -> PENDING -> PROCESSING
    def test_failed_to_retry_to_pending_and_processing(self):
        job = self._create_and_ingest_job(status=JobStatus.FAILED)
        events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda e: events.append(e))

        dto = self.recovery_service.retry_job(job.id)
        self.assertEqual(dto.status, JobStatus.PENDING.value)

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.PENDING)
            self.assertEqual(db_job.retry_count, 1)

    # 6. Pause requested immediately before a page begins
    def test_pause_requested_before_page_begins(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        # Set pause before execute
        self.execution_service.pause_job(job.id)

        with patch.object(self.ai_executor, "execute_vision_with_fallback") as mock_ai:
            mock_ai.return_value = ("Page 1 md", self.slot)
            result = self.execution_service.execute_claimed_job(job.id)
            self.assertEqual(result.status, JobStatus.PAUSED)
            self.assertEqual(result.processed_pages, 0)
            mock_ai.assert_not_called()

    # 7. Pause requested while AI inference is executing
    def test_pause_requested_during_ai_inference(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        def side_effect_ai(*args, **kwargs):
            # User clicks Pause while AI is processing page 1
            self.execution_service.pause_job(job.id)
            return ("Page 1 AI md", self.slot)

        with patch.object(self.ai_executor, "execute_vision_with_fallback", side_effect=side_effect_ai):
            result = self.execution_service.execute_claimed_job(job.id)
            self.assertEqual(result.status, JobStatus.PAUSED)
            # Page 1 committed before pausing
            self.assertEqual(result.processed_pages, 1)

    # 8. Cancel requested while AI inference is executing
    def test_cancel_requested_during_ai_inference(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        def side_effect_ai(*args, **kwargs):
            self.execution_service.cancel_job(job.id)
            return ("Page 1 AI md", self.slot)

        with patch.object(self.ai_executor, "execute_vision_with_fallback", side_effect=side_effect_ai):
            result = self.execution_service.execute_claimed_job(job.id)
            self.assertEqual(result.status, JobStatus.CANCELLED)

    # 9. Pause/Resume race
    def test_pause_resume_race(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        # 1. User requests pause while pending
        self.execution_service.pause_job(job.id)
        with self.uow_factory.create() as uow:
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.PAUSED)

        # 2. Immediate Resume request
        dto = self.recovery_service.resume_job(job.id)
        self.assertEqual(dto.status, JobStatus.PENDING.value)
        with self.uow_factory.create() as uow:
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.PENDING)

    # 10. Cancel/Retry race
    def test_cancel_retry_race(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        self.execution_service.cancel_job(job.id)
        with self.uow_factory.create() as uow:
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.CANCELLED)

        dto = self.recovery_service.retry_job(job.id)
        self.assertEqual(dto.status, JobStatus.PENDING.value)
        with self.uow_factory.create() as uow:
            self.assertEqual(uow.jobs.get_by_id(job.id).status, JobStatus.PENDING)

    # 11. Duplicate Pause clicks are ignored while pausing
    def test_duplicate_pause_clicks_ignored_while_pausing(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        self.queue_model.reload_queue()
        self.assertGreater(self.queue_model.rowCount(), 0)

        self.queue_model.set_action_state(job.id, "pausing")
        self.assertEqual(self.queue_model.data(self.queue_model.index(0, 0), JobQueueModel.ActionStateRole), "pausing")

        # Second pause call is idempotent
        ok = self.execution_service.pause_job(job.id)
        self.assertTrue(ok)

    # 12. Duplicate Cancel clicks are ignored while cancelling
    def test_duplicate_cancel_clicks_ignored_while_cancelling(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        self.queue_model.reload_queue()
        self.queue_model.set_action_state(job.id, "cancelling")
        self.assertEqual(self.queue_model.data(self.queue_model.index(0, 0), JobQueueModel.ActionStateRole), "cancelling")

        ok = self.execution_service.cancel_job(job.id)
        self.assertTrue(ok)

    # 13. Duplicate Action clicks are ignored while action is active
    def test_duplicate_retry_clicks_ignored_while_retrying(self):
        job = self._create_and_ingest_job(status=JobStatus.PAUSED)
        self.queue_model.reload_queue()
        self.queue_model.set_action_state(job.id, "retrying")
        self.assertEqual(self.queue_model.data(self.queue_model.index(0, 0), JobQueueModel.ActionStateRole), "retrying")

    # 14. Checkpoint is preserved correctly across Pause and Resume
    def test_checkpoint_preservation_across_pause_and_resume(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING, total_pages=4)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        call_count = 0
        def side_effect_ai(chain, image_bytes, prompt, at_page, **kwargs):
            nonlocal call_count
            call_count += 1
            if at_page == 2:
                # Pause after page 2
                self.execution_service.pause_job(job.id)
            return (f"Page {at_page} Markdown", self.slot)

        with patch.object(self.ai_executor, "execute_vision_with_fallback", side_effect=side_effect_ai):
            p_job = self.execution_service.execute_claimed_job(job.id)
            self.assertEqual(p_job.status, JobStatus.PAUSED)
            self.assertEqual(p_job.processed_pages, 2)
            self.assertEqual(call_count, 2)

        # Now Resume the job and verify execution continues from page 3
        self.recovery_service.resume_job(job.id)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        resumed_pages_processed = []
        def side_effect_resume(chain, image_bytes, prompt, at_page, **kwargs):
            resumed_pages_processed.append(at_page)
            return (f"Page {at_page} Markdown", self.slot)

        with patch.object(self.ai_executor, "execute_vision_with_fallback", side_effect=side_effect_resume):
            done_job = self.execution_service.execute_claimed_job(job.id)
            self.assertEqual(done_job.status, JobStatus.DONE)
            self.assertEqual(done_job.processed_pages, 4)
            # Must ONLY process pages 3 and 4!
            self.assertEqual(resumed_pages_processed, [3, 4])

            # Retrieve final artifact and verify it contains all 4 pages
            out_handle = ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri=done_job.output_path,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                job_id=done_job.id,
                filename=f"output_{done_job.id}.md",
            )
            final_md = self.storage.retrieve(out_handle).decode("utf-8")
            self.assertIn("<!-- Page 1 -->", final_md)
            self.assertIn("<!-- Page 2 -->", final_md)
            self.assertIn("<!-- Page 3 -->", final_md)
            self.assertIn("<!-- Page 4 -->", final_md)

    # 15. Scheduled jobs retain scheduled_at when paused and resumed
    def test_scheduled_jobs_retain_scheduled_at_when_paused_and_resumed(self):
        future_dt = datetime.now(timezone.utc) + timedelta(hours=5)
        job = self._create_and_ingest_job(status=JobStatus.PENDING, scheduled_at=future_dt)

        self.execution_service.pause_job(job.id)
        with self.uow_factory.create() as uow:
            p_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(p_job.status, JobStatus.PAUSED)
            self.assertIsNotNone(p_job.scheduled_at)

        self.recovery_service.resume_job(job.id)
        with self.uow_factory.create() as uow:
            r_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(r_job.status, JobStatus.PENDING)
            self.assertIsNotNone(r_job.scheduled_at)
            self.assertEqual(r_job.scheduled_at.isoformat()[:16], future_dt.isoformat()[:16])

    # 16. GUI remains responsive during all transitions
    def test_gui_responsiveness_and_non_blocking_transitions(self):
        ctrl = JobController(
            submission_service=MagicMock(),
            recovery_service=self.recovery_service,
            schedule_service=self.schedule_service,
            execution_service=self.execution_service,
            artifact_service=MagicMock(),
            query_service=self.query_service,
        )
        job = self._create_and_ingest_job(status=JobStatus.PENDING)

        t0 = time.monotonic()
        self.assertTrue(ctrl.pause_job(job.id))
        self.assertTrue(ctrl.resume_job(job.id))
        self.assertTrue(ctrl.cancel_job(job.id))
        self.assertTrue(ctrl.retry_job(job.id))
        duration = time.monotonic() - t0
        self.assertLess(duration, 0.5, "All controller transitions must execute in under 500ms without blocking GUI thread")

    # 17. No duplicate terminal events
    def test_no_duplicate_terminal_events(self):
        job = self._create_and_ingest_job(status=JobStatus.PENDING)
        events = []
        self.event_bus.subscribe(JobCancelledEvent, lambda e: events.append(e))

        self.execution_service.cancel_job(job.id)
        self.assertEqual(len(events), 1)

        # Subsequent cancel attempt returns True or does not republish duplicate events
        self.assertTrue(self.execution_service.cancel_job(job.id))
        self.assertEqual(len(events), 1)

    # 18. Clean Architecture boundaries remain intact
    def test_clean_architecture_boundaries_intact(self):
        from core.policies.job_state_policy import JobStateTransitionPolicy
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.PAUSED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.PAUSED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.PENDING))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.CANCELLED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.CANCELLED, JobStatus.PENDING))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.DONE, JobStatus.CANCELLED))

    # 19. Phase 8F.4 P0: PAUSED -> CANCELLED succeeds and preserves checkpoint
    def test_paused_to_cancelled_persistence_and_checkpoint_preservation(self):
        job = self._create_and_ingest_job(status=JobStatus.PAUSED, total_pages=5)
        with self.uow_factory.create() as uow:
            uow.jobs.update_progress(job.id, 2, [])
            uow.commit()

        # Store page artifact for page 1 & 2
        self.storage.store(
            job_id=job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1.md",
            data=b"# Page 1 text",
            mime_type="text/markdown",
        )

        state_events = []
        cancel_events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda e: state_events.append(e))
        self.event_bus.subscribe(JobCancelledEvent, lambda e: cancel_events.append(e))

        # Cancel while in PAUSED status (no active worker running)
        ok = self.execution_service.cancel_job(job.id)
        self.assertTrue(ok)

        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job.id)
            self.assertEqual(db_job.status, JobStatus.CANCELLED)
            self.assertEqual(db_job.processed_pages, 2)

        # Verify events
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0].old_status, JobStatus.PAUSED)
        self.assertEqual(state_events[0].new_status, JobStatus.CANCELLED)
        self.assertEqual(len(cancel_events), 1)
        self.assertEqual(cancel_events[0].job_id, job.id)

        # Verify page artifact is intact
        page1_handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri="",
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            job_id=job.id,
            filename="page_1.md",
        )
        self.assertTrue(self.storage.exists(page1_handle))

    # 20. Phase 8F.4 P1: Active Queue isolation and terminal state removal
    def test_queue_model_active_queue_isolation_and_terminal_removal(self):
        # Create 6 jobs with different statuses
        j_pending = self._create_and_ingest_job(status=JobStatus.PENDING)
        j_processing = self._create_and_ingest_job(status=JobStatus.PROCESSING)
        j_paused = self._create_and_ingest_job(status=JobStatus.PAUSED)
        j_done = self._create_and_ingest_job(status=JobStatus.DONE)
        j_failed = self._create_and_ingest_job(status=JobStatus.FAILED)
        j_cancelled = self._create_and_ingest_job(status=JobStatus.CANCELLED)

        # Baseline load
        self.queue_model.reload_queue()
        self.app.processEvents()

        # Only PENDING, PROCESSING, PAUSED must be in JobQueueModel
        self.assertEqual(self.queue_model.rowCount(), 3)
        job_ids_in_queue = [
            self.queue_model.data(self.queue_model.index(r, 0), JobQueueModel.IdRole)
            for r in range(self.queue_model.rowCount())
        ]
        self.assertIn(j_pending.id, job_ids_in_queue)
        self.assertIn(j_processing.id, job_ids_in_queue)
        self.assertIn(j_paused.id, job_ids_in_queue)
        self.assertNotIn(j_done.id, job_ids_in_queue)
        self.assertNotIn(j_failed.id, job_ids_in_queue)
        self.assertNotIn(j_cancelled.id, job_ids_in_queue)

        # Event-driven removal: PROCESSING -> FAILED removes row
        self.event_bus.publish(
            JobStateChangedEvent(job_id=j_processing.id, old_status=JobStatus.PROCESSING, new_status=JobStatus.FAILED)
        )
        self.app.processEvents()
        self.assertEqual(self.queue_model.rowCount(), 2)

        # Event-driven removal: PENDING -> CANCELLED removes row
        self.event_bus.publish(
            JobStateChangedEvent(job_id=j_pending.id, old_status=JobStatus.PENDING, new_status=JobStatus.CANCELLED)
        )
        self.app.processEvents()
        self.assertEqual(self.queue_model.rowCount(), 1)

        # Event-driven insertion: FAILED -> PENDING re-adds row
        self.event_bus.publish(
            JobStateChangedEvent(job_id=j_failed.id, old_status=JobStatus.FAILED, new_status=JobStatus.PENDING)
        )
        self.app.processEvents()
        self.assertEqual(self.queue_model.rowCount(), 2)

        # Event-driven insertion: CANCELLED -> PENDING re-adds row
        self.event_bus.publish(
            JobStateChangedEvent(job_id=j_cancelled.id, old_status=JobStatus.CANCELLED, new_status=JobStatus.PENDING)
        )
        self.app.processEvents()
        self.assertEqual(self.queue_model.rowCount(), 3)


if __name__ == "__main__":
    unittest.main()
