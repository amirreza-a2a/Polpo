# ============================================================
#  tests/unit/test_keyring_security.py
# ============================================================

import os
import json
import base64
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import keyring
from keyring.backend import KeyringBackend

from core.entities.credential_ref import CredentialRef
from core.exceptions.domain_exceptions import (
    EntityNotFoundError,
    CredentialConsistencyError,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.encrypted_store import (
    EncryptedFileCredentialStore,
    InvalidPassphraseError,
    CorruptedVaultError,
    PassphraseRequiredError,
    CredentialSecurityError,
)
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from application.dto.api_dto import RegisterKeyCommand, UpdateKeyCommand
from application.services.api_key_service import ApiKeyService


class MockMemoryKeyring(KeyringBackend):
    """Deterministic in-memory keyring backend for isolated unit testing."""
    priority = 10

    def __init__(self):
        self._vault = {}

    def get_password(self, service, username):
        return self._vault.get(f"{service}::{username}")

    def set_password(self, service, username, password):
        self._vault[f"{service}::{username}"] = password

    def delete_password(self, service, username):
        key = f"{service}::{username}"
        if key in self._vault:
            del self._vault[key]
        else:
            raise keyring.errors.PasswordDeleteError("Password not found")


class TestKeyringSecurity(unittest.TestCase):
    """
    Unit tests for EncryptedFileCredentialStore, KeyringCredentialResolver,
    and ApiKeyService credential lifecycle.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.temp_dir.name) / "credentials.enc"
        self.passphrase = "MasterSecretPassphrase123!"

        self.db_path = Path(self.temp_dir.name) / "test_security.db"
        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.mock_keyring = MockMemoryKeyring()
        keyring.set_keyring(self.mock_keyring)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ------------------------------------------------------------
    # 1. EncryptedFileCredentialStore Tests
    # ------------------------------------------------------------

    def test_encrypted_store_crud_lifecycle(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)

        # Store secret
        store.store_secret("google_key_01", "AIzaSy_Secret_Google_Key")
        store.store_secret("openai_key_01", "sk-proj-Secret_OpenAI_Key")

        # Resolve secrets
        self.assertEqual(store.resolve_secret("google_key_01"), "AIzaSy_Secret_Google_Key")
        self.assertEqual(store.resolve_secret("openai_key_01"), "sk-proj-Secret_OpenAI_Key")
        self.assertIsNone(store.resolve_secret("non_existent"))

        # Update secret
        store.store_secret("google_key_01", "AIzaSy_Updated_Google_Key")
        self.assertEqual(store.resolve_secret("google_key_01"), "AIzaSy_Updated_Google_Key")

        # List identifiers
        identifiers = store.list_identifiers()
        self.assertIn("google_key_01", identifiers)
        self.assertIn("openai_key_01", identifiers)

        # Delete secret
        self.assertTrue(store.delete_secret("google_key_01"))
        self.assertIsNone(store.resolve_secret("google_key_01"))
        self.assertFalse(store.delete_secret("google_key_01"))

    def test_kdf_derivation_deterministic(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        salt = os.urandom(16)

        key1 = store._derive_fernet_key(self.passphrase, salt)
        key2 = store._derive_fernet_key(self.passphrase, salt)
        self.assertEqual(key1, key2)

        different_salt = os.urandom(16)
        key3 = store._derive_fernet_key(self.passphrase, different_salt)
        self.assertNotEqual(key1, key3)

    def test_random_salt_generated_on_save(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store.store_secret("k1", "v1")

        with open(self.vault_path, "r", encoding="utf-8") as f:
            env1 = json.load(f)
        salt1 = env1["salt"]

        store.store_secret("k2", "v2")
        with open(self.vault_path, "r", encoding="utf-8") as f:
            env2 = json.load(f)
        salt2 = env2["salt"]

        self.assertNotEqual(salt1, salt2)

    def test_encrypted_store_persistence_across_instances(self):
        store1 = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store1.store_secret("anthropic_key_01", "sk-ant-SecretAnthropic")

        # Reopen with new instance
        store2 = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        self.assertEqual(store2.resolve_secret("anthropic_key_01"), "sk-ant-SecretAnthropic")

    def test_encrypted_store_wrong_passphrase_raises_error(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store.store_secret("key1", "secret1")

        # Attempt to open with wrong passphrase
        store_bad = EncryptedFileCredentialStore(self.vault_path, passphrase="WrongPassword123!")
        with self.assertRaises(InvalidPassphraseError):
            store_bad.resolve_secret("key1")

    def test_encrypted_store_missing_passphrase_raises_error(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store.store_secret("key1", "secret1")

        store_no_pass = EncryptedFileCredentialStore(self.vault_path, passphrase=None)
        with self.assertRaises(PassphraseRequiredError):
            store_no_pass.resolve_secret("key1")

    def test_encrypted_store_corrupted_file_detection(self):
        # Write corrupted JSON envelope
        with open(self.vault_path, "w", encoding="utf-8") as f:
            f.write("THIS IS CORRUPTED NOT JSON")

        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        with self.assertRaises(CorruptedVaultError):
            store.resolve_secret("key1")

    def test_encrypted_store_file_permissions(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store.store_secret("k1", "v1")

        if hasattr(os, "stat") and os.name != "nt":
            mode = os.stat(self.vault_path).st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_encrypted_store_concurrent_writes(self):
        """Tests concurrent threads storing secrets safely through the shared store instance."""
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        num_threads = 10
        barrier = threading.Barrier(num_threads)
        errors = []

        def worker(idx):
            try:
                barrier.wait()
                store.store_secret(f"worker_key_{idx}", f"secret_val_{idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        identifiers = store.list_identifiers()
        self.assertEqual(len(identifiers), num_threads)
        for i in range(num_threads):
            self.assertEqual(store.resolve_secret(f"worker_key_{i}"), f"secret_val_{i}")

    # ------------------------------------------------------------
    # 2. KeyringCredentialResolver Tests
    # ------------------------------------------------------------

    def test_keyring_resolver_crud(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        cred_ref = CredentialRef(identifier="cred_slot_1", provider="google", slot_type="byok")

        # Store
        resolver.store_api_key(cred_ref, "test_secret_api_key_123")

        # Resolve
        resolved = resolver.resolve_api_key(cred_ref)
        self.assertEqual(resolved, "test_secret_api_key_123")

        # Delete
        self.assertTrue(resolver.delete_api_key(cred_ref))
        with self.assertRaises(EntityNotFoundError):
            resolver.resolve_api_key(cred_ref)

    def test_keyring_resolver_fallback_to_encrypted_store(self):
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        resolver = KeyringCredentialResolver(service_name="test_polpot", fallback_store=store)
        cred_ref = CredentialRef(identifier="cred_fallback_1", provider="openai", slot_type="byok")

        # Simulate OS Keyring failure
        with patch.object(keyring, "set_password", side_effect=RuntimeError("Keyring locked")):
            resolver.store_api_key(cred_ref, "sk-proj-fallback-secret")

        # Verify it was stored in fallback encrypted store
        self.assertEqual(store.resolve_secret("cred_fallback_1"), "sk-proj-fallback-secret")

        # Verify resolution falls back seamlessly
        with patch.object(keyring, "get_password", side_effect=RuntimeError("Keyring unavailable")):
            resolved = resolver.resolve_api_key(cred_ref)
            self.assertEqual(resolved, "sk-proj-fallback-secret")

    def test_keyring_resolver_propagates_explicit_fallback_errors(self):
        """Verifies that corrupted vault or invalid passphrase in fallback store propagate explicitly."""
        store = EncryptedFileCredentialStore(self.vault_path, passphrase="WrongPassword!")
        resolver = KeyringCredentialResolver(service_name="test_polpot", fallback_store=store)
        cred_ref = CredentialRef(identifier="cred_bad_pass", provider="google", slot_type="byok")

        # Create valid vault on disk with correct password
        valid_store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        valid_store.store_secret("cred_bad_pass", "secret_val")

        # Keyring missing -> queries fallback with wrong password -> must raise InvalidPassphraseError
        with self.assertRaises(InvalidPassphraseError):
            resolver.resolve_api_key(cred_ref)

    def test_is_keyring_available_diagnostic(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        # With mock keyring registered, availability is True
        self.assertTrue(resolver.is_keyring_available())

        # If get_keyring returns None
        with patch.object(keyring, "get_keyring", return_value=None):
            self.assertFalse(resolver.is_keyring_available())

    # ------------------------------------------------------------
    # 3. ApiKeyService Tests
    # ------------------------------------------------------------

    def test_api_key_service_lifecycle(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        # 1. Register new BYOK key
        reg_cmd = RegisterKeyCommand(
            provider="google",
            api_key="AIzaSy_LiveGoogleKey_999",
            label="Production Gemini Slot",
            selected_model="gemini-2.5-flash",
            supported_models=["gemini-2.5-flash", "gemini-2.5-pro"],
        )
        slot_dto = service.register_key(reg_cmd)

        self.assertIsNotNone(slot_dto.id)
        self.assertEqual(slot_dto.provider, "google")
        self.assertEqual(slot_dto.label, "Production Gemini Slot")
        self.assertFalse(hasattr(slot_dto, "api_key"))

        # Test key resolution via service
        self.assertTrue(service.test_key(slot_dto.id))

        # 2. List slots
        slots = service.list_slots()
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].id, slot_dto.id)

        # 3. Update slot
        update_cmd = UpdateKeyCommand(
            slot_id=slot_dto.id,
            label="Updated Production Gemini Slot",
            api_key="AIzaSy_UpdatedGoogleKey_111",
        )
        updated_dto = service.update_key(update_cmd)
        self.assertEqual(updated_dto.label, "Updated Production Gemini Slot")

        # Verify secret was updated in resolver
        with self.uow_factory.create() as uow:
            loaded_entity = uow.apis.get_by_id(slot_dto.id)
            self.assertEqual(resolver.resolve_api_key(loaded_entity.credential_ref), "AIzaSy_UpdatedGoogleKey_111")

        # 4. Delete slot
        deleted = service.delete_key(slot_dto.id)
        self.assertTrue(deleted)
        self.assertIsNone(service.get_slot(slot_dto.id))
        self.assertFalse(service.test_key(slot_dto.id))

    def test_update_fails_before_changing_secret_if_old_credential_missing(self):
        """Verify update_key aborts immediately before modifying secret if existing secret cannot be resolved."""
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        # Register slot normally
        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="initial_key", label="Slot 1"))

        # Delete secret from resolver directly so it becomes unresolvable
        with self.uow_factory.create() as uow:
            slot_entity = uow.apis.get_by_id(slot_dto.id)
        resolver.delete_api_key(slot_entity.credential_ref)

        # Attempt update with new secret
        with self.assertRaises(EntityNotFoundError):
            service.update_key(UpdateKeyCommand(slot_id=slot_dto.id, api_key="new_key_attempt"))

        # Verify new secret was NOT stored
        with self.assertRaises(EntityNotFoundError):
            resolver.resolve_api_key(slot_entity.credential_ref)

    def test_update_fails_before_changing_secret_if_backend_fails(self):
        """Verify update_key aborts immediately if credential resolver backend raises an error during pre-resolution."""
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="initial_key", label="Slot 1"))

        with patch.object(resolver, "resolve_api_key", side_effect=RuntimeError("Keyring locked")):
            with self.assertRaises(RuntimeError):
                service.update_key(UpdateKeyCommand(slot_id=slot_dto.id, api_key="new_key_attempt"))

    def test_update_restores_old_secret_on_metadata_failure(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="AIzaSy_OriginalSecret", label="Initial Slot"))

        with self.uow_factory.create() as uow:
            slot_entity = uow.apis.get_by_id(slot_dto.id)

        update_cmd = UpdateKeyCommand(slot_id=slot_dto.id, api_key="AIzaSy_NewFailedSecret", label="Failed Update Label")

        with patch("infrastructure.persistence.sqlite.repositories.SQLiteApiSlotRepository.save", side_effect=RuntimeError("Simulated DB Write Error")):
            with self.assertRaises(RuntimeError):
                service.update_key(update_cmd)

        # Verify secret was restored to original
        self.assertEqual(resolver.resolve_api_key(slot_entity.credential_ref), "AIzaSy_OriginalSecret")

    def test_update_raises_consistency_error_if_rollback_fails(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="OriginalSecret", label="Slot"))

        update_cmd = UpdateKeyCommand(slot_id=slot_dto.id, api_key="NewSecret", label="Updated")

        # First store_api_key succeeds, but second (rollback) store_api_key raises error
        original_store = resolver.store_api_key
        call_count = 0

        def failing_store(cred_ref, key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return original_store(cred_ref, key)
            raise RuntimeError("Rollback storage failure")

        with patch.object(resolver, "store_api_key", side_effect=failing_store):
            with patch("infrastructure.persistence.sqlite.repositories.SQLiteApiSlotRepository.save", side_effect=RuntimeError("DB Save Failed")):
                with self.assertRaises(CredentialConsistencyError) as ctx:
                    service.update_key(update_cmd)
                self.assertIn("Metadata persistence failed and secret rollback failed", str(ctx.exception))

    def test_delete_key_raises_consistency_error_if_secret_delete_fails(self):
        """Verify delete_key raises CredentialConsistencyError if SQLite delete succeeds but secret delete fails."""
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="ToDeleteSecret", label="Slot"))

        with patch.object(resolver, "delete_api_key", side_effect=RuntimeError("Keyring delete locked")):
            with self.assertRaises(CredentialConsistencyError) as ctx:
                service.delete_key(slot_dto.id)
            self.assertIn("secret deletion failed", str(ctx.exception))

    def test_delete_key_leaves_secret_intact_if_database_fails(self):
        """Verify delete_key does not delete secret if SQLite delete transaction fails."""
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        slot_dto = service.register_key(RegisterKeyCommand(provider="google", api_key="KeepSecret", label="Slot"))
        with self.uow_factory.create() as uow:
            slot_entity = uow.apis.get_by_id(slot_dto.id)

        with patch("infrastructure.persistence.sqlite.repositories.SQLiteApiSlotRepository.delete", side_effect=RuntimeError("DB Delete Error")):
            with self.assertRaises(RuntimeError):
                service.delete_key(slot_dto.id)

        # Secret is still intact
        self.assertEqual(resolver.resolve_api_key(slot_entity.credential_ref), "KeepSecret")

    def test_api_key_service_validation_and_errors(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        # Empty provider
        with self.assertRaises(ValueError):
            service.register_key(RegisterKeyCommand(provider="", api_key="secret", label="L"))

        # Empty api_key
        with self.assertRaises(ValueError):
            service.register_key(RegisterKeyCommand(provider="google", api_key="", label="L"))

        # Empty label
        with self.assertRaises(ValueError):
            service.register_key(RegisterKeyCommand(provider="google", api_key="secret", label=""))

        # Update non-existent
        with self.assertRaises(EntityNotFoundError):
            service.update_key(UpdateKeyCommand(slot_id=99999, label="New Label"))

        # Delete non-existent
        self.assertFalse(service.delete_key(99999))

    def test_api_key_service_partial_failure_cleanup(self):
        resolver = KeyringCredentialResolver(service_name="test_polpot")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        reg_cmd = RegisterKeyCommand(
            provider="google",
            api_key="AIzaSy_OrphanSecret",
            label="Broken Slot",
        )

        with patch.object(SQLiteUnitOfWorkFactory, "create", side_effect=RuntimeError("DB Lock Failed")):
            with self.assertRaises(RuntimeError):
                service.register_key(reg_cmd)

        self.assertEqual(len(self.mock_keyring._vault), 0)


if __name__ == "__main__":
    unittest.main()
