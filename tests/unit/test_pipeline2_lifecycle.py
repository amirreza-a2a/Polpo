# ============================================================
#  tests/unit/test_pipeline2_lifecycle.py
# ============================================================

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

from core.entities.job import Job, Pipeline2Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.artifact import ArtifactType, ArtifactHandle, StorageBackendType
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError
from application.events import JobStateChangedEvent, JobFailedEvent, JobCancelledEvent
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from interfaces.desktop.workers.runtime import DesktopJobRuntime


class FakeDocumentProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"fake_jpeg_bytes"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []

    def unify_markdown(self, markdown_text: str) -> str:
        return markdown_text.strip()


class FakeAIExecutor:
    def __init__(self, response_text: Optional[str] = "# Refined Markdown Content"):
        self.response_text = response_text
        self.call_count = 0
        self.last_prompt = None
        self.last_input_text = None
        self.raise_exception = None

    def execute_text_with_fallback(self, chain, prompt, input_text):
        self.call_count += 1
        self.last_prompt = prompt
        self.last_input_text = input_text
        if self.raise_exception:
            raise self.raise_exception
        if self.response_text is None:
            return None, None
        return self.response_text, chain[0] if chain else None


class TestPipeline2Lifecycle(unittest.TestCase):
    """
    Unit tests for Pipeline 2 lifecycle transitions, cooperative cancellation,
    missing input artifacts, AI failure/exhaustion handling, and startup recovery.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "p2_lifecycle_test.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.events = []
        self.event_bus.subscribe(JobStateChangedEvent, lambda evt: self.events.append(evt))
        self.event_bus.subscribe(JobFailedEvent, lambda evt: self.events.append(evt))
        self.event_bus.subscribe(JobCancelledEvent, lambda evt: self.events.append(evt))

        self.doc_processor = FakeDocumentProcessor()
        self.ai_executor = FakeAIExecutor()

        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P2", text="Refine Text", prompt_type=PromptType.PIPELINE_2, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Slot 1", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.commit()

        self.job_execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
        )
        self.job_recovery_service = JobRecoveryService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_source_and_p2_job(self, status: JobStatus = JobStatus.PENDING, create_artifact: bool = True) -> tuple[Job, Pipeline2Job]:
        with self.uow_factory.create() as uow:
            src = Job(id=None, file_name="sample.pdf", file_path="/sample.pdf", status=JobStatus.DONE)
            saved_src = uow.jobs.save(src)
            slot = uow.apis.get_by_id(1)

            input_uri = None
            if create_artifact:
                handle = self.storage.store(
                    job_id=saved_src.id,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    filename="output_1_v1.md",
                    data=b"# Initial Markdown from P1",
                    mime_type="text/markdown",
                )
                input_uri = handle.uri

            p2 = Pipeline2Job(
                id=None,
                source_job_id=saved_src.id,
                status=status,
                input_path=input_uri,
                api_chain=[slot] if slot else [],
            )
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()
            return saved_src, saved_p2

    def test_cancel_pending_pipeline2_job(self):
        """
        Cooperative cancellation of PENDING Pipeline 2 job:
        Transitions immediately to CANCELLED, publishes JobStateChangedEvent and JobCancelledEvent.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        result = self.job_execution_service.cancel_pipeline2_job(p2.id)
        self.assertTrue(result)

        with self.uow_factory.create() as uow:
            updated_p2 = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(updated_p2.status, JobStatus.CANCELLED)

        # Claiming should return None since job was cancelled
        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            self.assertIsNone(claimed)

        cancelled_events = [e for e in self.events if isinstance(e, JobCancelledEvent)]
        self.assertTrue(len(cancelled_events) >= 1)
        self.assertEqual(cancelled_events[0].job_id, src.id)

    def test_cancel_processing_pipeline2_job_requests_cancellation(self):
        """
        Cooperative cancellation of PROCESSING Pipeline 2 job:
        Flags cancel_requested=True in repository without abrupt thread termination.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, p2.id)
            self.assertEqual(claimed.status, JobStatus.PROCESSING)
            uow.commit()

        result = self.job_execution_service.cancel_pipeline2_job(p2.id)
        self.assertTrue(result)

        with self.uow_factory.create() as uow:
            updated_p2 = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(updated_p2.status, JobStatus.PROCESSING)
            self.assertTrue(updated_p2.cancel_requested)

    def test_execute_claimed_pipeline2_job_aborts_at_checkpoint_on_cancellation(self):
        """
        If cancel_requested is set before AI execution, execute_claimed_pipeline2_job
        aborts at checkpoint, marks CANCELLED, does NOT invoke AI, and does NOT reach DONE.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            # Simulate cancellation requested before worker runs execution
            uow.pipeline2_jobs.request_cancellation(claimed.id)
            uow.commit()

        finished_job = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished_job.status, JobStatus.CANCELLED)
        self.assertEqual(self.ai_executor.call_count, 0)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.CANCELLED)

    def test_late_ai_response_does_not_overwrite_cancelled_pipeline2_job(self):
        """
        If cancellation happens while AI is running, checkpoint after AI call
        detects cancellation and does not publish output or transition to DONE.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        # Hook into ai_executor to simulate cancellation arriving during AI call
        original_execute = self.ai_executor.execute_text_with_fallback

        def execute_and_cancel(*args, **kwargs):
            with self.uow_factory.create() as uow:
                uow.pipeline2_jobs.request_cancellation(p2.id)
                uow.commit()
            return original_execute(*args, **kwargs)

        self.ai_executor.execute_text_with_fallback = execute_and_cancel

        finished_job = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished_job.status, JobStatus.CANCELLED)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.CANCELLED)
            self.assertIsNone(db_job.output_path)

    def test_missing_input_artifact_transitions_to_failed_with_sanitized_error(self):
        """
        Failure during input artifact retrieval cleanly transitions job to FAILED,
        sanitizes the error details (no raw secrets or system leaks), and publishes FAILED.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING, create_artifact=False)

        # Explicitly set invalid input path that does not exist on disk
        with self.uow_factory.create() as uow:
            uow.pipeline2_jobs.update_paths(p2.id, input_path="file:///nonexistent/secret_path/input.md")
            uow.commit()

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        finished_job = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished_job.status, JobStatus.FAILED)
        self.assertIsNotNone(finished_job.error_message)
        self.assertNotIn("secret_path", finished_job.error_message)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.FAILED)

        failed_events = [e for e in self.events if isinstance(e, JobFailedEvent)]
        self.assertTrue(len(failed_events) >= 1)
        self.assertFalse(failed_events[0].is_retryable)

    def test_ai_provider_exhaustion_transitions_to_paused_with_retryable(self):
        """
        When all AI APIs are exhausted (execute_text_with_fallback returns None),
        transitions to PAUSED with is_retryable=True, sanitized error message.
        """
        self.ai_executor.response_text = None
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        finished_job = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished_job.status, JobStatus.PAUSED)
        self.assertEqual(finished_job.error_message, "All APIs exhausted for Pipeline 2.")

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.PAUSED)

        failed_events = [e for e in self.events if isinstance(e, JobFailedEvent)]
        self.assertTrue(len(failed_events) >= 1)
        self.assertTrue(failed_events[0].is_retryable)

    def test_unexpected_ai_exception_transitions_to_failed_with_sanitized_error(self):
        """
        When an unhandled exception occurs during AI execution, execute_claimed_pipeline2_job
        catches it, records sanitized error, transitions to FAILED, and avoids stuck PROCESSING.
        """
        self.ai_executor.raise_exception = RuntimeError("Connection dropped to https://api.openai.com/v1/models/gpt-4?token=super_secret_token_123")
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        finished_job = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished_job.status, JobStatus.FAILED)
        self.assertNotIn("super_secret_token_123", finished_job.error_message)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.FAILED)

    def test_startup_recovery_reconciles_interrupted_processing_pipeline2_jobs(self):
        """
        Startup crash recovery: jobs left in PROCESSING state from interrupted runs
        are safely transitioned to PAUSED with standard recovery message.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        # Claim the job so it is in PROCESSING
        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            self.assertIsNotNone(claimed)
            uow.commit()

        # Simulate restart recovery
        recovered_count = self.job_recovery_service.reconcile_stale_jobs()
        self.assertEqual(recovered_count, 1)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.PAUSED)
            self.assertEqual(db_job.error_message, "Interrupted by application crash or unexpected shutdown")

    def test_startup_recovery_respects_active_ownership_lease(self):
        """
        When an active lease / older_than threshold is specified, jobs claimed
        recently are NOT clobbered by recovery.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        # Set older_than to 5 minutes ago; our job was claimed just now
        threshold = datetime.now(timezone.utc) - timedelta(minutes=5)
        with self.uow_factory.create() as uow:
            count = uow.pipeline2_jobs.reconcile_stale_jobs(older_than=threshold)
            uow.commit()

        self.assertEqual(count, 0)

        # The recently claimed job must remain in PROCESSING
        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.PROCESSING)

    def test_cancel_paused_pipeline2_job(self):
        """Cooperative cancellation of PAUSED Pipeline 2 job transitions to CANCELLED."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PAUSED)
        res = self.job_execution_service.cancel_pipeline2_job(p2.id)
        self.assertTrue(res)

        with self.uow_factory.create() as uow:
            db_job = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_job.status, JobStatus.CANCELLED)

        state_events = [e for e in self.events if isinstance(e, JobStateChangedEvent)]
        self.assertTrue(any(e.old_status == JobStatus.PAUSED and e.new_status == JobStatus.CANCELLED for e in state_events))

    def test_cancel_pipeline2_job_idempotent_on_already_cancelled(self):
        """Repeated cancellation of an already CANCELLED job returns True without error."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.CANCELLED)
        res = self.job_execution_service.cancel_pipeline2_job(p2.id)
        self.assertTrue(res)

    def test_cancel_pipeline2_job_done_raises_domain_error(self):
        """Attempting to cancel a completed (DONE) Pipeline 2 job raises DomainError."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.DONE)
        with self.assertRaises(DomainError):
            self.job_execution_service.cancel_pipeline2_job(p2.id)

    def test_cancel_pipeline2_job_nonexistent_raises_not_found(self):
        """Attempting to cancel a nonexistent Pipeline 2 job raises EntityNotFoundError."""
        with self.assertRaises(EntityNotFoundError):
            self.job_execution_service.cancel_pipeline2_job(999999)

    def test_execute_claimed_pipeline2_job_rejects_non_processing_status(self):
        """Calling execute_claimed_pipeline2_job on a job in DONE status raises DomainError."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.DONE)
        with self.assertRaises(DomainError):
            self.job_execution_service.execute_claimed_pipeline2_job(p2.id)

    def test_execute_claimed_pipeline2_job_on_already_cancelled_returns_without_events(self):
        """Calling execute_claimed_pipeline2_job on an already CANCELLED job returns immediately with zero new events."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.CANCELLED)
        self.events.clear()
        res = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(res.status, JobStatus.CANCELLED)
        self.assertEqual(self.events, [])

    def test_execute_claimed_pipeline2_job_checkpoint4_cancellation(self):
        """
        Simulates cancellation arriving at Checkpoint 4 (inside the final commit transaction).
        Verifies the job transitions to CANCELLED, output_path is not saved in DB, and DONE is avoided.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        # Hook storage.store to request cancellation right before Checkpoint 4
        orig_store = self.storage.store

        def store_and_cancel(*args, **kwargs):
            ret = orig_store(*args, **kwargs)
            with self.uow_factory.create() as uow:
                uow.pipeline2_jobs.request_cancellation(p2.id)
                uow.commit()
            return ret

        self.storage.store = store_and_cancel

        finished = self.job_execution_service.execute_claimed_pipeline2_job(p2.id)
        self.assertEqual(finished.status, JobStatus.CANCELLED)

        with self.uow_factory.create() as uow:
            db_p2 = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_p2.status, JobStatus.CANCELLED)
            self.assertIsNone(db_p2.output_path)

    def test_startup_recovery_handles_naive_older_than_without_type_error(self):
        """
        Passing an offset-naive datetime to reconcile_stale_jobs or reconcile_stale_pipeline2_jobs
        does not crash with TypeError: can't compare offset-naive and offset-aware datetimes.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        naive_threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        # Should execute cleanly without TypeError
        count = self.job_recovery_service.reconcile_stale_jobs(older_than=naive_threshold)
        self.assertEqual(count, 0)

        # Older threshold in the future to trigger recovery
        future_naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
        count_future = self.job_recovery_service.reconcile_stale_jobs(older_than=future_naive)
        self.assertEqual(count_future, 1)

    def test_startup_recovery_deduplicates_events_for_shared_source_id(self):
        """
        When both a standard job and its Pipeline 2 job are recovered, exactly one
        JobStateChangedEvent is published per unique source job ID.
        """
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            uow.jobs.update_status(src.id, JobStatus.PROCESSING)
            uow.commit()

        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.events.clear()
        recovered = self.job_recovery_service.reconcile_stale_jobs()
        self.assertEqual(recovered, 2)

        state_events = [e for e in self.events if isinstance(e, JobStateChangedEvent) and e.job_id == src.id]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0].old_status, JobStatus.PROCESSING)
        self.assertEqual(state_events[0].new_status, JobStatus.PAUSED)

    def test_reconcile_stale_pipeline2_jobs_method(self):
        """Tests public reconcile_stale_pipeline2_jobs method."""
        src, p2 = self._create_source_and_p2_job(status=JobStatus.PENDING)
        with self.uow_factory.create() as uow:
            claimed = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        recovered = self.job_recovery_service.reconcile_stale_pipeline2_jobs()
        self.assertEqual(recovered, 1)
        with self.uow_factory.create() as uow:
            db_p2 = uow.pipeline2_jobs.get_by_id(p2.id)
            self.assertEqual(db_p2.status, JobStatus.PAUSED)

    def test_sqlite_pipeline2_repository_list_and_request_cancellation(self):
        """Tests SQLitePipeline2JobRepository list filtering, pagination, and request_cancellation return."""
        with self.uow_factory.create() as uow:
            for i in range(5):
                src = uow.jobs.save(Job(id=None, file_name=f"f{i}.pdf", file_path=f"/f{i}.pdf", status=JobStatus.DONE))
                st = JobStatus.PENDING if i < 3 else JobStatus.DONE
                uow.pipeline2_jobs.save(Pipeline2Job(id=None, source_job_id=src.id, status=st, input_path=f"/in{i}.md"))
            uow.commit()

        with self.uow_factory.create() as uow:
            all_p2 = uow.pipeline2_jobs.list(limit=10, offset=0)
            self.assertGreaterEqual(len(all_p2), 5)

            pending_p2 = uow.pipeline2_jobs.list(status=JobStatus.PENDING)
            self.assertGreaterEqual(len(pending_p2), 3)

            # Test string status
            pending_str = uow.pipeline2_jobs.list(status="pending")
            self.assertEqual(len(pending_p2), len(pending_str))

            p2_to_cancel = pending_p2[0]
            ok = uow.pipeline2_jobs.request_cancellation(p2_to_cancel.id)
            self.assertTrue(ok)

            bad = uow.pipeline2_jobs.request_cancellation(999999)
            self.assertFalse(bad)
            uow.commit()

    def test_runtime_cancel_active_jobs_cancels_both_standard_and_pipeline2(self):
        """Verifies DesktopJobRuntime.cancel_active_jobs invokes cancel for both job types."""
        from unittest.mock import MagicMock
        from concurrent.futures import Future

        mock_execution_service = MagicMock()
        mock_uow_factory = MagicMock()

        runtime = DesktopJobRuntime(
            job_execution_service=mock_execution_service,
            uow_factory=mock_uow_factory,
            max_workers=2,
        )

        f1 = Future()
        f2 = Future()
        runtime._active_jobs[101] = f1
        runtime._active_p2_jobs[202] = f2

        # Make job 101 raise an error to verify error resilience
        mock_execution_service.cancel_job.side_effect = RuntimeError("Simulated failure")

        runtime.cancel_active_jobs()

        mock_execution_service.cancel_job.assert_called_once_with(101)
        mock_execution_service.cancel_pipeline2_job.assert_called_once_with(202)

        runtime.shutdown(wait=False)

    def test_mysql_pipeline2_repository_claim_rollback_on_deserialization_error(self):
        """
        Verifies MySQLPipeline2JobRepository.claim_next_pending rolls back the connection
        when entity deserialization fails, avoiding dangling open transactions.
        """
        from unittest.mock import MagicMock
        from infrastructure.persistence.repositories import MySQLPipeline2JobRepository

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # First fetchone: job found
            {"id": 42, "api_chain": "invalid_json_corrupted"},  # Second fetchone: row for entity
        ]
        mock_cursor.rowcount = 1

        repo = MySQLPipeline2JobRepository(mock_conn)

        with self.assertRaises(Exception):
            repo.claim_next_pending()

        mock_conn.rollback.assert_called()


if __name__ == "__main__":
    unittest.main()
