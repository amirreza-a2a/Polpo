# ============================================================
#  tests/unit/test_phase4_architecture.py
# ============================================================

import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import tests.characterization.conftest_base

# Core imports
import core.entities.job as job_entity_mod
import core.entities.user as user_entity_mod
import core.entities.api_slot as api_slot_mod
import core.entities.prompt as prompt_mod
import core.entities.artifact as artifact_mod
from core.entities.job import Job, JobStatus
from core.entities.user import User, QuotaAllocation, UserPreferences
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import ArtifactType
from core.policies.job_state_policy import JobStateTransitionPolicy, InvalidStateTransitionError
from core.policies.retry_policy import RetryPolicy
from core.exceptions.domain_exceptions import (
    DomainError,
    ArtifactNotFoundError,
    AuthenticationError,
)
from core.ai.types import AIResponse

# Application DTOs & Services
from application.dto.job_dto import SubmitJobCommand
from application.dto.quick_convert_dto import QuickConvertCommand
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.quick_convert import QuickConvertService
from application.ports.ai_executor import IAIExecutionService
from application.ports.notifier import IProgressNotifier

# Infrastructure
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from infrastructure.notifier.event_notifier import InMemoryEventNotifier


class TestPhase4DomainPurity(unittest.TestCase):
    """
    Verifies that core/entities and core/policies have zero external dependencies.
    """

    def test_core_layer_has_no_external_dependencies(self):
        """Hardening 9: core/ must not import framework, infrastructure, or transport dependencies."""
        forbidden_modules = {"PIL", "google", "openai", "telegram", "fastapi", "pymysql", "fitz", "pydantic", "infrastructure", "interfaces", "application"}
        core_dir = Path(__file__).resolve().parent.parent.parent / "core"

        for py_file in core_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
            for forbidden in forbidden_modules:
                self.assertNotIn(
                    f"import {forbidden}",
                    content,
                    f"Core module '{py_file.name}' must not import '{forbidden}'",
                )
                self.assertNotIn(
                    f"from {forbidden}",
                    content,
                    f"Core module '{py_file.name}' must not import from '{forbidden}'",
                )

    def test_application_layer_has_no_infrastructure_or_framework_imports(self):
        """Hardening 9: application/ must not import infrastructure, interfaces, or external SDKs."""
        forbidden_imports = {"infrastructure", "interfaces", "fastapi", "telegram", "pymysql", "fitz", "google", "openai", "PIL"}
        app_dir = Path(__file__).resolve().parent.parent.parent / "application"

        for py_file in app_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
            for forbidden in forbidden_imports:
                self.assertNotIn(
                    f"import {forbidden}",
                    content,
                    f"Application file '{py_file.name}' must not import '{forbidden}'",
                )
                self.assertNotIn(
                    f"from {forbidden}",
                    content,
                    f"Application file '{py_file.name}' must not import from '{forbidden}'",
                )


    def test_api_slot_retains_credential_ref_not_raw_key(self):
        slot = ApiSlot(
            id=1,
            provider="google",
            label="Google Slot 1",
            slot_type="byok",
            credential_ref=CredentialRef(identifier="1", provider="google", slot_type="byok"),
            selected_model="gemini-3.5-flash",
        )
        self.assertEqual(str(slot.credential_ref), "byok:google:1")
        self.assertFalse(hasattr(slot, "api_key"))

    def test_job_state_transition_policy(self):
        JobStateTransitionPolicy.validate_transition(JobStatus.PENDING, JobStatus.PROCESSING)
        JobStateTransitionPolicy.validate_transition(JobStatus.PROCESSING, JobStatus.DONE)
        JobStateTransitionPolicy.validate_transition(JobStatus.PROCESSING, JobStatus.PAUSED)
        JobStateTransitionPolicy.validate_transition(JobStatus.PAUSED, JobStatus.PENDING)

        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.DONE, JobStatus.PROCESSING)

    def test_retry_policy(self):
        self.assertTrue(RetryPolicy.is_eligible_for_retry(0))
        self.assertTrue(RetryPolicy.is_eligible_for_retry(2))
        self.assertFalse(RetryPolicy.is_eligible_for_retry(3))


class TestLocalStorageAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_store_and_retrieve_artifact(self):
        data = b"# Test Markdown Output\nPage 1 text"
        handle = self.storage.store(
            job_id=101,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="output_101.md",
            data=data,
            mime_type="text/markdown",
        )
        self.assertTrue(self.storage.exists(handle))
        self.assertEqual(self.storage.retrieve(handle), data)

        stream = self.storage.open_stream(handle)
        self.assertEqual(stream.read(), data)
        stream.close()

    def test_malicious_filenames_cannot_escape_artifact_directory(self):
        """Hardening 8: Path traversal attempts are sanitized and contained within job directory."""
        handle = self.storage.store(
            job_id=42,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="../../../../../etc/passwd",
            data=b"dummy",
        )
        expected_dir = (Path(self.temp_dir) / "job_42").resolve()
        resolved_path = Path(handle.metadata["path"]).resolve()
        self.assertTrue(resolved_path.is_relative_to(expected_dir))
        self.assertEqual(handle.filename, "passwd")

    def test_cleanup_and_missing_artifact(self):
        handle = self.storage.store(
            job_id=102,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="doc.pdf",
            data=b"PDF bytes",
        )
        self.storage.cleanup_job_artifacts(102)
        self.assertFalse(self.storage.exists(handle))
        with self.assertRaises(ArtifactNotFoundError):
            self.storage.retrieve(handle)


