# ============================================================
#  tests/unit/test_subprocess_supervisor.py
#  Unit tests for Subprocess Supervisor & Lifecycle Manager
#  Phase 13 — Ticket #99
# ============================================================

import os
from pathlib import Path
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.pdf.subprocess_supervisor import (
    _WIN32_CREATE_NO_WINDOW,
    SubprocessSupervisor,
)


def test_supervisor_executes_simple_command(tmp_path: Path):
    """Executes a simple Python command, capturing returncode 0 and stdout."""
    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('hello supervisor\\n'); sys.stderr.write('notice\\n')"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=5.0)

    assert res.returncode == 0
    assert "hello supervisor" in res.stdout
    assert "notice" in res.stderr
    assert res.timed_out is False
    assert res.cancelled is False


def test_supervisor_sanitizes_environment(tmp_path: Path, monkeypatch):
    """Verifies child process environment strips QUARTO_* and TEXINPUTS variables."""
    monkeypatch.setenv("QUARTO_PROJECT_DIR", "/evil/path")
    monkeypatch.setenv("QUARTO_PROFILE", "production")
    monkeypatch.setenv("QUARTO_DENO_EXTRA_OPTIONS", "--allow-all")
    monkeypatch.setenv("TEXINPUTS", "/custom/tex/inputs")
    monkeypatch.setenv("SAFE_APP_VAR", "preserved_value")

    supervisor = SubprocessSupervisor()
    code = (
        "import os\n"
        "assert 'QUARTO_PROJECT_DIR' not in os.environ\n"
        "assert 'QUARTO_PROFILE' not in os.environ\n"
        "assert 'QUARTO_DENO_EXTRA_OPTIONS' not in os.environ\n"
        "assert 'TEXINPUTS' not in os.environ\n"
        "assert os.environ.get('SAFE_APP_VAR') == 'preserved_value'\n"
        "assert os.environ.get('EXTRA_TEST_VAR') == 'injected'\n"
    )
    cmd = [sys.executable, "-c", code]

    res = supervisor.execute(
        cmd=cmd,
        cwd=tmp_path,
        timeout_seconds=5.0,
        extra_env={
            "EXTRA_TEST_VAR": "injected",
            "QUARTO_EXTRA_INJECT": "bad",
            "TEXINPUTS": "bad",
        },
    )

    assert res.returncode == 0
    assert res.timed_out is False
    assert res.cancelled is False


def test_supervisor_enforces_timeout(tmp_path: Path):
    """Spawns a sleeping subprocess; asserts timeout expiry and process termination."""
    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]

    start = time.monotonic()
    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=0.3)
    elapsed = time.monotonic() - start

    assert res.timed_out is True
    assert res.cancelled is False
    assert elapsed < 3.0


def test_supervisor_enforces_cancellation(tmp_path: Path):
    """Triggers cancellation callback; asserts cooperative process termination."""
    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]

    cancelled_flag = False

    def check_cancelled():
        return cancelled_flag

    # Trigger cancellation shortly after starting
    def trigger_cancel():
        time.sleep(0.15)
        nonlocal cancelled_flag
        cancelled_flag = True

    import threading
    t = threading.Thread(target=trigger_cancel)
    t.start()

    start = time.monotonic()
    res = supervisor.execute(
        cmd=cmd,
        cwd=tmp_path,
        timeout_seconds=10.0,
        is_cancelled=check_cancelled,
    )
    elapsed = time.monotonic() - start
    t.join()

    assert res.cancelled is True
    assert res.timed_out is False
    assert elapsed < 3.0


def test_supervisor_bounds_memory_usage(tmp_path: Path):
    """Streams large volume of output; asserts buffer truncated at size cap."""
    small_cap = 64 * 1024  # 64 KB for testing cap
    supervisor = SubprocessSupervisor(max_stream_bytes=small_cap)

    # Generate 500 KB of text
    code = "import sys; sys.stdout.write('A' * 500000)"
    cmd = [sys.executable, "-c", code]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=5.0)

    assert res.returncode == 0
    assert len(res.stdout) == small_cap
    assert res.timed_out is False


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_hardening_flags(mock_popen, mock_system, tmp_path: Path):
    """Verifies CREATE_NO_WINDOW creationflag on Windows platform."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    supervisor = SubprocessSupervisor()
    res = supervisor.execute(cmd=["quarto", "render"], cwd=tmp_path)

    assert res.returncode == 0
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("creationflags") == _WIN32_CREATE_NO_WINDOW
    assert "start_new_session" not in kwargs


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_posix_hardening_flags(mock_popen, mock_system, tmp_path: Path):
    """Verifies start_new_session=True on POSIX platform."""
    mock_system.return_value = "Linux"

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    supervisor = SubprocessSupervisor()
    res = supervisor.execute(cmd=["quarto", "render"], cwd=tmp_path)

    assert res.returncode == 0
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill(mock_sub_run):
    """Verifies Windows process tree kill invokes taskkill."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    supervisor._kill_process_tree(mock_proc, is_windows=True)

    mock_sub_run.assert_called_once()
    called_cmd = mock_sub_run.call_args[0][0]
    assert called_cmd == ["taskkill", "/F", "/T", "/PID", "12345"]
    assert mock_sub_run.call_args.kwargs.get("creationflags") == _WIN32_CREATE_NO_WINDOW


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group termination is POSIX-only")
@patch("infrastructure.pdf.subprocess_supervisor.os.killpg")
@patch("infrastructure.pdf.subprocess_supervisor.os.getpgid")
def test_supervisor_posix_tree_kill(mock_getpgid, mock_killpg):
    """Verifies POSIX process tree kill invokes os.killpg with SIGKILL."""
    import signal
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 999
    mock_getpgid.return_value = 999

    supervisor._kill_process_tree(mock_proc, is_windows=False)

    mock_getpgid.assert_called_once_with(999)
    mock_killpg.assert_called_once_with(999, signal.SIGKILL)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group termination is POSIX-only")
@patch("infrastructure.pdf.subprocess_supervisor.os.killpg")
@patch("infrastructure.pdf.subprocess_supervisor.os.getpgid")
def test_supervisor_posix_tree_kill_when_session_leader_exited(mock_getpgid, mock_killpg):
    """Verifies that if session leader has exited, killpg is still invoked directly with PID as PGID."""
    import signal
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 888
    # Session leader exited, so getpgid raises ProcessLookupError
    mock_getpgid.side_effect = ProcessLookupError("No such process")

    supervisor._kill_process_tree(mock_proc, is_windows=False)

    mock_getpgid.assert_called_once_with(888)
    mock_killpg.assert_called_once_with(888, signal.SIGKILL)
