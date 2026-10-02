"""Unit tests for MathJaxProcessSupervisor write-path watchdog and circuit breaker integration (TICK-P07G).

Verifies:
1. Write-path watchdog timer protection:
   - Arms before writing to stdin and disarms on normal completion.
   - On blocked stdin write/flush, terminates/kills process from outside supervisor lock.
   - Deadlock-free unblocking of the writing thread with BrokenPipeError / OSError.
2. Canonical supervisor evaluation order:
   - Step 1: Pre-flight MAX_TEX_LENGTH check before acquiring probe permit.
   - Step 2: Circuit breaker probe permit acquisition (fails fast in O(1) if OPEN).
   - Step 3: RPC dispatch under supervisor lock.
   - Step 4: Post-response MAX_SVG_LENGTH check.
   - Step 5: Probe classification.
3. Probe semantics:
   - Successful probes: valid response, MathSyntaxError, worker-side -32600, oversized SVG.
   - Failed probes: timeout, worker crash, startup handshake failure.
   - Neutral events: pre-flight MAX_TEX_LENGTH rejection, MathSupervisorShutdownError.
4. Clean initial startup invariant (initial start != restart; records 0 failures).
5. Clean architecture invariant: infrastructure/math contains zero Qt imports.
"""

from __future__ import annotations

import ast
import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from application.ports.math_renderer import (
    MathBufferLimitExceededError,
    MathCircuitBreakerOpenError,
    MathRenderError,
    MathRenderTimeoutError,
    MathSupervisorShutdownError,
    MathWorkerCrashedError,
    MathWorkerStartupError,
    MathSyntaxError,
)
from infrastructure.math.circuit_breaker import (
    INITIAL_COOLDOWN_SECONDS,
    MAX_COOLDOWN_SECONDS,
    MAX_RESTARTS_PER_MINUTE,
    CircuitBreakerState,
    MathCircuitBreaker,
)
from infrastructure.math.mathjax_supervisor import (
    MAX_SVG_LENGTH,
    MAX_TEX_LENGTH,
    MathJaxProcessSupervisor,
    _WriteWatchdog,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOOD_STDERR_WORKER = FIXTURES_DIR / "flood_stderr_worker.py"
HUNG_WORKER_REQUEST = FIXTURES_DIR / "hung_worker_request.py"
HUNG_WORKER_STARTUP = FIXTURES_DIR / "hung_worker_startup.py"


class SyntheticClock:
    """Deterministic monotonic clock provider for testing circuit breaker transitions."""

    def __init__(self, start_time: float = 1000.0) -> None:
        self.time: float = start_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


# ==============================================================================
# 1. Write-Path Watchdog Tests
# ==============================================================================

def test_write_watchdog_arms_and_disarms_on_successful_write():
    """Watchdog arms before write and cleanly disarms without firing when write succeeds."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    watchdog = _WriteWatchdog(mock_proc, timeout=0.5)
    assert watchdog._timer is None

    watchdog.arm()
    assert watchdog._timer is not None
    assert watchdog._timer.is_alive()

    watchdog.disarm()
    assert watchdog._timer is None
    mock_proc.kill.assert_not_called()


def test_write_watchdog_kills_process_outside_lock_on_timeout():
    """Watchdog kills the child process when write/flush blocks past timeout."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 99999

    watchdog = _WriteWatchdog(mock_proc, timeout=0.05)
    watchdog.arm()

    # Sleep longer than the watchdog timeout to trigger it
    time.sleep(0.15)

    try:
        mock_proc.kill.assert_called_once()
        assert watchdog._triggered is True
    finally:
        watchdog.disarm()


def test_write_watchdog_unblocks_blocked_stdin_flush_without_deadlock():
    """Supervisor write-path watchdog kills worker on blocked stdin flush and raises MathWorkerCrashedError."""
    supervisor = MathJaxProcessSupervisor(
        write_timeout_seconds=0.05,
    )
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 12345

    def blocking_flush():
        # Simulate blocking in kernel pipe buffer until killed
        time.sleep(0.1)
        raise BrokenPipeError("Broken pipe simulated after process kill")

    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.flush = MagicMock(side_effect=blocking_flush)
    mock_proc.stdout.readline = MagicMock(return_value="")

    supervisor._response_queue = queue.Queue()
    supervisor._process_generation = 1

    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathWorkerCrashedError) as exc_info:
            supervisor.render("x + y")

    assert "Failed to communicate with MathJax worker" in exc_info.value.message
    # Verify mock_proc.kill was invoked by the watchdog
    mock_proc.kill.assert_called()