class TestApplicationServices(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir))

        self.mock_uow = MagicMock()
        self.mock_uow_factory = MagicMock()
        self.mock_uow_factory.create.return_value.__enter__.return_value = self.mock_uow
        self.mock_uow_factory.create.return_value.__exit__.return_value = None

        self.mock_doc_processor = MagicMock()
        self.mock_notifier = MagicMock(spec=IProgressNotifier)
        self.mock_ai_executor = MagicMock(spec=IAIExecutionService)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_quick_convert_delegates_fallback_without_manual_loop(self):
        """Hardening 1 & 9a: QuickConvertService delegates fallback directly to IAIExecutionService."""
        service = QuickConvertService(self.mock_uow_factory, self.mock_ai_executor)
        user = User(id=1, quota=QuotaAllocation(daily_limit=50, daily_pages_used=0, last_active_date=date.today()))
        self.mock_uow.users.get_by_id.return_value = user
        self.mock_uow.prompts.get_default.return_value = Prompt(id=1, name="QC", text="Prompt")
        self.mock_uow.apis.list_by_user.return_value = [ApiSlot(id=1, provider="google", label="Fast Slot")]

        used_slot = ApiSlot(id=1, provider="google", label="Fast Slot")
        self.mock_ai_executor.execute_vision_with_fallback.return_value = ("# Transcribed", used_slot)

        cmd = QuickConvertCommand(user_id=1, image_bytes=b"IMG", prompt_text="Convert")
        res = service.convert_image(cmd)

        self.assertEqual(res.markdown_content, "# Transcribed")
        self.mock_ai_executor.execute_vision_with_fallback.assert_called_once()
        self.mock_uow.users.increment_daily_pages.assert_not_called()

    def test_job_submission_success(self):
        service = JobSubmissionService(self.mock_uow_factory, self.storage, self.mock_doc_processor)
        self.mock_doc_processor.get_page_count.return_value = 5

        user = User(
            id=1,
            telegram_id=1001,
            username="testuser",
            quota=QuotaAllocation(daily_limit=50, daily_pages_used=0, last_active_date=date.today()),
        )
        self.mock_uow.users.get_by_id.return_value = user
        self.mock_uow.prompts.get_default.return_value = Prompt(id=1, name="P1", text="Convert to MD")
        self.mock_uow.apis.list_by_user.return_value = [
            ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        ]

        saved_job = Job(
            id=10,
            file_name="sample.pdf",
            file_path="",
            total_pages=5,
            status=JobStatus.PENDING,
        )
        self.mock_uow.jobs.save.return_value = saved_job

        cmd = SubmitJobCommand(user_id=1, filename="sample.pdf", file_bytes=b"%PDF-1.4 dummy bytes")
        res = service.submit_job(cmd)

        self.assertEqual(res.id, 10)
        self.assertEqual(res.total_pages, 5)
        self.assertEqual(res.status, "pending")
        self.mock_uow.commit.assert_called()

    def test_job_execution_page_loop_and_completion(self):
        service = JobExecutionService(
            uow_factory=self.mock_uow_factory,
            storage=self.storage,
            doc_processor=self.mock_doc_processor,
            ai_executor=self.mock_ai_executor,
            notifier=self.mock_notifier,
        )

        handle = self.storage.store(
            job_id=20,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="doc.pdf",
            data=b"Fake PDF Data",
        )

        job = Job(
            id=20,
            file_name="doc.pdf",
            file_path=handle.uri,
            total_pages=2,
            processed_pages=0,
            prompt_text="Extract Markdown",
            api_chain=[
                ApiSlot(id=1, provider="google", label="Slot 1", slot_type="byok"),
            ],
            current_api_index=0,
            status=JobStatus.PENDING,
            auto_pipeline2=True,
            pipeline2_prompt_id=5,
        )
        def mock_claim():
            job.status = JobStatus.PROCESSING
            return job

        self.mock_uow.jobs.claim_next_pending.side_effect = mock_claim
        self.mock_uow.jobs.get_next_pending.return_value = job
        self.mock_uow.jobs.get_by_id.return_value = job
        self.mock_uow.publish_intents.get_by_job_id.return_value = None
        self.mock_uow.document_versions.get_latest.return_value = None

        self.mock_doc_processor.render_page_to_jpeg.return_value = b"\xff\xd8\xff\xe0FakeJPEG"
        self.mock_doc_processor.extract_and_crop_images.return_value = ("Page text markdown", [("crop_1.jpg", b"CropData")])
        self.mock_ai_executor.execute_vision_with_fallback.return_value = ("Transcribed Page Content", job.api_chain[0])

        executed_job = service.execute_next_job()
        self.assertIsNotNone(executed_job)
        self.assertEqual(executed_job.status, JobStatus.DONE)
        self.assertEqual(executed_job.processed_pages, 2)
        self.mock_notifier.notify_job_completed.assert_called_once()
        self.mock_uow.pipeline2_jobs.save.assert_called_once()

    def test_non_ai_programming_exceptions_propagate_in_executor(self):
        """Hardening 7 & 9g: Non-AI programming exceptions propagate up without being swallowed."""
        mock_factory = MagicMock()
        mock_limiter = MagicMock()
        executor = RateLimitedAIExecutor(adapter_factory=mock_factory, rate_limiter=mock_limiter)

        mock_adapter = MagicMock()
        mock_adapter.generate_vision.side_effect = TypeError("Internal Programming Type Bug")
        mock_factory.return_value = mock_adapter

        chain = [ApiSlot(id=1, provider="google", label="Slot 1")]

        with self.assertRaises(TypeError) as ctx:
            executor.execute_vision_with_fallback(chain, b"IMG", "Prompt")
        self.assertIn("Internal Programming Type Bug", str(ctx.exception))


class TestNotifierSSEEvents(unittest.TestCase):
    def test_sse_api_switch_event_contains_reason_and_page(self):
        """Hardening 6 & 9f: SSE api_switch event includes reason and page number."""
        import asyncio
        notifier = InMemoryEventNotifier()
        q = notifier.subscribe(job_id=42)

        notifier.notify_api_switch(
            user_id=1,
            job_id=42,
            old_label="Google Gemini",
            new_label="OpenAI GPT-4o",
            reason="rate_limit",
            page=3,
        )

        async def get_event():
            return await asyncio.wait_for(q.get(), timeout=1.0)

        event = asyncio.run(get_event())
        self.assertEqual(event["event"], "api_switch")
        self.assertEqual(event["data"]["reason"], "rate_limit")
        self.assertEqual(event["data"]["page"], 3)
        self.assertEqual(event["data"]["from_api"], "Google Gemini")
        self.assertEqual(event["data"]["to_api"], "OpenAI GPT-4o")


if __name__ == "__main__":
    unittest.main()
