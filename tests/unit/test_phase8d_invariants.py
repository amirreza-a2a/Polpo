# ============================================================
#  tests/unit/test_phase8d_invariants.py
# ============================================================

import ast
import time
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from core.entities.job import JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from application.dto.job_dto import SubmitJobCommand
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
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime


class InvariantDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 2

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"jpeg_data"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int):
        return markdown_text, []


class InvariantAIExecutor:
    def __init__(self):
        self.active_db_transactions_detected = []

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        return "Page markdown", chain[0] if chain else None


class TestPhase8DInvariants(unittest.TestCase):
    """
    Machine-testable architectural invariant verification for Phase 8D.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "invariants.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.doc_processor = InvariantDocProcessor()
        self.ai_executor = InvariantAIExecutor()

        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Google", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=3))
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

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invariant_1_no_incomplete_pending_jobs(self):
        """A committed PENDING job must always have a valid file_path pointing to existing on-disk artifact."""
        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="test.pdf", file_bytes=b"%PDF-1.4 binary content")
        )
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertIsNotNone(job)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertTrue(len(job.file_path) > 0)
            self.assertTrue(job.file_path.startswith("file://"))

            # Verify file exists on disk
            rel_path = job.file_path.replace("file://", "")
            self.assertTrue(Path(rel_path).exists())

    def test_invariant_2_no_duplicate_claims_under_concurrency(self):
        """When multiple worker threads attempt to claim pending jobs concurrently, no two workers get the same job ID."""
        # Submit 10 jobs
        job_ids = []
        for i in range(10):
            dto = self.submission_service.submit_job(
                SubmitJobCommand(user_id=1, filename=f"doc_{i}.pdf", file_bytes=b"%PDF-1.4 content")
            )
            job_ids.append(dto.id)

        claimed_ids = []
        lock = threading.Lock()

        def claim_worker():
            with self.uow_factory.create() as uow:
                claimed = uow.jobs.claim_next_pending()
                uow.commit()
                if claimed:
                    with lock:
                        claimed_ids.append(claimed.id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(claim_worker) for _ in range(15)]
            for f in futures:
                f.result()

        # Invariant: Each claimed ID is unique (no duplicates)
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
        self.assertEqual(len(claimed_ids), 10)

    def test_invariant_4_cancellation_mutual_exclusivity(self):
        """A cancelled job emits JobCancelledEvent and NEVER emits JobCompletedEvent."""
        events = []
        self.event_bus.subscribe(object, lambda e: events.append(e))

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="cancel_me.pdf", file_bytes=b"%PDF-1.4 data")
        )

        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_job(dto.id)
            uow.commit()

        # Request cancellation
        self.execution_service.cancel_job(claimed.id)

        # Run worker
        result_job = self.execution_service.execute_claimed_job(claimed.id)
        self.assertEqual(result_job.status, JobStatus.CANCELLED)

        completed_events = [e for e in events if isinstance(e, JobCompletedEvent)]
        cancelled_events = [e for e in events if isinstance(e, JobCancelledEvent)]

        self.assertEqual(len(completed_events), 0, "CRITICAL: JobCompletedEvent emitted for cancelled job!")
        self.assertEqual(len(cancelled_events), 1)

    def test_invariant_6_recovery_order_before_runtime_dispatch(self):
        """reconcile_stale_jobs() recovers crashed PROCESSING jobs to PAUSED before runtime dispatch."""
        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="crashed.pdf", file_bytes=b"%PDF-1.4 data")
        )
        with self.uow_factory.create() as uow:
            uow.jobs.claim_job(dto.id)  # Left in PROCESSING state
            uow.commit()

        reconciled = self.recovery_service.reconcile_stale_jobs()
        self.assertEqual(reconciled, 1)

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PAUSED)

    def test_invariant_9_ast_clean_architecture_boundaries(self):
        """AST analysis verifying zero forbidden imports in core/ or application/."""
        root_dir = Path(__file__).parent.parent.parent
        app_dir = root_dir / "application"
        core_dir = root_dir / "core"

        forbidden_in_app = {"keyring", "cryptography", "sqlite3", "PySide6", "fastapi", "pymysql", "infrastructure", "interfaces"}
        forbidden_in_core = {"keyring", "cryptography", "sqlite3", "PySide6", "fastapi", "pymysql", "infrastructure", "application", "interfaces"}

        for fpath in app_dir.rglob("*.py"):
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        for forbidden in forbidden_in_app:
                            self.assertFalse(
                                name.name == forbidden or name.name.startswith(forbidden + "."),
                                f"Forbidden import '{name.name}' in application file {fpath}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_in_app:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' in application file {fpath}",
                        )

        for fpath in core_dir.rglob("*.py"):
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        for forbidden in forbidden_in_core:
                            self.assertFalse(
                                name.name == forbidden or name.name.startswith(forbidden + "."),
                                f"Forbidden import '{name.name}' in core file {fpath}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_in_core:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' in core file {fpath}",
                        )


if __name__ == "__main__":
    unittest.main()