# ==============================================================================
# 2. Canonical Supervisor Evaluation Order Tests
# ==============================================================================

def test_evaluation_order_preflight_tex_check_precedes_circuit_breaker():
    """Pre-flight MAX_TEX_LENGTH check raises MathBufferLimitExceededError even when breaker is OPEN."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    # Trip the breaker to OPEN
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    huge_tex = "a" * (MAX_TEX_LENGTH + 10)
    # Pre-flight check MUST raise MathBufferLimitExceededError, NOT MathCircuitBreakerOpenError
    with pytest.raises(MathBufferLimitExceededError) as exc_info:
        supervisor.render(huge_tex)

    assert exc_info.value.code == -32600
    assert "Buffer limit exceeded" in exc_info.value.message
    # Breaker state must remain OPEN and failure count unaltered
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.is_probe_in_flight is False


def test_evaluation_order_circuit_breaker_fails_fast_in_o1_when_open():
    """When circuit breaker is OPEN, render() fails fast in O(1) without touching worker process."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(supervisor, "_call_rpc_locked") as mock_rpc:
        with pytest.raises(MathCircuitBreakerOpenError) as exc_info:
            supervisor.render("x + y")

        mock_rpc.assert_not_called()

    assert "circuit breaker is OPEN" in exc_info.value.message


# ==============================================================================
# 3. Probe Semantics: Successful Probes
# ==============================================================================

def test_probe_success_on_valid_worker_response():
    """Successful render response in HALF_OPEN transitions breaker to CLOSED and clears history."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    # Advance clock past cooldown deadline
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    valid_payload = {
        "svg": "<svg>valid</svg>",
        "width": "2ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }
    with patch.object(supervisor, "_call_rpc_locked", return_value=valid_payload):
        res = supervisor.render("x + y")

    assert res["svg_xml"] == "<svg>valid</svg>"
    # Breaker transitions to CLOSED and resets history
    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS


def test_probe_success_on_syntax_error():
    """MathSyntaxError proves worker is responsive; in HALF_OPEN it transitions breaker to CLOSED."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathSyntaxError(code=-32602, message="Missing close brace"),
    ):
        with pytest.raises(MathSyntaxError):
            supervisor.render(r"\frac{1}{")

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


def test_probe_success_on_worker_side_buffer_error():
    """Worker-side -32600 error proves worker is responsive; in HALF_OPEN it transitions breaker to CLOSED."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathBufferLimitExceededError(code=-32600, message="Buffer limit exceeded: Worker buffer limit"),
    ):
        with pytest.raises(MathBufferLimitExceededError):
            supervisor.render("valid_tex_that_worker_rejected")

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


def test_probe_success_on_oversized_svg_output():
    """Oversized SVG output (>512 KB) raises MathBufferLimitExceededError but closes the circuit breaker."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    oversized_svg_payload = {
        "svg": "<svg>" + ("x" * (MAX_SVG_LENGTH + 100)) + "</svg>",
        "width": "100ex",
        "height": "50ex",
        "vertical_align": "0ex",
    }
    with patch.object(supervisor, "_call_rpc_locked", return_value=oversized_svg_payload):
        with pytest.raises(MathBufferLimitExceededError) as exc_info:
            supervisor.render("huge_output")

    assert exc_info.value.code == -32600
    assert "SVG output" in exc_info.value.message
    # Proves worker responded, so probe was successful
    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


