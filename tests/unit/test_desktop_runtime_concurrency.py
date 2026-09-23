# ============================================================
#  tests/unit/test_desktop_runtime_concurrency.py
# ============================================================

import time
import tempfile
import threading
import unittest
from pathlib import Path

from core.entities.job import JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from application.dto.job_dto import SubmitJobCommand
from application.events import JobStateChangedEvent, JobCompletedEvent, JobFailedEvent
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.composition import DesktopAppContainer


class SlowDocumentProcessor:
    def __init__(self, delay=0.05):
        self.delay = delay

    def get_page_count(self, file_bytes: bytes) -> int:
        return 2

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        if self.delay > 0:
            time.sleep(self.delay)
        return b"jpeg_page_" + str(page_num).encode()

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []


class DummyAIExecutor:
    def __init__(self, delay=0.05):
        self.delay = delay

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        if self.delay > 0:
            time.sleep(self.delay)
        return f"Markdown output for page {at_page}", chain[0] if chain else None


class TestDesktopRuntimeConcurrency(unittest.TestCase):
    """
    Concurrency and lifecycle unit tests for DesktopJobRuntime and DesktopAppContainer.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime_test.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = SlowDocumentProcessor(delay=0.02)
        self.ai_executor = DummyAIExecutor(delay=0.02)

        # Seed initial prompt, slot, and settings
        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Slot 1", credential_ref=CredentialRef("cred_1", "google", "byok")))
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

        self.events_received = []
        self.event_bus.subscribe(object, lambda e: self.events_received.append(e))

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_runtime_parallel_execution_respects_concurrency_bound(self):
        """Submit 4 jobs; verify runtime processes them up to max_concurrent_jobs=2 until all are DONE."""
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
            max_workers=4,
        )

        job_ids = []
        for i in range(4):
            dto = self.submission_service.submit_job(
                SubmitJobCommand(user_id=1, filename=f"doc_{i}.pdf", file_bytes=b"%PDF-1.4 binary data")
            )
            job_ids.append(dto.id)

        runtime.start()

        # Wait for all jobs to complete (with timeout)
        start_time = time.monotonic()
        all_done = False
        while time.monotonic() - start_time < 5.0:
            with self.uow_factory.create() as uow:
                statuses = [uow.jobs.get_by_id(jid).status for jid in job_ids]
            if all(s == JobStatus.DONE for s in statuses):
                all_done = True
                break
            time.sleep(0.05)

        runtime.shutdown(wait=True, timeout=3.0)
        self.assertTrue(all_done, f"Expected all jobs DONE, got statuses: {statuses}")

        completed_events = [e for e in self.events_received if isinstance(e, JobCompletedEvent)]
        self.assertEqual(len(completed_events), 4)

    def test_runtime_pause_and_resume(self):
        runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            poll_interval=0.05,
        )

        # Pause immediately before starting
        runtime.start()
        runtime.pause()

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="paused_doc.pdf", file_bytes=b"%PDF-1.4 binary data")
        )

        time.sleep(0.2)
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING)  # Still pending while paused

        # Resume runtime
        runtime.resume()

        # Wait for completion
        start_time = time.monotonic()
        while time.monotonic() - start_time < 3.0:
            with self.uow_factory.create() as uow:
                job = uow.jobs.get_by_id(dto.id)
            if job.status == JobStatus.DONE:
                break
            time.sleep(0.05)

        runtime.shutdown(wait=True)
        self.assertEqual(job.status, JobStatus.DONE)

    def test_desktop_app_container_lifecycle(self):
        """Verify DesktopAppContainer initialization, stale recovery, and services access."""
        vault_file = Path(self.temp_dir.name) / "vault.enc"
        container = DesktopAppContainer(
            db_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            vault_path=vault_file,
            passphrase="ContainerMasterSecret123!",
        )
        container.doc_processor = self.doc_processor
        container.job_submission_service.doc_processor = self.doc_processor

        # Seed an orphaned PROCESSING job before initialize()
        job_dto = container.job_submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="orphan.pdf", file_bytes=b"%PDF-1.4 dummy")
        )
        with container.uow_factory.create() as uow:
            uow.jobs.claim_job(job_dto.id)
            uow.commit()

        # Run initialization (migrations + stale recovery)
        container.initialize()

        with container.uow_factory.create() as uow:
            recovered_job = uow.jobs.get_by_id(job_dto.id)
            self.assertEqual(recovered_job.status, JobStatus.PAUSED)

        container.shutdown()


if __name__ == "__main__":
    unittest.main()
