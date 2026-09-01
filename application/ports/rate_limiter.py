# ============================================================
#  application/ports/rate_limiter.py
# ============================================================

from abc import ABC, abstractmethod
from core.entities.api_slot import ApiSlot


class IRateLimiter(ABC):
    """
    Port for outbound AI provider rate limiting and throttle control.
    Supports atomic sliding-window capacity reservations under concurrency.
    """

    @abstractmethod
    def wait_if_needed(self, slot: ApiSlot) -> None:
        """
        Atomically checks capacity and reserves a dispatch window for the given API slot.
        If current requests exceed configured RPM in the sliding window, sleeps until
        the earliest reserved position becomes active.
        """
        pass

    @abstractmethod
    def mark_request_sent(self, slot: ApiSlot) -> None:
        """
        Confirms or records the outbound request timestamp for sliding-window accounting.
        """
        pass
