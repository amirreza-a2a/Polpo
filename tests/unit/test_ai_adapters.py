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
from core.ai.types import ApiSlot, VisionPromptRequest, TextPromptRequest, AIResponse
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
from services.ai_executor import (
    execute_single_vision_request,
    execute_single_text_request,
    execute_vision_with_fallback,
    execute_text_with_fallback,
)
from services.pdf_processor import _process_single_page, process_job
from services.pipeline2_processor import _call_ai_text, process_pipeline2_job
from handlers.quick_convert import _call_vision_api, handle_quick_photo


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

    def test_ai_response_contains_no_raw_sdk_leakage(self):
        """Verify AIResponse contract does not expose raw provider SDK objects."""
        resp = AIResponse(content="Clean Markdown Output", model="gemini-3.5-flash", usage={"tokens": 42})
        self.assertEqual(resp.content, "Clean Markdown Output")
        self.assertEqual(resp.text, "Clean Markdown Output")
        self.assertEqual(resp.model, "gemini-3.5-flash")
        self.assertFalse(hasattr(resp, "raw_response"), "AIResponse must not have a raw_response field")

    def test_ai_exceptions_retain_no_raw_error(self):
        """Verify domain AI exceptions do not store raw SDK exception instances."""
        err = AIRateLimitError("Quota exceeded", provider="google", model="gemini-3.5-flash", status_code=429)
        self.assertEqual(err.message, "Quota exceeded")
        self.assertEqual(err.provider, "google")
        self.assertEqual(err.model, "gemini-3.5-flash")
        self.assertEqual(err.status_code, 429)
        self.assertFalse(hasattr(err, "raw_error"), "AIError must not retain raw_error attribute")


class TestAIContractsAndEntities(unittest.TestCase):
    """
    Tests for transport/provider-neutral AI types, request contracts, and ApiSlot entity.
    """

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
            "type": "private",
            "provider": "google",
            "api_key": "sec_key",
            "label": "My Key",
            "selected_model": "gemini-3.5-flash",
            "base_url": None,
            "models": ["gemini-3.5-flash"],
        }
        slot = ApiSlot.from_dict(data)
        self.assertEqual(slot.id, 5)
        self.assertEqual(slot.provider, "google")
        self.assertEqual(slot.slot_type, "private")
        self.assertEqual(slot.selected_model, "gemini-3.5-flash")

        serialized = slot.to_dict()
        self.assertEqual(serialized["id"], 5)
        self.assertEqual(serialized["type"], "private")
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


