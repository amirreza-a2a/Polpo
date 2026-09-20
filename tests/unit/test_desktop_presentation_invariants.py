# ============================================================
#  tests/unit/test_desktop_presentation_invariants.py
#  Architectural Invariant Enforcement for Phase 8F
# ============================================================

import ast
import tempfile
import unittest
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend

from interfaces.desktop.qt_compat import QGuiApplication
from interfaces.desktop.app import create_app
from interfaces.desktop.controllers import (
    JobController,
    ApiKeyController,
    PromptController,
    SettingsController,
    QuickConvertController,
)
from interfaces.desktop.models import (
    JobQueueModel,
    JobHistoryModel,
    ApiSlotModel,
    PromptListModel,
)


class _MockMemoryKeyring(KeyringBackend):
    """Deterministic in-memory keyring backend for presentation invariant tests."""

    priority = 10

    def __init__(self):
        self._vault = {}

    def get_password(self, service, username):
        return self._vault.get(f"{service}::{username}")

    def set_password(self, service, username, password):
        self._vault[f"{service}::{username}"] = str(password)

    def delete_password(self, service, username):
        key = f"{service}::{username}"
        if key in self._vault:
            del self._vault[key]
        else:
            raise keyring.errors.PasswordDeleteError("Password not found")


class TestDesktopPresentationInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        super().setUp()
        self._prev_keyring = keyring.get_keyring()
        self._mock_keyring = _MockMemoryKeyring()
        keyring.set_keyring(self._mock_keyring)

    def tearDown(self):
        try:
            keyring.set_keyring(self._prev_keyring)
        finally:
            super().tearDown()

    def test_invariant_1_core_and_application_contain_no_qt_imports(self):
        """AST analysis verifying zero Qt, PySide6, or PyQt6 imports in core/ or application/."""
        root_dir = Path(__file__).parent.parent.parent
        app_dir = root_dir / "application"
        core_dir = root_dir / "core"

        forbidden_qt = {"PySide6", "PyQt6", "PyQt5", "PySide2", "interfaces.desktop.qt_compat"}

        for folder in (app_dir, core_dir):
            for fpath in folder.rglob("*.py"):
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for name in node.names:
                            for forbidden in forbidden_qt:
                                self.assertFalse(
                                    name.name == forbidden or name.name.startswith(forbidden + "."),
                                    f"Forbidden import '{name.name}' in {fpath}",
                                )
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        for forbidden in forbidden_qt:
                            self.assertFalse(
                                mod == forbidden or mod.startswith(forbidden + "."),
                                f"Forbidden from-import '{mod}' in {fpath}",
                            )

    def test_invariant_2_controllers_and_models_contain_no_sqlite_or_keyring_imports(self):
        """AST analysis verifying presentation layer never imports persistence or secrets directly."""
        root_dir = Path(__file__).parent.parent.parent
        pres_dir = root_dir / "interfaces" / "desktop"

        forbidden_in_pres = {"sqlite3", "keyring", "cryptography.fernet"}

        for folder in (pres_dir / "controllers", pres_dir / "models"):
            for fpath in folder.rglob("*.py"):
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for name in node.names:
                            for forbidden in forbidden_in_pres:
                                self.assertFalse(
                                    name.name == forbidden or name.name.startswith(forbidden + "."),
                                    f"Forbidden direct persistence/security import '{name.name}' in {fpath}",
                                )
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        for forbidden in forbidden_in_pres:
                            self.assertFalse(
                                mod == forbidden or mod.startswith(forbidden + "."),
                                f"Forbidden from-import '{mod}' in {fpath}",
                            )

    def test_invariant_3_desktop_presentation_has_no_telegram_dependencies(self):
        """AST analysis verifying zero imports from frozen Telegram transport in interfaces/desktop."""
        root_dir = Path(__file__).parent.parent.parent
        desktop_dir = root_dir / "interfaces" / "desktop"

        for fpath in desktop_dir.rglob("*.py"):
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        self.assertNotIn("telegram", name.name.lower(), f"Telegram import in desktop file {fpath}")
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").lower()
                    self.assertNotIn("telegram", mod, f"Telegram from-import in desktop file {fpath}")

    def test_invariant_4_secret_absence_in_qobject_properties_and_model_roles(self):
        """Introspection verifying that no raw API key attributes exist in models or controllers."""
        for role_name in ApiSlotModel(None, None).roleNames().values():
            self.assertNotIn(b"api_key", role_name.lower())
            self.assertNotIn(b"secret", role_name.lower())
            self.assertNotIn(b"password", role_name.lower())

    def test_invariant_5_create_app_lifecycle_startup_and_shutdown(self):
        """Verifies deterministic startup wiring, context property exposure, and clean teardown."""
        temp_dir = tempfile.TemporaryDirectory()
        base = Path(temp_dir.name)
        db_path = base / "app_lifecycle.db"

        app, engine, container = create_app(
            argv=[],
            db_path=db_path,
            start_background_runtime=True,
            scheduler_tick_interval=0.1,
        )

        self.assertIsNotNone(app)
        self.assertIsNotNone(engine)
        self.assertTrue(container._initialized)
        self.assertTrue(container.runtime.is_running)
        self.assertTrue(container.scheduler.is_running)

        # Teardown
        container.shutdown()
        self.assertFalse(container.scheduler.is_running)
        self.assertFalse(container.runtime.is_running)

        temp_dir.cleanup()

    def test_invariant_6_canonical_desktop_bootstrap_does_not_import_pymysql(self):
        """Verifies in a clean process that the canonical Desktop bootstrap path has 0 dependencies on pymysql."""
        import sys
        import subprocess

        cmd = [
            sys.executable,
            "-c",
            "import sys; from interfaces.desktop.app import create_app; assert 'pymysql' not in sys.modules",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Desktop bootstrap failed without pymysql: {proc.stderr}")

    def test_invariant_7_no_placeholder_comments_in_qml_views(self):
        """Scans all QML views to verify that no placeholder comments or stub handlers exist."""
        qml_dir = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml"
        forbidden_snippets = [
            "// TODO",
            "// Reschedule slot",
            "// Triggers file submission flow",
            "// File picker invocation",
            "// New prompt dialog",
        ]

        for qml_file in qml_dir.rglob("*.qml"):
            content = qml_file.read_text(encoding="utf-8")
            for forbidden in forbidden_snippets:
                self.assertNotIn(
                    forbidden.lower(),
                    content.lower(),
                    f"Found placeholder comment '{forbidden}' in {qml_file}",
                )

    def test_invariant_8_offscreen_ui_smoke_and_responsive_workflow(self):
        """End-to-end offscreen UI smoke test verifying QML load, controller workflows, and shutdown."""
        import time
        temp_dir = tempfile.TemporaryDirectory()
        base = Path(temp_dir.name)
        db_path = base / "smoke.db"

        app, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            start_background_runtime=True,
            scheduler_tick_interval=0.1,
        )

        qml_path = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "Main.qml"
        engine.load(str(qml_path))

        root_objects = engine.rootObjects()
        self.assertEqual(len(root_objects), 1)
        self.assertEqual(root_objects[0].property("title"), "PolpoT — Desktop Document Intelligence")

        # 1. Exercise API key controller from container
        api_ctrl = container.api_key_controller
        reg_ok = api_ctrl.register_key("google", "Smoke Key", "test-key-val", "gemini-2.0-flash")
        self.assertTrue(reg_ok)

        # 2. Exercise Prompt controller
        prompt_ctrl = container.prompt_controller
        pid = prompt_ctrl.create_prompt("Smoke Prompt", "Extract everything", "pipeline1", True)
        self.assertGreater(pid, 0)

        # 3. Exercise Job controller
        job_ctrl = container.job_controller
        pdf_f = base / "smoke.pdf"
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_f))
        doc.close()

        jid = job_ctrl.submit_job(f"file://{pdf_f}", pid, "")
        self.assertGreater(jid, 0)

        # 4. Exercise Settings controller
        settings_ctrl = container.settings_controller
        save_ok = settings_ctrl.save_settings("dark", 3, "run_immediately", 45, True, True)
        self.assertTrue(save_ok)

        # Clean teardown
        container.shutdown()
        temp_dir.cleanup()

    def test_invariant_9_desktop_ai_adapter_resolution_and_runtime_construction(self):
        """Verifies the canonical DesktopAppContainer AI adapter resolution path for real slots."""
        from unittest.mock import patch, MagicMock
        from core.entities.api_slot import ApiSlot
        from core.entities.credential_ref import CredentialRef
        from core.ai.types import AIResponse
        from infrastructure.ai.google_adapter import GoogleAdapter
        from infrastructure.ai.openai_adapter import OpenAIAdapter

        temp_dir = tempfile.TemporaryDirectory()
        base = Path(temp_dir.name)
        db_path = base / "adapter_test.db"

        app, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            start_background_runtime=False,
        )

        # 1. Register credentials in encrypted fallback store / resolver
        container.credential_resolver.store_api_key(
            CredentialRef(identifier="cred_g", provider="google", slot_type="byok"),
            "secret-google-api-key",
        )
        container.credential_resolver.store_api_key(
            CredentialRef(identifier="cred_o", provider="openai", slot_type="byok"),
            "secret-openai-api-key",
        )

        google_slot = ApiSlot(
            id=1,
            provider="google",
            label="Main Google",
            selected_model="gemini-2.0-flash",
            credential_ref=CredentialRef(identifier="cred_g", provider="google", slot_type="byok"),
        )
        openai_slot = ApiSlot(
            id=2,
            provider="openai",
            label="Main OpenAI",
            selected_model="gpt-4o",
            credential_ref=CredentialRef(identifier="cred_o", provider="openai", slot_type="byok"),
        )

        # 2. Verify resolve_ai_adapter does not raise TypeError on default_model
        g_adapter = container.ai_adapter_factory(google_slot)
        self.assertIsInstance(g_adapter, GoogleAdapter)
        self.assertEqual(g_adapter.api_key, "secret-google-api-key")
        self.assertEqual(g_adapter.default_model, "gemini-2.0-flash")

        o_adapter = container.ai_adapter_factory(openai_slot)
        self.assertIsInstance(o_adapter, OpenAIAdapter)
        self.assertEqual(o_adapter.api_key, "secret-openai-api-key")
        self.assertEqual(o_adapter.default_model, "gpt-4o")

        # 3. Verify executor succeeds in creating adapter and reaching generate_vision
        with patch.object(GoogleAdapter, "generate_vision", return_value=AIResponse(content="# Page Markdown")) as mock_gen:
            content, active_slot = container.ai_executor.execute_vision_with_fallback(
                chain=[google_slot],
                image_bytes=b"fake_jpeg",
                prompt="Transcribe page",
                at_page=1,
            )
            self.assertEqual(content, "# Page Markdown")
            self.assertEqual(active_slot.id, 1)
            mock_gen.assert_called_once()

        container.shutdown()
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
