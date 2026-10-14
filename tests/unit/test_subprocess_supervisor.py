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
    mock_sub_run.return_value.returncode = 0

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is True
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


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_nonzero_exit(mock_sub_run):
    """Verifies failed taskkill exit code returns False and still invokes proc.kill()."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    mock_sub_run.return_value.returncode = 1
    mock_sub_run.return_value.stderr = "ERROR: Access denied."

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is False
    mock_proc.kill.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_timeout(mock_sub_run):
    """Verifies taskkill timeout returns False and still invokes proc.kill()."""
    import subprocess as sp
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    mock_sub_run.side_effect = sp.TimeoutExpired(cmd=["taskkill"], timeout=5.0)

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is False
    mock_proc.kill.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_command_not_found(mock_sub_run):
    """Verifies taskkill missing executable returns False and still invokes proc.kill()."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    mock_sub_run.side_effect = FileNotFoundError("taskkill not found")

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is False
    mock_proc.kill.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_failure_appends_warning_to_stderr(
    mock_sub_run, mock_popen, mock_system, tmp_path: Path
):
    """Verifies execute on Windows records a diagnostic in stderr when taskkill fails."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 9999
    # Simulate process running until killed, then exiting
    mock_proc.poll.side_effect = [None, None, None, -1]
    mock_proc.returncode = -1
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_sub_run.return_value.returncode = 1
    mock_sub_run.return_value.stderr = "ERROR: Process tree kill failed"

    supervisor = SubprocessSupervisor(job_factory=lambda: None)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=0.05)

    assert res.timed_out is True
    assert "Failed to terminate descendant process tree" in res.stderr


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_cancellation_failure_appends_warning_to_stderr(
    mock_sub_run, mock_popen, mock_system, tmp_path: Path
):
    """Verifies execute on Windows records a diagnostic in stderr when taskkill fails during cancellation."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 8888
    mock_proc.poll.side_effect = [None, None, -1]
    mock_proc.returncode = -1
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_sub_run.return_value.returncode = 1
    mock_sub_run.return_value.stderr = "ERROR: Access denied"

    supervisor = SubprocessSupervisor(job_factory=lambda: None)
    cmd = ["quarto", "render", "doc.qmd"]

    cancelled_flag = False

    def is_cancelled():
        nonlocal cancelled_flag
        cancelled_flag = True
        return True

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, is_cancelled=is_cancelled)

    assert res.cancelled is True
    assert "Failed to terminate descendant process tree" in res.stderr


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_tree_kill_process_remains_alive_returns_false(mock_sub_run):
    """Verifies _kill_process_tree returns False if proc.poll() is still None after kill."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_sub_run.return_value.returncode = 0
    # Process is unkillable / poll still returns None
    mock_proc.poll.return_value = None

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is False


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_tree_kill_exit_code_128_returns_false(mock_sub_run):
    """Verifies taskkill exit code 128 (process not found) returns False because descendant tree cannot be verified."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = 0  # Root process already exited

    mock_sub_run.return_value.returncode = 128
    mock_sub_run.return_value.stderr = "ERROR: The process with PID 12345 not found."

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    assert success is False
    mock_proc.kill.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_root_terminated_but_tree_unverified_returns_false(mock_sub_run):
    """Verifies root process termination succeeding does not prove descendant termination if taskkill failed with 128."""
    supervisor = SubprocessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 54321
    # Process was running initially, but after kill it returns exitcode -9
    mock_proc.poll.return_value = -9

    mock_sub_run.return_value.returncode = 128
    mock_sub_run.return_value.stderr = "ERROR: The process with PID 54321 not found."

    success = supervisor._kill_process_tree(mock_proc, is_windows=True)

    # Root process is terminated, but descendant tree could not be verified
    assert success is False


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_taskkill_fallback_code_128_appends_warning_on_timeout(
    mock_sub_run, mock_popen, mock_system, tmp_path: Path
):
    """Verifies taskkill fallback records a diagnostic in stderr when taskkill exits with 128 during timeout."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 7777
    killed = False

    def fake_kill():
        nonlocal killed
        killed = True

    mock_proc.kill.side_effect = fake_kill
    mock_proc.poll.side_effect = lambda: 0 if killed else None
    mock_proc.returncode = -9
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_sub_run.return_value.returncode = 128
    mock_sub_run.return_value.stderr = "ERROR: The process with PID 7777 not found."

    supervisor = SubprocessSupervisor(job_factory=lambda: None)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=0.05)

    assert res.timed_out is True
    assert "Failed to terminate descendant process tree" in res.stderr


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.run")
def test_supervisor_windows_taskkill_fallback_code_128_appends_warning_on_cancellation(
    mock_sub_run, mock_popen, mock_system, tmp_path: Path
):
    """Verifies taskkill fallback records a diagnostic in stderr when taskkill exits with 128 during cancellation."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 6666
    killed = False

    def fake_kill():
        nonlocal killed
        killed = True

    mock_proc.kill.side_effect = fake_kill
    mock_proc.poll.side_effect = lambda: 0 if killed else None
    mock_proc.returncode = -9
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_sub_run.return_value.returncode = 128
    mock_sub_run.return_value.stderr = "ERROR: The process with PID 6666 not found."

    supervisor = SubprocessSupervisor(job_factory=lambda: None)
    cmd = ["quarto", "render", "doc.qmd"]

    cancelled_flag = False

    def is_cancelled():
        nonlocal cancelled_flag
        cancelled_flag = True
        return True

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, is_cancelled=is_cancelled)

    assert res.cancelled is True
    assert "Failed to terminate descendant process tree" in res.stderr


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_job_object_termination_success_on_timeout(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies Job Object termination succeeds cleanly on timeout without warning diagnostics."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 1111
    killed = False

    def fake_kill():
        nonlocal killed
        killed = True

    mock_proc.kill.side_effect = fake_kill
    mock_proc.poll.side_effect = lambda: 0 if killed else None
    mock_proc.returncode = -9
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.terminate.return_value = True

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=0.05)

    assert res.timed_out is True
    assert res.cancelled is False
    assert "Failed to terminate descendant process tree" not in res.stderr
    mock_job.terminate.assert_called_with(1)
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_job_object_termination_failure_appends_warning_on_timeout(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies Job Object termination failure appends a warning diagnostic to stderr on timeout."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 2222
    killed = False

    def fake_kill():
        nonlocal killed
        killed = True

    mock_proc.kill.side_effect = fake_kill
    mock_proc.poll.side_effect = lambda: 0 if killed else None
    mock_proc.returncode = -9
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.terminate.return_value = False

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=0.05)

    assert res.timed_out is True
    assert "Failed to terminate descendant process tree" in res.stderr
    mock_job.terminate.assert_called_with(1)
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_job_object_cancellation(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies cooperative cancellation on Windows triggers Job Object tree termination."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 4444
    killed = False

    def fake_kill():
        nonlocal killed
        killed = True

    mock_proc.kill.side_effect = fake_kill
    mock_proc.poll.side_effect = lambda: 0 if killed else None
    mock_proc.returncode = 1
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.terminate.return_value = True

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(
        cmd=cmd,
        cwd=tmp_path,
        timeout_seconds=10.0,
        is_cancelled=lambda: True,
    )

    assert res.cancelled is True
    assert res.timed_out is False
    assert "Failed to terminate descendant process tree" not in res.stderr
    mock_job.terminate.assert_called_with(1)
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_job_object_normal_exit_terminates_lingering_descendants(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies normal root exit detects active lingering descendants and terminates them."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 3333
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    # Report 2 lingering child processes remaining in job
    mock_job.get_active_processes.return_value = 2
    mock_job.terminate.return_value = True

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path)

    assert res.returncode == 0
    assert res.timed_out is False
    assert res.cancelled is False
    mock_job.terminate.assert_called_with(0)
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_job_closed_on_setup_failure(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies Job Object is closed safely if process spawn fails."""
    mock_system.return_value = "Windows"

    mock_job = MagicMock()
    mock_job.spawn_process.side_effect = RuntimeError("Spawn failed")

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    with pytest.raises(RuntimeError, match="Spawn failed"):
        supervisor.execute(cmd=cmd, cwd=tmp_path)

    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_normal_exit_query_active_processes_failure_appends_warning(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies that an error querying lingering descendants on normal exit marks tree_killed=False and logs warning."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 5555
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.get_active_processes.side_effect = OSError("QueryInformationJobObject failed with error code 5")

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path)

    assert res.returncode == 0
    assert "Failed to terminate descendant process tree for PID 5555" in res.stderr
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_normal_exit_terminate_lingering_failure_appends_warning(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies that failure to terminate lingering descendants on normal exit marks tree_killed=False and logs warning."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 5556
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.get_active_processes.return_value = 1
    mock_job.terminate.return_value = False

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path)

    assert res.returncode == 0
    assert "Failed to terminate descendant process tree for PID 5556" in res.stderr
    mock_job.close.assert_called_once()


@patch("infrastructure.pdf.subprocess_supervisor.platform.system")
@patch("infrastructure.pdf.subprocess_supervisor.subprocess.Popen")
def test_supervisor_windows_normal_exit_terminate_lingering_exception_appends_warning(
    mock_popen, mock_system, tmp_path: Path
):
    """Verifies that an exception during lingering descendant termination marks tree_killed=False and logs warning."""
    mock_system.return_value = "Windows"

    mock_proc = MagicMock()
    mock_proc.pid = 5557
    mock_proc.poll.return_value = 0
    mock_proc.returncode = 0
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr.read.return_value = b""
    mock_popen.return_value = mock_proc

    mock_job = MagicMock()
    mock_job.spawn_process.return_value = mock_proc
    mock_job.get_active_processes.return_value = 1
    mock_job.terminate.side_effect = RuntimeError("Kernel termination call crashed")

    supervisor = SubprocessSupervisor(job_factory=lambda: mock_job)
    cmd = ["quarto", "render", "doc.qmd"]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path)

    assert res.returncode == 0
    assert "Failed to terminate descendant process tree for PID 5557" in res.stderr
    mock_job.close.assert_called_once()
