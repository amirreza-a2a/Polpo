# ============================================================
#  tests/unit/test_phase7_desktop_rest_architecture.py
#  Phase 7 — REST API Boundary & Desktop-First Architecture
# ============================================================

import ast
import io
import os
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from fastapi.testclient import TestClient

import tests.characterization.conftest_base
from interfaces.api.app import app
from interfaces.api.deps import get_current_user, get_current_admin_user, get_container
from application.dto.user_dto import UserDTO
from application.dto.job_dto import JobResponseDTO, JobDetailDTO
from application.dto.prompt_dto import PromptDTO
from application.dto.api_dto import ApiSlotDTO, DetectApiResultDTO
from application.dto.quick_convert_dto import QuickConvertResultDTO
from application.ports.provider_detector import IProviderDetector
from infrastructure.composition import AppContainer


class TestPhase7DesktopRestArchitecture(unittest.TestCase):
    """
    Verifies that the REST API layer satisfies the Desktop-First architectural requirements,
    exposing 100% of domain and application services through typed, secure HTTP endpoints.
    """

    def setUp(self):
        self.mock_container = MagicMock()
        self.client = TestClient(app)

        self.regular_user = UserDTO(
            id=10,
            telegram_id=123456,
            username="desktop_user",
            is_admin=False,
            daily_pages_used=5,
            daily_limit=50,
            remaining_pages=45,
            use_public_fallback=True,
            auto_retry=False,
            auto_pipeline2=False,
        )


        self.admin_user = UserDTO(
            id=1,
            telegram_id=999999,
            username="admin_user",
            is_admin=True,
            daily_pages_used=0,
            daily_limit=1000,
            remaining_pages=1000,
            use_public_fallback=True,
            auto_retry=True,
            auto_pipeline2=True,
        )

        # Override dependencies for deterministic unit testing
        app.dependency_overrides[get_container] = lambda: self.mock_container
        app.dependency_overrides[get_current_user] = lambda: self.regular_user

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health_check_endpoint(self):
        """Verify GET /health returns 200 OK."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("PolpoT REST API", data["app"])

    def test_openapi_schema_generation_and_completeness(self):
        """Verify OpenAPI 3.1 JSON schema is generated and contains all canonical routes."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("paths", schema)
        paths = schema["paths"]

        # Verify key desktop routes are present in OpenAPI schema
        self.assertIn("/api/v1/auth/exchange", paths)
        self.assertIn("/api/v1/auth/register", paths)
        self.assertIn("/api/v1/users/me", paths)
        self.assertIn("/api/v1/users/me/preferences", paths)
        self.assertIn("/api/v1/jobs", paths)
        self.assertIn("/api/v1/jobs/{job_id}", paths)
        self.assertIn("/api/v1/jobs/{job_id}/pipeline2", paths)
        self.assertIn("/api/v1/jobs/{job_id}/resume", paths)
        self.assertIn("/api/v1/jobs/{job_id}/retry", paths)
        self.assertIn("/api/v1/jobs/{job_id}/artifacts/{artifact_type}", paths)
        self.assertIn("/api/v1/jobs/{job_id}/events", paths)
        self.assertIn("/api/v1/quick-convert", paths)
        self.assertIn("/api/v1/prompts", paths)
        self.assertIn("/api/v1/apis", paths)
        self.assertIn("/api/v1/apis/detect", paths)
        self.assertIn("/api/v1/apis/donate", paths)
        self.assertIn("/api/v1/admin/stats", paths)
        self.assertIn("/api/v1/admin/donations", paths)
        self.assertIn("/api/v1/admin/public-apis", paths)

    def test_auth_exchange_endpoint(self):
        """Verify POST /api/v1/auth/exchange exchanges Telegram OTP code for JWT."""
        self.mock_container.auth_service.exchange_code_for_token.return_value = (
            "jwt.token.sample",
            self.regular_user,
        )

        response = self.client.post("/api/v1/auth/exchange", json={"code": "123456"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["access_token"], "jwt.token.sample")
        self.assertEqual(data["user"]["id"], 10)

    def test_auth_register_standalone_desktop_user(self):
        """Verify POST /api/v1/auth/register creates/logs in standalone desktop client user."""
        self.mock_container.auth_service.register_or_login_desktop_user.return_value = (
            "jwt.desktop.sample",
            self.regular_user,
        )

        response = self.client.post("/api/v1/auth/register", json={"username": "desktop_user"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["access_token"], "jwt.desktop.sample")

    def test_get_current_user_profile(self):
        """Verify GET /api/v1/users/me returns UserDTO."""
        self.mock_container.user_service.get_user_by_id.return_value = self.regular_user
        response = self.client.get("/api/v1/users/me")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], 10)
        self.assertEqual(data["username"], "desktop_user")
        self.assertEqual(data["remaining_pages"], 45)

    def test_update_user_preferences(self):
        """Verify PATCH /api/v1/users/me/preferences updates user settings."""
        updated = UserDTO(
            id=10,
            telegram_id=123456,
            username="desktop_user",
            is_admin=False,
            daily_pages_used=5,
            daily_limit=50,
            remaining_pages=45,
            use_public_fallback=False,
            auto_retry=True,
            auto_pipeline2=True,
        )
        self.mock_container.user_service.update_preferences.return_value = updated

        response = self.client.patch(
            "/api/v1/users/me/preferences",
            json={"use_public_fallback": False, "auto_retry": True, "auto_pipeline2": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["use_public_fallback"])
        self.assertTrue(data["auto_retry"])
        self.assertTrue(data["auto_pipeline2"])

    def test_submit_job_multipart_pdf(self):
        """Verify POST /api/v1/jobs submits a PDF document for processing."""
        mock_job_res = JobResponseDTO(
            id=42,
            user_id=10,
            file_name="sample.pdf",
            status="pending",
            total_pages=3,
            processed_pages=0,
            auto_pipeline2=False,
        )
        self.mock_container.job_submission_service.submit_job.return_value = mock_job_res

        pdf_bytes = b"%PDF-1.4 sample pdf content"
        response = self.client.post(
            "/api/v1/jobs",
            files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
            data={"prompt_id": "1", "auto_pipeline2": "false"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["id"], 42)
        self.assertEqual(data["status"], "pending")
        self.mock_container.job_submission_service.submit_job.assert_called_once()

    def test_submit_pipeline2_job_over_rest(self):
        """Verify POST /api/v1/jobs/{id}/pipeline2 triggers Pipeline 2 for a completed job."""
        self.mock_container.job_submission_service.submit_pipeline2_job.return_value = 88

        response = self.client.post(
            "/api/v1/jobs/42/pipeline2",
            json={"prompt_id": 2, "api_chain_ids": [1, 2]},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["pipeline2_job_id"], 88)
        self.assertEqual(data["source_job_id"], 42)
        self.mock_container.job_submission_service.submit_pipeline2_job.assert_called_once_with(
            source_job_id=42,
            user_id=10,
            prompt_id=2,
            api_chain_ids=[1, 2],
        )

    def test_download_artifact_stream(self):
        """Verify GET /api/v1/jobs/{id}/artifacts/{type} streams stored artifact data."""
        sample_stream = io.BytesIO(b"# Transcribed Markdown Document Content")
        self.mock_container.artifact_service.get_job_artifact_stream.return_value = (
            sample_stream,
            "job_42.md",
            "text/markdown",
        )

        response = self.client.get("/api/v1/jobs/42/artifacts/output_markdown")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"# Transcribed Markdown Document Content")
        self.assertEqual(response.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertIn("job_42.md", response.headers["content-disposition"])

    def test_quick_convert_synchronous_rest(self):
        """Verify POST /api/v1/quick-convert returns OCR markdown synchronously."""
        qc_result = QuickConvertResultDTO(
            markdown_content="# OCR Header\nScanned paragraph text.",
            used_api_label="Google Gemini",
            pages_consumed=1,
        )
        self.mock_container.quick_convert_service.convert_image.return_value = qc_result

        img_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        response = self.client.post(
            "/api/v1/quick-convert",
            files={"image": ("photo.jpg", img_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("OCR Header", data["markdown_content"])
        self.assertEqual(data["pages_consumed"], 1)


    def test_detect_api_provider_and_models_over_rest(self):
        """Verify POST /api/v1/apis/detect probes and returns provider and model list."""
        detect_dto = DetectApiResultDTO(
            provider="google",
            models=["gemini-3.5-flash", "gemini-2.5-flash"],
            default_model="gemini-3.5-flash",
            default_base_url=None,
        )
        self.mock_container.api_service.detect_provider_and_models.return_value = detect_dto

        response = self.client.post(
            "/api/v1/apis/detect",
            json={"api_key": "AIzaSyTestKey123"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider"], "google")
        self.assertEqual(data["default_model"], "gemini-3.5-flash")
        self.assertIn("gemini-3.5-flash", data["models"])

    def test_admin_endpoints_enforce_admin_authorization(self):
        """Verify non-admin users receive 403 Forbidden on /api/v1/admin/* routes."""
        # regular_user has is_admin=False
        app.dependency_overrides[get_current_admin_user] = lambda: (_ for _ in ()).throw(
            unittest.mock.MagicMock()
        )
        # Using standard FastAPI dependency behavior
        app.dependency_overrides.pop(get_current_admin_user, None)
        app.dependency_overrides[get_current_user] = lambda: self.regular_user

        response = self.client.get("/api/v1/admin/stats")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Admin privileges required", response.json()["detail"])

    def test_admin_endpoints_success_for_admin_user(self):
        """Verify admin user receives 200 OK on /api/v1/admin/* routes."""
        app.dependency_overrides[get_current_user] = lambda: self.admin_user
        app.dependency_overrides[get_current_admin_user] = lambda: self.admin_user

        self.mock_container.job_query_service.get_today_stats.return_value = {
            "today_jobs": 15,
            "today_pages": 120,
            "active_users": 8,
        }

        response = self.client.get("/api/v1/admin/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["today_jobs"], 15)
        self.assertEqual(data["today_pages"], 120)

    def test_admin_donation_approval_and_rejection_over_rest(self):
        """Verify admin can approve or reject user donations over REST."""
        app.dependency_overrides[get_current_user] = lambda: self.admin_user
        app.dependency_overrides[get_current_admin_user] = lambda: self.admin_user

        approved_slot = ApiSlotDTO(
            id=55,
            provider="openai",
            label="Donated OpenAI Key",
            slot_type="public",
            selected_model="gpt-4o",
            base_url="https://api.openai.com/v1",
        )
        self.mock_container.api_service.approve_donation.return_value = approved_slot
        self.mock_container.api_service.reject_donation.return_value = True

        # Approve
        res_approve = self.client.post(
            "/api/v1/admin/donations/10/approve",
            json={"selected_model": "gpt-4o", "base_url": "https://api.openai.com/v1"},
        )
        self.assertEqual(res_approve.status_code, 200)
        self.assertEqual(res_approve.json()["id"], 55)

        # Reject
        res_reject = self.client.post("/api/v1/admin/donations/11/reject")
        self.assertEqual(res_reject.status_code, 200)
        self.assertEqual(res_reject.json()["message"], "Donation marked as rejected.")

    def test_application_and_core_layers_have_zero_rest_framework_imports(self):
        """
        Verify that core/ and application/ layers have 0 imports from fastapi, starlette, or interfaces.
        """
        root_dir = Path(__file__).resolve().parent.parent.parent
        target_dirs = [root_dir / "core", root_dir / "application"]
        forbidden_frameworks = {"fastapi", "starlette", "interfaces", "telegram"}

        violations = []
        for target_dir in target_dirs:
            for root, _, files in os.walk(target_dir):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    fp = os.path.join(root, f)
                    with open(fp, "r", encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filename=fp)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                for fb in forbidden_frameworks:
                                    if alias.name == fb or alias.name.startswith(fb + "."):
                                        violations.append(f"{fp}: import {alias.name}")
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                for fb in forbidden_frameworks:
                                    if node.module == fb or node.module.startswith(fb + "."):
                                        violations.append(f"{fp}: from {node.module} import ...")

        self.assertEqual(
            violations,
            [],
            f"Core and Application layers must have 0 framework/interface imports: {violations}",
        )

    def test_provider_detector_port_is_implemented_by_infrastructure(self):
        """Verify AIProviderDetector implements IProviderDetector port contract."""
        from infrastructure.ai.provider_detector import AIProviderDetector
        detector = AIProviderDetector()
        self.assertIsInstance(detector, IProviderDetector)
        self.assertEqual(detector.get_default_model("google"), "gemini-3.5-flash")
        self.assertEqual(detector.get_default_model("openai"), "gpt-4o")


if __name__ == "__main__":
    unittest.main()
