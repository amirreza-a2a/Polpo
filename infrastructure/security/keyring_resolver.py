# ============================================================
#  infrastructure/security/keyring_resolver.py
# ============================================================

import logging
from typing import Optional

import keyring
import keyring.errors

from application.ports.credential_resolver import ICredentialResolver
from core.entities.credential_ref import CredentialRef
from core.exceptions.domain_exceptions import EntityNotFoundError
from infrastructure.security.encrypted_store import (
    EncryptedFileCredentialStore,
    CredentialSecurityError,
    InvalidPassphraseError,
    CorruptedVaultError,
    PassphraseRequiredError,
)

logger = logging.getLogger("security.keyring_resolver")


class KeyringCredentialResolver(ICredentialResolver):
    """
    Primary desktop credential resolver using OS Keyring (Windows Credential Manager,
    macOS Keychain, Linux Secret Service / KWallet), with an optional EncryptedFileCredentialStore fallback.
    """

    DEFAULT_SERVICE_NAME = "polpot_desktop"

    def __init__(
        self,
        service_name: str = DEFAULT_SERVICE_NAME,
        fallback_store: Optional[EncryptedFileCredentialStore] = None,
    ):
        self.service_name = service_name
        self.fallback_store = fallback_store

    def is_keyring_available(self) -> bool:
        """
        Checks whether a viable OS keyring backend is registered and active.
        """
        try:
            backend = keyring.get_keyring()
            if backend is None:
                return False
            name = backend.__class__.__name__.lower()
            if "fail" in name or "null" in name:
                return False
            return True
        except Exception:
            return False

    def resolve_api_key(self, credential_ref: CredentialRef) -> str:
        """
        Resolves the raw secret string for the given CredentialRef.
        Queries OS keyring first; if missing or if keyring fails, queries the fallback store.
        Cryptographic or vault corruption errors in fallback store propagate explicitly.
        """
        identifier = credential_ref.identifier
        secret: Optional[str] = None
        keyring_failed = False

        # 1. Attempt OS Keyring resolution
        try:
            secret = keyring.get_password(self.service_name, identifier)
        except (keyring.errors.KeyringError, keyring.errors.NoKeyringError, OSError, RuntimeError) as e:
            keyring_failed = True
            logger.debug("Keyring access failed for identifier %s: %s", identifier, e)

        if secret:
            return secret

        # 2. Attempt fallback store if credential was not in keyring or keyring failed
        if self.fallback_store:
            # Note: Do not swallow InvalidPassphraseError or CorruptedVaultError here.
            # They must surface explicitly to alert the application of security store issues.
            secret = self.fallback_store.resolve_secret(identifier)
            if secret:
                return secret

        raise EntityNotFoundError("API Credential", identifier)

    def store_api_key(self, credential_ref: CredentialRef, api_key: str) -> None:
        """
        Stores the raw secret string under the reference identifier.
        Tries OS keyring first; falls back to encrypted file store if keyring is unavailable.
        """
        identifier = credential_ref.identifier
        keyring_success = False

        try:
            keyring.set_password(self.service_name, identifier, api_key)
            keyring_success = True
        except (keyring.errors.KeyringError, keyring.errors.NoKeyringError, OSError, RuntimeError) as e:
            logger.warning("OS Keyring storage failed for identifier %s: %s", identifier, e)

        if not keyring_success:
            if self.fallback_store:
                self.fallback_store.store_secret(identifier, api_key)
            else:
                raise CredentialSecurityError(
                    f"Failed to store secret in OS Keyring for identifier '{identifier}' and no encrypted fallback store is configured."
                )

    def delete_api_key(self, credential_ref: CredentialRef) -> bool:
        """
        Deletes the secret from OS Keyring and fallback store.
        """
        identifier = credential_ref.identifier
        deleted = False

        try:
            keyring.delete_password(self.service_name, identifier)
            deleted = True
        except keyring.errors.PasswordDeleteError:
            pass
        except (keyring.errors.KeyringError, keyring.errors.NoKeyringError, OSError, RuntimeError) as e:
            logger.debug("Keyring delete_password failed for identifier %s: %s", identifier, e)

        if self.fallback_store:
            if self.fallback_store.delete_secret(identifier):
                deleted = True

        return deleted
