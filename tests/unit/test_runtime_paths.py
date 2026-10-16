"""Tests for runtime resource path resolution (TICK-001)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from infrastructure.paths import get_runtime_resource_path


def test_get_runtime_resource_path_dev_mode():
    """In unfrozen development checkout, path resolves relative to repo root."""
    target_rel = "resources/runtimes.json"
    resolved = get_runtime_resource_path(target_rel)

    assert isinstance(resolved, Path)
    assert resolved.is_absolute()
    assert resolved.name == "runtimes.json"
    assert resolved.exists()


def test_get_runtime_resource_path_accepts_path_object():
    """get_runtime_resource_path should accept Path instances as well as strings."""
    resolved = get_runtime_resource_path(Path("resources/runtimes.json"))
    assert isinstance(resolved, Path)
    assert resolved.is_absolute()
    assert resolved.name == "runtimes.json"


def test_get_runtime_resource_path_already_absolute(tmp_path: Path):
    """If an absolute path is passed, return it directly."""
    abs_file = tmp_path / "custom.json"
    resolved = get_runtime_resource_path(abs_file)
    assert resolved == abs_file


def test_get_runtime_resource_path_pyinstaller_frozen(tmp_path: Path):
    """In PyInstaller frozen mode (sys.frozen + sys._MEIPASS), path resolves from _MEIPASS."""
    mock_bundle_dir = tmp_path / "mock_meipass"
    mock_bundle_dir.mkdir()
    fake_resource = mock_bundle_dir / "resources" / "bin" / "pandoc"
    fake_resource.parent.mkdir(parents=True)
    fake_resource.write_text("binary content")

    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", str(mock_bundle_dir), create=True):
        resolved = get_runtime_resource_path("resources/bin/pandoc")
        assert resolved == fake_resource
        assert resolved.is_absolute()


def test_get_runtime_resource_path_briefcase_frozen(tmp_path: Path):
    """In frozen mode without _MEIPASS, falls back to executable parent directory."""
    mock_app_dir = tmp_path / "mock_app_root"
    mock_app_dir.mkdir()
    fake_resource = mock_app_dir / "resources" / "mathjax" / "mathjax_worker.js"
    fake_resource.parent.mkdir(parents=True)
    fake_resource.write_text("// worker")

    fake_exe = mock_app_dir / "polpo_app"

    original_meipass = getattr(sys, "_MEIPASS", None)
    try:
        if hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(fake_exe)):
            resolved = get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
            assert resolved == fake_resource
            assert resolved.is_absolute()
    finally:
        if original_meipass is not None:
            sys._MEIPASS = original_meipass


def test_runtimes_json_manifest_structure_and_hashes():
    """Verify resources/runtimes.json conforms to the manifest contract."""
    manifest_path = get_runtime_resource_path("resources/runtimes.json")
    assert manifest_path.exists()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "runtimes" in data
    assert "pandoc" in data["runtimes"]
    assert "node" in data["runtimes"]

    pandoc = data["runtimes"]["pandoc"]
    assert pandoc["version"] == "3.1.11"
    for platform in ["linux-x86_64", "windows-x64", "darwin-x86_64", "darwin-arm64"]:
        assert platform in pandoc["platforms"]
        info = pandoc["platforms"][platform]
        assert info["url"].startswith("https://")
        assert len(info["sha256"]) == 64
        int(info["sha256"], 16)  # must be valid hex

    node = data["runtimes"]["node"]
    assert node["version"] == "22.23.2"
    for platform in ["linux-x86_64", "windows-x64", "darwin-x86_64", "darwin-arm64"]:
        assert platform in node["platforms"]
        info = node["platforms"][platform]
        assert info["url"].startswith("https://")
        assert len(info["sha256"]) == 64
        int(info["sha256"], 16)  # must be valid hex


def test_critical_packaged_assets_dev_mode():
    """Verify all critical packaged assets resolve to existing readable files in dev mode."""
    main_qml = get_runtime_resource_path("interfaces/desktop/qml/Main.qml")
    assert main_qml.is_file()
    assert "ApplicationWindow" in main_qml.read_text(encoding="utf-8")

    preamble = get_runtime_resource_path("resources/templates/polpo_preamble.tex")
    assert preamble.is_file()
    assert "usepackage" in preamble.read_text(encoding="utf-8")

    migrations_dir = get_runtime_resource_path("infrastructure/persistence/sqlite/migrations")
    assert migrations_dir.is_dir()
    sql_files = list(migrations_dir.glob("*.sql"))
    assert len(sql_files) >= 4


def test_frozen_mode_critical_assets_resolution(tmp_path: Path):
    """Simulate PyInstaller bundle layout at sys._MEIPASS and verify resource resolution."""
    bundle_dir = tmp_path / "mock_pyinstaller_bundle"
    bundle_dir.mkdir()

    # Stage mock assets inside bundle
    mock_qml = bundle_dir / "interfaces" / "desktop" / "qml" / "Main.qml"
    mock_qml.parent.mkdir(parents=True)
    mock_qml.write_text("Mock Main QML", encoding="utf-8")

    mock_preamble = bundle_dir / "resources" / "templates" / "polpo_preamble.tex"
    mock_preamble.parent.mkdir(parents=True)
    mock_preamble.write_text("Mock Preamble", encoding="utf-8")

    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", str(bundle_dir), create=True):
        resolved_qml = get_runtime_resource_path("interfaces/desktop/qml/Main.qml")
        assert resolved_qml == mock_qml
        assert resolved_qml.is_file()

        resolved_preamble = get_runtime_resource_path("resources/templates/polpo_preamble.tex")
        assert resolved_preamble == mock_preamble
        assert resolved_preamble.is_file()


def test_staging_workspace_manager_frozen_resolution(tmp_path: Path):
    """Verify StagingWorkspaceManager defaults to sys._MEIPASS templates in frozen mode."""
    from infrastructure.pdf.staging_workspace import StagingWorkspaceManager

    bundle_dir = tmp_path / "mock_bundle"
    mock_templates = bundle_dir / "resources" / "templates"
    mock_templates.mkdir(parents=True)
    (mock_templates / "polpo_preamble.tex").write_text("% Mock", encoding="utf-8")

    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", str(bundle_dir), create=True):
        manager = StagingWorkspaceManager()
        assert manager.templates_dir == mock_templates
        assert (manager.templates_dir / "polpo_preamble.tex").is_file()


def test_sqlite_migration_runner_frozen_resolution(tmp_path: Path):
    """Verify SQLiteMigrationRunner defaults to sys._MEIPASS migrations in frozen mode."""
    from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
    from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner

    bundle_dir = tmp_path / "mock_bundle"
    mock_migrations = bundle_dir / "infrastructure" / "persistence" / "sqlite" / "migrations"
    mock_migrations.mkdir(parents=True)
    (mock_migrations / "001_initial_schema.sql").write_text("CREATE TABLE test (id INT);", encoding="utf-8")

    db_manager = SQLiteDatabaseManager.create_in_memory()

    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", str(bundle_dir), create=True):
        runner = SQLiteMigrationRunner(db_manager)
        assert runner.migrations_dir == mock_migrations
        assert (runner.migrations_dir / "001_initial_schema.sql").is_file()


def test_runtime_paths_working_directory_independent(tmp_path: Path, monkeypatch):
    """Resolution of runtime resources must NOT depend on current working directory."""
    expected_qml = get_runtime_resource_path("interfaces/desktop/qml/Main.qml")
    expected_preamble = get_runtime_resource_path("resources/templates/polpo_preamble.tex")

    # Change working directory to isolated empty directory
    foreign_dir = tmp_path / "unrelated_dir"
    foreign_dir.mkdir()
    monkeypatch.chdir(foreign_dir)

    resolved_qml = get_runtime_resource_path("interfaces/desktop/qml/Main.qml")
    resolved_preamble = get_runtime_resource_path("resources/templates/polpo_preamble.tex")

    assert resolved_qml == expected_qml
    assert resolved_qml.is_file()
    assert resolved_preamble == expected_preamble
    assert resolved_preamble.is_file()
