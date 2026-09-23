"""Tests for Pandoc binary discovery and active probe capability verification (TICK-002)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.markdown.exceptions import (
    PandocError,
    PandocIncompatibleError,
    PandocNotFoundError,
)
from infrastructure.markdown.pandoc_binary import (
    PandocBinaryResolver,
    get_current_platform_key,
)

SAMPLE_VALID_EXTENSIONS_OUTPUT = """-ascii_identifiers
+attributes
+bracketed_spans
+pipe_tables
-sourcepos
+task_lists
+tex_math_dollars
+yaml_metadata_block
"""

SAMPLE_VALID_PROBE_JSON = json.dumps({
    "pandoc-api-version": [1, 23, 1],
    "meta": {},
    "blocks": [
        {
            "t": "Header",
            "c": [
                1,
                ["probe", [], [["data-pos", "1:1-2:1"]]],
                [
                    {
                        "t": "Span",
                        "c": [["", [], [["data-pos", "1:3-1:8"]]], [{"t": "Str", "c": "Probe"}]],
                    }
                ],
            ],
        }
    ],
})


def _create_mock_binary(path: Path) -> Path:
    """Create a mock binary file and mark it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    if os.name == "posix":
        path.chmod(0o755)
    return path


def test_exception_hierarchy():
    """Verify PandocError, PandocNotFoundError, and PandocIncompatibleError inheritance."""
    assert issubclass(PandocNotFoundError, PandocError)
    assert issubclass(PandocIncompatibleError, PandocError)
    assert issubclass(PandocError, Exception)

    not_found = PandocNotFoundError("Binary missing")
    assert "Binary missing" in str(not_found)

    incompatible = PandocIncompatibleError("Version mismatch", binary_path=Path("/usr/bin/pandoc"))
    assert "Version mismatch" in str(incompatible)
    assert incompatible.binary_path == Path("/usr/bin/pandoc")


def test_platform_key_detection():
    """Verify platform key detection formats for supported platforms."""
    with patch("sys.platform", "linux"), patch("platform.machine", return_value="x86_64"):
        assert get_current_platform_key() == "linux-x86_64"

    with patch("sys.platform", "win32"), patch("platform.machine", return_value="AMD64"):
        assert get_current_platform_key() == "windows-x64"

    with patch("sys.platform", "darwin"), patch("platform.machine", return_value="arm64"):
        assert get_current_platform_key() == "darwin-arm64"

    with patch("sys.platform", "darwin"), patch("platform.machine", return_value="x86_64"):
        assert get_current_platform_key() == "darwin-x86_64"


def test_resolve_bundled_priority(tmp_path: Path):
    """Bundled resource takes precedence over configured override and system PATH."""
    bundled_binary = _create_mock_binary(tmp_path / "resources" / "bin" / "linux-x86_64" / "pandoc")
    configured_binary = _create_mock_binary(tmp_path / "custom" / "pandoc")
    system_binary = _create_mock_binary(tmp_path / "system" / "pandoc")

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=bundled_binary), \
         patch("shutil.which", return_value=str(system_binary)), \
         patch.object(resolver, "validate_binary") as mock_validate:
        resolved = resolver.resolve(configured_path=configured_binary)

        assert resolved == bundled_binary
        mock_validate.assert_called_once_with(bundled_binary)


def test_resolve_configured_fallback_when_bundled_missing(tmp_path: Path):
    """Configured override is used when bundled binary is missing."""
    configured_binary = _create_mock_binary(tmp_path / "custom" / "pandoc")
    system_binary = _create_mock_binary(tmp_path / "system" / "pandoc")

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    # Point bundled check to a non-existent file
    missing_bundled = tmp_path / "nonexistent" / "pandoc"

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled), \
         patch("shutil.which", return_value=str(system_binary)), \
         patch.object(resolver, "validate_binary") as mock_validate:
        resolved = resolver.resolve(configured_path=configured_binary)

        assert resolved == configured_binary
        mock_validate.assert_called_once_with(configured_binary)


def test_resolve_constructor_configured_path(tmp_path: Path):
    """Configured path passed to constructor is used when resolve() has no argument."""
    configured_binary = _create_mock_binary(tmp_path / "custom" / "pandoc")
    missing_bundled = tmp_path / "nonexistent" / "pandoc"

    resolver = PandocBinaryResolver(configured_path=configured_binary, platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled), \
         patch.object(resolver, "validate_binary") as mock_validate:
        resolved = resolver.resolve()

        assert resolved == configured_binary
        mock_validate.assert_called_once_with(configured_binary)


def test_resolve_configured_empty_string_falls_back_to_system(tmp_path: Path):
    """Empty or whitespace configured_path is treated as unset and falls back to system PATH."""
    missing_bundled = tmp_path / "nonexistent" / "pandoc"
    system_binary = _create_mock_binary(tmp_path / "system" / "pandoc")

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled), \
         patch("shutil.which", return_value=str(system_binary)), \
         patch.object(resolver, "validate_binary") as mock_validate:
        resolved = resolver.resolve(configured_path="")

        assert resolved == system_binary
        mock_validate.assert_called_once_with(system_binary)


