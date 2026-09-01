# ============================================================
#  tests/unit/test_memory_rate_limiter.py
# ============================================================

import time
import threading
import unittest
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from infrastructure.rate_limiting.memory_rate_limiter import ThreadSafeMemoryRateLimiter


class TestMemoryRateLimiter(unittest.TestCase):
    """
    Unit tests for ThreadSafeMemoryRateLimiter verifying sliding window,
    monotonic time intervals, configurable RPMs, and concurrent thread safety.
    """

    def setUp(self):
        self.slot = ApiSlot(
            id=1,
            provider="google",
            label="Test Slot",
            credential_ref=CredentialRef("google_cred_1", "google", "byok"),
            selected_model="gemini-2.5-flash",
        )

    def test_rate_limiter_reserves_on_wait(self):
        """wait_if_needed atomically reserves a slot in the sliding window."""
        limiter = ThreadSafeMemoryRateLimiter(default_rpms={"google": 5})
        limiter.wait_if_needed(self.slot)
        self.assertEqual(limiter.get_history_count(self.slot), 1)

    def test_rate_limiter_records_on_mark_sent(self):
        """mark_request_sent confirms sent timestamp."""
        limiter = ThreadSafeMemoryRateLimiter(default_rpms={"google": 5})
        limiter.mark_request_sent(self.slot)
        limiter.mark_request_sent(self.slot)
        self.assertEqual(limiter.get_history_count(self.slot), 1)

    def test_rate_limiter_configurable_rpm(self):
        """Verify custom RPM dictionary overrides default."""
        limiter = ThreadSafeMemoryRateLimiter(default_rpms={"google": 120, "custom_provider": 10})
        self.assertEqual(limiter._get_rpm(self.slot), 120)

        custom_slot = ApiSlot(
            id=2,
            provider="custom_provider",
            label="Custom Slot",
            credential_ref=CredentialRef("custom_cred_1", "custom_provider", "byok"),
        )
        self.assertEqual(limiter._get_rpm(custom_slot), 10)

    def test_rate_limiter_concurrent_access_thread_safety(self):
        """Verify concurrent worker threads marking requests sent execute safely."""
        limiter = ThreadSafeMemoryRateLimiter(default_rpms={"google": 1000})
        num_threads = 10
        requests_per_thread = 20
        barrier = threading.Barrier(num_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(requests_per_thread):
                    limiter.wait_if_needed(self.slot)
                    limiter.mark_request_sent(self.slot)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(limiter.get_history_count(self.slot), num_threads * requests_per_thread)


if __name__ == "__main__":
    unittest.main()
