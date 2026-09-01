# ============================================================
#  tests/unit/test_architecture_boundaries.py
#  Architectural Boundary Enforcement for PolpoT Desktop
# ============================================================

import ast
import unittest
from pathlib import Path


class TestArchitectureBoundaries(unittest.TestCase):
    """
    Automated AST tests enforcing strict Clean Architecture dependency directions:
      core/          -> Pure Python domain (Zero external framework imports)
      application/   -> Application services & ports (Zero UI, DB drivers, or concrete infrastructure)
      presentation/  -> Desktop UI (Zero direct SQL, zero direct DB/keyring imports, zero Telegram dependencies)
      composition/   -> Desktop composition root has zero dependencies on legacy Telegram/MySQL container
    """

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent.parent.parent
        cls.core_dir = cls.root_dir / "core"
        cls.app_dir = cls.root_dir / "application"
        cls.desktop_dir = cls.root_dir / "interfaces" / "desktop"
        cls.infra_dir = cls.root_dir / "infrastructure"

    def test_core_layer_purity(self):
        """
        Rule: core/ must NOT import UI, persistence drivers, secret stores, server frameworks,
        concrete infrastructure, presentation, application services, or vendor SDKs.
        """
        forbidden_in_core = {
            "PySide6", "PyQt6", "PyQt5", "PySide2", "Qt",
            "sqlite3", "pymysql", "sqlalchemy",
            "keyring", "cryptography",
            "fastapi", "uvicorn", "starlette",
            "infrastructure", "interfaces", "application",
            "telegram", "google", "openai", "PIL", "fitz", "pydantic",
        }

        for py_file in self.core_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_in_core:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Forbidden import '{alias.name}' in core file: {py_file}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_in_core:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' in core file: {py_file}",
                        )

    def test_application_layer_purity(self):
        """
        Rule: application/ must NOT import UI, database drivers, secret store backends,
        server frameworks, concrete infrastructure modules, presentation, or vendor SDKs.
        """
        forbidden_in_app = {
            "PySide6", "PyQt6", "PyQt5", "PySide2", "Qt",
            "sqlite3", "pymysql", "sqlalchemy",
            "keyring", "cryptography",
            "fastapi", "uvicorn", "starlette",
            "infrastructure", "interfaces",
            "telegram", "google", "openai", "PIL", "fitz",
        }

        for py_file in self.app_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_in_app:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Forbidden import '{alias.name}' in application file: {py_file}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_in_app:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden from-import '{mod}' in application file: {py_file}",
                        )

    def test_desktop_presentation_layer_boundaries(self):
        """
        Rule: interfaces/desktop/controllers/ and interfaces/desktop/models/ must NOT directly
        import persistence drivers, secret stores, vendor AI SDKs, or legacy Telegram modules.
        """
        forbidden_in_presentation = {
            "sqlite3", "pymysql", "sqlalchemy",
            "keyring", "cryptography.fernet",
            "infrastructure.persistence",
            "google", "openai",
            "handlers", "interfaces.telegram", "services.worker",
            "infrastructure.composition",
        }

        for target_folder in [self.desktop_dir / "controllers", self.desktop_dir / "models"]:
            for py_file in target_folder.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for forbidden in forbidden_in_presentation:
                                self.assertFalse(
                                    alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                    f"Forbidden import '{alias.name}' in presentation file: {py_file}",
                                )
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        for forbidden in forbidden_in_presentation:
                            self.assertFalse(
                                mod == forbidden or mod.startswith(forbidden + "."),
                                f"Forbidden from-import '{mod}' in presentation file: {py_file}",
                            )

    def test_zero_direct_database_operations_in_presentation_and_application(self):
        """
        Mandatory Correction 1: Structural check verifying that interfaces/desktop/ and application/
        never perform direct database connection or raw cursor/connection execution calls.
        """
        forbidden_call_names = {"connect", "executescript"}

        for scan_dir in [self.desktop_dir, self.app_dir]:
            for py_file in scan_dir.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        # Detect sqlite3.connect(...) or pymysql.connect(...)
                        if isinstance(node.func, ast.Attribute):
                            if isinstance(node.func.value, ast.Name):
                                caller = node.func.value.id
                                method = node.func.attr
                                if caller in {"sqlite3", "pymysql", "db"} and method in forbidden_call_names:
                                    self.fail(f"Direct DB call '{caller}.{method}' found in: {py_file}")
                                if method == "executescript":
                                    self.fail(f"Direct DB executescript call found in: {py_file}")

    def test_desktop_composition_root_isolation(self):
        """
        Rule: DesktopAppContainer (interfaces/desktop/composition.py) must have ZERO imports or
        references to the legacy MySQL composition root (infrastructure/composition.py) or Telegram modules.
        """
        comp_file = self.desktop_dir / "composition.py"
        tree = ast.parse(comp_file.read_text(encoding="utf-8"), filename=str(comp_file))

        forbidden_in_desktop_comp = {
            "infrastructure.composition",
            "handlers",
            "interfaces.telegram",
            "services.worker",
            "database.connection",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_in_desktop_comp:
                        self.assertFalse(
                            alias.name == forbidden or alias.name.startswith(forbidden + "."),
                            f"DesktopAppContainer must not import '{alias.name}'",
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in forbidden_in_desktop_comp:
                    self.assertFalse(
                        mod == forbidden or mod.startswith(forbidden + "."),
                        f"DesktopAppContainer must not from-import '{mod}'",
                    )


if __name__ == "__main__":
    unittest.main()
