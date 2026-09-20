# ============================================================
#  tests/unit/test_desktop_controllers.py
# ============================================================

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from interfaces.desktop.qt_compat import QGuiApplication
from interfaces.desktop.controllers import (
    JobController,
    ApiKeyController,
    PromptController,
    SettingsController,
    QuickConvertController,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from infrastructure.security.encrypted_store import EncryptedFileCredentialStore
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.ai.provider_detector import AIProviderDetector

from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.schedule_service import ScheduleService
from application.services.job_query import JobQueryService
from application.services.api_key_service import ApiKeyService
from application.services.prompt_service import PromptService
from application.services.settings_service import LocalSettingsService
from application.services.artifact_service import ArtifactService
from application.services.quick_convert import QuickConvertService
from core.entities.job import JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.settings import AppSettings


class DummyDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_image(self, file_bytes: bytes, page_number: int, dpi: int = 150) -> bytes:
        return b"fake_jpeg_image_bytes"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []


class MockAIExecutor:
    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg"):
        return "# Extracted Markdown Header", chain[0] if chain else None

    def execute_text_with_fallback(self, chain, prompt, input_text):
        return "# Refined Markdown", chain[0] if chain else None


class TestDesktopControllers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        self.db_path = base_path / "test.db"
        self.artifacts_dir = base_path / "artifacts"
        self.vault_path = base_path / "vault.enc"

        self.db_mgr = SQLiteDatabaseManager(self.db_path)
        self.migrations = SQLiteMigrationRunner(self.db_mgr)
        self.migrations.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_mgr)

        self.enc_store = EncryptedFileCredentialStore(self.vault_path, passphrase="test-passphrase")
        self.cred_resolver = KeyringCredentialResolver(fallback_store=self.enc_store)
        self.storage = LocalStorageAdapter(str(self.artifacts_dir))
        self.doc_processor = DummyDocProcessor()
        self.event_bus = InMemoryEventBus()
        self.ai_executor = MockAIExecutor()
        self.detector = AIProviderDetector()

        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Google Slot", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.settings.save(AppSettings(max_concurrent_jobs=2, missed_schedule_policy="prompt"))
            uow.commit()

        self.sub_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor, self.event_bus)
        self.exec_service = JobExecutionService(self.uow_factory, self.storage, self.doc_processor, self.ai_executor, self.event_bus)
        self.rec_service = JobRecoveryService(self.uow_factory, self.event_bus)
        self.sched_service = ScheduleService(self.uow_factory, self.event_bus, execution_service=self.exec_service)
        self.query_service = JobQueryService(self.uow_factory)
        self.api_service = ApiKeyService(self.uow_factory, self.cred_resolver, self.detector)
        self.prompt_service = PromptService(self.uow_factory)
        self.settings_service = LocalSettingsService(self.uow_factory)
        self.artifact_service = ArtifactService(self.storage, self.uow_factory)
        self.qc_service = QuickConvertService(self.uow_factory, self.ai_executor)

        self.job_ctrl = JobController(
            self.sub_service, self.exec_service, self.sched_service, self.rec_service,
            self.artifact_service, self.query_service
        )
        self.api_ctrl = ApiKeyController(self.api_service)
        self.prompt_ctrl = PromptController(self.prompt_service)
        self.settings_ctrl = SettingsController(self.settings_service, self.artifact_service)
        self.qc_ctrl = QuickConvertController(self.qc_service)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_controller_submission_cancellation_and_reschedule(self):
        # Create a sample PDF file
        pdf_path = Path(self.temp_dir.name) / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 sample content")

        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        job_id = self.job_ctrl.submit_job(str(pdf_path), 0, future_iso)
        self.assertGreater(job_id, 0)

        # Inspect details
        detail = self.job_ctrl.get_job_detail(job_id)
        self.assertEqual(detail["id"], job_id)
        self.assertEqual(detail["status"], "pending")

        # Reschedule
        new_future_iso = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        ok = self.job_ctrl.reschedule_job(job_id, new_future_iso)
        self.assertTrue(ok)

        # Cancel
        cancelled = self.job_ctrl.cancel_job(job_id)
        self.assertTrue(cancelled)

        updated_detail = self.job_ctrl.get_job_detail(job_id)
        self.assertEqual(updated_detail["status"], "cancelled")

    def test_job_controller_acknowledge_missed_schedule(self):
        pdf_path = Path(self.temp_dir.name) / "missed.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 missed content")

        past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        job_id = self.job_ctrl.submit_job(str(pdf_path), 0, past_iso)
        self.assertGreater(job_id, 0)

        # Acknowledge run_now
        ok = self.job_ctrl.acknowledge_missed_schedule(job_id, "run_now", "")
        self.assertTrue(ok)

    def test_api_key_controller_crud_and_security(self):
        # 1. Register key
        reg_ok = self.api_ctrl.register_key("openai", "My OpenAI", "sk-proj-test12345", "gpt-4o")
        self.assertTrue(reg_ok)

        # 2. List slots -> must contain 0 secret keys
        slots = self.api_ctrl.list_slots()
        self.assertGreaterEqual(len(slots), 2)
        my_slot = next(s for s in slots if s["label"] == "My OpenAI")
        self.assertEqual(my_slot["provider"], "openai")
        self.assertNotIn("api_key", my_slot)
        self.assertNotIn("sk-proj-test12345", str(my_slot))

        # 3. Test key resolution
        slot_id = my_slot["id"]
        test_ok = self.api_ctrl.test_key(slot_id)
        self.assertTrue(test_ok)

        # 4. Update key
        upd_ok = self.api_ctrl.update_key(slot_id, "Updated Label")
        self.assertTrue(upd_ok)

        # 5. Delete key
        del_ok = self.api_ctrl.delete_key(slot_id)
        self.assertTrue(del_ok)
        self.assertIsNone(next((s for s in self.api_ctrl.list_slots() if s["id"] == slot_id), None))

    def test_prompt_controller_crud(self):
        pid = self.prompt_ctrl.create_prompt("Test Prompt", "Transcribe everything accurately", "pipeline1", True)
        self.assertGreater(pid, 0)

        prompts = self.prompt_ctrl.list_prompts("pipeline1")
        self.assertGreaterEqual(len(prompts), 2)
        test_p = next(p for p in prompts if p["id"] == pid)
        self.assertEqual(test_p["name"], "Test Prompt")
        self.assertTrue(test_p["is_default"])

        upd_ok = self.prompt_ctrl.update_prompt(pid, "Renamed Prompt")
        self.assertTrue(upd_ok)

        del_ok = self.prompt_ctrl.delete_prompt(pid)
        self.assertTrue(del_ok)
        self.assertIsNone(next((p for p in self.prompt_ctrl.list_prompts("pipeline1") if p["id"] == pid), None))

    def test_settings_controller_properties_and_save(self):
        s_dict = self.settings_ctrl.get_settings()
        self.assertEqual(s_dict["theme"], "system")
        self.assertEqual(self.settings_ctrl.theme, "system")

        save_ok = self.settings_ctrl.save_settings("light", 4, "run_immediately", 30, True, True)
        self.assertTrue(save_ok)
        self.assertEqual(self.settings_ctrl.theme, "light")
        self.assertEqual(self.settings_ctrl.maxConcurrentJobs, 4)
        self.assertEqual(self.settings_ctrl.missedSchedulePolicy, "run_immediately")

    def test_quick_convert_controller_async_image_conversion_and_clipboard(self):
        import time
        # Register an API slot for quick convert
        self.api_ctrl.register_key("openai", "QC Key", "sk-proj-qc123", "gpt-4o")

        img_path = Path(self.temp_dir.name) / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00fake_image_bytes")

        received_content = []
        received_provider = []
        self.qc_ctrl.conversion_completed.connect(lambda c, p: (received_content.append(c), received_provider.append(p)))

        # Pass file:// URL to verify cross-platform path resolution
        file_url = img_path.resolve().as_uri()
        self.qc_ctrl.convert_image(file_url, "Transcribe formula")

        # Wait for async background worker to complete
        for _ in range(50):
            self.app.processEvents()
            if received_content:
                break
            time.sleep(0.02)

        self.assertEqual(len(received_content), 1)
        self.assertEqual(received_content[0], "# Extracted Markdown Header")
        self.assertFalse(self.qc_ctrl.isBusy)

        # Test clipboard copy
        clip_ok = self.qc_ctrl.copy_to_clipboard(received_content[0])
        self.assertTrue(clip_ok)

    def test_job_controller_cross_platform_paths_and_run_now(self):
        pdf_path = Path(self.temp_dir.name) / "url_path.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 sample content")

        future_iso = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        file_url = pdf_path.resolve().as_uri()

        job_id = self.job_ctrl.submit_job(file_url, 0, future_iso, True)
        self.assertGreater(job_id, 0)

        detail = self.job_ctrl.get_job_detail(job_id)
        self.assertEqual(detail["id"], job_id)
        self.assertTrue(detail["auto_pipeline2"])

        # Test run_now
        run_ok = self.job_ctrl.run_now(job_id)
        self.assertTrue(run_ok)

    def test_api_key_controller_supported_providers(self):
        provs = self.api_ctrl.get_supported_providers()
        self.assertIn("google", provs)
        self.assertIn("openai", provs)
        self.assertIn("anthropic", provs)


if __name__ == "__main__":
    unittest.main()
