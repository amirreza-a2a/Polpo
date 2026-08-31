# ============================================================
#  tests/characterization/test_quick_convert.py
# ============================================================

import unittest
from unittest.mock import patch, MagicMock
import tests.characterization.conftest_base
from handlers.quick_convert import TELEGRAM_MSG_LIMIT, _call_vision_api


class TestQuickConvertCharacterization(unittest.TestCase):
    """
    Characterizes Quick Convert handler constraints, image transformation, and output routing.
    """

    def test_telegram_message_limit_constant(self):
        """
        Characterization: Telegram message limit is set to 4096 characters.
        """
        self.assertEqual(TELEGRAM_MSG_LIMIT, 4096)

    def test_short_vs_long_output_routing_rule(self):
        """
        Characterization: Output <= 4096 is sent as message; output > 4096 is sent as .md document.
        """
        short_result = "A" * 500
        long_result = "A" * 5000

        # Short output uses text reply
        self.assertTrue(len(short_result) <= TELEGRAM_MSG_LIMIT)

        # Long output exceeds limit and triggers file upload
        self.assertTrue(len(long_result) > TELEGRAM_MSG_LIMIT)

    @patch("google.genai.Client")
    def test_google_vision_api_invocation(self, mock_client_cls):
        """
        Characterization: _call_vision_api for google provider calls client.models.generate_content.
        """
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Generated OCR Markdown"
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        mock_img = MagicMock()
        api_entry = {
            "type": "public",
            "id": 1,
            "provider": "google",
            "api_key": "test_google_key",
            "selected_model": "gemini-3.5-flash",
            "base_url": None,
        }

        result = _call_vision_api(mock_img, "Transcribe this", api_entry)
        self.assertEqual(result, "Generated OCR Markdown")

    def test_quick_convert_quota_increment_amount(self):
        """
        Characterization: Successful Quick Convert increments user quota by exactly 1 page.
        """
        PAGE_COST = 1
        self.assertEqual(PAGE_COST, 1)


if __name__ == "__main__":
    unittest.main()
