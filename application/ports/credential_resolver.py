# ============================================================
#  application/ports/credential_resolver.py
# ============================================================

from abc import ABC, abstractmethod
from core.entities.credential_ref import CredentialRef


class ICredentialResolver(ABC):
    """
    Port for resolving, storing, and deleting provider credentials based on CredentialRef.
    Raw secrets are handled strictly behind this boundary in infrastructure.
    """

    @abstractmethod
    def resolve_api_key(self, credential_ref: CredentialRef) -> str:
        """
        Resolves a CredentialRef to the raw plaintext API key string.
        """
        pass

    @abstractmethod
    def store_api_key(self, credential_ref: CredentialRef, api_key: str) -> None:
        """
        Securely stores a raw API key under the reference identifier.
        """
        pass

    @abstractmethod
    def delete_api_key(self, credential_ref: CredentialRef) -> bool:
        """
        Deletes the stored API key associated with the reference.
        """
        pass
