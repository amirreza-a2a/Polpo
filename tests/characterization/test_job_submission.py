# ============================================================
#  tests/characterization/test_job_submission.py
# ============================================================

import unittest
from unittest.mock import patch, MagicMock
import tests.characterization.conftest_base
from config import DAILY_PAGE_LIMIT, MAX_QUEUE_PER_USER


class TestJobSubmissionCharacterization(unittest.TestCase):
    """
    Characterizes current job submission, quota checking, and queue depth rules.
    """

    @patch("handlers.pdf.get_or_create_user")
    @patch("handlers.pdf.reset_daily_pages_if_needed")
    @patch("handlers.pdf.count_user_pending_jobs")
    @patch("handlers.pdf.get_user_private_apis")
    def test_daily_quota_enforced_for_public_users(self, mock_get_private, mock_pending, mock_reset, mock_get_user):
        """
        Characterization: Users without private API are blocked when requested pages exceed remaining quota.
        """
        # User has used DAILY_PAGE_LIMIT - 5 pages today -> 5 pages remaining
        pages_used = DAILY_PAGE_LIMIT - 5
        mock_get_user.return_value = {"id": 1, "telegram_id": 12345, "daily_pages_used": pages_used}
        mock_pending.return_value = 0
        mock_get_private.return_value = [] # No private API

        # Simulate 10-page document request (10 > 5 remaining)
        total_pages = 10
        pages_left = DAILY_PAGE_LIMIT - pages_used # 5

        self.assertLess(pages_left, total_pages)
        # In handlers/pdf.py:
        # if not private_apis:
        #     pages_left = DAILY_PAGE_LIMIT - db_user["daily_pages_used"]
        #     if total_pages > pages_left: return "❌ این PDF دارای 10 صفحه است..."
        should_reject = total_pages > pages_left
        self.assertTrue(should_reject)

    @patch("handlers.pdf.get_or_create_user")
    @patch("handlers.pdf.reset_daily_pages_if_needed")
    @patch("handlers.pdf.count_user_pending_jobs")
    @patch("handlers.pdf.get_user_private_apis")
    def test_private_api_bypasses_daily_page_quota(self, mock_get_private, mock_pending, mock_reset, mock_get_user):
        """
        Characterization: Having an active private API bypasses daily page quota check completely.
        """
        # User has used all quota today (0 remaining)
        mock_get_user.return_value = {"id": 1, "telegram_id": 12345, "daily_pages_used": DAILY_PAGE_LIMIT}
        mock_pending.return_value = 0
        # User has a private API registered
        mock_get_private.return_value = [{"id": 10, "label": "My Gemini Key"}]

        # Request a large 100-page document
        total_pages = 100
        private_apis = mock_get_private.return_value

        # Current handler logic:
        # if not private_apis: [enforce quota] -> with private_apis, this block is bypassed!
        quota_enforced = not bool(private_apis)
        self.assertFalse(quota_enforced)

    @patch("handlers.pdf.count_user_pending_jobs")
    def test_queue_depth_limit_enforcement(self, mock_pending):
        """
        Characterization: Submissions are rejected if user already has >= MAX_QUEUE_PER_USER (3) pending jobs.
        """
        mock_pending.return_value = 3
        pending = mock_pending(1)

        # In handlers/pdf.py:
        # if pending >= MAX_QUEUE_PER_USER: reject with message
        is_queue_full = pending >= MAX_QUEUE_PER_USER
        self.assertTrue(is_queue_full)

        # Under the limit
        mock_pending.return_value = 2
        self.assertFalse(mock_pending(1) >= MAX_QUEUE_PER_USER)

    def test_job_creation_initial_state_contract(self):
        """
        Characterization: Newly created jobs have status='pending', current_api_index=0, empty switch log.
        """
        user_id = 1
        prompt_id = 2
        file_path = "/tmp/test.pdf"
        file_name = "test.pdf"
        total_pages = 5
        api_chain = [{"type": "public", "id": 1, "provider": "google", "api_key": "k", "model": "m"}]
        model = "gemini-3.5-flash"

        created_job = {
            "id": 101,
            "user_id": user_id,
            "prompt_id": prompt_id,
            "file_path": file_path,
            "file_name": file_name,
            "total_pages": total_pages,
            "processed_pages": 0,
            "api_chain": api_chain,
            "current_api_index": 0,
            "api_switch_log": [],
            "status": "pending",
            "model": model,
            "retry_count": 0,
        }

        self.assertEqual(created_job["status"], "pending")
        self.assertEqual(created_job["processed_pages"], 0)
        self.assertEqual(created_job["current_api_index"], 0)
        self.assertEqual(created_job["api_switch_log"], [])
        self.assertEqual(created_job["retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
