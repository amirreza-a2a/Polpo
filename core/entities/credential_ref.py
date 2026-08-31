# ============================================================
#  core/entities/credential_ref.py
# ============================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialRef:
    """
    Value object جهت ارجاع به اطلاعات هویتی بدون نگهداری رمز/کلید خام در دامنه.
    زیرساخت این ارجاع را به کلید واقعی نگاشت می‌کند.
    """
    identifier: str
    provider: str
    slot_type: str = "private"  # 'private' | 'public' | 'system'

    def __str__(self) -> str:
        return f"{self.slot_type}:{self.provider}:{self.identifier}"
