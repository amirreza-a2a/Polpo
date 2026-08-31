# ============================================================
#  core/ai/exceptions.py
# ============================================================

from typing import Optional


class AIError(Exception):
    """
    خطای پایه برای تمام خطاهای لایه هوش مصنوعی.
    تنها فراداده‌های مستقل از زیرساخت (message, provider, model, status_code) را نگهداری می‌کند
    و هیچ شیء خامی از SDKهای خارجی در این لایه ذخیره نمی‌شود.
    """
    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code


class AIRetryableError(AIError):
    """خطای گذرا و قابل بازآزمایی (Rate limit، Timeout، قطعی موقت سرور)."""
    pass


class AIRateLimitError(AIRetryableError):
    """خطای محدودیت نرخ درخواست (429 Too Many Requests / Quota Exceeded)."""
    pass


class AITimeoutError(AIRetryableError):
    """خطای اتمام مهلت زمانی درخواست."""
    pass


class AIProviderUnavailableError(AIRetryableError):
    """خطای عدم دسترسی یا خطای داخلی سرور سرویس‌دهنده (500, 502, 503, 504)."""
    pass


class AINonRetryableError(AIError):
    """خطای غیرقابل بازآزمایی با همان کلید/پارامترها (کلید نامعتبر، مدل ناموجود، فیلتر محتوا)."""
    pass


class AIAuthenticationError(AINonRetryableError):
    """خطای اعتبارسنجی کلید API (401 / 403 Invalid API Key)."""
    pass


class AIModelNotFoundError(AINonRetryableError):
    """خطای عدم وجود مدل درخواستی (404 Model Not Found)."""
    pass


class AIContentFilterError(AINonRetryableError):
    """خطای فیلتر ایمنی و محتوای سرویس‌دهنده."""
    pass


class AIChainExhaustedError(AIError):
    """خطای اتمام تمام اسلات‌های زنجیره API بدون موفقیت."""
    pass