class TestAIExecutionPolicy(unittest.TestCase):
    """
    Tests for execution policy, exception propagation, multi-slot fallback orchestration, and error classification.
    """

    @patch("services.ai_executor.create_ai_adapter")
    def test_single_vision_request_propagates_normalized_exception(self, mock_create):
        mock_adapter = MagicMock(spec=AIProviderPort)
        mock_adapter.generate_vision.side_effect = AIAuthenticationError("Invalid Key", provider="google", status_code=401)
        mock_create.return_value = mock_adapter

        slot = ApiSlot(id=1, provider="google", api_key="bad_key")
        req = VisionPromptRequest(prompt="OCR", image_bytes=b"dummy")

        with self.assertRaises(AIAuthenticationError):
            execute_single_vision_request(req, slot)

    @patch("services.ai_executor.create_ai_adapter")
    def test_single_text_request_propagates_normalized_exception(self, mock_create):
        mock_adapter = MagicMock(spec=AIProviderPort)
        mock_adapter.generate_text.side_effect = AIRateLimitError("Quota reached", provider="openai", status_code=429)
        mock_create.return_value = mock_adapter

        slot = ApiSlot(id=2, provider="openai", api_key="key")
        req = TextPromptRequest(prompt="Refine")

        with self.assertRaises(AIRateLimitError):
            execute_single_text_request(req, slot)

    @patch("services.ai_executor.wait_if_needed")
    @patch("services.ai_executor.mark_request_sent")
    @patch("services.ai_executor.execute_single_vision_request")
    def test_execute_vision_with_fallback_success_on_first_slot(self, mock_execute, mock_mark, mock_wait):
        mock_execute.return_value = AIResponse(content="Page 1 Markdown")

        job = {
            "id": 1,
            "user_id": 100,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"},
            ],
            "api_switch_log": [],
        }

        content, active_api = execute_vision_with_fallback(job, b"dummy_bytes", "Prompt", at_page=1)

        self.assertEqual(content, "Page 1 Markdown")
        self.assertEqual(active_api["id"], 1)
        mock_wait.assert_called_once()
        mock_mark.assert_called_once()

    @patch("services.ai_executor.wait_if_needed")
    @patch("services.ai_executor.mark_request_sent")
    @patch("services.ai_executor.execute_single_vision_request")
    @patch("services.ai_executor.switch_to_next_api")
    def test_execute_vision_with_fallback_distinguishes_error_reasons(self, mock_switch, mock_execute, mock_mark, mock_wait):
        # Slot 1 fails with AIRateLimitError, Slot 2 succeeds
        mock_execute.side_effect = [
            AIRateLimitError("429 Too Many Requests", provider="google", status_code=429),
            AIResponse(content="Page 1 Success from Slot 2"),
        ]

        slot_1 = {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"}
        slot_2 = {"type": "public", "id": 2, "provider": "openai", "api_key": "k2", "label": "Key 2"}

        job = {
            "id": 1,
            "user_id": 100,
            "current_api_index": 0,
            "api_chain": [slot_1, slot_2],
            "api_switch_log": [],
        }
        mock_switch.return_value = slot_2
        mock_notify = MagicMock()

        content, active_api = execute_vision_with_fallback(
            job, b"dummy_bytes", "Prompt", at_page=1, notify_switch_callback=mock_notify
        )

        self.assertEqual(content, "Page 1 Success from Slot 2")
        self.assertEqual(active_api["id"], 2)
        # Reason must be "rate_limit" based on exception classification
        mock_switch.assert_called_once_with(job, reason="rate_limit", at_page=1)
        mock_notify.assert_called_once_with(100, "Key 1", "Key 2")

    @patch("services.ai_executor.wait_if_needed")
    @patch("services.ai_executor.mark_request_sent")
    @patch("services.ai_executor.execute_single_vision_request")
    @patch("services.ai_executor.switch_to_next_api")
    def test_execute_vision_exhausted_chain_returns_none(self, mock_switch, mock_execute, mock_mark, mock_wait):
        mock_execute.side_effect = AIProviderUnavailableError("503 Service Unavailable", provider="google")
        mock_switch.return_value = None  # No more slots

        job = {
            "id": 1,
            "user_id": 100,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"},
            ],
            "api_switch_log": [],
        }

        content, active_api = execute_vision_with_fallback(job, b"dummy_bytes", "Prompt", at_page=1)

        self.assertIsNone(content)
        self.assertIsNone(active_api)

    @patch("services.ai_executor.wait_if_needed")
    @patch("services.ai_executor.mark_request_sent")
    @patch("services.ai_executor.execute_single_vision_request")
    @patch("services.ai_executor.switch_to_next_api")
    def test_execute_vision_propagates_non_ai_exceptions(self, mock_switch, mock_execute, mock_mark, mock_wait):
        """
        Verify that unexpected non-AI exceptions (TypeError, KeyError, etc.) propagate unchanged
        and DO NOT trigger fallback switching.
        """
        mock_execute.side_effect = TypeError("Unexpected argument error")

        job = {
            "id": 1,
            "user_id": 100,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"},
                {"type": "public", "id": 2, "provider": "openai", "api_key": "k2", "label": "Key 2"},
            ],
            "api_switch_log": [],
        }

        with self.assertRaises(TypeError):
            execute_vision_with_fallback(job, b"dummy_bytes", "Prompt", at_page=1)

        # switch_to_next_api must NOT have been called
        mock_switch.assert_not_called()

    @patch("services.ai_executor.wait_if_needed")
    @patch("services.ai_executor.mark_request_sent")
    @patch("services.ai_executor.execute_single_text_request")
    @patch("services.ai_executor.switch_to_next_api")
    def test_execute_text_propagates_non_ai_exceptions(self, mock_switch, mock_execute, mock_mark, mock_wait):
        """
        Verify that unexpected non-AI exceptions in text path propagate unchanged
        and DO NOT trigger fallback switching.
        """
        mock_execute.side_effect = RuntimeError("Fatal system crash")

        job = {
            "id": 1,
            "user_id": 100,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"},
                {"type": "public", "id": 2, "provider": "openai", "api_key": "k2", "label": "Key 2"},
            ],
            "api_switch_log": [],
        }

        with self.assertRaises(RuntimeError):
            execute_text_with_fallback(job, "Prompt", at_page=0)

        # switch_to_next_api must NOT have been called
        mock_switch.assert_not_called()


