# ============================================================
#  tests/unit/test_ai_adapters.py
# ============================================================

import io
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from PIL import Image

import tests.characterization.conftest_base
from application.ports.ai_provider import AIProviderPort
from core.entities.api_slot import ApiSlot
from core.ai.types import VisionPromptRequest, TextPromptRequest, AIResponse
import core.ai.types as core_ai_types
import core.ai.exceptions as core_ai_exceptions
from core.ai.exceptions import (
    AIError,
    AIRetryableError,
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
    AINonRetryableError,
    AIAuthenticationError,
    AIModelNotFoundError,
    AIContentFilterError,
    AIChainExhaustedError,
)
from infrastructure.ai.google_adapter import GoogleAdapter
from infrastructure.ai.openai_adapter import OpenAIAdapter
from infrastructure.ai.factory import create_ai_adapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from handlers.quick_convert import handle_quick_photo


class TestCoreAIIndependence(unittest.TestCase):
    """
    Verifies that core/ai contains zero external framework, SDK, or image library dependencies.
    """

    def test_core_ai_has_no_external_library_imports(self):
        """Verify core/ai/types.py and core/ai/exceptions.py only import standard library modules."""
        forbidden_modules = {"PIL", "google", "openai", "telegram", "fastapi", "pymysql", "fitz"}

        for mod in (core_ai_types, core_ai_exceptions):
            module_source = open(mod.__file__, "r", encoding="utf-8").read()
            for forbidden in forbidden_modules:
                self.assertNotIn(
                    f"import {forbidden}",
                    module_source,
                    f"Core module '{mod.__name__}' must not import '{forbidden}'"
                )
                self.assertNotIn(
                    f"from {forbidden}",
                    module_source,
                    f"Core module '{mod.__name__}' must not import from '{forbidden}'"
                )

    def test_vision_prompt_request_contract(self):
        dummy_bytes = b"\xff\xd8\xff\xe0"  # JPEG header bytes
        req = VisionPromptRequest(prompt="Extract text", image_bytes=dummy_bytes, mime_type="image/jpeg")
        self.assertEqual(req.prompt, "Extract text")
        self.assertEqual(req.image_bytes, dummy_bytes)
        self.assertEqual(req.mime_type, "image/jpeg")
        self.assertEqual(req.timeout, 120.0)

    def test_text_prompt_request_contract(self):
        req = TextPromptRequest(prompt="Summarize this")
        self.assertEqual(req.prompt, "Summarize this")
        self.assertEqual(req.timeout, 120.0)

    def test_api_slot_entity_creation_and_serialization(self):
        data = {
            "id": 5,
            "type": "byok",
            "provider": "google",
            "label": "My Key",
            "selected_model": "gemini-3.5-flash",
            "base_url": None,
            "models": ["gemini-3.5-flash"],
        }
        slot = ApiSlot.from_dict(data)
        self.assertEqual(slot.id, 5)
        self.assertEqual(slot.provider, "google")
        self.assertEqual(slot.slot_type, "byok")
        self.assertEqual(slot.selected_model, "gemini-3.5-flash")

        serialized = slot.to_dict()
        self.assertEqual(serialized["id"], 5)
        self.assertEqual(serialized["type"], "byok")
        self.assertEqual(serialized["provider"], "google")


