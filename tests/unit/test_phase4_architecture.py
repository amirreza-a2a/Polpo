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
from fastapi.testclient import TestClient

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
from core.policies.quota_policy import QuotaPolicy
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
from application.services.auth_service import AuthService
from application.ports.ai_executor import IAIExecutionService

# Infrastructure
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.security.token_service import SecureTokenService
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from infrastructure.notifier.event_notifier import InMemoryEventNotifier
from interfaces.api.app import app
from interfaces.api.deps import get_container


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
            slot_type="private",
            credential_ref=CredentialRef(identifier="1", provider="google", slot_type="private"),
            selected_model="gemini-3.5-flash",
        )
        self.assertEqual(str(slot.credential_ref), "private:google:1")
        self.assertFalse(hasattr(slot, "api_key"))

    def test_job_state_transition_policy(self):
        JobStateTransitionPolicy.validate_transition(JobStatus.PENDING, JobStatus.PROCESSING)
        JobStateTransitionPolicy.validate_transition(JobStatus.PROCESSING, JobStatus.DONE)
        JobStateTransitionPolicy.validate_transition(JobStatus.PROCESSING, JobStatus.PAUSED)
        JobStateTransitionPolicy.validate_transition(JobStatus.PAUSED, JobStatus.PENDING)

        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.DONE, JobStatus.PROCESSING)

    def test_quota_and_retry_policies(self):
        quota = QuotaAllocation(daily_limit=50, daily_pages_used=49)
        self.assertTrue(QuotaPolicy.can_consume(quota, 1))
        self.assertFalse(QuotaPolicy.can_consume(quota, 2))

        self.assertTrue(QuotaPolicy.should_reset_quota(date(2026, 8, 30), date(2026, 9, 1)))
        self.assertFalse(QuotaPolicy.should_reset_quota(date(2026, 9, 1), date(2026, 9, 1)))

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


