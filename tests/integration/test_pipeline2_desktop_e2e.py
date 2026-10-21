# ============================================================
#  tests/integration/test_pipeline2_desktop_e2e.py
#  Integration tests for Pipeline 2 execution in Desktop Runtime
# ============================================================

import gc
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.entities.job import Job, Pipeline2Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from core.entities.artifact import ArtifactType, ArtifactHandle, StorageBackendType
from application.dto.job_dto import SubmitJobCommand
from application.events import JobCompletedEvent, JobStateChangedEvent
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime


class FakeDocumentProcessor:
    def __init__(self, delay: float = 0.01):
        self.delay = delay

    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        if self.delay > 0:
            time.sleep(self.delay)
        return b"fake_jpeg_bytes"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []

    def unify_markdown(self, markdown_text: str) -> str:
        return markdown_text.strip()


class FakeAIExecutor:
    def __init__(self, delay: float = 0.01):
        self.delay = delay

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        if self.delay > 0:
            time.sleep(self.delay)
        return f"# Page {at_page} Content\nRaw markdown transcription.", chain[0] if chain else None

    def execute_text_with_fallback(self, chain, prompt, input_text):
        if self.delay > 0:
            time.sleep(self.delay)
        return f"# Refined Content\n{input_text}\n[Refined via AI]", chain[0] if chain else None


