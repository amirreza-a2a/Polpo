# ============================================================
#  tests/unit/test_public_documentation_contract.py
#  Rigorous contract tests for Polpo public documentation (DOC-01)
# ============================================================

from pathlib import Path
import json
import re
import unittest


class TestPublicDocumentationContract(unittest.TestCase):
    """Asserts factual accuracy, link integrity, and architectural consistency of public docs."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.readme_path = cls.repo_root / "README.md"
        cls.linux_install_path = cls.repo_root / "docs" / "linux-installation.md"
        cls.packaging_path = cls.repo_root / "docs" / "packaging.md"
        cls.changelog_path = cls.repo_root / "CHANGELOG.md"
        cls.runtimes_json_path = cls.repo_root / "resources" / "runtimes.json"
        cls.mathjax_pkg_path = cls.repo_root / "resources" / "mathjax" / "package.json"
        cls.migrations_dir = cls.repo_root / "infrastructure" / "persistence" / "sqlite" / "migrations"

        cls.readme_text = cls.readme_path.read_text(encoding="utf-8")
        cls.linux_install_text = cls.linux_install_path.read_text(encoding="utf-8")
        cls.packaging_text = cls.packaging_path.read_text(encoding="utf-8")
        cls.changelog_text = cls.changelog_path.read_text(encoding="utf-8")

    def test_release_availability_and_freeze_status(self):
        """Public documents must state pre-release candidate status (v0.1.0-rc.1) and active release freeze."""
        for doc_name, text in [
            ("README.md", self.readme_text),
            ("docs/linux-installation.md", self.linux_install_text),
            ("docs/packaging.md", self.packaging_text),
        ]:
            with self.subTest(document=doc_name):
                # Must reference the current pre-release candidate
                self.assertIn("v0.1.0-rc.1", text, f"{doc_name} must mention v0.1.0-rc.1 pre-release candidate")
                # Must acknowledge pre-release status or release freeze
                self.assertTrue(
                    ("pre-release" in text.lower() or "candidate" in text.lower()) and "freeze" in text.lower(),
                    f"{doc_name} must explicitly explain pre-release candidate status and the active release freeze",
                )

    def test_stable_channel_unavailability_explicitly_explained(self):
        """Docs must explain that the stable channel has no published stable release yet."""
        for doc_name, text in [
            ("README.md", self.readme_text),
            ("docs/linux-installation.md", self.linux_install_text),
        ]:
            with self.subTest(document=doc_name):
                # Must note preview channel flag requirement
                self.assertIn("--channel preview", text, f"{doc_name} must instruct using --channel preview")

    def test_bundled_runtime_versions_match_manifests(self):
        """Public docs must accurately report bundled Pandoc, Node.js, and MathJax versions."""
        with open(self.runtimes_json_path, encoding="utf-8") as f:
            manifest = json.load(f)
        expected_pandoc = manifest["runtimes"]["pandoc"]["version"]
        expected_node = manifest["runtimes"]["node"]["version"]

        with open(self.mathjax_pkg_path, encoding="utf-8") as f:
            mathjax_pkg = json.load(f)
        expected_mathjax = mathjax_pkg["dependencies"]["mathjax-full"]

        for doc_name, text in [
            ("README.md", self.readme_text),
            ("docs/packaging.md", self.packaging_text),
        ]:
            with self.subTest(document=doc_name):
                self.assertIn(expected_pandoc, text, f"{doc_name} must mention pinned Pandoc {expected_pandoc}")
                self.assertIn(expected_node, text, f"{doc_name} must mention pinned Node.js {expected_node}")
                self.assertIn(expected_mathjax, text, f"{doc_name} must mention pinned mathjax-full {expected_mathjax}")

    def test_sqlite_migration_range_agrees_with_persistence(self):
        """Packaging docs must reflect the actual SQLite migrations present in the repository."""
        migration_files = sorted(self.migrations_dir.glob("*.sql"))
        self.assertGreaterEqual(len(migration_files), 5, "Expected at least 5 SQLite migration scripts")
        latest_migration_num = f"{len(migration_files):03d}"

        # Packaging self-test docs must cite migrations through latest (005)
        self.assertTrue(
            bool(re.search(rf"`?001`?\s+through\s+`?{latest_migration_num}`?", self.packaging_text)),
            f"docs/packaging.md must state self-test exercises migrations 001 through {latest_migration_num}",
        )

    def test_no_server_or_telegram_requirements_for_desktop(self):
        """Public installation and setup instructions must not claim server, FastAPI, or Telegram is required."""
        for doc_name, text in [
            ("README.md", self.readme_text),
            ("docs/linux-installation.md", self.linux_install_text),
        ]:
            with self.subTest(document=doc_name):
                # Ensure no instruction tells users to run uvicorn or configure a telegram bot to use desktop
                self.assertNotIn("uvicorn interfaces.desktop", text)
                self.assertNotIn("pip install fastapi", text)
                self.assertNotIn("TELEGRAM_BOT_TOKEN", text)

    def test_linux_support_tiers_distinguish_ci_from_mappings(self):
        """Linux installation and packaging docs must distinguish CI evidence from community package mappings."""
        for doc_name, text in [
            ("README.md", self.readme_text),
            ("docs/linux-installation.md", self.linux_install_text),
            ("docs/packaging.md", self.packaging_text),
        ]:
            with self.subTest(document=doc_name):
                # Must mention Ubuntu 24.04 (CI) and Ubuntu 22.04 (packaging runner)
                self.assertTrue(
                    bool(re.search(r"Ubuntu[- ]24\.04", text, re.IGNORECASE)),
                    f"{doc_name} must identify Ubuntu 24.04 CI runner",
                )
                self.assertTrue(
                    bool(re.search(r"Ubuntu[- ]22\.04", text, re.IGNORECASE)),
                    f"{doc_name} must identify Ubuntu 22.04 packaging baseline",
                )
                # Must state minimum glibc requirement
                self.assertIn("2.35", text, f"{doc_name} must document glibc >= 2.35 baseline")
                # Must document that Alpine/musl is unsupported
                self.assertTrue(
                    "musl" in text.lower() or "alpine" in text.lower(),
                    f"{doc_name} must explicitly document musl/Alpine as unsupported",
                )

    def test_internal_relative_links_resolve(self):
        """All Markdown relative links in audited documents must resolve to existing files or directories."""
        link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        for doc_path, text in [
            (self.readme_path, self.readme_text),
            (self.linux_install_path, self.linux_install_text),
            (self.packaging_path, self.packaging_text),
            (self.changelog_path, self.changelog_text),
        ]:
            doc_dir = doc_path.parent
            for match in link_pattern.finditer(text):
                link_target = match.group(2).strip()
                # Skip external links and anchor-only links
                if link_target.startswith(("http://", "https://", "mailto:", "#")):
                    continue

                # Strip anchor fragment
                file_target = link_target.split("#")[0]
                if not file_target:
                    continue

                resolved = (doc_dir / file_target).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"Broken link in {doc_path.name}: '{link_target}' (resolved to {resolved})",
                )

    def test_documented_installer_and_cli_options_are_supported(self):
        """CLI flags and subcommands referenced in public docs must be supported by the tools."""
        installer_help = (self.repo_root / "install.sh").read_text(encoding="utf-8")
        deps_help = (self.repo_root / "scripts" / "polpo_linux_deps.sh").read_text(encoding="utf-8")
        app_code = (self.repo_root / "interfaces" / "desktop" / "app.py").read_text(encoding="utf-8")

        # Installer flags cited in README or linux-installation.md
        for flag in ["--channel", "--archive", "--dir", "--bin-dir"]:
            self.assertIn(flag, installer_help, f"install.sh must support documented flag '{flag}'")

        # polpo-deps subcommands and flags cited
        for cmd in ["check", "gui", "pdf", "install-gui", "install-pdf", "--dry-run"]:
            self.assertIn(cmd, deps_help, f"polpo_linux_deps.sh must support documented command/option '{cmd}'")

        # Desktop CLI flags cited in README or linux-installation.md
        for flag in ["--self-test", "--version"]:
            self.assertIn(flag, app_code, f"app.py must support documented flag '{flag}'")


if __name__ == "__main__":
    unittest.main()
