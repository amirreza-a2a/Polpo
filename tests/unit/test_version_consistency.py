# ============================================================
#  tests/unit/test_version_consistency.py
#  Phase 13 — Ticket TICK-REL-01: Version, Metadata, & Licensing Verification
# ============================================================

import ast
from pathlib import Path
import re
import unittest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

import core.version as version_mod

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class TestVersionConsistency(unittest.TestCase):
    """
    Automated verification of version single-source of truth,
    PEP 621 packaging metadata, clean desktop requirements isolation,
    and AGPL-3.0 licensing compliance.
    """

    def test_version_string_matches_semver_format(self):
        """core.version.__version__ must be a valid Semantic Version 2.0 string."""
        self.assertIsInstance(version_mod.__version__, str)
        self.assertRegex(
            version_mod.__version__,
            SEMVER_REGEX,
            f"Version string '{version_mod.__version__}' does not adhere to SemVer 2.0 specification",
        )

    def test_version_info_matches_version_string(self):
        """core.version.VERSION_INFO must match the major, minor, patch of __version__."""
        parts = version_mod.__version__.split("-")[0].split("+")[0].split(".")
        expected_tuple = tuple(int(p) for p in parts)
        self.assertEqual(version_mod.VERSION_INFO[:3], expected_tuple)

    def test_app_name_constant(self):
        """core.version.APP_NAME must be 'Polpo'."""
        self.assertEqual(version_mod.APP_NAME, "Polpo")

    def test_pyproject_matches_core_version(self):
        """pyproject.toml [project].version must match core.version.__version__ exactly."""
        pyproject_path = REPO_ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "pyproject.toml must exist")

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        project_table = data.get("project", {})
        self.assertEqual(
            project_table.get("name"),
            "polpo",
            "Project name in pyproject.toml must be 'polpo'",
        )
        self.assertEqual(
            project_table.get("version"),
            version_mod.__version__,
            f"pyproject.toml version ({project_table.get('version')}) must match "
            f"core.version.__version__ ({version_mod.__version__})",
        )

    def test_core_version_ast_purity(self):
        """
        core/version.py must be pure Python with zero imports or I/O calls,
        strictly obeying core/ architectural invariants.
        """
        version_file = REPO_ROOT / "core" / "version.py"
        self.assertTrue(version_file.is_file(), "core/version.py must exist")

        tree = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))

        for node in ast.walk(tree):
            self.assertNotIsInstance(
                node,
                (ast.Import, ast.ImportFrom),
                f"Forbidden import statement found in core/version.py: {ast.dump(node)}",
            )
            if isinstance(node, ast.Call):
                func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                self.assertNotIn(
                    func_name,
                    {"open", "exec", "eval", "read", "write", "print"},
                    f"Forbidden callable '{func_name}' found in core/version.py",
                )

    def test_requirements_desktop_isolates_legacy_transports(self):
        """
        requirements-desktop.txt must contain all essential desktop dependencies
        and strictly exclude legacy Telegram/MySQL server dependencies.
        """
        req_desktop_path = REPO_ROOT / "requirements-desktop.txt"
        self.assertTrue(req_desktop_path.is_file(), "requirements-desktop.txt must exist")

        req_text = req_desktop_path.read_text(encoding="utf-8")
        req_lines = [
            line.strip().lower()
            for line in req_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        # Must NOT contain frozen legacy transport dependencies
        forbidden_deps = ["python-telegram-bot", "pymysql"]
        for forbidden in forbidden_deps:
            for line in req_lines:
                self.assertFalse(
                    line.startswith(forbidden),
                    f"Forbidden legacy dependency '{forbidden}' found in requirements-desktop.txt: '{line}'",
                )

        # Must contain required desktop dependencies
        required_deps = [
            "pyside6",
            "pymupdf",
            "pillow",
            "keyring",
            "cryptography",
            "platformdirs",
            "httpx",
            "pydantic",
            "python-dotenv",
            "google-genai",
            "openai",
        ]
        for required in required_deps:
            found = any(line.startswith(required) for line in req_lines)
            self.assertTrue(
                found,
                f"Expected desktop dependency '{required}' not found in requirements-desktop.txt",
            )

    def test_license_file_exists_and_agpl(self):
        """LICENSE file must exist in repository root and contain AGPL-3.0 with third-party notices."""
        license_path = REPO_ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "LICENSE file must exist in repository root")

        license_text = license_path.read_text(encoding="utf-8")
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3", license_text)
        self.assertIn("PyMuPDF", license_text)
        self.assertIn("PySide6", license_text)
        self.assertIn("Pandoc", license_text)
        self.assertIn("MathJax", license_text)
        self.assertIn("Node.js", license_text)

    def test_pyproject_dependencies_match_requirements_desktop(self):
        """pyproject.toml [project].dependencies must match requirements-desktop.txt."""
        pyproject_path = REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        project_deps = set(data.get("project", {}).get("dependencies", []))

        req_desktop_path = REPO_ROOT / "requirements-desktop.txt"
        desktop_lines = [
            line.strip()
            for line in req_desktop_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        desktop_deps = set(desktop_lines)

        self.assertEqual(
            project_deps,
            desktop_deps,
            f"pyproject.toml dependencies do not match requirements-desktop.txt: "
            f"diff={project_deps ^ desktop_deps}",
        )

    def test_pyproject_setuptools_package_discovery_configuration(self):
        """
        pyproject.toml must configure [tool.setuptools.packages.find]
        to prevent multiple top-level package discovery build failures in flat layout,
        and must exclude legacy server/bot directories per AGENTS.md Rule 4.
        """
        pyproject_path = REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        tool_setuptools = data.get("tool", {}).get("setuptools", {})
        self.assertIn("packages", tool_setuptools, "tool.setuptools must define packages")
        packages_find = tool_setuptools["packages"].get("find", {})
        self.assertIn("include", packages_find, "packages.find must specify include")
        self.assertIn("exclude", packages_find, "packages.find must specify exclude")

        include_patterns = packages_find["include"]
        for expected_pkg in ["core*", "application*", "infrastructure*", "interfaces*"]:
            self.assertIn(expected_pkg, include_patterns)

        exclude_patterns = packages_find["exclude"]
        for legacy_pkg in ["handlers*", "database*", "interfaces.telegram*", "tests*"]:
            self.assertIn(legacy_pkg, exclude_patterns)

    def test_pyproject_gui_scripts_entrypoint(self):
        """pyproject.toml [project.gui-scripts] must point to interfaces.desktop.app:main."""
        pyproject_path = REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        gui_scripts = data.get("project", {}).get("gui-scripts", {})
        self.assertEqual(gui_scripts.get("polpo"), "interfaces.desktop.app:main")

        # Verify entrypoint is importable and callable
        import importlib
        module_name, func_name = gui_scripts["polpo"].split(":")
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name, None)
        self.assertTrue(callable(func), f"Entrypoint '{gui_scripts['polpo']}' is not callable")

    def test_requirements_desktop_matches_requirements_prod_desktop_subset(self):
        """All desktop dependencies in requirements-desktop.txt must exist in requirements.txt."""
        req_desktop_path = REPO_ROOT / "requirements-desktop.txt"
        desktop_lines = [
            line.strip()
            for line in req_desktop_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        req_prod_path = REPO_ROOT / "requirements.txt"
        prod_lines = [
            line.strip()
            for line in req_prod_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        for dep in desktop_lines:
            self.assertIn(
                dep,
                prod_lines,
                f"Desktop requirement '{dep}' is missing from legacy requirements.txt",
            )

    def test_changelog_file_exists_and_tracks_release(self):
        """CHANGELOG.md must exist in repository root and mention the initial release version."""
        changelog_path = REPO_ROOT / "CHANGELOG.md"
        self.assertTrue(changelog_path.is_file(), "CHANGELOG.md must exist in repository root")

        changelog_text = changelog_path.read_text(encoding="utf-8")
        self.assertIn("Keep a Changelog", changelog_text)
        self.assertIn(version_mod.__version__, changelog_text)


if __name__ == "__main__":
    unittest.main()
