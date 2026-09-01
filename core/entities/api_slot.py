# ============================================================
#  core/entities/api_slot.py
# ============================================================

from dataclasses import dataclass
from typing import List, Optional
from core.entities.credential_ref import CredentialRef


@dataclass(frozen=True)
class ApiSlot:
    """
    Domain entity representing a configured AI slot in the local desktop chain (BYOK).
    Contains zero raw API keys; references secrets via CredentialRef only.
    """
    id: int
    provider: str
    label: str
    credential_ref: Optional[CredentialRef] = None
    slot_type: str = "byok"  # 'byok' | 'custom'
    selected_model: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[List[str]] = None

    def __post_init__(self):
        valid_types = ("byok", "custom")
        if self.slot_type not in valid_types:
            raise ValueError(f"Invalid slot_type '{self.slot_type}'. Canonical desktop slot types must be one of {valid_types}")

        if self.credential_ref is None:
            object.__setattr__(
                self,
                "credential_ref",
                CredentialRef(identifier=str(self.id), provider=self.provider, slot_type=self.slot_type),
            )
        elif not isinstance(self.credential_ref, CredentialRef):
            raise ValueError("ApiSlot must have a valid CredentialRef instance.")

    @classmethod
    def from_dict(cls, data: dict) -> "ApiSlot":
        slot_type = data.get("slot_type", data.get("type", "byok"))
        cred_ref = data.get("credential_ref")

        if cred_ref is None:
            cred_ref = CredentialRef(
                identifier=str(data["id"]),
                provider=data["provider"],
                slot_type=slot_type,
            )
        elif isinstance(cred_ref, dict):
            cred_ref = CredentialRef(
                identifier=cred_ref["identifier"],
                provider=cred_ref.get("provider", data["provider"]),
                slot_type=cred_ref.get("slot_type", slot_type),
            )
        elif isinstance(cred_ref, str):
            parts = cred_ref.split(":")
            if len(parts) == 3:
                cred_ref = CredentialRef(identifier=parts[2], provider=parts[1], slot_type=parts[0])
            else:
                cred_ref = CredentialRef(identifier=cred_ref, provider=data["provider"], slot_type=slot_type)

        return cls(
            id=data["id"],
            provider=data["provider"],
            label=data.get("label", f"{data['provider']}_{data['id']}"),
            credential_ref=cred_ref,
            slot_type=slot_type,
            selected_model=data.get("selected_model"),
            base_url=data.get("base_url"),
            supported_models=data.get("models") or data.get("supported_models"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label,
            "slot_type": self.slot_type,
            "type": self.slot_type,
            "credential_ref": str(self.credential_ref),
            "selected_model": self.selected_model,
            "base_url": self.base_url,
            "models": self.supported_models,
        }
