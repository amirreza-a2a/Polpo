# ============================================================
#  application/ports/rate_limiter.py
# ============================================================

from abc import ABC, abstractmethod
from core.entities.api_slot import ApiSlot


class IRateLimiter(ABC):
    """
    درگاه کنترل محدودیت نرخ درخواست (Rate Limiting).
    """

    @abstractmethod
    def wait_if_needed(self, slot: ApiSlot) -> None:
        """بررسی وقفه لازم پیش از ارسال درخواست."""
        pass

    @abstractmethod
    def mark_request_sent(self, slot: ApiSlot) -> None:
        """ثبت برچسب زمانی ارسال درخواست جهت تنظیم وقفه‌های آتی."""
        pass