class TestSecureTokenService(unittest.TestCase):
    def setUp(self):
        self.token_service = SecureTokenService(secret_key="test_secret_32_bytes_long_key_!")

    def test_access_token_creation_and_verification(self):
        token = self.token_service.create_access_token(user_id=42, expires_minutes=60)
        self.assertIsInstance(token, str)
        verified_user_id = self.token_service.verify_access_token(token)
        self.assertEqual(verified_user_id, 42)

    def test_missing_signing_secret_fails_securely(self):
        """Hardening 3d: Missing signing secret raises ValueError."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("infrastructure.security.token_service.BOT_TOKEN", ""):
                with self.assertRaises(ValueError) as ctx:
                    SecureTokenService(secret_key="")
                self.assertIn("Security fatal", str(ctx.exception))

    def test_machine_api_key_creation_and_verification(self):
        """Hardening 3c: Machine API key is created and verified independently."""
        api_key = self.token_service.create_machine_api_key(user_id=77)
        self.assertTrue(api_key.startswith("polpot_key_77_"))
        verified_user_id = self.token_service.verify_machine_api_key(api_key)
        self.assertEqual(verified_user_id, 77)

        # Invalid or tampered keys
        self.assertIsNone(self.token_service.verify_machine_api_key("polpot_key_77_invalid_signature"))
        self.assertIsNone(self.token_service.verify_machine_api_key("invalid_format"))

    def test_invalid_and_tampered_tokens(self):
        self.assertIsNone(self.token_service.verify_access_token("invalid.token"))
        self.assertIsNone(self.token_service.verify_access_token("garbage_string"))

    def test_one_time_exchange_code(self):
        code = self.token_service.create_one_time_exchange_code(user_id=99)
        user_id = self.token_service.exchange_code_for_user_id(code)
        self.assertEqual(user_id, 99)
        self.assertIsNone(self.token_service.exchange_code_for_user_id(code))


class TestAuthServiceHardening(unittest.TestCase):
    def setUp(self):
        self.mock_uow = MagicMock()
        self.mock_uow_factory = MagicMock()
        self.mock_uow_factory.create.return_value.__enter__.return_value = self.mock_uow
        self.mock_uow_factory.create.return_value.__exit__.return_value = None

        self.mock_token_service = MagicMock()
        self.auth_service = AuthService(self.mock_uow_factory, self.mock_token_service)

    def test_existing_username_cannot_be_registered_again(self):
        """Hardening 3a: Existing username cannot be registered again to hijack account."""
        self.mock_uow.users.get_by_username.return_value = User(
            id=10, username="target_victim", is_admin=False
        )

        with self.assertRaises(DomainError) as ctx:
            self.auth_service.register_desktop_user("target_victim")
        self.assertIn("already registered", str(ctx.exception))


class TestApplicationServices(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir))

        self.mock_uow = MagicMock()
        self.mock_uow_factory = MagicMock()
        self.mock_uow_factory.create.return_value.__enter__.return_value = self.mock_uow
        self.mock_uow_factory.create.return_value.__exit__.return_value = None

        self.mock_doc_processor = MagicMock()
        self.mock_notifier = MagicMock()
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
        self.mock_uow.users.increment_daily_pages.assert_called_once_with(1, 1)

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
            ApiSlot(id=1, provider="google", label="Key 1", slot_type="private")
        ]

        saved_job = Job(
            id=10,
            user_id=1,
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
            user_id=1,
            file_name="doc.pdf",
            file_path=handle.uri,
            total_pages=2,
            processed_pages=0,
            prompt_text="Extract Markdown",
            api_chain=[
                ApiSlot(id=1, provider="google", label="Slot 1", slot_type="private"),
            ],
            current_api_index=0,
            status=JobStatus.PENDING,
            auto_pipeline2=True,
            pipeline2_prompt_id=5,
        )
        self.mock_uow.jobs.get_next_pending.return_value = job

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


class TestFastAPIRestBoundary(unittest.TestCase):
    """
    Integration tests for the FastAPI REST API boundary serving Qt Desktop and web clients.
    """

    def setUp(self):
        self.mock_container = MagicMock()
        app.dependency_overrides[get_container] = lambda: self.mock_container

        self.client = TestClient(app)
        self.token_service = SecureTokenService(secret_key="test_secret_key_for_api_suite_")
        self.test_token = self.token_service.create_access_token(user_id=1)
        self.auth_headers = {"Authorization": f"Bearer {self.test_token}"}

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health_check_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_get_current_user_profile(self):
        self.mock_container.auth_service.authenticate_token.return_value = User(
            id=1, telegram_id=123, username="qt_user", quota=QuotaAllocation(daily_limit=50, daily_pages_used=2)
        )
        self.mock_container.user_service.get_user_by_id.return_value = {
            "id": 1,
            "telegram_id": 123,
            "username": "qt_user",
            "is_admin": False,
            "daily_pages_used": 2,
            "daily_limit": 50,
            "remaining_pages": 48,
            "use_public_fallback": True,
            "auto_retry": False,
            "auto_pipeline2": False,
            "default_prompt_id": None,
            "default_pipeline2_prompt_id": None,
        }

        resp = self.client.get("/api/v1/users/me", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "qt_user")
        self.assertEqual(resp.json()["remaining_pages"], 48)

    def test_x_api_key_behavior(self):
        """Hardening 3c & 9c: Machine API Key header x-api-key is verified explicitly via authenticate_api_key."""
        self.mock_container.auth_service.authenticate_api_key.return_value = User(
            id=1, username="api_machine"
        )
        self.mock_container.user_service.get_user_by_id.return_value = {
            "id": 1,
            "telegram_id": None,
            "username": "api_machine",
            "is_admin": False,
            "daily_pages_used": 0,
            "daily_limit": 50,
            "remaining_pages": 50,
            "use_public_fallback": True,
            "auto_retry": False,
            "auto_pipeline2": False,
            "default_prompt_id": None,
            "default_pipeline2_prompt_id": None,
        }

        api_key = self.token_service.create_machine_api_key(user_id=1)
        headers = {"X-API-Key": api_key}
        resp = self.client.get("/api/v1/users/me", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.mock_container.auth_service.authenticate_api_key.assert_called_once_with(api_key)

        # Invalid format rejected
        self.mock_container.auth_service.authenticate_api_key.side_effect = AuthenticationError("Invalid machine API key.")
        resp_invalid = self.client.get("/api/v1/users/me", headers={"X-API-Key": "invalid_raw_key"})
        self.assertEqual(resp_invalid.status_code, 401)


    def test_rfc_7807_problem_json_response(self):
        """Hardening 5: Errors return application/problem+json RFC 7807 formatted body."""
        resp = self.client.get("/api/v1/users/me")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("application/problem+json", resp.headers["content-type"])
        body = resp.json()
        self.assertIn("type", body)
        self.assertIn("title", body)
        self.assertEqual(body["status"], 401)
        self.assertIn("detail", body)
        self.assertEqual(body["instance"], "/api/v1/users/me")

    def test_rest_routes_do_not_access_repositories_directly(self):
        """Hardening 4 & 9e: Jobs routes call Application Services rather than uow_factory."""
        self.mock_container.auth_service.authenticate_token.return_value = User(id=1)
        self.mock_container.job_query_service.list_user_jobs.return_value = []

        resp = self.client.get("/api/v1/jobs", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        self.mock_container.job_query_service.list_user_jobs.assert_called_once_with(1, 50, 0)
        # uow_factory must not have been called by the route
        self.mock_container.uow_factory.assert_not_called()



if __name__ == "__main__":
    unittest.main()
