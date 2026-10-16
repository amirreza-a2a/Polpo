"""Tests for desktop CLI modes (--version, --self-test) and fail-fast startup (TICK-REL-02)."""

import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from core.version import APP_NAME, __version__
from infrastructure.paths import get_runtime_resource_path, get_user_log_dir
from infrastructure.logging.setup import setup_logging
from interfaces.desktop.app import main
from interfaces.desktop.self_test import run_self_test


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path):
    """Ensure tests run headlessly and in an isolated data environment."""
    orig_platform = os.environ.get("QT_QPA_PLATFORM")
    orig_xdg = os.environ.get("XDG_DATA_HOME")

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "test_isolated_xdg")

    yield

    if orig_platform is not None:
        os.environ["QT_QPA_PLATFORM"] = orig_platform
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)

    if orig_xdg is not None:
        os.environ["XDG_DATA_HOME"] = orig_xdg
    else:
        os.environ.pop("XDG_DATA_HOME", None)


def test_version_flag_in_process(capsys):
    """Calling main(['--version']) prints authoritative version and returns 0 without GUI."""
    code = main(["--version"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"{APP_NAME} {__version__}"
    assert captured.err == ""


def test_version_short_flag_in_process(capsys):
    """Calling main(['-v']) prints authoritative version and returns 0 without GUI."""
    code = main(["-v"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"{APP_NAME} {__version__}"


def test_help_flags_in_process(capsys):
    """Calling main(['--help']) or main(['-h']) prints usage and returns 0 without GUI."""
    code_long = main(["--help"])
    assert code_long == 0
    captured_long = capsys.readouterr()
    assert "Usage: polpo" in captured_long.out
    assert "--version" in captured_long.out
    assert "--self-test" in captured_long.out

    code_short = main(["-h"])
    assert code_short == 0
    captured_short = capsys.readouterr()
    assert "Usage: polpo" in captured_short.out


def test_version_subprocess():
    """Subprocess invocation of --version produces deterministic output and exit status 0."""
    result = subprocess.run(
        [sys.executable, "-m", "interfaces.desktop.app", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"{APP_NAME} {__version__}"
    assert result.stderr == ""


def test_self_test_in_process(capsys):
    """Calling main(['--self-test']) runs non-interactive diagnostics and returns 0."""
    code = main(["--self-test"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Polpo Desktop Diagnostic Self-Test" in captured.out
    assert "Pandoc Markdown AST parse execution" in captured.out
    assert "MathJax vector SVG render execution" in captured.out
    assert "Default prompt seeding bootstrap" in captured.out
    assert "LaTeX preamble template staging" in captured.out
    assert "Self-test PASSED" in captured.out


def test_self_test_subprocess():
    """Subprocess invocation of --self-test runs clean diagnostics and exits 0."""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-m", "interfaces.desktop.app", "--self-test"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 0
    assert "Self-test PASSED" in result.stdout


def test_self_test_failure_on_missing_critical_asset():
    """If a critical packaged asset is missing, run_self_test returns 1 and reports the failure."""
    real_resolver = get_runtime_resource_path

    def mock_resolver(rel_path):
        if "polpo_preamble.tex" in str(rel_path):
            return Path("/nonexistent/polpo_preamble.tex")
        return real_resolver(rel_path)

    buf = io.StringIO()
    with patch("interfaces.desktop.self_test.get_runtime_resource_path", side_effect=mock_resolver):
        code = run_self_test(output=buf)

    assert code == 1
    output = buf.getvalue()
    assert "Self-test FAILED" in output
    assert "LaTeX typesetting preamble" in output


def test_fail_fast_missing_main_qml(tmp_path: Path, capsys):
    """When Main.qml is missing, main() must shutdown container and return 1 without app.exec()."""
    with patch(
        "interfaces.desktop.app.get_runtime_resource_path",
        return_value=tmp_path / "nonexistent" / "Main.qml",
    ):
        code = main([])
        assert code == 1

    captured = capsys.readouterr()
    assert "Fatal error: Main QML interface file not found" in captured.err


def test_fail_fast_invalid_qml_no_root_objects(tmp_path: Path, capsys):
    """When Main.qml fails compilation (0 root objects), main() shuts down and returns 1."""
    broken_qml = tmp_path / "BrokenMain.qml"
    broken_qml.write_text("import QtQuick 2.15\nInvalidItem {{{", encoding="utf-8")

    with patch("interfaces.desktop.app.get_runtime_resource_path", return_value=broken_qml):
        code = main([])
        assert code == 1

    captured = capsys.readouterr()
    assert "Fatal error" in captured.err
    assert "QML diagnostics" in captured.err


def test_successful_startup_reaching_normal_event_loop():
    """Normal startup reaches the app.exec() event loop, returns its exit status, and cleans up."""
    with patch("interfaces.desktop.qt_compat.QGuiApplication.exec", return_value=0) as mock_exec:
        exit_code = main(["-platform", "offscreen"])
        assert exit_code == 0
        mock_exec.assert_called_once()


def test_main_handles_data_migration_conflict(tmp_path: Path, capsys):
    """When data migration detects a conflict, main() reports the fatal error and returns 1."""
    from infrastructure.paths import DataMigrationConflictError

    with patch(
        "interfaces.desktop.app.create_app",
        side_effect=DataMigrationConflictError("Simulated conflict"),
    ):
        exit_code = main([])
        assert exit_code == 1

    captured = capsys.readouterr()
    assert "Fatal error: Simulated conflict" in captured.err


def test_logging_setup_creates_log_file_in_user_data_dir(tmp_path: Path):
    """setup_logging creates polpot.log inside user-data log directory with secret masking."""
    log_dir = tmp_path / "test_user_data" / "logs"
    logger = setup_logging(log_dir=log_dir)

    assert (log_dir / "polpot.log").is_file()

    # Log secret to verify redaction filter
    logger.info("Connecting with key sk-abcdef1234567890abcdef123456")
    log_content = (log_dir / "polpot.log").read_text(encoding="utf-8")
    assert "sk-abcdef1234567890abcdef123456" not in log_content
    assert "[REDACTED_API_KEY]" in log_content


def test_logging_redacts_secrets_in_exception_tracebacks(tmp_path: Path):
    """Exception tracebacks must have credentials redacted before writing to file logs."""
    log_dir = tmp_path / "test_redact_tb" / "logs"
    logger = setup_logging(log_dir=log_dir)

    secret_key = "sk-ant-api03-abcdef1234567890abcdef123456"
    try:
        raise ValueError(f"Authentication failed with key {secret_key}")
    except ValueError:
        logger.exception("Provider error occurred:")

    log_content = (log_dir / "polpot.log").read_text(encoding="utf-8")
    assert secret_key not in log_content
    assert "[REDACTED_API_KEY]" in log_content
    assert "Provider error occurred:" in log_content


def test_logging_rotation_configuration_and_trigger(tmp_path: Path):
    """RotatingFileHandler triggers log rotation when file size exceeds maxBytes threshold."""
    log_dir = tmp_path / "test_rotation" / "logs"
    # Configure tiny 300-byte threshold with 2 backups for deterministic rotation testing
    logger = setup_logging(log_dir=log_dir, max_bytes=300, backup_count=2)

    # Write enough lines to exceed 300 bytes and trigger rotation
    for i in range(25):
        logger.info("Deterministic rotation line padding number %03d", i)

    log_files = list(log_dir.glob("polpot.log*"))
    assert len(log_files) >= 2  # Main log and at least polpot.log.1 rotated backup
    assert any("polpot.log.1" in p.name for p in log_files)


def test_logging_redacts_quoted_credentials_with_whitespace(tmp_path: Path):
    """Quoted credential assignments containing whitespace must be redacted."""
    log_dir = tmp_path / "test_quoted_redact" / "logs"
    logger = setup_logging(log_dir=log_dir)

    double_quoted_secret = "super secret value with spaces and special! 123"
    single_quoted_secret = "another secret token with whitespace"

    logger.info('Connecting with api_key = "%s"', double_quoted_secret)
    logger.info("Authorizing with password: '%s'", single_quoted_secret)

    log_content = (log_dir / "polpot.log").read_text(encoding="utf-8")
    assert double_quoted_secret not in log_content
    assert single_quoted_secret not in log_content
    assert '[REDACTED]' in log_content


def test_logging_redacts_registered_custom_provider_secrets(tmp_path: Path):
    """Explicitly registered custom provider secrets are sanitized by the redacting filter."""
    import logging
    log_dir = tmp_path / "test_custom_secrets" / "logs"
    logger = setup_logging(log_dir=log_dir)

    # Find the filter on the handler
    redacting_filter = None
    for h in logging.getLogger().handlers:
        for f in h.filters:
            if hasattr(f, "register_secret"):
                redacting_filter = f
                break
    assert redacting_filter is not None

    custom_secret = "custom-auth-passphrase-xyz-987"
    redacting_filter.register_secret(custom_secret)

    logger.info("Custom upstream provider authentication header: %s", custom_secret)

    log_content = (log_dir / "polpot.log").read_text(encoding="utf-8")
    assert custom_secret not in log_content
    assert "[REDACTED]" in log_content


def test_self_test_does_not_mutate_or_migrate_production_user_data(tmp_path: Path):
    """--self-test executes against isolated temp databases and never mutates production database."""
    import sqlite3

    prod_dir = tmp_path / "fake_production" / "polpot"
    prod_dir.mkdir(parents=True)
    prod_db = prod_dir / "polpot.db"

    # Pre-populate fake production database with canary data
    conn = sqlite3.connect(str(prod_db))
    conn.execute("CREATE TABLE prod_canary (id INT, item TEXT);")
    conn.execute("INSERT INTO prod_canary VALUES (1, 'untouchable_production_data');")
    conn.commit()
    conn.close()

    # Isolate XDG to this fake production directory
    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path / "fake_production")}):
        buf = io.StringIO()
        code = run_self_test(output=buf)
        assert code == 0

    # Production database was NOT overwritten or dropped
    check_conn = sqlite3.connect(str(prod_db))
    row = check_conn.execute("SELECT item FROM prod_canary WHERE id=1;").fetchone()
    assert row is not None
    assert row[0] == "untouchable_production_data"
    check_conn.close()


def test_desktop_app_container_explicit_db_path_skips_migration(tmp_path: Path):
    """When an explicit db_path is passed to DesktopAppContainer, legacy data migration is bypassed."""
    from interfaces.desktop.composition import DesktopAppContainer

    custom_data_dir = tmp_path / "custom_data"
    custom_data_dir.mkdir(parents=True)
    custom_db = custom_data_dir / "custom_app.db"

    with patch("interfaces.desktop.composition.migrate_legacy_user_data_if_needed") as mock_migrate:
        container = DesktopAppContainer(
            db_path=custom_db,
            artifacts_dir=custom_data_dir / "artifacts",
            vault_path=custom_data_dir / "vault.enc",
            scheduler_tick_interval=100.0,
        )
        container.initialize()
        container.shutdown()

        mock_migrate.assert_not_called()
    assert custom_db.is_file()


def test_windows_console_streams_attach_and_reconfigure():
    """On Windows, _ensure_windows_console_streams reconfigures active streams to UTF-8."""
    from interfaces.desktop.app import _ensure_windows_console_streams

    mock_stdout = MagicMock()
    mock_stderr = MagicMock()
    mock_ctypes = MagicMock()

    with patch("sys.platform", "win32"), \
         patch.dict("sys.modules", {"ctypes": mock_ctypes}), \
         patch("sys.stdout", mock_stdout), \
         patch("sys.stderr", mock_stderr):
        _ensure_windows_console_streams()
        mock_ctypes.windll.kernel32.AttachConsole.assert_called_once_with(-1)
        mock_stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        mock_stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_windows_console_streams_opens_conout_when_streams_none():
    """On Windows GUI executables without redirection, sys.stdout/stderr are None and opened to CONOUT$."""
    from interfaces.desktop.app import _ensure_windows_console_streams

    mock_ctypes = MagicMock()
    mock_file = MagicMock()

    with patch("sys.platform", "win32"), \
         patch.dict("sys.modules", {"ctypes": mock_ctypes}), \
         patch("sys.stdout", None), \
         patch("sys.stderr", None), \
         patch("builtins.open", return_value=mock_file) as mock_open:
        _ensure_windows_console_streams()
        # Verify CONOUT$ was attempted for both stdout and stderr
        mock_open.assert_any_call("CONOUT$", "w", encoding="utf-8", errors="replace")


def test_windows_console_streams_non_windows_noop():
    """On Linux/macOS, _ensure_windows_console_streams is a no-op."""
    from interfaces.desktop.app import _ensure_windows_console_streams

    mock_ctypes = MagicMock()
    with patch("sys.platform", "linux"), \
         patch.dict("sys.modules", {"ctypes": mock_ctypes}):
        _ensure_windows_console_streams()
        mock_ctypes.windll.kernel32.AttachConsole.assert_not_called()
