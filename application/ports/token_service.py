# ============================================================
#  application/ports/token_service.py
# ============================================================

from abc import ABC, abstractmethod
from typing import Optional


class ITokenService(ABC):
    """
    درگاه ایجاد، تایید و ابطال توکن‌های احراز هویت دسکتاپ، کلیدهای دسترسی ماشین و کدهای تبادل یک‌بار مصرف.
    """

    @abstractmethod
    def create_access_token(self, user_id: int, expires_minutes: int = 60 * 24 * 7) -> str:
        """تولید توکن احراز هویت Bearer Token برای کلاینت دسکتاپ."""
        pass

    @abstractmethod
    def verify_access_token(self, token: str) -> Optional[int]:
        """اعتبارسنجی توکن Bearer و بازگرداندن شناسه user_id."""
        pass

    @abstractmethod
    def create_machine_api_key(self, user_id: int) -> str:
        """تولید کلید دسترسی ماشین (Machine API Key) مستقل برای یکپارچه‌سازی‌های خارجی."""
        pass

    @abstractmethod
    def verify_machine_api_key(self, api_key: str) -> Optional[int]:
        """اعتبارسنجی مستقل کلید دسترسی ماشین و بازگرداندن شناسه user_id."""
        pass

    @abstractmethod
    def create_one_time_exchange_code(self, user_id: int, expires_seconds: int = 300) -> str:
        """تولید کد تبادل کوتاه‌مدت، تک‌کاربره و یک‌بار مصرف برای اتصال تلگرام به دسکتاپ."""
        pass

    @abstractmethod
    def exchange_code_for_user_id(self, code: str) -> Optional[int]:
        """تبادل کد یک‌بار مصرف با شناسه کاربر (و ابطال فوری آن)."""
        pass
