# ============================================================
#  infrastructure/rate_limiting/rate_limiter_adapter.py
# ============================================================

from application.ports.rate_limiter import IRateLimiter
from core.entities.api_slot import ApiSlot
from utils.rate_limiter import (
    wait_if_needed as legacy_wait_if_needed,
    mark_request_sent as legacy_mark_request_sent,
)


class RateLimiterAdapter(IRateLimiter):
    """
    Infrastructure adapter for request rate limiting leveraging the atomic rate limiter.
    """

    def wait_if_needed(self, slot: ApiSlot) -> None:
        legacy_wait_if_needed(slot.to_dict())

    def mark_request_sent(self, slot: ApiSlot) -> None:
        legacy_mark_request_sent(slot.to_dict())
