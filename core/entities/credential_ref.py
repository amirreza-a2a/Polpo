# ============================================================
#  core/entities/credential_ref.py
# ============================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialRef:
    """
    Value object representing a reference to stored credentials without holding plaintext secrets in domain entities.
    The infrastructure layer resolves this reference at runtime via the OS keyring or secure vault.
    """
    identifier: str
    provider: str
    slot_type: str = "byok"  # 'byok' | 'custom'

    def __str__(self) -> str:
        return f"{self.slot_type}:{self.provider}:{self.identifier}"
