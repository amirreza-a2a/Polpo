# ============================================================
#  interfaces/telegram/error_formatter.py
#  Domain to Telegram Persian UX Error Translator
# ============================================================

from core.exceptions.domain_exceptions import (
    DomainError,
    EntityNotFoundError,
    QuotaExceededError,
    AuthenticationError,
    ArtifactNotFoundError,
)
from core.ai.exceptions import (
    AIError,
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
    AIAuthenticationError,
    AIModelNotFoundError,
    AIContentFilterError,
    AIChainExhaustedError,
)


def format_telegram_error(exc: Exception) -> str:
    """
    ترجمه استثناهای دامنه و کاربرد به پیام‌های راهنمای فارسی سازگار با محیط تلگرام.
    """
    if isinstance(exc, QuotaExceededError):
        return "❌ سهمیه‌ی روزانه‌ی شما تمام شده است.\nمی‌توانید فردا دوباره تلاش کنید یا با کلید API اختصاصی ادامه دهید."

    if isinstance(exc, AIChainExhaustedError):
        return "❌ هیچ API فعالی برای انجام درخواست شما در دسترس نیست.\nلطفاً یک کلید API شخصی در پنل اضافه کنید."

    if isinstance(exc, AIRateLimitError):
        return "⚠️ محدودیت نرخ درخواست (Rate Limit) برای هوش مصنوعی رخ داد. در حال تلاش خودکار..."

    if isinstance(exc, AITimeoutError):
        return "⏱ مهلت پاسخگویی سرویس هوش مصنوعی به پایان رسید."

    if isinstance(exc, (AIAuthenticationError, AuthenticationError)):
        return "🔒 خطای احراز هویت یا نامعتبر بودن کلید API."

    if isinstance(exc, (EntityNotFoundError, ArtifactNotFoundError)):
        return "❌ آیتم یا فایل مورد نظر یافت نشد."

    if isinstance(exc, DomainError):
        return f"⚠️ {str(exc)}"

    if isinstance(exc, AIError):
        return f"❌ خطای سرویس هوش مصنوعی: {str(exc)}"

    return "⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً کمی بعد دوباره تلاش کنید."
