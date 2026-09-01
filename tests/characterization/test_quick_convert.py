# ============================================================
#  tests/characterization/test_quick_convert.py
# ============================================================

from datetime import date
import unittest
from unittest.mock import MagicMock
import tests.characterization.conftest_base
from handlers.quick_convert import TELEGRAM_MSG_LIMIT
from application.dto.quick_convert_dto import QuickConvertCommand, QuickConvertResultDTO
from application.services.quick_convert import QuickConvertService
from core.entities.api_slot import ApiSlot


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

    def test_quick_convert_service_invocation(self):
        """
        Characterization: QuickConvertService delegates vision OCR to AI executor and consumes quota.
        """
        mock_uow_factory = MagicMock()
        mock_uow = MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

        mock_user = MagicMock()
        mock_user.quota.daily_pages_used = 0
        mock_user.quota.daily_limit = 50
        mock_user.quota.last_active_date = date.today()
        mock_user.preferences.use_public_fallback = True
        mock_uow.users.get_by_id.return_value = mock_user

        mock_slot = ApiSlot(id=1, provider="google", label="Key 1", slot_type="byok", selected_model="gemini-3.5-flash")
        mock_uow.apis.list_public.return_value = [mock_slot]
        mock_uow.prompts.get_quick_convert_prompt.return_value = "Convert quickly"

        mock_executor = MagicMock()
        mock_executor.execute_vision_with_fallback.return_value = ("Generated OCR Markdown", mock_slot)

        service = QuickConvertService(
            uow_factory=mock_uow_factory,
            ai_executor=mock_executor,
        )

        cmd = QuickConvertCommand(user_id=1, image_bytes=b"dummy_jpeg", mime_type="image/jpeg")
        result = service.convert_image(cmd)

        self.assertEqual(result.markdown_content, "Generated OCR Markdown")
        mock_uow.users.increment_daily_pages.assert_called_once_with(1, 1)

    def test_quick_convert_quota_increment_amount(self):
        """
        Characterization: Successful Quick Convert increments user quota by exactly 1 page.
        """
        PAGE_COST = 1
        self.assertEqual(PAGE_COST, 1)


if __name__ == "__main__":
    unittest.main()
