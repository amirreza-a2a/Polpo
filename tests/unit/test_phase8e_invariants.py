# ============================================================
#  tests/unit/test_phase8e_invariants.py
# ============================================================

import ast
import time
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from application.dto.job_dto import SubmitJobCommand
from application.events import (
    JobStateChangedEvent,
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
from interfaces.desktop.composition import DesktopAppContainer
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.workers.scheduler import DesktopJobScheduler


class InvariantDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"jpeg_data"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []


class InvariantAIExecutor:
    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        return "Invariant AI result", chain[0] if chain else None


class TestPhase8EInvariants(unittest.TestCase):
    """
    Machine-verifiable architectural invariant tests for Phase 8E.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "invariants8e.db"
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
        )

        self.scheduler = DesktopJobScheduler(
            uow_factory=self.uow_factory,
            runtime=self.runtime,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            tick_interval=0.05,
        )

    def tearDown(self):
        self.scheduler.shutdown()
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_invariant_1_scheduler_never_claims_or_executes_jobs(self):
        """DesktopJobScheduler wakes the runtime but NEVER claims jobs directly or transitions them to PROCESSING."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(seconds=5)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="test_sched.pdf", file_bytes=b"%PDF-1.4 content", scheduled_at=past_dt)
        )

        wake_called = []
        original_wake = self.runtime.wake
        self.runtime.wake = lambda: wake_called.append(True)

        # Start ONLY scheduler (Runtime is NOT started)
        self.scheduler.start()
        time.sleep(0.15)

        # Scheduler must have detected due job and called wake()
        self.assertTrue(len(wake_called) > 0, "Scheduler did not wake runtime for due job")

        # But in SQLite, job status MUST still be PENDING (scheduler did not claim it!)
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertEqual(job.status, JobStatus.PENDING, "CRITICAL: Scheduler illegally transitioned job status!")

    def test_invariant_2_startup_order_reconciliation_before_runtime_start(self):
        """DesktopAppContainer refuses to start runtime if initialize() has not completed."""
        container = DesktopAppContainer(
            db_path=self.db_path,
            artifacts_dir=self.artifacts_dir,
            vault_path=Path(self.temp_dir.name) / "vault.enc",
            passphrase="master_passphrase",
        )

        # Attempting start_runtime before initialize() must raise RuntimeError
        with self.assertRaises(RuntimeError):
            container.start_runtime()

        container.initialize()
        self.assertTrue(container._initialized)

        # Now start_runtime succeeds cleanly
        container.start_runtime()
        container.shutdown()

    def test_invariant_3_run_immediately_clears_scheduled_at_atomically(self):
        """Under run_immediately, scheduled_at becomes NULL in SQLite so it is never re-evaluated as missed."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(days=1)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="clean_missed.pdf", file_bytes=b"%PDF-1.4 data", scheduled_at=past_dt)
        )

        reconciled = self.recovery_service.reconcile_missed_schedules(
            startup_time=now_utc, policy="run_immediately"
        )
        self.assertEqual(len(reconciled), 1)

        # In DB, scheduled_at is NULL
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(dto.id)
            self.assertIsNone(job.scheduled_at)
            self.assertEqual(job.status, JobStatus.PENDING)

        # Second reconciliation finds 0 missed schedules
        reconciled_again = self.recovery_service.reconcile_missed_schedules(
            startup_time=now_utc, policy="run_immediately"
        )
        self.assertEqual(len(reconciled_again), 0)

    def test_invariant_4_prompt_policy_does_not_repeat_events_on_ticker(self):
        """Under prompt policy, MissedScheduleDetectedEvent is emitted during startup, NOT on every scheduler tick."""
        now_utc = datetime.now(timezone.utc)
        past_dt = now_utc - timedelta(hours=5)

        dto = self.submission_service.submit_job(
            SubmitJobCommand(user_id=1, filename="prompt_tick.pdf", file_bytes=b"%PDF-1.4 data", scheduled_at=past_dt)
        )

        missed_events = []
        self.event_bus.subscribe(MissedScheduleDetectedEvent, lambda e: missed_events.append(e))

        # 1. Startup reconciliation emits 1 event
        self.recovery_service.reconcile_missed_schedules(startup_time=now_utc, policy="prompt")
        self.assertEqual(len(missed_events), 1)

        # 2. Run scheduler for multiple ticks
        self.scheduler.start()
        time.sleep(0.25)

        # 3. Ticker must NOT have emitted duplicate MissedScheduleDetectedEvent
        self.assertEqual(len(missed_events), 1, "CRITICAL: Scheduler ticker repeatedly emitted missed schedule event!")

    def test_invariant_5_ast_clean_architecture_boundaries(self):
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
