# ============================================================
#  core/policies/retry_policy.py
# ============================================================

class RetryPolicy:
    """
    Policies governing job retry eligibility and recovery limits.
    """
    MAX_AUTO_RETRIES: int = 3

    @classmethod
    def is_eligible_for_retry(cls, current_retries: int) -> bool:
        """Checks whether a job remains eligible for retry under the maximum retry threshold."""
        return current_retries < cls.MAX_AUTO_RETRIES
