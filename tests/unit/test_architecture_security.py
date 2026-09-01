# ============================================================
#  tests/unit/test_architecture_security.py
#  Architectural Security & Secret Non-Persistence Enforcement
# ============================================================

import logging
import tempfile
import unittest
from pathlib import Path
from dataclasses import fields
from unittest.mock import MagicMock, patch

import keyring
from keyring.backend import KeyringBackend

from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.settings import AppSettings
from core.exceptions.domain_exceptions import EntityNotFoundError
from core.ai.exceptions import AIAuthenticationError
from application.dto.api_dto import ApiSlotDTO, RegisterKeyCommand
from application.services.api_key_service import ApiKeyService
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.encrypted_store import (
    EncryptedFileCredentialStore,
    InvalidPassphraseError,
)
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from infrastructure.ai.google_adapter import GoogleAdapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from interfaces.desktop.models.api_slot_model import ApiSlotModel


class MockDeterministicKeyring(KeyringBackend):
    """Deterministic in-memory keyring backend for portable architecture test suites."""
    priority = 10

    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get(f"{service}::{username}")

    def set_password(self, service, username, password):
        self._store[f"{service}::{username}"] = password

    def delete_password(self, service, username):
        key = f"{service}::{username}"
        if key in self._store:
            del self._store[key]


class LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


