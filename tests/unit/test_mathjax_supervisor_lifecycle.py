"""Unit tests for MathJaxProcessSupervisor lifecycle, termination escalation, and stderr draining (TICK-P07C)."""

from __future__ import annotations

import ast
import inspect
import logging
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from infrastructure.math.mathjax_supervisor import (
    BoundedStderrBuffer,
    MathJaxProcessSupervisor,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SIGTERM_IGNORING_WORKER = FIXTURES_DIR / "sigterm_ignoring_worker.py"
FLOOD_STDERR_WORKER = FIXTURES_DIR / "flood_stderr_worker.py"




# ==============================================================================
# 1. Cooperative Child Termination
# ==============================================================================

def test_cooperative_child_termination():
    """Supervisor terminates cooperative child process cleanly and clears state."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True
    proc = supervisor._process
    assert proc is not None

    # Track kill calls to verify cooperative termination does not escalate
    orig_kill = proc.kill
    kill_called = False

    def spy_kill():
        nonlocal kill_called
        kill_called = True
        return orig_kill()

    proc.kill = spy_kill

    supervisor.shutdown()

    assert supervisor.is_alive is False
    assert supervisor._process is None
    assert kill_called is False, "Cooperative worker should not escalate to SIGKILL"
    if hasattr(signal, "SIGTERM") and sys.platform != "win32":
        assert proc.poll() == 0
    else:
        assert proc.poll() is not None
    assert proc.stdin is None or proc.stdin.closed
    assert proc.stdout is None or proc.stdout.closed
    assert proc.stderr is None or proc.stderr.closed


# ==============================================================================
# 2. Two-Phase Termination Escalation (SIGTERM -> SIGKILL)
# ==============================================================================

def test_sigterm_escalation_to_sigkill_on_hung_worker(caplog: pytest.LogCaptureFixture):
    """Worker ignoring SIGTERM is forcefully reaped via SIGKILL escalation with stderr diagnostics."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=SIGTERM_IGNORING_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True
    proc = supervisor._process
    assert proc is not None

    # Track terminate and kill calls
    orig_terminate = proc.terminate
    orig_kill = proc.kill
    terminate_called = False
    kill_called = False

    def spy_terminate():
        nonlocal terminate_called
        terminate_called = True
        return orig_terminate()

    def spy_kill():
        nonlocal kill_called
        kill_called = True
        return orig_kill()

    proc.terminate = spy_terminate
    proc.kill = spy_kill

    with caplog.at_level(logging.WARNING):
        supervisor.shutdown()

    assert terminate_called is True
    # On POSIX, sigterm_ignoring_worker ignores SIGTERM, so SIGKILL must have been invoked
    if hasattr(signal, "SIGTERM") and sys.platform != "win32":
        assert kill_called is True
        escalation_logs = [
            rec.message for rec in caplog.records
            if "escalating to SIGKILL" in rec.message
        ]
        assert len(escalation_logs) == 1
        assert "Worker running in loop ignoring SIGTERM" in escalation_logs[0]

    assert supervisor.is_alive is False
    assert supervisor._process is None
    assert proc.poll() is not None


def test_cleanup_preserves_process_on_second_phase_reap_timeout(caplog: pytest.LogCaptureFixture):
    """Supervisor preserves _process when second-phase reap times out, allowing retry."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    proc = supervisor._process
    assert proc is not None
    real_pid = proc.pid

    # Mock wait to simulate second-phase reap timeout and poll returning None
    proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["mock"], timeout=0.5))
    proc.poll = MagicMock(return_value=None)

    try:
        with caplog.at_level(logging.WARNING):
            with supervisor._lock:
                supervisor._cleanup_process_handles_locked(reason="TEST_REAP_TIMEOUT")

        # 1. Process reference is preserved because reap could not be confirmed
        assert supervisor._process is not None
        assert supervisor._process is proc
        assert supervisor.is_alive is True

        reap_timeout_logs = [
            rec.message for rec in caplog.records
            if "failed to exit after SIGKILL reap timeout" in rec.message
        ]
        assert len(reap_timeout_logs) == 1
        assert "retaining process reference" in reap_timeout_logs[0]

        # 2. Simulate subsequent cleanup after process has actually exited
        proc.poll = MagicMock(return_value=0)
        proc.wait = MagicMock(return_value=0)

        with supervisor._lock:
            supervisor._cleanup_process_handles_locked(reason="RETRY_CLEANUP")

        # 3. Reference is now cleared after confirmed reap
        assert supervisor._process is None
        assert supervisor.is_alive is False
    finally:
        # Tear down fixture worker process cleanly
        try:
            if hasattr(signal, "SIGKILL"):
                os.kill(real_pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            subprocess.Popen.wait(proc, timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        proc.poll = MagicMock(return_value=0)
        proc.wait = MagicMock(return_value=0)
        supervisor.shutdown()


def test_cleanup_preserves_process_when_final_poll_raises_generic_oserror(caplog: pytest.LogCaptureFixture):
    """Generic OSError from poll() does not equate to reaping; _process is preserved for retry."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    proc = supervisor._process
    assert proc is not None
    real_pid = proc.pid

    # Initial poll calls in cleanup check if alive (returns None).
    # Then after Phase 1 and 2, final reap confirmation poll raises generic OSError.
    poll_calls = 0

    def mock_poll():
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls <= 2:
            return None
        raise OSError("Simulated kernel I/O error during waitpid")

    proc.poll = mock_poll

    try:
        with caplog.at_level(logging.WARNING):
            with supervisor._lock:
                supervisor._cleanup_process_handles_locked(reason="TEST_POLL_OSERROR")

        # 1. Process reference MUST be preserved because generic OSError is not proof of reaping
        assert supervisor._process is not None
        assert supervisor._process is proc

        poll_warning = [
            rec.message for rec in caplog.records
            if "poll failed with OSError" in rec.message
        ]
        assert len(poll_warning) == 1
        assert "retaining process reference" in poll_warning[0]

        # 2. Later cleanup retries and succeeds when poll returns confirmed exit status
        proc.poll = MagicMock(return_value=0)
        proc.wait = MagicMock(return_value=0)

        with supervisor._lock:
            supervisor._cleanup_process_handles_locked(reason="RETRY_AFTER_OSERROR")

        # 3. Process reference is now cleared after confirmed reap
        assert supervisor._process is None
        assert supervisor.is_alive is False
    finally:
        # Tear down fixture worker process cleanly
        try:
            if hasattr(signal, "SIGKILL"):
                os.kill(real_pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            subprocess.Popen.wait(proc, timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        proc.poll = MagicMock(return_value=0)
        proc.wait = MagicMock(return_value=0)
        supervisor.shutdown()


def test_terminate_failure_still_escalates_to_kill_if_process_alive(caplog: pytest.LogCaptureFixture):
    """When proc.terminate() raises OSError but child is alive, cleanup still escalates to proc.kill()."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    proc = supervisor._process
    assert proc is not None
    real_pid = proc.pid

    # Simulate terminate() failing with OSError
    proc.terminate = MagicMock(side_effect=OSError("Operation not permitted on SIGTERM"))

    kill_called = False
    orig_kill = proc.kill

    def spy_kill():
        nonlocal kill_called
        kill_called = True
        return orig_kill()

    proc.kill = spy_kill

    try:
        with caplog.at_level(logging.WARNING):
            supervisor.shutdown()

        # 1. Verify terminate was attempted and failed
        proc.terminate.assert_called_once()

        # 2. Verify kill was invoked because process remained alive
        assert kill_called is True, "Cleanup must escalate to proc.kill() when terminate fails and child is alive"

        escalation_logs = [
            rec.message for rec in caplog.records
            if "escalating to SIGKILL" in rec.message
        ]
        assert len(escalation_logs) == 1

        # 3. Final process reference is cleared only after confirmed reaping
        assert supervisor._process is None
        assert supervisor.is_alive is False
        assert proc.poll() is not None
    finally:
        # Tear down fixture worker process cleanly
        try:
            if hasattr(signal, "SIGKILL"):
                os.kill(real_pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            subprocess.Popen.wait(proc, timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        supervisor.shutdown()


# ==============================================================================
# 3. Idempotent Lifecycle Cleanup
# ==============================================================================

def test_idempotent_cleanup_and_shutdown():
    """Multiple calls to cleanup or shutdown do not raise errors."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    # First shutdown
    supervisor.shutdown()
    assert supervisor.is_alive is False
    assert supervisor._process is None

    # Repeated shutdown
    supervisor.shutdown()
    assert supervisor.is_alive is False

    # Direct calls to handle cleanup when _process is None
    with supervisor._lock:
        supervisor._cleanup_process_handles_locked()
        supervisor._cleanup_process_handles_locked()


# ==============================================================================
# 4. Stream Closure & Narrow Exception Handling
# ==============================================================================

def test_stream_closure_and_narrow_exception_handling():
    """Cleanup closes streams and catches narrow OS/stream exceptions safely."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    proc = supervisor._process
    assert proc is not None

    # Close real OS pipes before replacing with mock streams to avoid ResourceWarnings
    if proc.stdin and not proc.stdin.closed:
        proc.stdin.close()
    if proc.stdout and not proc.stdout.closed:
        proc.stdout.close()
    if proc.stderr and not proc.stderr.closed:
        proc.stderr.close()

    # Mock streams that raise expected narrow exceptions on close
    mock_stdin = MagicMock()
    mock_stdin.closed = False
    mock_stdin.close.side_effect = OSError("Pipe broken")

    mock_stdout = MagicMock()
    mock_stdout.closed = False
    mock_stdout.close.side_effect = ValueError("I/O on closed file")

    mock_stderr = MagicMock()
    mock_stderr.closed = False
    mock_stderr.close.side_effect = ProcessLookupError("No such process")

    proc.stdin = mock_stdin
    proc.stdout = mock_stdout
    proc.stderr = mock_stderr

    # Must not raise when encountering narrow stream errors
    supervisor.shutdown()
    assert supervisor._process is None


def test_cleanup_does_not_swallow_unexpected_broad_exceptions():
    """Cleanup does not use blanket 'except Exception' and preserves _process on unexpected errors."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    proc = supervisor._process
    assert proc is not None
    real_pid = proc.pid

    # Mock proc.terminate to raise a programming error (TypeError)
    proc.poll = MagicMock(return_value=None)
    proc.terminate = MagicMock(side_effect=TypeError("Unexpected programming bug"))

    try:
        with pytest.raises(TypeError, match="Unexpected programming bug"):
            with supervisor._lock:
                supervisor._cleanup_process_handles_locked()
        # Assert process reference was not discarded prematurely
        assert supervisor._process is not None
        assert supervisor._process is proc
    finally:
        # Tear down fixture worker process and restore mock state to avoid resource leaks
        try:
            if hasattr(signal, "SIGKILL"):
                os.kill(real_pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            subprocess.Popen.wait(proc, timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        proc.poll = MagicMock(return_value=0)
        supervisor.shutdown()


def test_no_broad_exception_handler_in_cleanup_ast():
    """Static AST check: _cleanup_process_handles_locked must not have 'except Exception'."""
    import textwrap
    import infrastructure.math.mathjax_supervisor as mod
    source = textwrap.dedent(inspect.getsource(mod.MathJaxProcessSupervisor._cleanup_process_handles_locked))
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                pytest.fail("Found bare 'except:' in _cleanup_process_handles_locked")
            if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                pytest.fail("Found blanket 'except Exception:' in _cleanup_process_handles_locked")


# ==============================================================================
# 5. Continuous Stderr Drain Thread & 16 KB Bounded Buffer
# ==============================================================================

def test_stderr_drain_thread_is_daemon_and_active():
    """Stderr drain thread runs as a daemon thread and terminates cleanly."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True
    assert supervisor._stderr_drain_thread is not None
    assert supervisor._stderr_drain_thread.daemon is True
    assert supervisor._stderr_drain_thread.is_alive() is True

    supervisor.shutdown()
    assert supervisor._stderr_drain_thread is None or not supervisor._stderr_drain_thread.is_alive()


def test_flood_stderr_does_not_deadlock_and_buffer_is_bounded():
    """Emitting megabytes of stderr does not deadlock and buffer is capped at 16 KB."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    # Allow worker to emit initial 1000 lines (>120 KB)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        content = supervisor.get_stderr_diagnostics()
        if len(content.encode("utf-8")) > 1000:
            break
        time.sleep(0.05)

    diagnostics = supervisor.get_stderr_diagnostics()
    encoded_len = len(diagnostics.encode("utf-8"))
    assert encoded_len <= 16 * 1024, f"Buffer exceeded 16 KB: {encoded_len} bytes"
    assert encoded_len > 0, "Buffer should contain drained stderr"

    # Most recent entries should be retained (e.g. high line numbers like [0999])
    assert "[0999]" in diagnostics or "[099" in diagnostics

    supervisor.shutdown()


def test_bounded_stderr_buffer_oversized_single_line():
    """A single oversized stderr line (>16 KB) is strictly capped at 16 KB."""
    buf = BoundedStderrBuffer(max_bytes=16 * 1024)

    # Append a single 32 KB string
    giant_line = "A" * (32 * 1024)
    buf.append(giant_line)

    content = buf.get_content()
    encoded = content.encode("utf-8")
    assert len(encoded) <= 16 * 1024
    assert len(encoded) == 16 * 1024


def test_bounded_stderr_buffer_fifo_pruning():
    """Buffer retains most recent chunks and discards oldest when exceeding limit."""
    buf = BoundedStderrBuffer(max_bytes=100)

    buf.append("PREFIX: " + ("1" * 60) + "\n")
    buf.append("MIDDLE: " + ("2" * 60) + "\n")

    content = buf.get_content()
    encoded_len = len(content.encode("utf-8"))
    assert encoded_len <= 100
    assert "MIDDLE" in content
    assert "PREFIX" not in content


# ==============================================================================
# 6. Architecture Invariants
# ==============================================================================

def test_supervisor_has_no_qt_imports():
    """Architecture invariant: mathjax_supervisor.py must not import Qt or UI modules."""
    import infrastructure.math.mathjax_supervisor as mod
    source = inspect.getsource(mod)
    tree = ast.parse(source)

    forbidden = ("PySide6", "PyQt5", "PyQt6", "interfaces", "application.services")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden:
                    assert not alias.name.startswith(prefix)
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            for prefix in forbidden:
                assert not mod_name.startswith(prefix)
