# ============================================================
#  application/ports/ai_provider.py
# ============================================================

from abc import ABC, abstractmethod
from core.ai.types import VisionPromptRequest, TextPromptRequest, AIResponse


class AIProviderPort(ABC):
    """
    درگاه کاربردی (Application Port) برای تطبیق‌دهنده‌های سرویس‌های هوش مصنوعی.
    لایه Application وابسته به این Interface است و لایه Infrastructure آن را پیاده‌سازی می‌کند.
    """

    @abstractmethod
    def generate_vision(self, request: VisionPromptRequest) -> AIResponse:
        """ارسال درخواست بینایی ماشین (تصویر + پرامپت) و دریافت پاسخ استاندارد."""
        pass

    @abstractmethod
    def generate_text(self, request: TextPromptRequest) -> AIResponse:
        """ارسال درخواست متنی خالص و دریافت پاسخ استاندارد."""
        pass
