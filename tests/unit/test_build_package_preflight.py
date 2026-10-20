# ============================================================
#  tests/unit/test_build_package_preflight.py
# ============================================================

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import scripts.build_package as build_package
from core.version import __version__


class TestBuildPackagePreflight(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._populate_valid_repo(self.repo_root)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _populate_valid_repo(self, root: Path, platform_name: str = "linux-x86_64") -> None:
        """Populates a mock repository directory containing all mandatory assets."""
        # Spec file
        (root / "packaging").mkdir(parents=True, exist_ok=True)
        (root / "packaging" / "polpo.spec").write_text("# mock spec", encoding="utf-8")

        # License and documentation
        (root / "LICENSE").write_text("AGPL-3.0-only", encoding="utf-8")
        (root / "README.md").write_text("# Polpo", encoding="utf-8")
        (root / "CHANGELOG.md").write_text("# Changelog", encoding="utf-8")

        # QML interface
        qml_dir = root / "interfaces" / "desktop" / "qml"
        qml_dir.mkdir(parents=True, exist_ok=True)
        (qml_dir / "Main.qml").write_text("import QtQuick\nItem {}", encoding="utf-8")

        # SQL migrations
        migrations_dir = root / "infrastructure" / "persistence" / "sqlite" / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        for m in [
            "001_initial_schema.sql",
            "002_visual_regions.sql",
            "003_artifact_version_watermarks.sql",
            "004_publish_intents_and_document_versions.sql",
        ]:
            (migrations_dir / m).write_text("-- migration", encoding="utf-8")

        # LaTeX template & manifest
        templates_dir = root / "resources" / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "polpo_preamble.tex").write_text("% preamble", encoding="utf-8")
        (root / "resources" / "runtimes.json").write_text("{}", encoding="utf-8")

        # Curated prompts
        fa_dir = root / "prompts" / "templates" / "fa"
        en_dir = root / "prompts" / "templates" / "en"
        p2_dir = root / "prompts" / "pipeline2"
        fa_dir.mkdir(parents=True, exist_ok=True)
        en_dir.mkdir(parents=True, exist_ok=True)
        p2_dir.mkdir(parents=True, exist_ok=True)

        for f in [
            fa_dir / "stem_transcription.txt",
            fa_dir / "stem_transcription_with_figure_description.txt",
            fa_dir / "stem_transcription_with_figure_description_and_netlist.txt",
            fa_dir / "educational_stem_transcription_griffiths.txt",
            fa_dir / "electromagnetics_griffiths_editor.txt",
            fa_dir / "stem_transcription_tikz.txt",
            fa_dir / "quick_convert.txt",
            en_dir / "stem_handwritten_transcription.txt",
            p2_dir / "unify_markdown.txt",
        ]:
            f.write_text("Prompt content", encoding="utf-8")

        # Bundled runtimes (platform specific)
        if platform_name == "linux-x86_64":
            pandoc_path = root / "resources" / "bin" / "linux-x86_64" / "pandoc"
            node_path = root / "resources" / "mathjax" / "node"
        elif platform_name == "windows-x64":
            pandoc_path = root / "resources" / "bin" / "windows-x64" / "pandoc.exe"
            node_path = root / "resources" / "mathjax" / "node.exe"
        else:
            pandoc_path = root / "resources" / "bin" / platform_name / "pandoc"
            node_path = root / "resources" / "mathjax" / "node"

        pandoc_path.parent.mkdir(parents=True, exist_ok=True)
        pandoc_path.write_bytes(b"dummy_pandoc")
        node_path.parent.mkdir(parents=True, exist_ok=True)
        node_path.write_bytes(b"dummy_node")

        # MathJax worker & node_modules/mathjax-full
        mathjax_dir = root / "resources" / "mathjax"
        (mathjax_dir / "mathjax_worker.js").write_text("// worker", encoding="utf-8")
        (mathjax_dir / "package.json").write_text("{}", encoding="utf-8")
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        mj_full = mathjax_dir / "node_modules" / "mathjax-full"
        mj_full.mkdir(parents=True, exist_ok=True)
        (mj_full / "package.json").write_text("{}", encoding="utf-8")

    def test_preflight_passes_on_complete_repository(self):
        """Preflight passes with zero errors on a complete, staged mock repository."""
        report = build_package.run_preflight_checks(
            repo_root=self.repo_root,
            target_platform="linux-x86_64",
        )
        self.assertTrue(report.is_valid, f"Preflight errors: {report.errors}")
        self.assertEqual(len(report.errors), 0)

    def test_missing_pandoc_fails_preflight(self):
        """Missing staged Pandoc executable fails preflight fast before PyInstaller starts."""
        pandoc_path = self.repo_root / "resources" / "bin" / "linux-x86_64" / "pandoc"
        pandoc_path.unlink()

        report = build_package.run_preflight_checks(
            repo_root=self.repo_root,
            target_platform="linux-x86_64",
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("pandoc" in err.lower() for err in report.errors))

    def test_missing_node_fails_preflight(self):
        """Missing staged Node.js executable fails preflight fast."""
        node_path = self.repo_root / "resources" / "mathjax" / "node"
        node_path.unlink()

        report = build_package.run_preflight_checks(
            repo_root=self.repo_root,
            target_platform="linux-x86_64",
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("node" in err.lower() for err in report.errors))

    def test_missing_mathjax_full_fails_preflight(self):
        """Missing node_modules/mathjax-full directory fails preflight fast."""
        mj_full = self.repo_root / "resources" / "mathjax" / "node_modules" / "mathjax-full"
        import shutil
        shutil.rmtree(mj_full)

        report = build_package.run_preflight_checks(
            repo_root=self.repo_root,
            target_platform="linux-x86_64",
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("mathjax-full" in err.lower() for err in report.errors))

    def test_missing_qml_preamble_or_migrations_fail_preflight(self):
        """Missing Main.qml, polpo_preamble.tex, or SQL migrations fail preflight."""
        # 1. Missing Main.qml
        qml = self.repo_root / "interfaces" / "desktop" / "qml" / "Main.qml"
        qml.unlink()
        report = build_package.run_preflight_checks(self.repo_root, "linux-x86_64")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("main.qml" in err.lower() for err in report.errors))
        qml.write_text("Item {}", encoding="utf-8")

        # 2. Missing polpo_preamble.tex
        preamble = self.repo_root / "resources" / "templates" / "polpo_preamble.tex"
        preamble.unlink()
        report = build_package.run_preflight_checks(self.repo_root, "linux-x86_64")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("polpo_preamble.tex" in err.lower() for err in report.errors))
        preamble.write_text("%", encoding="utf-8")

        # 3. Missing migration
        sql = self.repo_root / "infrastructure" / "persistence" / "sqlite" / "migrations" / "004_publish_intents_and_document_versions.sql"
        sql.unlink()
        report = build_package.run_preflight_checks(self.repo_root, "linux-x86_64")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("004" in err for err in report.errors))

    def test_missing_prompts_or_license_fail_preflight(self):
        """Missing curated prompts or LICENSE file fails preflight."""
        # Missing LICENSE
        license_file = self.repo_root / "LICENSE"
        license_file.unlink()
        report = build_package.run_preflight_checks(self.repo_root, "linux-x86_64")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("license" in err.lower() for err in report.errors))
        license_file.write_text("AGPL-3.0-only", encoding="utf-8")

        # Missing curated prompt
        prompt = self.repo_root / "prompts" / "templates" / "fa" / "stem_transcription.txt"
        prompt.unlink()
        report = build_package.run_preflight_checks(self.repo_root, "linux-x86_64")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("stem_transcription.txt" in err.lower() for err in report.errors))

    def test_archive_naming_convention(self):
        """Archive names adhere strictly to Polpo-v{version}-{platform}.{tar.gz|zip}."""
        linux_name = build_package.get_archive_filename("0.1.0", "linux-x86_64")
        windows_name = build_package.get_archive_filename("0.1.0", "windows-x64")

        self.assertEqual(linux_name, "Polpo-v0.1.0-linux-x86_64.tar.gz")
        self.assertEqual(windows_name, "Polpo-v0.1.0-windows-x64.zip")

    def test_checksum_sidecar_generation(self):
        """Creates a standard GNU sha256sum sidecar file."""
        test_file = self.repo_root / "test_artifact.tar.gz"
        test_file.write_bytes(b"hello world package content")
        sidecar_path = build_package.generate_checksum_sidecar(test_file)

        self.assertTrue(sidecar_path.is_file())
        self.assertEqual(sidecar_path.name, "test_artifact.tar.gz.sha256")
        expected_hash = hashlib.sha256(b"hello world package content").hexdigest()
        content = sidecar_path.read_text(encoding="utf-8").strip()
        self.assertEqual(content, f"{expected_hash}  test_artifact.tar.gz")

    def test_deterministic_archive_generation_tar_gz(self):
        """Creating tar.gz twice from identical directory contents yields identical digests."""
        source_dir = self.repo_root / "stage_bundle"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "b.txt").write_bytes(b"file B content")
        (source_dir / "a.txt").write_bytes(b"file A content")
        sub = source_dir / "sub"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "c.txt").write_bytes(b"file C content")

        out1 = self.repo_root / "dist1.tar.gz"
        out2 = self.repo_root / "dist2.tar.gz"

        build_package.create_deterministic_tar_gz(source_dir, out1, root_arcname="Polpo")
        build_package.create_deterministic_tar_gz(source_dir, out2, root_arcname="Polpo")

        digest1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        digest2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(digest1, digest2)

    def test_deterministic_archive_generation_zip(self):
        """Creating zip twice from identical directory contents yields identical digests."""
        source_dir = self.repo_root / "stage_bundle_zip"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "b.txt").write_bytes(b"file B content")
        (source_dir / "a.txt").write_bytes(b"file A content")
        sub = source_dir / "sub"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "c.txt").write_bytes(b"file C content")

        out1 = self.repo_root / "dist1.zip"
        out2 = self.repo_root / "dist2.zip"

        build_package.create_deterministic_zip(source_dir, out1, root_arcname="Polpo")
        build_package.create_deterministic_zip(source_dir, out2, root_arcname="Polpo")

        digest1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        digest2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(digest1, digest2)

    def test_spec_file_package_exclusions(self):
        """packaging/polpo.spec explicitly excludes legacy Telegram, MySQL, and test suites."""
        real_spec = Path(__file__).resolve().parents[2] / "packaging" / "polpo.spec"
        self.assertTrue(real_spec.is_file(), f"polpo.spec not found at {real_spec}")
        content = real_spec.read_text(encoding="utf-8")

        for excluded_module in [
            "telegram",
            "python-telegram-bot",
            "pymysql",
            "handlers",
            "interfaces.telegram",
            "database",
            "tests",
            "pytest",
            "unittest",
        ]:
            self.assertIn(f'"{excluded_module}"', content, f"Expected {excluded_module} in spec excludes")

    def test_cross_compilation_rejection_in_build_package(self):
        """Attempting to build for a foreign platform raises RuntimeError before PyInstaller starts."""
        host = build_package.detect_host_platform()
        foreign = "windows-x64" if host != "windows-x64" else "linux-x86_64"

        with self.assertRaises(RuntimeError) as ctx:
            build_package.build_package(
                repo_root=self.repo_root,
                target_platform=foreign,
            )
        self.assertIn("does not support cross-compilation", str(ctx.exception))

    def test_create_stable_alias(self):
        """create_stable_alias creates unversioned archive copy and valid sha256 sidecar."""
        dist_dir = self.repo_root / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        archive_path = dist_dir / "Polpo-v0.1.0-linux-x86_64.tar.gz"
        archive_path.write_bytes(b"sample archive binary payload")

        result = build_package.create_stable_alias(archive_path, "linux-x86_64")
        self.assertIsNotNone(result)
        alias_path, sidecar_path = result

        self.assertEqual(alias_path.name, "polpo-linux-x86_64.tar.gz")
        self.assertEqual(sidecar_path.name, "polpo-linux-x86_64.tar.gz.sha256")
        self.assertTrue(alias_path.is_file())
        self.assertTrue(sidecar_path.is_file())

        expected_hash = hashlib.sha256(b"sample archive binary payload").hexdigest()
        self.assertIn(expected_hash, sidecar_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
