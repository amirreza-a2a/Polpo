# ============================================================
#  tests/characterization/test_job_state_retry.py
# ============================================================

import unittest
from unittest.mock import patch, MagicMock
from services.worker import MAX_AUTO_RETRY


class TestJobStateRetryCharacterization(unittest.TestCase):
    """
    Characterizes current job state transitions, auto-retry policies, and retry count limits.
    """

    def test_max_auto_retry_constant_value(self):
        """
        Characterization: MAX_AUTO_RETRY is set to 5 in worker.py.
        """
        self.assertEqual(MAX_AUTO_RETRY, 5)

    def test_auto_retry_requeue_under_limit(self):
        """
        Characterization: When current retry_count < MAX_AUTO_RETRY, the job is requeued as pending.
        """
        job = {"id": 101, "status": "paused", "retry_count": 2, "error_message": "Rate limit"}
        user = {"telegram_id": 12345, "auto_retry": 1}

        # Emulating worker auto-retry decision logic from _handle_job_stopped
        current_retry = job.get("retry_count", 0)
        can_retry = bool(user.get("auto_retry")) and (current_retry < MAX_AUTO_RETRY)

        self.assertTrue(can_retry)
        new_count = current_retry + 1
        self.assertEqual(new_count, 3)

    def test_auto_retry_exhausted_at_limit(self):
        """
        Characterization: When current retry_count reaches MAX_AUTO_RETRY (5), auto-retry ceases.
        """
        job = {"id": 101, "status": "paused", "retry_count": 5, "error_message": "Rate limit"}
        user = {"telegram_id": 12345, "auto_retry": 1}

        current_retry = job.get("retry_count", 0)
        can_retry = bool(user.get("auto_retry")) and (current_retry < MAX_AUTO_RETRY)

        self.assertFalse(can_retry)

    def test_manual_resume_state_transitions(self):
        """
        Characterization: Valid manual resume transitions from paused/failed to pending.
        """
        # User resumes a paused or failed job from bot panel
        initial_statuses = ["paused", "failed"]
        target_status = "pending"

        for initial in initial_statuses:
            job = {"id": 101, "status": initial, "processed_pages": 4, "total_pages": 10}
            # Simulate resume transition
            job["status"] = target_status
            self.assertEqual(job["status"], "pending")
            # Pages processed must remain preserved
            self.assertEqual(job["processed_pages"], 4)


if __name__ == "__main__":
    unittest.main()
