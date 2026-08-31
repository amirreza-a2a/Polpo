# ============================================================
#  core/entities/api_slot.py
# ============================================================

from dataclasses import dataclass
from typing import List, Optional
from core.entities.credential_ref import CredentialRef


@dataclass(frozen=True)
class ApiSlot:
    """
    موجودیت دامنه جهت نمایش یک اسلات API در زنجیره پردازش.
    هیچ کلید خام یا رمزی در این کلاس نگهداری نمی‌شود؛ فقط CredentialRef.
    """
    id: int
    provider: str
    label: str
    slot_type: str = "private"  # 'private' | 'public'
    credential_ref: Optional[CredentialRef] = None
    selected_model: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ApiSlot":
        cred_ref = data.get("credential_ref")
        if cred_ref is None:
            cred_ref = CredentialRef(
                identifier=str(data["id"]),
                provider=data["provider"],
                slot_type=data.get("type", data.get("slot_type", "private")),
            )
        elif isinstance(cred_ref, dict):
            cred_ref = CredentialRef(
                identifier=cred_ref["identifier"],
                provider=cred_ref["provider"],
                slot_type=cred_ref.get("slot_type", "private"),
            )

        return cls(
            id=data["id"],
            provider=data["provider"],
            label=data.get("label", f"{data['provider']}_{data['id']}"),
            slot_type=data.get("type", data.get("slot_type", "private")),
            credential_ref=cred_ref,
            selected_model=data.get("selected_model"),
            base_url=data.get("base_url"),
            supported_models=data.get("models") or data.get("supported_models"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.slot_type,
            "slot_type": self.slot_type,
            "provider": self.provider,
            "label": self.label,
            "credential_ref": str(self.credential_ref) if self.credential_ref else None,
            "selected_model": self.selected_model,
            "base_url": self.base_url,
            "models": self.supported_models,
        }