# ==============================================================================
# 4. Probe Semantics: Failed Probes
# ==============================================================================

def test_probe_failure_on_request_timeout():
    """Request timeout in HALF_OPEN fails probe, trips back to OPEN, and doubles cooldown."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathRenderTimeoutError(code=-32603, message="RPC request 'render' timed out"),
    ):
        with pytest.raises(MathRenderTimeoutError):
            supervisor.render("x")

    assert breaker.state == CircuitBreakerState.OPEN
    # Cooldown doubled from 30s to 60s
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS * 2.0


def test_probe_failure_on_worker_crash():
    """Worker crash in HALF_OPEN fails probe, trips back to OPEN, and doubles cooldown."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathWorkerCrashedError(code=-32603, message="Worker process exited unexpectedly"),
    ):
        with pytest.raises(MathWorkerCrashedError):
            supervisor.render("x")

    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS * 2.0


def test_probe_failure_on_startup_handshake_failure():
    """Startup failure in HALF_OPEN fails probe, trips back to OPEN, and doubles cooldown."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(MAX_RESTARTS_PER_MINUTE):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS + 1.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathWorkerStartupError(code=-32603, message="Handshake failed"),
    ):
        with pytest.raises(MathWorkerStartupError):
            supervisor.render("x")

    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS * 2.0


# ==============================================================================
# 5. Clean Initial Boot & Repeated Failures
# ==============================================================================

def test_clean_initial_boot_records_zero_failures():
    """Initial worker startup does not consume restart budget (P10 invariant: initial start != restart)."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
        circuit_breaker=breaker,
    )
    try:
        assert supervisor.is_alive is True
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0
    finally:
        supervisor.shutdown()


def test_three_consecutive_failures_trips_breaker_to_open():
    """Three consecutive failures trip the circuit breaker into OPEN state."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    for i in range(MAX_RESTARTS_PER_MINUTE - 1):
        with patch.object(
            supervisor,
            "_call_rpc_locked",
            side_effect=MathWorkerCrashedError(code=-32603, message="Crash"),
        ):
            with pytest.raises(MathWorkerCrashedError):
                supervisor.render("x")
        assert breaker.state == CircuitBreakerState.CLOSED

    # 3rd failure trips the breaker
    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathWorkerCrashedError(code=-32603, message="Crash 3"),
    ):
        with pytest.raises(MathWorkerCrashedError):
            supervisor.render("x")

    assert breaker.state == CircuitBreakerState.OPEN


# ==============================================================================
# 6. Neutral Shutdown Event
# ==============================================================================

def test_shutdown_error_does_not_record_breaker_failure():
    """MathSupervisorShutdownError is a neutral lifecycle event and does not record breaker failure."""
    clock = SyntheticClock(1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    with patch.object(
        supervisor,
        "_call_rpc_locked",
        side_effect=MathSupervisorShutdownError(code=-32603, message="Supervisor shut down"),
    ):
        with pytest.raises(MathSupervisorShutdownError):
            supervisor.render("x")

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


# ==============================================================================
# 7. Clean Architecture Invariant: No Qt Imports
# ==============================================================================

def test_supervisor_and_tests_have_no_qt_imports():
    """Clean Architecture invariant: math infrastructure and tests must not import Qt/PySide6."""
    files_to_check = [
        Path(__file__).resolve().parent.parent.parent / "infrastructure" / "math" / "mathjax_supervisor.py",
        Path(__file__).resolve(),
    ]
    forbidden = ("pyside", "pyqt", "qt")

    for fpath in files_to_check:
        tree = ast.parse(fpath.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(x in alias.name.lower() for x in forbidden), f"Forbidden import {alias.name} in {fpath}"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert not any(x in mod for x in forbidden), f"Forbidden from-import {mod} in {fpath}"
