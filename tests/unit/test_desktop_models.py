# ============================================================
#  tests/unit/test_desktop_models.py
# ============================================================

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from interfaces.desktop.qt_compat import QGuiApplication, QModelIndex
from interfaces.desktop.bridge import QtSignalEventBridge
from interfaces.desktop.models import (
    JobQueueModel,
    JobHistoryModel,
    ApiSlotModel,
    PromptListModel,
)
from interfaces.desktop.controllers import ApiKeyController, PromptController
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from infrastructure.security.encrypted_store import EncryptedFileCredentialStore
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.ai.provider_detector import AIProviderDetector

from application.services.job_query import JobQueryService
from application.services.api_key_service import ApiKeyService
from application.services.prompt_service import PromptService
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobStateChangedEvent,
)
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.settings import AppSettings


class TestDesktopModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        self.db_path = base_path / "test_models.db"
        self.vault_path = base_path / "vault.enc"

        self.db_mgr = SQLiteDatabaseManager(self.db_path)
        self.migrations = SQLiteMigrationRunner(self.db_mgr)
        self.migrations.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_mgr)

        self.enc_store = EncryptedFileCredentialStore(self.vault_path, passphrase="test-passphrase")
        self.cred_resolver = KeyringCredentialResolver(fallback_store=self.enc_store)
        self.event_bus = InMemoryEventBus()
        self.bridge = QtSignalEventBridge(self.event_bus)
        self.detector = AIProviderDetector()

        # Seed initial data
        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert text", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.prompts.save(Prompt(id=None, name="P2", text="Rewrite text", prompt_type=PromptType.PIPELINE_2, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="openai", label="Main OpenAI", credential_ref=CredentialRef("c1", "openai", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=2))

            # Add sample jobs: 1 pending, 1 processing, 1 done
            uow.jobs.save(Job(id=None, file_name="pending.pdf", file_path="/p.pdf", total_pages=5, status=JobStatus.PENDING))
            uow.jobs.save(Job(id=None, file_name="processing.pdf", file_path="/proc.pdf", total_pages=10, processed_pages=2, status=JobStatus.PROCESSING))
            uow.jobs.save(Job(id=None, file_name="done.pdf", file_path="/d.pdf", total_pages=3, processed_pages=3, status=JobStatus.DONE, output_path="/out.md"))
            uow.commit()

        self.query_service = JobQueryService(self.uow_factory)
        self.api_service = ApiKeyService(self.uow_factory, self.cred_resolver, self.detector)
        self.prompt_service = PromptService(self.uow_factory)

        self.api_ctrl = ApiKeyController(self.api_service)
        self.prompt_ctrl = PromptController(self.prompt_service)

    def tearDown(self):
        self.bridge.detach()
        self.temp_dir.cleanup()

    def test_job_queue_model_initialization_and_roles(self):
        model = JobQueueModel(self.query_service, self.bridge)
        # Should contain pending and processing jobs (2 jobs)
        self.assertEqual(model.rowCount(), 2)

        idx0 = model.index(0, 0)
        self.assertIn(model.data(idx0, JobQueueModel.FileNameRole), ["pending.pdf", "processing.pdf"])
        roles = model.roleNames()
        self.assertIn(JobQueueModel.ProgressPercentRole, roles)
        self.assertEqual(roles[JobQueueModel.ProgressPercentRole], b"progressPercent")

    def test_job_queue_model_reactive_progress_and_api_switch(self):
        model = JobQueueModel(self.query_service, self.bridge)
        proc_job_id = None
        for i in range(model.rowCount()):
            idx = model.index(i, 0)
            if model.data(idx, JobQueueModel.FileNameRole) == "processing.pdf":
                proc_job_id = model.data(idx, JobQueueModel.IdRole)
                break
        self.assertIsNotNone(proc_job_id)

        # Emit progress event
        self.event_bus.publish(JobProgressEvent(job_id=proc_job_id, processed_pages=5, total_pages=10, percent=50.0))
        self.app.processEvents()

        # Find row and verify updated data
        for i in range(model.rowCount()):
            idx = model.index(i, 0)
            if model.data(idx, JobQueueModel.IdRole) == proc_job_id:
                self.assertEqual(model.data(idx, JobQueueModel.ProcessedPagesRole), 5)
                self.assertEqual(model.data(idx, JobQueueModel.ProgressPercentRole), 50.0)

        # Emit API switch event
        self.event_bus.publish(ApiSwitchEvent(job_id=proc_job_id, old_label="Key1", new_label="Key2", reason="Rate limit", page=6))
        self.app.processEvents()

        for i in range(model.rowCount()):
            idx = model.index(i, 0)
            if model.data(idx, JobQueueModel.IdRole) == proc_job_id:
                self.assertEqual(model.data(idx, JobQueueModel.ActiveApiLabelRole), "Key2")

    def test_job_queue_model_removal_on_completion(self):
        model = JobQueueModel(self.query_service, self.bridge)
        initial_count = model.rowCount()
        self.assertGreater(initial_count, 0)

        job_id = model.data(model.index(0, 0), JobQueueModel.IdRole)
        # Emit completion event
        self.event_bus.publish(JobStateChangedEvent(job_id=job_id, old_status=JobStatus.PROCESSING, new_status=JobStatus.DONE))
        self.app.processEvents()

        self.assertEqual(model.rowCount(), initial_count - 1)

    def test_job_history_model_pagination_and_roles(self):
        model = JobHistoryModel(self.query_service, self.bridge)
        self.assertEqual(model.totalJobs, 3)
        self.assertGreaterEqual(model.rowCount(), 1)

        idx = model.index(0, 0)
        self.assertIsNotNone(model.data(idx, JobHistoryModel.FileNameRole))

        roles = model.roleNames()
        self.assertIn(JobHistoryModel.OutputPathRole, roles)
        self.assertEqual(roles[JobHistoryModel.OutputPathRole], b"outputPath")

    def test_api_slot_model_and_zero_secrets(self):
        model = ApiSlotModel(self.api_service, self.api_ctrl)
        self.assertEqual(model.rowCount(), 1)

        idx = model.index(0, 0)
        self.assertEqual(model.data(idx, ApiSlotModel.LabelRole), "Main OpenAI")
        self.assertEqual(model.data(idx, ApiSlotModel.ProviderRole), "openai")

        # Register a new slot via controller -> model should auto-refresh
        self.api_ctrl.register_key("google", "Google Gemini", "sk-secret-key-12345", "gemini-2.0-flash")
        self.assertEqual(model.rowCount(), 2)

        # Inspect all role outputs -> verify 0 secrets
        for r in range(model.rowCount()):
            row_idx = model.index(r, 0)
            for role_key in [ApiSlotModel.IdRole, ApiSlotModel.ProviderRole, ApiSlotModel.LabelRole, ApiSlotModel.SelectedModelRole]:
                val = str(model.data(row_idx, role_key))
                self.assertNotIn("sk-secret-key-12345", val)

    def test_prompt_list_model_and_filtering(self):
        all_model = PromptListModel(self.prompt_service, self.prompt_ctrl)
        self.assertEqual(all_model.rowCount(), 2)

        p1_model = PromptListModel(self.prompt_service, self.prompt_ctrl, prompt_type="pipeline1")
        self.assertEqual(p1_model.rowCount(), 1)
        self.assertEqual(p1_model.data(p1_model.index(0, 0), PromptListModel.NameRole), "P1")

        # Create a new prompt via controller -> model reloads
        self.prompt_ctrl.create_prompt("New P1", "Extra instruction", "pipeline1", False)
        p1_model.reload_prompts()
        self.assertEqual(p1_model.rowCount(), 2)

    def test_job_queue_model_action_state_and_duplicate_protection(self):
        """Verifies transient action states ('cancelling', 'retrying', 'resuming') and auto-clearing."""
        model = JobQueueModel(self.query_service, self.bridge)
        self.assertGreater(model.rowCount(), 0)

        job_id = model.data(model.index(0, 0), JobQueueModel.IdRole)

        # 1. Set transient action state to 'cancelling'
        model.set_action_state(job_id, "cancelling")
        idx0 = model.index(0, 0)
        self.assertEqual(model.data(idx0, JobQueueModel.ActionStateRole), "cancelling")

        # 2. Progress event automatically clears action state
        self.event_bus.publish(JobProgressEvent(job_id=job_id, processed_pages=1, total_pages=5, percent=20.0))
        self.app.processEvents()
        self.assertEqual(model.data(idx0, JobQueueModel.ActionStateRole), "")

        # 3. Set transient action state to 'retrying'
        model.set_action_state(job_id, "retrying")
        self.assertEqual(model.data(idx0, JobQueueModel.ActionStateRole), "retrying")

        # 4. State change event automatically clears action state and converges status
        self.event_bus.publish(JobStateChangedEvent(job_id=job_id, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING))
        self.app.processEvents()
        self.assertEqual(model.data(idx0, JobQueueModel.ActionStateRole), "")
        self.assertEqual(model.data(idx0, JobQueueModel.StatusRole), "processing")

    def test_job_queue_model_retry_and_resume_lifecycle_convergence(self):
        """Verifies complete FAILED (in History) -> Retry (enters Queue as PENDING) -> PROCESSING -> DONE lifecycle convergence."""
        with self.uow_factory.create() as uow:
            saved = uow.jobs.save(Job(id=None, file_name="failed_retry_test.pdf", file_path="/f.pdf", total_pages=4, status=JobStatus.FAILED, error_message="Network timeout"))
            uow.commit()
            failed_id = saved.id

        model = JobQueueModel(self.query_service, self.bridge)
        # FAILED job must NOT be in active queue initially
        self.assertEqual(model._find_job_index(failed_id), -1)

        # 1. User triggers Retry from History -> Backend publishes FAILED -> PENDING
        self.event_bus.publish(JobStateChangedEvent(job_id=failed_id, old_status=JobStatus.FAILED, new_status=JobStatus.PENDING))
        self.app.processEvents()

        # Job enters active queue as PENDING
        idx_f = model._find_job_index(failed_id)
        self.assertGreaterEqual(idx_f, 0, "Retried job must enter active queue model as PENDING")
        model_idx = model.index(idx_f, 0)
        self.assertEqual(model.data(model_idx, JobQueueModel.StatusRole), "pending")
        self.assertEqual(model.data(model_idx, JobQueueModel.ActionStateRole), "")

        # 2. Runtime claims job -> converges to PROCESSING
        self.event_bus.publish(JobStateChangedEvent(job_id=failed_id, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING))
        self.app.processEvents()
        self.assertEqual(model.data(model_idx, JobQueueModel.StatusRole), "processing")

        # 4. Progress updates
        self.event_bus.publish(JobProgressEvent(job_id=failed_id, processed_pages=2, total_pages=4, percent=50.0))
        self.app.processEvents()
        self.assertEqual(model.data(model.index(idx_f, 0), JobQueueModel.ProcessedPagesRole), 2)
        self.assertEqual(model.data(model.index(idx_f, 0), JobQueueModel.ProgressPercentRole), 50.0)

        # 5. Job completes -> removed from queue
        self.event_bus.publish(JobStateChangedEvent(job_id=failed_id, old_status=JobStatus.PROCESSING, new_status=JobStatus.DONE))
        self.app.processEvents()
        self.assertEqual(model._find_job_index(failed_id), -1)

    def test_prompt_model_mixed_persian_english_support(self):
        """Verifies PromptListModel preserves mixed Persian/English text without corruption."""
        persian_prompt_text = (
            "متن آزمایشی فارسی با اصطلاحات تخصصی انگلیسی نظیر OCR, Deep Learning و فرمول ریاضی \\int_0^1 x^2 dx. "
            "دستورالعمل جامع جهت استخراج به فرمت Markdown با رعایت نشانه‌گذاری و ارقام فارسی ۱۲۳۴۵."
        )
        pid = self.prompt_ctrl.create_prompt("Persian Technical OCR", persian_prompt_text, "pipeline1", True)
        self.assertGreater(pid, 0)

        model = PromptListModel(self.prompt_service, self.prompt_ctrl, prompt_type="pipeline1")
        found = False
        for i in range(model.rowCount()):
            idx = model.index(i, 0)
            if model.data(idx, PromptListModel.NameRole) == "Persian Technical OCR":
                self.assertEqual(model.data(idx, PromptListModel.TextRole), persian_prompt_text)
                found = True
                break
        self.assertTrue(found, "Persian/English prompt must be retrieved correctly from model")


if __name__ == "__main__":
    unittest.main()