class TestProcessorPropagationBoundary(unittest.TestCase):
    """
    Tests proving that PDF processor, Pipeline2 processor, and Quick Convert
    propagate non-AI exceptions (RuntimeError, TypeError, KeyError) without swallowing them into None.
    """

    @patch("services.pdf_processor.execute_single_vision_request")
    def test_pdf_processor_propagates_non_ai_exceptions(self, mock_vision_exec):
        mock_vision_exec.side_effect = RuntimeError("PDF Vision Internal Serialization Bug")
        img = Image.new("RGB", (10, 10))
        api_entry = {"id": 1, "provider": "google", "api_key": "k", "selected_model": "gemini-3.5-flash"}

        with self.assertRaises(RuntimeError) as ctx:
            _process_single_page(img, "Prompt", api_entry)
        self.assertIn("PDF Vision Internal Serialization Bug", str(ctx.exception))

    @patch("services.ai_executor.execute_single_text_request")
    def test_pipeline2_processor_propagates_non_ai_exceptions(self, mock_text_exec):
        mock_text_exec.side_effect = TypeError("Pipeline2 Type Misconfiguration")
        api_entry = {"id": 1, "provider": "openai", "api_key": "k", "selected_model": "gpt-4o"}

        with self.assertRaises(TypeError) as ctx:
            _call_ai_text("Sample Document Text", "Refine Prompt", api_entry)
        self.assertIn("Pipeline2 Type Misconfiguration", str(ctx.exception))

    @patch("handlers.quick_convert.execute_single_vision_request")
    def test_quick_convert_propagates_non_ai_exceptions(self, mock_vision_exec):
        mock_vision_exec.side_effect = KeyError("Missing required internal field")
        img = Image.new("RGB", (10, 10))
        api_entry = {"id": 1, "provider": "google", "api_key": "k", "selected_model": "gemini-3.5-flash"}

        with self.assertRaises(KeyError):
            _call_vision_api(img, "Prompt", api_entry)

    @patch("handlers.quick_convert.increment_user_pages")
    @patch("handlers.quick_convert.execute_vision_with_fallback")
    @patch("handlers.quick_convert.build_api_chain")
    @patch("handlers.quick_convert.get_quick_convert_prompt")
    @patch("handlers.quick_convert.reset_daily_pages_if_needed")
    @patch("handlers.quick_convert.get_or_create_user")
    def test_quick_convert_delegates_fallback_to_ai_executor(
        self, mock_get_user, mock_reset, mock_prompt, mock_chain, mock_exec_fallback, mock_incr
    ):
        """
        Verify that handle_quick_photo delegates multi-slot fallback to execute_vision_with_fallback
        and does not implement an independent fallback loop.
        """
        import asyncio
        mock_get_user.return_value = {"id": 42, "daily_pages_used": 0, "use_public_fallback": False}
        mock_prompt.return_value = "Convert quickly"
        mock_chain.return_value = [
            {"id": 1, "provider": "google", "api_key": "k1", "label": "Key 1"},
            {"id": 2, "provider": "openai", "api_key": "k2", "label": "Key 2"},
        ]
        mock_exec_fallback.return_value = ("# Transcribed Quick Output", {"id": 2, "label": "Key 2"})

        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_update.message = mock_msg
        mock_photo = MagicMock()
        mock_file = AsyncMock()
        mock_file.download_as_bytearray.return_value = bytearray(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        mock_photo.get_file = AsyncMock(return_value=mock_file)
        mock_msg.photo = [mock_photo]

        mock_status = AsyncMock()
        mock_msg.reply_text = AsyncMock(return_value=mock_status)
        mock_msg.reply_document = AsyncMock()

        asyncio.run(handle_quick_photo(mock_update, MagicMock()))

        # execute_vision_with_fallback must be called exactly once
        mock_exec_fallback.assert_called_once()
        call_kwargs = mock_exec_fallback.call_args[1]
        self.assertEqual(call_kwargs["prompt"], "Convert quickly")
        self.assertEqual(call_kwargs["job"]["api_chain"], mock_chain.return_value)
        mock_incr.assert_called_once_with(42, 1)


if __name__ == "__main__":
    unittest.main()