def test_get_bundled_path_windows_platform_uses_exe(tmp_path: Path):
    """Specifying a Windows platform key looks for pandoc.exe regardless of host OS."""
    fake_exe = tmp_path / "resources" / "bin" / "windows-x64" / "pandoc.exe"
    _create_mock_binary(fake_exe)

    resolver = PandocBinaryResolver(platform_key="windows-x64")

    def mock_get_resource_path(rel_path):
        if "pandoc.exe" in str(rel_path):
            return fake_exe
        return tmp_path / "nonexistent"

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", side_effect=mock_get_resource_path):
        bundled = resolver.get_bundled_path()
        assert bundled == fake_exe
        assert bundled.name == "pandoc.exe"


def test_resolve_configured_not_found_raises(tmp_path: Path):
    """If configured path is provided but does not exist, raise PandocNotFoundError."""
    missing_bundled = tmp_path / "nonexistent" / "pandoc"
    missing_configured = tmp_path / "missing_dir" / "pandoc"

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled):
        with pytest.raises(PandocNotFoundError, match="Configured Pandoc executable path does not exist"):
            resolver.resolve(configured_path=missing_configured)


def test_resolve_system_path_fallback(tmp_path: Path):
    """When bundled and configured are absent, falls back to system PATH."""
    missing_bundled = tmp_path / "nonexistent" / "pandoc"
    system_binary = _create_mock_binary(tmp_path / "system" / "pandoc")

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled), \
         patch("shutil.which", return_value=str(system_binary)), \
         patch.object(resolver, "validate_binary") as mock_validate:
        resolved = resolver.resolve()

        assert resolved == system_binary
        mock_validate.assert_called_once_with(system_binary)


def test_resolve_all_missing_raises_not_found(tmp_path: Path):
    """When bundled, configured, and system PATH are all absent, raise PandocNotFoundError."""
    missing_bundled = tmp_path / "nonexistent" / "pandoc"

    resolver = PandocBinaryResolver(platform_key="linux-x86_64")

    with patch("infrastructure.markdown.pandoc_binary.get_runtime_resource_path", return_value=missing_bundled), \
         patch("shutil.which", return_value=None):
        with pytest.raises(PandocNotFoundError, match="Pandoc executable could not be found"):
            resolver.resolve()


@pytest.mark.skipif(os.name != "posix", reason="POSIX-specific permission handling")
def test_executable_permission_chmod_success(tmp_path: Path):
    """Non-executable file is made executable via chmod on POSIX."""
    test_binary = tmp_path / "pandoc"
    test_binary.write_text("#!/bin/sh\nexit 0\n")
    test_binary.chmod(0o644)  # non-executable

    resolver = PandocBinaryResolver()

    with patch.object(resolver, "_verify_extensions"), \
         patch.object(resolver, "_verify_active_probe"):
        resolver.validate_binary(test_binary)
        # Verify chmod +x was applied
        assert os.access(test_binary, os.X_OK)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-specific permission handling")
def test_executable_permission_chmod_failure(tmp_path: Path):
    """If chmod raises OSError, raise PandocIncompatibleError."""
    test_binary = tmp_path / "pandoc"
    test_binary.write_text("#!/bin/sh\nexit 0\n")
    test_binary.chmod(0o644)

    resolver = PandocBinaryResolver()

    with patch("os.chmod", side_effect=PermissionError("Operation not permitted")):
        with pytest.raises(PandocIncompatibleError, match="not executable and chmod failed"):
            resolver.validate_binary(test_binary)


def test_validate_binary_not_a_file(tmp_path: Path):
    """A directory passed as binary path raises PandocIncompatibleError."""
    test_dir = tmp_path / "pandoc_dir"
    test_dir.mkdir()

    resolver = PandocBinaryResolver()
    with pytest.raises(PandocIncompatibleError, match="not a valid file"):
        resolver.validate_binary(test_dir)


def test_verify_extensions_failure_exit_code(tmp_path: Path):
    """Pandoc extension check failing with non-zero exit code raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    mock_proc = MagicMock(returncode=1, stderr="Unknown format commonmark_x", stdout="")
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(PandocIncompatibleError, match="does not support 'commonmark_x' format"):
            resolver.validate_binary(test_binary)


def test_verify_extensions_missing_required(tmp_path: Path):
    """Pandoc extension check missing required extensions raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    # Output missing +pipe_tables
    incomplete_extensions = """+attributes
+task_lists
+tex_math_dollars
"""
    mock_proc = MagicMock(returncode=0, stderr="", stdout=incomplete_extensions)
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(PandocIncompatibleError, match="missing required extensions"):
            resolver.validate_binary(test_binary)