class TestPipeline2DesktopExecutionGap(unittest.TestCase):
    """
    Demonstrates and verifies the Pipeline 2 desktop dispatch gap (P2-01).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_p2.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = FakeDocumentProcessor(delay=0.01)
        self.ai_executor = FakeAIExecutor(delay=0.01)

        # Seed initial prompt (P1 and P2), slot, and settings
        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert PDF", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.prompts.save(Prompt(id=None, name="P2", text="Refine Markdown", prompt_type=PromptType.PIPELINE_2, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Slot 1", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=2, auto_pipeline2=True))
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

        self.events_received = []
        self.event_bus.subscribe(object, lambda e: self.events_received.append(e))

    def tearDown(self):
        gc.collect()
        if os.name == "nt":
            for _ in range(5):
                try:
                    self.temp_dir.cleanup()
                    return
                except PermissionError:
                    time.sleep(0.1)
                    gc.collect()
            try:
                self.temp_dir.cleanup()
            except PermissionError:
                pass
        else:
            self.temp_dir.cleanup()

    def test_pipeline2_job_is_dispatched_and_completed_by_desktop_runtime(self):
        """
        Regression test for P2-01:
        Submitting a job with auto_pipeline2=True must result in both Pipeline 1
        AND Pipeline 2 executing and completing to DONE via DesktopJobRuntime.

        Before the P2-01 fix, the DesktopJobRuntime only claims from uow.jobs,
        so the Pipeline2Job record created after Pipeline 1 remains in PENDING status forever.
        """
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
            max_workers=2,
        )

        # 1. Submit job with auto_pipeline2 enabled
        dto = self.submission_service.submit_job(
            SubmitJobCommand(
                user_id=1,
                filename="document_with_p2.pdf",
                file_bytes=b"%PDF-1.4 mock binary",
                auto_pipeline2=True,
            )
        )
        source_job_id = dto.id

        # 2. Start canonical desktop runtime
        runtime.start()

        # 3. Wait for Pipeline 1 and Pipeline 2 to complete
        start_time = time.monotonic()
        p1_done = False
        p2_done = False
        p2_job_record: Optional[Pipeline2Job] = None

        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                src_job = uow.jobs.get_by_id(source_job_id)
                if src_job and src_job.status == JobStatus.DONE:
                    p1_done = True

                # Check if Pipeline2Job exists
                cur = uow._conn.cursor()
                cur.execute("SELECT id FROM pipeline2_jobs WHERE source_job_id = ?", (source_job_id,))
                row = cur.fetchone()
                if row:
                    p2_job_record = uow.pipeline2_jobs.get_by_id(row["id"])
                    if p2_job_record and p2_job_record.status == JobStatus.DONE:
                        p2_done = True
                        break

            time.sleep(0.05)

        runtime.shutdown(wait=True, timeout=2.0)

        # Verification: Pipeline 1 must be DONE
        self.assertTrue(p1_done, "Pipeline 1 should have completed to DONE.")
        # Verification: Pipeline 2 job must have been created
        self.assertIsNotNone(p2_job_record, "Pipeline 2 job should have been created by Pipeline 1 completion.")
        # Regressive assertion: Pipeline 2 must be dispatched and completed to DONE
        self.assertTrue(
            p2_done,
            f"Pipeline 2 job remained in status '{p2_job_record.status.value if p2_job_record else 'missing'}' "
            "because DesktopJobRuntime does not dispatch pipeline2_jobs!"
        )

        # Verify output artifact
        self.assertIsNotNone(p2_job_record.output_path)
        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=p2_job_record.output_path,
            artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
            job_id=source_job_id,
            filename="output.md",
        )
        data = self.storage.retrieve(handle).decode("utf-8")
        self.assertIn("[Refined via AI]", data)

    def test_pipeline2_disabled_no_pipeline2_job_created(self):
        """When auto_pipeline2=False, Pipeline 1 completes and no Pipeline2Job is created."""
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
            max_workers=2,
        )

        dto = self.submission_service.submit_job(
            SubmitJobCommand(
                user_id=1,
                filename="document_no_p2.pdf",
                file_bytes=b"%PDF-1.4 mock binary",
                auto_pipeline2=False,
            )
        )
        source_job_id = dto.id

        runtime.start()

        start_time = time.monotonic()
        p1_done = False
        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                src_job = uow.jobs.get_by_id(source_job_id)
                if src_job and src_job.status == JobStatus.DONE:
                    p1_done = True
                    break
            time.sleep(0.05)

        runtime.shutdown(wait=True, timeout=2.0)

        self.assertTrue(p1_done)
        with self.uow_factory.create() as uow:
            cur = uow._conn.cursor()
            cur.execute("SELECT COUNT(*) as c FROM pipeline2_jobs WHERE source_job_id = ?", (source_job_id,))
            count = cur.fetchone()["c"]
            self.assertEqual(count, 0, "No Pipeline2Job should be created when auto_pipeline2 is False.")

    def test_pipeline2_standalone_pending_job_dispatched_without_p1(self):
        """A pending Pipeline2Job in pipeline2_jobs is dispatched even when no P1 jobs are pending."""
        # 1. Create a DONE source job and an input artifact
        with self.uow_factory.create() as uow:
            src_job = Job(
                id=None,
                file_name="existing.pdf",
                file_path="/in/existing.pdf",
                status=JobStatus.DONE,
            )
            saved_src = uow.jobs.save(src_job)
            uow.commit()

        source_job_id = saved_src.id
        in_handle = self.storage.store(
            job_id=source_job_id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="source_p1_out.md",
            data=b"# Base Document Content",
            mime_type="text/markdown",
        )

        # 2. Insert a PENDING Pipeline2Job directly
        with self.uow_factory.create() as uow:
            slot = uow.apis.list_all()[0]
            p2_job = Pipeline2Job(
                id=None,
                source_job_id=source_job_id,
                prompt_id=2,
                status=JobStatus.PENDING,
                input_path=in_handle.uri,
                api_chain=[slot],
                current_api_index=0,
            )
            saved_p2 = uow.pipeline2_jobs.save(p2_job)
            uow.commit()

        p2_id = saved_p2.id

        # 3. Start runtime with zero pending P1 jobs
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
            max_workers=2,
        )
        runtime.start()

        start_time = time.monotonic()
        p2_done = False
        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                p2 = uow.pipeline2_jobs.get_by_id(p2_id)
                if p2 and p2.status == JobStatus.DONE:
                    p2_done = True
                    break
            time.sleep(0.05)

        runtime.shutdown(wait=True, timeout=2.0)

        self.assertTrue(p2_done, "Standalone Pipeline 2 job should be dispatched and completed.")

    def test_pipeline2_concurrency_capacity_respected(self):
        """Verify that total running workers across P1 and P2 never exceeds max_concurrent_jobs."""
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.02,
            max_workers=2,
        )

        # Submit 3 jobs with auto_pipeline2=True
        job_ids = []
        for i in range(3):
            dto = self.submission_service.submit_job(
                SubmitJobCommand(
                    user_id=1,
                    filename=f"batch_{i}.pdf",
                    file_bytes=f"%PDF-1.4 mock {i}".encode(),
                    auto_pipeline2=True,
                )
            )
            job_ids.append(dto.id)

        runtime.start()

        # Track observed active worker counts
        max_observed_active = 0
        start_time = time.monotonic()
        all_completed = False

        while time.monotonic() - start_time < 5.0:
            active = runtime.active_worker_count
            if active > max_observed_active:
                max_observed_active = active

            with self.uow_factory.create() as uow:
                cur = uow._conn.cursor()
                cur.execute("SELECT COUNT(*) as c FROM pipeline2_jobs WHERE status = 'done'")
                p2_done_count = cur.fetchone()["c"]
                if p2_done_count == 3:
                    all_completed = True
                    break
            time.sleep(0.02)

        runtime.shutdown(wait=True, timeout=2.0)

        self.assertTrue(all_completed, "All 3 jobs should complete Pipeline 1 and Pipeline 2.")
        self.assertLessEqual(
            max_observed_active,
            runtime.max_concurrent_jobs,
            f"Active worker count ({max_observed_active}) exceeded max_concurrent_jobs ({runtime.max_concurrent_jobs})",
        )

    def test_pipeline2_runtime_pause_and_resume(self):
        """When runtime is paused, pending Pipeline 2 jobs are not dispatched until resumed."""
        # Pre-create DONE source job and pending Pipeline2Job
        with self.uow_factory.create() as uow:
            src_job = Job(id=None, file_name="pause_test.pdf", file_path="/in/p.pdf", status=JobStatus.DONE)
            saved_src = uow.jobs.save(src_job)
            uow.commit()

        in_handle = self.storage.store(
            job_id=saved_src.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="p2_pause_in.md",
            data=b"# Content",
            mime_type="text/markdown",
        )

        with self.uow_factory.create() as uow:
            slot = uow.apis.list_all()[0]
            p2_job = Pipeline2Job(
                id=None,
                source_job_id=saved_src.id,
                prompt_id=2,
                status=JobStatus.PENDING,
                input_path=in_handle.uri,
                api_chain=[slot],
            )
            saved_p2 = uow.pipeline2_jobs.save(p2_job)
            uow.commit()

        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
        )

        runtime.start()
        runtime.pause()

        # While paused, wait 0.2s; job must remain PENDING
        time.sleep(0.2)
        with self.uow_factory.create() as uow:
            p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(p2.status, JobStatus.PENDING, "Job must remain PENDING while runtime is paused.")

        # Resume runtime; job should complete
        runtime.resume()

        start_time = time.monotonic()
        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
                if p2.status == JobStatus.DONE:
                    break
            time.sleep(0.05)

        runtime.shutdown(wait=True, timeout=2.0)
        self.assertEqual(p2.status, JobStatus.DONE)

    def test_pipeline2_unhandled_worker_failure_handled_safely(self):
        """Unhandled exceptions in Pipeline 2 worker thread mark job FAILED and don't kill dispatcher."""
        # Create AI executor that fails during Pipeline 2
        failing_executor = FakeAIExecutor()
        failing_executor.execute_text_with_fallback = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Simulated LLM connection failure")
        )

        failing_exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=failing_executor,
            event_publisher=self.event_bus,
        )

        with self.uow_factory.create() as uow:
            src_job = Job(id=None, file_name="fail.pdf", file_path="/in/f.pdf", status=JobStatus.DONE)
            saved_src = uow.jobs.save(src_job)
            uow.commit()

        in_handle = self.storage.store(
            job_id=saved_src.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="p2_fail_in.md",
            data=b"# Content",
            mime_type="text/markdown",
        )

        with self.uow_factory.create() as uow:
            slot = uow.apis.list_all()[0]
            p2_job = Pipeline2Job(
                id=None,
                source_job_id=saved_src.id,
                prompt_id=2,
                status=JobStatus.PENDING,
                input_path=in_handle.uri,
                api_chain=[slot],
            )
            saved_p2 = uow.pipeline2_jobs.save(p2_job)
            uow.commit()

        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=failing_exec_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
        )
        runtime.start()

        start_time = time.monotonic()
        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
                if p2.status in (JobStatus.FAILED, JobStatus.DONE):
                    break
            time.sleep(0.05)

        # Dispatcher must still be alive and running
        self.assertTrue(runtime.is_running, "Dispatcher thread must remain alive despite worker crash.")
        runtime.shutdown(wait=True, timeout=2.0)

        self.assertEqual(p2.status, JobStatus.FAILED)
        self.assertIn("Simulated LLM connection failure", p2.error_message or "")


if __name__ == "__main__":
    unittest.main()