class TestGoogleAdapter(unittest.TestCase):
    """
    Tests for GoogleAdapter dispatch and normalized exception mapping.
    """

    def test_implements_ai_provider_port(self):
        adapter = GoogleAdapter(api_key="test_key")
        self.assertIsInstance(adapter, AIProviderPort)

    @patch("google.genai.Client")
    def test_google_adapter_vision_dispatch(self, mock_client_cls):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "# Page Title\nPage content"
        mock_client.models.generate_content.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        adapter = GoogleAdapter(api_key="test_google_key", default_model="gemini-3.5-flash")
        dummy_bytes = b"fake_jpeg_bytes"
        req = VisionPromptRequest(prompt="Convert page", image_bytes=dummy_bytes, mime_type="image/jpeg")

        resp = adapter.generate_vision(req)

        self.assertEqual(resp.content, "# Page Title\nPage content")
        self.assertEqual(resp.model, "gemini-3.5-flash")
        mock_client_cls.assert_called_once_with(api_key="test_google_key")
        self.assertTrue(mock_client.models.generate_content.called)

    @patch("google.genai.Client")
    def test_google_adapter_text_dispatch(self, mock_client_cls):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "Refined markdown"
        mock_client.models.generate_content.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        adapter = GoogleAdapter(api_key="test_google_key", default_model="gemini-3.5-flash")
        req = TextPromptRequest(prompt="Fix formatting")

        resp = adapter.generate_text(req)

        self.assertEqual(resp.content, "Refined markdown")
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-3.5-flash",
            contents=["Fix formatting"]
        )

    @patch("google.genai.Client")
    def test_google_adapter_rate_limit_normalization(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("429 ResourceExhausted: Quota exceeded")
        mock_client_cls.return_value = mock_client

        adapter = GoogleAdapter(api_key="test_google_key", default_model="gemini-3.5-flash")
        dummy_bytes = b"fake_bytes"

        with self.assertRaises(AIRateLimitError) as ctx:
            adapter.generate_vision(VisionPromptRequest(prompt="OCR", image_bytes=dummy_bytes))

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.provider, "google")

    @patch("google.genai.Client")
    def test_google_adapter_auth_error_normalization(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("403 PermissionDenied: API key invalid")
        mock_client_cls.return_value = mock_client

        adapter = GoogleAdapter(api_key="test_google_key")
        dummy_bytes = b"fake_bytes"

        with self.assertRaises(AIAuthenticationError) as ctx:
            adapter.generate_vision(VisionPromptRequest(prompt="OCR", image_bytes=dummy_bytes))

        self.assertEqual(ctx.exception.provider, "google")


class TestOpenAIAdapter(unittest.TestCase):
    """
    Tests for OpenAIAdapter (OpenAI & OpenRouter) dispatch, consistent MIME encoding, and error normalization.
    """

    def test_implements_ai_provider_port(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        self.assertIsInstance(adapter, AIProviderPort)

    @patch("openai.OpenAI")
    def test_openai_adapter_vision_dispatch_and_mime_consistency(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI Vision Result"
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        mock_client.chat.completions.create.return_value = mock_resp
        mock_openai_cls.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test", default_model="gpt-4o")
        dummy_bytes = b"sample_jpeg_content"
        req = VisionPromptRequest(prompt="Transcribe image", image_bytes=dummy_bytes, mime_type="image/jpeg")

        resp = adapter.generate_vision(req)

        self.assertEqual(resp.content, "OpenAI Vision Result")
        self.assertEqual(resp.model, "gpt-4o")

        # Verify MIME type in base64 URL matches request.mime_type
        call_args = mock_client.chat.completions.create.call_args[1]
        msg_content = call_args["messages"][0]["content"]
        image_url_obj = [c for c in msg_content if c.get("type") == "image_url"][0]
        self.assertTrue(image_url_obj["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    @patch("openai.OpenAI")
    def test_openrouter_adapter_text_dispatch(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenRouter Refined Text"
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        mock_client.chat.completions.create.return_value = mock_resp
        mock_openai_cls.return_value = mock_client

        adapter = create_ai_adapter(
            provider="openrouter",
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-4o",
        )
        self.assertIsInstance(adapter, OpenAIAdapter)
        self.assertEqual(adapter.base_url, "https://openrouter.ai/api/v1")

        resp = adapter.generate_text(TextPromptRequest(prompt="Improve text"))

        self.assertEqual(resp.content, "OpenRouter Refined Text")
        mock_openai_cls.assert_called_once_with(
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
            timeout=120.0,
        )

    @patch("openai.OpenAI")
    def test_openai_rate_limit_normalization(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("RateLimitError: 429 Too Many Requests")
        mock_openai_cls.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test", default_model="gpt-4o")

        with self.assertRaises(AIRateLimitError) as ctx:
            adapter.generate_text(TextPromptRequest(prompt="Test"))

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.provider, "openai")

    def test_create_ai_adapter_keyword_compatibility(self):
        """Verifies create_ai_adapter works seamlessly with both default_model and model keywords."""
        # 1. Google with default_model
        g_adapter = create_ai_adapter(
            provider="google",
            api_key="fake-google-key",
            default_model="gemini-2.0-flash",
        )
        self.assertIsInstance(g_adapter, GoogleAdapter)
        self.assertEqual(g_adapter.default_model, "gemini-2.0-flash")

        # 2. OpenAI with default_model
        o_adapter = create_ai_adapter(
            provider="openai",
            api_key="fake-openai-key",
            default_model="gpt-4o",
        )
        self.assertIsInstance(o_adapter, OpenAIAdapter)
        self.assertEqual(o_adapter.default_model, "gpt-4o")

        # 3. Model keyword fallback
        g_adapter_legacy = create_ai_adapter(
            provider="google",
            api_key="fake-google-key",
            model="gemini-1.5-pro",
        )
        self.assertEqual(g_adapter_legacy.default_model, "gemini-1.5-pro")


class TestAIExecutionPolicy(unittest.TestCase):
    """
    Tests for RateLimitedAIExecutor policy, exception propagation, multi-slot fallback orchestration, and error classification.
    """

    def setUp(self):
        self.mock_limiter = MagicMock()
        self.mock_factory = MagicMock()
        self.executor = RateLimitedAIExecutor(
            rate_limiter=self.mock_limiter,
            adapter_factory=self.mock_factory,
        )

    def test_execute_vision_with_fallback_success_on_first_slot(self):
        mock_adapter = MagicMock()
        mock_adapter.generate_vision.return_value = AIResponse(content="Page 1 Markdown")
        self.mock_factory.return_value = mock_adapter

        slot = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        chain = [slot]

        content, active_slot = self.executor.execute_vision_with_fallback(chain, b"dummy_bytes", "Prompt", at_page=1)

        self.assertEqual(content, "Page 1 Markdown")
        self.assertEqual(active_slot.id, 1)
        self.mock_limiter.wait_if_needed.assert_called_once_with(slot)
        self.mock_limiter.mark_request_sent.assert_called_once_with(slot)

    def test_execute_vision_with_fallback_distinguishes_error_reasons(self):
        mock_adapter_1 = MagicMock()
        mock_adapter_1.generate_vision.side_effect = AIRateLimitError("429 Too Many Requests", provider="google", status_code=429)

        mock_adapter_2 = MagicMock()
        mock_adapter_2.generate_vision.return_value = AIResponse(content="Page 1 Success from Slot 2")

        self.mock_factory.side_effect = [mock_adapter_1, mock_adapter_2]

        slot_1 = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        slot_2 = ApiSlot(id=2, provider="openai", label="Key 2", slot_type="byok")
        chain = [slot_1, slot_2]

        mock_switch_cb = MagicMock()

        content, active_slot = self.executor.execute_vision_with_fallback(
            chain, b"dummy_bytes", "Prompt", at_page=1, on_switch=mock_switch_cb
        )

        self.assertEqual(content, "Page 1 Success from Slot 2")
        self.assertEqual(active_slot.id, 2)
        mock_switch_cb.assert_called_once_with("Key 1", "Key 2", "rate_limit", 1)

    def test_execute_vision_exhausted_chain_returns_none(self):
        mock_adapter = MagicMock()
        mock_adapter.generate_vision.side_effect = AIProviderUnavailableError("503 Service Unavailable", provider="google")
        self.mock_factory.return_value = mock_adapter

        slot = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        chain = [slot]

        content, active_slot = self.executor.execute_vision_with_fallback(chain, b"dummy_bytes", "Prompt", at_page=1)

        self.assertIsNone(content)
        self.assertIsNone(active_slot)

    def test_execute_vision_propagates_non_ai_exceptions(self):
        """
        Verify that unexpected non-AI exceptions (TypeError, KeyError, etc.) propagate unchanged
        and DO NOT trigger fallback switching.
        """
        mock_adapter = MagicMock()
        mock_adapter.generate_vision.side_effect = TypeError("Unexpected argument error")
        self.mock_factory.return_value = mock_adapter

        slot_1 = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        slot_2 = ApiSlot(id=2, provider="openai", label="Key 2", slot_type="byok")
        chain = [slot_1, slot_2]

        with self.assertRaises(TypeError):
            self.executor.execute_vision_with_fallback(chain, b"dummy_bytes", "Prompt", at_page=1)

    def test_execute_text_propagates_non_ai_exceptions(self):
        """
        Verify that unexpected non-AI exceptions in text path propagate unchanged
        and DO NOT trigger fallback switching.
        """
        mock_adapter = MagicMock()
        mock_adapter.generate_text.side_effect = RuntimeError("Fatal system crash")
        self.mock_factory.return_value = mock_adapter

        slot_1 = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok")
        slot_2 = ApiSlot(id=2, provider="openai", label="Key 2", slot_type="byok")
        chain = [slot_1, slot_2]

        with self.assertRaises(RuntimeError):
            self.executor.execute_text_with_fallback(chain, "Prompt", at_page=0)


class TestHandlerQuickConvertDelegation(unittest.TestCase):
    """
    Tests for handle_quick_photo delegation to Application Services.
    """

    @patch("handlers.quick_convert.get_app_container")
    def test_quick_convert_delegates_to_service(self, mock_get_container):
        import asyncio
        mock_container = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 42
        mock_container.user_service.get_or_create_telegram_user.return_value = mock_user
        mock_res_dto = MagicMock()
        mock_res_dto.markdown_content = "# Transcribed Quick Output"
        mock_container.quick_convert_service.convert_image.return_value = mock_res_dto
        mock_get_container.return_value = mock_container

        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_update.message = mock_msg
        mock_photo = MagicMock()
        mock_file = MagicMock()
        mock_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"\xff\xd8\xff\xe0" + b"\x00" * 20))
        mock_photo.get_file = AsyncMock(return_value=mock_file)
        mock_msg.photo = [mock_photo]

        mock_status = AsyncMock()
        mock_msg.reply_text = AsyncMock(return_value=mock_status)
        mock_msg.reply_document = AsyncMock()

        asyncio.run(handle_quick_photo(mock_update, MagicMock()))

        mock_container.quick_convert_service.convert_image.assert_called_once()
        cmd = mock_container.quick_convert_service.convert_image.call_args[0][0]
        self.assertEqual(cmd.user_id, 42)


if __name__ == "__main__":
    unittest.main()
