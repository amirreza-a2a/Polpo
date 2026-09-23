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