class TestArchitectureSecurity(unittest.TestCase):
    """
    Automated architectural security and credential isolation tests:
      1. Domain entities and safe DTOs never contain raw secret fields.
      2. Presentation Qt models never expose raw secret roles.
      3. SQLite databases and serialized table columns never persist raw secrets.
      4. Encrypted file vaults never persist plaintext secrets in serialized bytes.
      5. Application logs and exceptions never leak raw secrets during real execution and error paths.
      6. Global keyring test state is safely preserved and restored.
    """

    SYNTHETIC_SECRET = "SYNTHETIC_CANARY_API_KEY_PHASE_8H_SEC_VALIDATION_998877"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "security_test.db"
        self.vault_path = self.base_path / "vault.enc"
        self.passphrase = "MasterKey_8H_Secure_Passphrase_123!"

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        # Save original keyring backend and restore in tearDown
        self._original_keyring = keyring.get_keyring()
        self.mock_keyring = MockDeterministicKeyring()
        keyring.set_keyring(self.mock_keyring)

        self.log_capture = LogCaptureHandler()
        logging.getLogger().addHandler(self.log_capture)

    def tearDown(self):
        # Restore original keyring backend
        logging.getLogger().removeHandler(self.log_capture)
        keyring.set_keyring(self._original_keyring)
        self.temp_dir.cleanup()

    def test_domain_entity_and_dto_secret_absence(self):
        """
        Rule: Domain entity ApiSlot and query DTO ApiSlotDTO must NEVER contain an api_key field.
        Credentials must be referenced exclusively via CredentialRef.
        """
        api_slot_fields = [f.name for f in fields(ApiSlot)]
        self.assertNotIn("api_key", api_slot_fields, "ApiSlot entity must not contain 'api_key' field")
        self.assertIn("credential_ref", api_slot_fields)

        api_dto_fields = [f.name for f in fields(ApiSlotDTO)]
        self.assertNotIn("api_key", api_dto_fields, "ApiSlotDTO must not expose 'api_key' field")

    def test_presentation_model_roles_secret_absence(self):
        """
        Rule: Qt QAbstractListModel roles exposed to QML must NEVER contain raw secrets.
        """
        model = ApiSlotModel(None, None)
        for role_id, role_name in model.roleNames().items():
            name_str = role_name.decode("utf-8") if isinstance(role_name, bytes) else str(role_name)
            self.assertNotIn("api_key", name_str.lower())
            self.assertNotIn("secret", name_str.lower())
            self.assertNotIn("password", name_str.lower())

    def test_canary_secret_never_persisted_in_sqlite_binary_or_tables(self):
        """
        Rule: Secrets stored in Keyring/Vault must NEVER leak into SQLite database binary files
        or table column values across api_slots, jobs, prompts, or app_settings.
        """
        resolver = KeyringCredentialResolver(service_name="sec_polpot_audit")
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        # 1. Register slot with synthetic secret
        cmd = RegisterKeyCommand(
            provider="google",
            api_key=self.SYNTHETIC_SECRET,
            label="Audit Canary Slot",
            selected_model="gemini-2.0-flash",
        )
        slot_dto = service.register_key(cmd)

        # 2. Persist job with api_chain referencing the slot
        with self.uow_factory.create() as uow:
            slot_entity = uow.apis.get_by_id(slot_dto.id)
            self.assertIsNotNone(slot_entity)

            job = Job(
                id=None,
                file_name="security_audit.pdf",
                file_path="file:///tmp/security_audit.pdf",
                total_pages=3,
                api_chain=[slot_entity],
                prompt_text="Extract text",
                status=JobStatus.PENDING,
            )
            uow.jobs.save(job)
            uow.prompts.save(Prompt(id=None, name="Audit Prompt", text="Sample", prompt_type=PromptType.PIPELINE_1))
            uow.settings.save(AppSettings(theme="dark", max_concurrent_jobs=2))
            uow.commit()

        # 3. Binary inspection of SQLite database on disk
        with open(self.db_path, "rb") as f:
            raw_db_content = f.read()

        self.assertNotIn(
            self.SYNTHETIC_SECRET.encode("utf-8"),
            raw_db_content,
            "CRITICAL SECURITY VIOLATION: Raw canary secret found in SQLite binary file on disk!",
        )

        # 4. SQL table column value inspection
        conn = self.db_manager.create_connection()
        try:
            cursor = conn.cursor()
            for table in ["api_slots", "jobs", "pipeline2_jobs", "prompts", "app_settings"]:
                cursor.execute(f"SELECT * FROM {table}")
                rows = cursor.fetchall()
                for row in rows:
                    for col_name, col_val in dict(row).items():
                        self.assertNotIn("api_key", col_name.lower())
                        if isinstance(col_val, str):
                            self.assertNotIn(
                                self.SYNTHETIC_SECRET,
                                col_val,
                                f"CRITICAL: Canary secret found in SQLite column {table}.{col_name}",
                            )
        finally:
            conn.close()

    def test_encrypted_vault_ciphertext_security(self):
        """
        Mandatory Correction 2: Verifies that the synthetic secret cannot be recovered
        from the serialized encrypted vault on disk without the legitimate passphrase.
        """
        store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        store.store_secret("test_sec_id", self.SYNTHETIC_SECRET)

        # 1. Plaintext secret is absent from on-disk vault file
        with open(self.vault_path, "rb") as f:
            vault_bytes = f.read()

        self.assertNotIn(
            self.SYNTHETIC_SECRET.encode("utf-8"),
            vault_bytes,
            "CRITICAL: Plaintext synthetic secret found in encrypted vault file on disk!",
        )

        # 2. Cannot decrypt with incorrect passphrase
        wrong_store = EncryptedFileCredentialStore(self.vault_path, passphrase="WrongPassword_123")
        with self.assertRaises(InvalidPassphraseError):
            wrong_store.resolve_secret("test_sec_id")

        # 3. Can decrypt with correct passphrase
        valid_store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        recovered = valid_store.resolve_secret("test_sec_id")
        self.assertEqual(recovered, self.SYNTHETIC_SECRET)

    def test_log_and_exception_masking(self):
        """
        Verifies that when synthetic canary secrets genuinely flow through real credential resolution,
        AI adapter construction, forced AI API authentication failures, and exception formatting,
        the raw canary secret is 100% absent from all captured logs and exception strings.
        """
        resolver = KeyringCredentialResolver(service_name="sec_polpot_masking")

        # --- Path A: ApiKeyService registration failure log check ---
        failing_uow = MagicMock()
        failing_uow.apis.save.side_effect = RuntimeError("Database disk full simulation")
        failing_uow_factory = MagicMock()
        failing_uow_factory.create.return_value.__enter__.return_value = failing_uow
        failing_uow_factory.create.return_value.__exit__.return_value = None

        failing_service = ApiKeyService(uow_factory=failing_uow_factory, credential_resolver=resolver)
        cmd = RegisterKeyCommand(
            provider="google",
            api_key=self.SYNTHETIC_SECRET,
            label="Canary Failing Slot",
            selected_model="gemini-2.0-flash",
        )
        try:
            failing_service.register_key(cmd)
        except Exception as ex:
            self.assertNotIn(self.SYNTHETIC_SECRET, str(ex))
            self.assertNotIn(self.SYNTHETIC_SECRET, repr(ex))

        # --- Path B: Genuine AI Credential Traversal & Forced Authentication Failure ---
        # 1. Store synthetic canary secret in real resolver
        cred_ref = CredentialRef("canary_ai_auth_key", "google", "byok")
        resolver.store_api_key(cred_ref, self.SYNTHETIC_SECRET)

        # 2. Slot with credential reference
        canary_slot = ApiSlot(
            id=42,
            provider="google",
            label="Canary Failing AI Provider",
            selected_model="gemini-2.0-flash",
            credential_ref=cred_ref,
        )

        # 3. Real resolve_ai_adapter factory that extracts the canary secret from resolver
        passed_keys_to_adapter = []
        def real_adapter_factory(slot: ApiSlot):
            raw_key = resolver.resolve_api_key(slot.credential_ref)
            passed_keys_to_adapter.append(raw_key)
            return GoogleAdapter(
                api_key=raw_key,
                default_model=slot.selected_model,
            )

        executor = RateLimitedAIExecutor(
            adapter_factory=real_adapter_factory,
            rate_limiter=MagicMock(),
        )

        # 4. Patch google.genai.Client to verify canary key genuinely enters client constructor, then force 401 failure
        with patch("google.genai.Client") as mock_genai_client:
            mock_client_instance = MagicMock()
            mock_client_instance.models.generate_content.side_effect = Exception(
                "401 API_KEY_INVALID: The provided API key is invalid or unauthorized."
            )
            mock_genai_client.return_value = mock_client_instance

            content, active_slot = executor.execute_vision_with_fallback(
                chain=[canary_slot],
                image_bytes=b"dummy_jpeg",
                prompt="Transcribe page",
                at_page=1,
            )

            # Assert canary was genuinely resolved and passed into adapter and SDK client
            self.assertIn(self.SYNTHETIC_SECRET, passed_keys_to_adapter)
            mock_genai_client.assert_called_once_with(api_key=self.SYNTHETIC_SECRET)
            self.assertIsNone(content)

        # --- Path C: Keyring resolution missing key exception ---
        missing_cred = CredentialRef(identifier="non_existent_slot", provider="google", slot_type="byok")
        with self.assertRaises(EntityNotFoundError) as ctx:
            resolver.resolve_api_key(missing_cred)
        self.assertNotIn(self.SYNTHETIC_SECRET, str(ctx.exception))

        # --- Global log inspection ---
        self.assertGreater(len(self.log_capture.records), 0, "Expected logs to be captured during error paths")
        for log_msg in self.log_capture.records:
            self.assertNotIn(
                self.SYNTHETIC_SECRET,
                log_msg,
                f"CRITICAL: Raw canary secret found in log message: {log_msg}",
            )


if __name__ == "__main__":
    unittest.main()
