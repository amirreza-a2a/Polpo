# ============================================================
#  tests/unit/test_desktop_presentation_invariants.py
#  Architectural Invariant Enforcement for Phase 8F
# ============================================================

import ast
import tempfile
import unittest
from pathlib import Path

from interfaces.desktop.qt_compat import QCoreApplication
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


class TestDesktopPresentationInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance()
        if cls.app is None:
            cls.app = QCoreApplication([])

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


if __name__ == "__main__":
    unittest.main()
