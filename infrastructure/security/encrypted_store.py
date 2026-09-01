# ============================================================
#  infrastructure/security/encrypted_store.py
# ============================================================

import os
import json
import uuid
import base64
import threading
from pathlib import Path
from typing import Dict, List, Optional, Union

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CredentialSecurityError(Exception):
    """Base exception for credential security operations."""
    pass


class InvalidPassphraseError(CredentialSecurityError):
    """Raised when an invalid passphrase is provided to unlock the encrypted store."""
    pass


class CorruptedVaultError(CredentialSecurityError):
    """Raised when the encrypted credential vault file is corrupted or unreadable."""
    pass


class PassphraseRequiredError(CredentialSecurityError):
    """Raised when a passphrase is required but has not been configured."""
    pass


class EncryptedFileCredentialStore:
    """
    Secure file-based credential vault using PBKDF2HMAC key derivation and Fernet encryption.
    Serves as an authenticated encrypted fallback when the OS keyring is unavailable.
    Thread-safe for concurrent operations within a single process.
    """

    FORMAT_VERSION = 1
    KDF_ALGORITHM = "PBKDF2HMAC-SHA256"
    ITERATIONS = 600_000
    SALT_BYTES = 16

    def __init__(
        self,
        vault_path: Union[Path, str],
        passphrase: Optional[str] = None,
    ):
        self.vault_path = Path(vault_path)
        self._passphrase: Optional[str] = passphrase
        self._lock = threading.Lock()
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)

    def set_passphrase(self, passphrase: str) -> None:
        """Sets or updates the in-memory master passphrase."""
        if not passphrase:
            raise ValueError("Passphrase must not be empty.")
        with self._lock:
            self._passphrase = passphrase

    @property
    def has_passphrase(self) -> bool:
        with self._lock:
            return self._passphrase is not None and len(self._passphrase) > 0

    def _derive_fernet_key(self, passphrase: str, salt: bytes) -> bytes:
        """Derives a URL-safe 32-byte Fernet key from the passphrase and salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        derived_key = kdf.derive(passphrase.encode("utf-8"))
        return base64.urlsafe_b64encode(derived_key)

    def _load_vault_unlocked(self) -> Dict[str, str]:
        """
        Decrypts and loads the credentials dictionary from disk.
        Must be called while holding self._lock.
        """
        if not self.vault_path.exists():
            return {}

        if not self._passphrase:
            raise PassphraseRequiredError("A master passphrase is required to unlock the credential vault.")

        try:
            with open(self.vault_path, "r", encoding="utf-8") as f:
                envelope = json.load(f)
        except Exception as e:
            raise CorruptedVaultError(f"Failed to read credential vault envelope: {e}") from e

        if not isinstance(envelope, dict) or "ciphertext" not in envelope or "salt" not in envelope:
            raise CorruptedVaultError("Malformed credential vault structure.")

        try:
            salt = base64.b64decode(envelope["salt"])
            fernet_key = self._derive_fernet_key(self._passphrase, salt)
            fernet = Fernet(fernet_key)
            decrypted_bytes = fernet.decrypt(envelope["ciphertext"].encode("utf-8"))
            payload = json.loads(decrypted_bytes.decode("utf-8"))
            return payload.get("credentials", {})
        except InvalidToken as e:
            raise InvalidPassphraseError("Incorrect master passphrase for credential vault.") from e
        except Exception as e:
            raise CorruptedVaultError(f"Failed to decrypt credential vault: {e}") from e

    def _save_vault_unlocked(self, credentials: Dict[str, str]) -> None:
        """
        Encrypts and atomically writes the credentials dictionary to disk.
        Uses a unique temporary file and restricts permissions to 0600 on POSIX.
        Must be called while holding self._lock.
        """
        if not self._passphrase:
            raise PassphraseRequiredError("A master passphrase is required to save credentials.")

        salt = os.urandom(self.SALT_BYTES)
        fernet_key = self._derive_fernet_key(self._passphrase, salt)
        fernet = Fernet(fernet_key)

        payload = {
            "version": self.FORMAT_VERSION,
            "credentials": credentials,
        }
        plaintext_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ciphertext = fernet.encrypt(plaintext_bytes).decode("utf-8")

        envelope = {
            "format_version": self.FORMAT_VERSION,
            "kdf": self.KDF_ALGORITHM,
            "iterations": self.ITERATIONS,
            "salt": base64.b64encode(salt).decode("utf-8"),
            "ciphertext": ciphertext,
        }

        # Unique temporary file to avoid collisions
        temp_file = self.vault_path.with_name(f"{self.vault_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(envelope, f, indent=2)

            if hasattr(os, "chmod") and os.name != "nt":
                try:
                    os.chmod(temp_file, 0o600)
                except OSError:
                    pass

            temp_file.replace(self.vault_path)
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise CredentialSecurityError(f"Failed to atomically write credential vault: {e}") from e

    def store_secret(self, identifier: str, secret: str) -> None:
        """Stores or updates a secret key under the given identifier."""
        if not identifier:
            raise ValueError("Identifier must not be empty.")
        if not secret:
            raise ValueError("Secret must not be empty.")

        with self._lock:
            vault = self._load_vault_unlocked()
            vault[identifier] = secret
            self._save_vault_unlocked(vault)

    def resolve_secret(self, identifier: str) -> Optional[str]:
        """Resolves and returns the secret for the given identifier, or None if not found."""
        if not identifier:
            return None
        with self._lock:
            vault = self._load_vault_unlocked()
            return vault.get(identifier)

    def delete_secret(self, identifier: str) -> bool:
        """Deletes the secret for the given identifier. Returns True if deleted."""
        with self._lock:
            vault = self._load_vault_unlocked()
            if identifier in vault:
                del vault[identifier]
                self._save_vault_unlocked(vault)
                return True
            return False

    def has_secret(self, identifier: str) -> bool:
        """Checks whether a secret exists for the given identifier."""
        with self._lock:
            vault = self._load_vault_unlocked()
            return identifier in vault

    def list_identifiers(self) -> List[str]:
        """Returns the list of all stored credential identifiers."""
        with self._lock:
            vault = self._load_vault_unlocked()
            return list(vault.keys())
