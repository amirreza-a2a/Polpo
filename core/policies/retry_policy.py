# ============================================================
#  core/policies/retry_policy.py
# ============================================================

class RetryPolicy:
    """
    سیاست‌های بازآزمایی و بازیابی کارها.
    """
    MAX_AUTO_RETRIES: int = 3

    @classmethod
    def is_eligible_for_retry(cls, current_retries: int) -> bool:
        """بررسی اینکه آیا کار به سقف بازآزمایی خود رسیده است یا خیر."""
        return current_retries < cls.MAX_AUTO_RETRIES
