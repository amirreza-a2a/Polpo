# ============================================================
#  core/ai/types.py
# ============================================================

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class ApiSlot:
    """موجودیت دامنه جهت نمایش یک اسلات در زنجیره API."""
    id: int
    provider: str
    api_key: str
    slot_type: str = "private"  # 'private' یا 'public'
    label: Optional[str] = None
    selected_model: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ApiSlot":
        return cls(
            id=data["id"],
            provider=data["provider"],
            api_key=data["api_key"],
            slot_type=data.get("type", "private"),
            label=data.get("label"),
            selected_model=data.get("selected_model"),
            base_url=data.get("base_url"),
            supported_models=data.get("models") or data.get("supported_models"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.slot_type,
            "provider": self.provider,
            "api_key": self.api_key,
            "label": self.label,
            "selected_model": self.selected_model,
            "base_url": self.base_url,
            "models": self.supported_models,
        }


@dataclass(frozen=True)
class VisionPromptRequest:
    """
    قرارداد درخواست پردازش تصویر و بینایی ماشین (Vision).
    کاملاً عاری از کتابخانه‌های خارجی؛ فقط داده‌های خام بایت و نوع MIME.
    """
    prompt: str
    image_bytes: bytes
    mime_type: str = "image/jpeg"
    model: Optional[str] = None
    system_instruction: Optional[str] = None
    timeout: float = 120.0


@dataclass(frozen=True)
class TextPromptRequest:
    """قرارداد درخواست پردازش متنی خالص."""
    prompt: str
    model: Optional[str] = None
    system_instruction: Optional[str] = None
    timeout: float = 120.0


@dataclass(frozen=True)
class AIResponse:
    """
    قرارداد پاسخ استاندارد و مستقل از ارائه‌دهنده.
    هیچ شیء خامی از SDKهای خارجی به این لایه نفوذ نمی‌کند.
    """
    content: str
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    @property
    def text(self) -> str:
        """سازگاری با اینترفیس‌های مبتنی بر response.text"""
        return self.content
