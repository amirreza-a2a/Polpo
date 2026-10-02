"""Unit tests for MathJax supervisor idempotent teardown and shutdown coordination.

Tests cover TICK-P07E and ADR-002 section D06:
1. RPC waiter unblocking via _AbortSentinel without waiting out request timeouts.
2. Strictly idempotent repeated shutdown() calls.
3. Spawn vs shutdown race prevention (no orphan processes).
4. Circuit breaker probe permit release on shutdown.
5. Silent teardown during application exit (zero ERROR logs, clean DTO return).
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from application.ports.math_renderer import (
    MathSupervisorShutdownError,
)
from infrastructure.math.circuit_breaker import (
    CircuitBreakerState,
    MathCircuitBreaker,
)
from infrastructure.math.mathjax_supervisor import (
    MathJaxProcessSupervisor,
)


class SyntheticClock:
    """Deterministic monotonic clock provider for testing circuit breaker transitions."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self.time: float = initial_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


# Test 1 — RPC waiter unblock
def test_rpc_waiter_unblocks_immediately_on_shutdown():
    """An active thread blocked waiting on an RPC response wakes up immediately on shutdown."""
    supervisor = MathJaxProcessSupervisor(
        request_timeout_seconds=10.0,
    )
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 4321
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    supervisor._process = mock_proc
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    result_holder = []
    error_holder = []
    started_event = threading.Event()

    def run_rpc():
        started_event.set()
        try:
            with supervisor._lock:
                res = supervisor._call_rpc_locked("render", {"tex": "x"})
            result_holder.append(res)
        except Exception as exc:
            error_holder.append(exc)

    rpc_thread = threading.Thread(target=run_rpc)
    rpc_thread.start()

    assert started_event.wait(timeout=2.0)
    time.sleep(0.05)

    start_time = time.monotonic()
    supervisor.shutdown()
    rpc_thread.join(timeout=2.0)
    elapsed = time.monotonic() - start_time

    assert not rpc_thread.is_alive()
    assert elapsed < 2.0
    assert len(error_holder) == 1
    assert isinstance(error_holder[0], MathSupervisorShutdownError)


# Test 2 — Idempotent shutdown
def test_shutdown_is_strictly_idempotent():
    """Calling shutdown() multiple times in succession causes no errors, double-kills, or state corruption."""
    supervisor = MathJaxProcessSupervisor()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 5678

    def mock_wait(timeout=None):
        mock_proc.poll.return_value = 0
        return 0

    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = MagicMock(side_effect=mock_wait)

    supervisor._process = mock_proc
    supervisor._response_queue = queue.Queue()

    supervisor.shutdown()
    supervisor.shutdown()
    supervisor.shutdown()

    assert supervisor.is_alive is False
    assert supervisor._process is None
    assert supervisor.is_shutdown is True


# Test 3 — Spawn/shutdown race
def test_spawn_shutdown_race_kills_process_and_raises_shutdown_error():
    """When shutdown occurs while _ensure_process_locked() is spawning, the new process is killed and reaped."""
    supervisor = MathJaxProcessSupervisor()
    spawned_procs = []

    def mock_popen(*args, **kwargs):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 9876
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()

        def kill_proc():
            proc.poll.return_value = -9

        def wait_proc(timeout=None):
            proc.poll.return_value = -9
            return -9

        proc.kill = MagicMock(side_effect=kill_proc)
        proc.wait = MagicMock(side_effect=wait_proc)
        spawned_procs.append(proc)

        # Trigger concurrent shutdown while Popen is returning
        supervisor.shutdown()
        return proc

    with patch("subprocess.Popen", side_effect=mock_popen):
        with pytest.raises(MathSupervisorShutdownError):
            with supervisor._lock:
                supervisor._ensure_process_locked()

    assert len(spawned_procs) == 1
    new_proc = spawned_procs[0]
    new_proc.kill.assert_called()
    assert supervisor._process is None
    assert supervisor.is_shutdown is True


# Test 4 — Probe release
def test_shutdown_while_probe_is_active_releases_probe_permit():
    """If shutdown() occurs while a HALF_OPEN probe is in flight, the probe reservation is released."""
    clock = SyntheticClock(initial_time=1000.0)
    breaker = MathCircuitBreaker(time_provider=clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(30.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    supervisor = MathJaxProcessSupervisor(circuit_breaker=breaker)

    # Claim a probe permit manually
    probe_permit = breaker.probe_permit()
    probe_permit.__enter__()
    assert breaker.is_probe_in_flight is True

    # Shutdown supervisor while probe is in flight
    supervisor.shutdown()

    # Probe slot must be cleanly released and not stuck in flight
    assert breaker.is_probe_in_flight is False
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    # Subsequent probe operations on the breaker succeed normally
    with breaker.probe_permit() as next_probe:
        assert breaker.is_probe_in_flight is True
        next_probe.record_success()

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


# Test 5 — Silent teardown
def test_silent_teardown_swallows_shutdown_error_without_logging_or_failure(caplog: pytest.LogCaptureFixture):
    """MathSupervisorShutdownError during application exit is handled silently by markdown viewer service."""
    mock_renderer = MagicMock()
    mock_renderer.render_batch.side_effect = MathSupervisorShutdownError(
        code=-32603,
        message="MathJaxProcessSupervisor has been shut down.",
    )

    from application.services.markdown_viewer_service import MarkdownViewerService

    mock_parser = MagicMock()
    mock_doc = MagicMock()
    math_block = MagicMock()
    math_block.type = "math_block"
    math_block.content = "x^2 + y^2 = z^2"
    mock_doc.blocks = [math_block]
    mock_parser.parse.return_value = mock_doc

    mock_uow_factory = MagicMock()

    service = MarkdownViewerService(
        uow_factory=mock_uow_factory,
        parser=mock_parser,
        storage=MagicMock(),
        math_renderer=mock_renderer,
    )

    with caplog.at_level(logging.ERROR):
        dto = service.render_text(
            raw_text="$$x^2 + y^2 = z^2$$",
            active_regions=[],
            job_id=1,
            version=1,
        )

    assert dto is not None
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_logs) == 0
