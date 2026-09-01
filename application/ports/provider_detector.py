from typing import List, Optional, Tuple
from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class IProviderDetector(Protocol):
    """
    پورت انتزاعی شناسایی خودکار ارائه‌دهنده و مدل‌های پشتیبانی‌شده از روی کلید API.
    """


    def detect_provider_and_models(
        self, api_key: str, base_url: Optional[str] = None
    ) -> Tuple[Optional[str], List[str]]:
        """
        تشخیص نام ارائه‌دهنده و لیست مدل‌های فعال از طریق کلید یا آدرس مبنا.
        خروجی: (provider_name, list_of_models)
        """
        ...

    def get_default_base_url(self, provider: str) -> Optional[str]:
        """دریافت آدرس مبنای پیش‌فرض برای ارائه‌دهنده."""
        ...

    def get_default_model(self, provider: str) -> str:
        """دریافت نام مدل پیش‌فرض برای ارائه‌دهنده."""
        ...
