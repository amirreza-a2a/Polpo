# ============================================================
#  tests/unit/test_secret_non_persistence.py
# ============================================================

import os
import ast
import logging
import tempfile
import unittest
from pathlib import Path
from dataclasses import fields

import keyring
from keyring.backend import KeyringBackend

from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, JobStatus
from core.exceptions.domain_exceptions import EntityNotFoundError
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.encrypted_store import (
    EncryptedFileCredentialStore,
    InvalidPassphraseError,
    CorruptedVaultError,
    PassphraseRequiredError,
)
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from application.dto.api_dto import ApiSlotDTO, RegisterKeyCommand, UpdateKeyCommand
from application.services.api_key_service import ApiKeyService


class MockMemoryKeyring(KeyringBackend):
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


class LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


class TestSecretNonPersistence(unittest.TestCase):
    """
    Automated security audits and AST boundary tests enforcing that raw API credentials
    never leak into SQLite databases, safe DTOs, domain serialization, logs, or application layers.
    """

    SYNTHETIC_SECRET = "TEST_SYNTHETIC_SECRET_PHASE8C_8A7F6E5D4C3B"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "security_audit.db"
        self.vault_path = Path(self.temp_dir.name) / "vault.enc"
        self.passphrase = "MasterKey8C_Secure!"

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.mock_keyring = MockMemoryKeyring()
        keyring.set_keyring(self.mock_keyring)

        self.log_capture = LogCaptureHandler()
        logging.getLogger().addHandler(self.log_capture)

    def tearDown(self):
        logging.getLogger().removeHandler(self.log_capture)
        self.temp_dir.cleanup()

    def test_synthetic_secret_never_leaks_to_sqlite_or_dto(self):
        """
        Registers a slot using synthetic secret and verifies zero leakage across
        raw SQLite bytes, table rows, DTO representations, and serialized jobs.
        """
        encrypted_store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        resolver = KeyringCredentialResolver(service_name="audit_polpot", fallback_store=encrypted_store)
        service = ApiKeyService(uow_factory=self.uow_factory, credential_resolver=resolver)

        # 1. Register slot via ApiKeyService
        cmd = RegisterKeyCommand(
            provider="google",
            api_key=self.SYNTHETIC_SECRET,
            label="Audit Google Slot",
            selected_model="gemini-2.5-flash",
        )
        slot_dto = service.register_key(cmd)

        # Verify DTO does not contain secret
        self.assertNotIn("api_key", [f.name for f in fields(ApiSlotDTO)])
        self.assertFalse(hasattr(slot_dto, "api_key"))

        # 2. Persist Job with api_chain containing this slot
        with self.uow_factory.create() as uow:
            slot_entity = uow.apis.get_by_id(slot_dto.id)
            self.assertIsNotNone(slot_entity)
            self.assertNotIn("api_key", [f.name for f in fields(ApiSlot)])

            job = Job(
                id=None,
                file_name="audit_test.pdf",
                file_path="/tmp/audit.pdf",
                total_pages=5,
                api_chain=[slot_entity],
            )
            saved_job = uow.jobs.save(job)
            uow.commit()

        # 3. Binary inspection of SQLite database file
        with open(self.db_path, "rb") as f:
            raw_db_bytes = f.read()

        self.assertNotIn(
            self.SYNTHETIC_SECRET.encode("utf-8"),
            raw_db_bytes,
            "CRITICAL: Synthetic secret found in raw SQLite database binary!",
        )

        # 4. SQL table row and column scan
        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            for table in ["api_slots", "jobs", "pipeline2_jobs", "prompts", "app_settings"]:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                for row in rows:
                    for col_name, col_val in dict(row).items():
                        self.assertNotIn("api_key", col_name.lower())
                        if isinstance(col_val, str):
                            self.assertNotIn(
                                self.SYNTHETIC_SECRET,
                                col_val,
                                f"CRITICAL: Synthetic secret found in SQLite column {table}.{col_name}",
                            )
        finally:
            conn.close()

        # 5. Domain entity serialization check
        slot_dict = slot_entity.to_dict()
        self.assertNotIn("api_key", slot_dict)
        serialized_chain = [s.to_dict() for s in saved_job.api_chain]
        self.assertNotIn("api_key", str(serialized_chain))
        self.assertNotIn(self.SYNTHETIC_SECRET, str(serialized_chain))

    def test_encrypted_file_store_contains_zero_plaintext_secrets(self):
        """Verifies that the on-disk encrypted vault file contains only ciphertext and zero plaintext secrets."""
        encrypted_store = EncryptedFileCredentialStore(self.vault_path, passphrase=self.passphrase)
        encrypted_store.store_secret("audit_key_id", self.SYNTHETIC_SECRET)

        with open(self.vault_path, "rb") as f:
            vault_bytes = f.read()

        self.assertNotIn(
            self.SYNTHETIC_SECRET.encode("utf-8"),
            vault_bytes,
            "CRITICAL: Plaintext synthetic secret found in encrypted vault file on disk!",
        )

    def test_exceptions_and_logs_do_not_contain_secret_values(self):
        """Verifies that log records and raised exceptions never contain raw secrets."""
        resolver = KeyringCredentialResolver(service_name="audit_polpot")
        cred_ref = CredentialRef(identifier="non_existent_ref", provider="google", slot_type="byok")

        with self.assertRaises(EntityNotFoundError) as ctx:
            resolver.resolve_api_key(cred_ref)
        self.assertNotIn(self.SYNTHETIC_SECRET, str(ctx.exception))

        # Check logs collected during test execution
        for msg in self.log_capture.records:
            self.assertNotIn(
                self.SYNTHETIC_SECRET,
                msg,
                "CRITICAL: Raw secret value found in application log output!",
            )

    def test_ast_security_boundary_rules(self):
        """
        Static AST analysis verifying clean architectural isolation:
        - application/ layer must not import keyring or cryptography.
        - infrastructure/security layer must not import fastapi, telegram, or pymysql.
        """
        app_dir = Path(__file__).parent.parent.parent / "application"
        sec_dir = Path(__file__).parent.parent.parent / "infrastructure" / "security"

        forbidden_app_imports = {"keyring", "cryptography", "sqlite3", "PySide6", "fastapi", "pymysql"}
        forbidden_sec_imports = {"fastapi", "starlette", "telegram", "pymysql", "PySide6", "Qt"}

        # Check application/
        for file in app_dir.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        for forbidden in forbidden_app_imports:
                            self.assertFalse(
                                name.name == forbidden or name.name.startswith(forbidden + "."),
                                f"Forbidden import '{name.name}' found in {file}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_app_imports:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' found in {file}",
                        )

        # Check infrastructure/security/
        for file in sec_dir.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        for forbidden in forbidden_sec_imports:
                            self.assertFalse(
                                name.name == forbidden or name.name.startswith(forbidden + "."),
                                f"Forbidden import '{name.name}' found in {file}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_sec_imports:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' found in {file}",
                        )


if __name__ == "__main__":
    unittest.main()