def test_active_probe_failure_exit_code(tmp_path: Path):
    """Active probe returning non-zero exit code raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=127, stderr="Segmentation fault", stdout="")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="probe .* failed with exit code 127"):
                resolver.validate_binary(test_binary)


def test_active_probe_invalid_json(tmp_path: Path):
    """Active probe returning invalid JSON raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout="Not JSON at all")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="emitted invalid JSON"):
                resolver.validate_binary(test_binary)


def test_active_probe_api_version_too_old(tmp_path: Path):
    """Active probe with API version < 1.23.0 raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    old_api_json = json.dumps({
        "pandoc-api-version": [1, 22, 2],
        "meta": {},
        "blocks": [
            {
                "t": "Header",
                "c": [1, ["probe", [], [["data-pos", "1:1-2:1"]]], []],
            }
        ],
    })

    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout=old_api_json)
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="is older than required 1.23.0"):
                resolver.validate_binary(test_binary)


def test_active_probe_missing_data_pos(tmp_path: Path):
    """Active probe output lacking data-pos attribute tuples raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    json_without_data_pos = json.dumps({
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [
            {
                "t": "Header",
                "c": [1, ["probe", [], []], [{"t": "Str", "c": "Probe"}]],
            }
        ],
    })

    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout=json_without_data_pos)
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="did not emit 'data-pos' attribute tuples"):
                resolver.validate_binary(test_binary)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-specific permission handling")
def test_executable_permission_remains_not_executable(tmp_path: Path):
    """If chmod succeeds but file remains not executable, raise PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch("os.name", "posix"), \
         patch("os.access", side_effect=[False, False]), \
         patch.object(Path, "chmod"):
        with pytest.raises(PandocIncompatibleError, match="remains not executable after applying chmod"):
            resolver.validate_binary(test_binary)


def test_verify_extensions_os_error(tmp_path: Path):
    """If inspecting extensions raises OSError, raise PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch("subprocess.run", side_effect=OSError("Exec format error")):
        with pytest.raises(PandocIncompatibleError, match="Failed to inspect Pandoc extensions"):
            resolver.validate_binary(test_binary)


def test_active_probe_os_error(tmp_path: Path):
    """If active probe raises OSError, raise PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch.object(resolver, "_verify_extensions"):
        with patch("subprocess.run", side_effect=OSError("Process spawn failure")):
            with pytest.raises(PandocIncompatibleError, match="Failed to execute Pandoc capability probe"):
                resolver.validate_binary(test_binary)


def test_active_probe_output_not_dict(tmp_path: Path):
    """Active probe returning a JSON array instead of an object raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout="[1, 2, 3]")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="is not a JSON object"):
                resolver.validate_binary(test_binary)


def test_active_probe_api_version_not_list(tmp_path: Path):
    """Active probe with invalid api-version type raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    invalid_api_json = json.dumps({"pandoc-api-version": "1.23.1", "blocks": []})
    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout=invalid_api_json)
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="missing valid 'pandoc-api-version' integer array"):
                resolver.validate_binary(test_binary)


def test_active_probe_api_version_contains_non_integers(tmp_path: Path):
    """Active probe with non-integer items in api-version array raises PandocIncompatibleError."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    invalid_api_json = json.dumps({"pandoc-api-version": [1, "23", 0], "blocks": []})
    with patch.object(resolver, "_verify_extensions"):
        mock_proc = MagicMock(returncode=0, stderr="", stdout=invalid_api_json)
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(PandocIncompatibleError, match="missing valid 'pandoc-api-version' integer array"):
                resolver.validate_binary(test_binary)


def test_validate_binary_complete_success(tmp_path: Path):
    """When extensions and active probe are valid, validate_binary passes without error."""
    test_binary = _create_mock_binary(tmp_path / "pandoc")
    resolver = PandocBinaryResolver()

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "--list-extensions=commonmark_x" in cmd:
            return MagicMock(returncode=0, stdout=SAMPLE_VALID_EXTENSIONS_OUTPUT, stderr="")
        elif "-f" in cmd and "commonmark_x+sourcepos" in cmd:
            return MagicMock(returncode=0, stdout=SAMPLE_VALID_PROBE_JSON, stderr="")
        return MagicMock(returncode=1, stdout="", stderr="Unknown command")

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        resolver.validate_binary(test_binary)


def test_real_system_pandoc_integration():
    """If real Pandoc is present on the testing host, verify full discovery and validation."""
    real_pandoc = _find_system_pandoc()
    if real_pandoc is None:
        pytest.skip("Pandoc not installed on test host")

    resolver = PandocBinaryResolver()
    # Explicitly test validation on the host Pandoc binary
    resolver.validate_binary(real_pandoc)


def _find_system_pandoc() -> Path | None:
    import shutil
    found = shutil.which("pandoc")
    return Path(found) if found else None
