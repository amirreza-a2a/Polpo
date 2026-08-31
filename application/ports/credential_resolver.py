# ============================================================
#  application/ports/credential_resolver.py
# ============================================================

from abc import ABC, abstractmethod
from typing import Optional
from core.entities.credential_ref import CredentialRef


class ICredentialResolver(ABC):
    """
    درگاه حل و واکشی کلید/رمز عبور ارائه‌دهنده بر اساس CredentialRef.
    این درگاه دسترسی به کلیدهای خام را صرفاً در مرز زیرساخت مجاز می‌سازد.
    """

    @abstractmethod
    def resolve_api_key(self, credential_ref: CredentialRef) -> str:
        """تبدیل شناسه هویتی به کلید API واقعی."""
        pass
