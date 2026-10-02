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
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOOD_STDERR_WORKER = FIXTURES_DIR / "flood_stderr_worker.py"

from application.ports.math_renderer import (
    MathRenderTimeoutError,
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


# Test 6 — Concurrent shutdown from multiple threads
def test_concurrent_simultaneous_shutdown_from_multiple_threads():
    """Simultaneous shutdown() calls from multiple threads do not raise, deadlock, or corrupt state."""
    supervisor = MathJaxProcessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.poll.return_value = None

    def mock_wait(timeout=None):
        mock_proc.poll.return_value = 0
        return 0

    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = MagicMock(side_effect=mock_wait)

    supervisor._process = mock_proc
    supervisor._response_queue = queue.Queue()

    thread_count = 10
    barrier = threading.Barrier(thread_count)
    exceptions = []

    def worker():
        try:
            barrier.wait(timeout=5.0)
            supervisor.shutdown()
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    # Verify:
    # - no exception
    assert exceptions == [], f"Unexpected exceptions during concurrent shutdown: {exceptions}"
    # - no deadlock
    assert all(not t.is_alive() for t in threads)
    # - final process reference is None
    assert supervisor._process is None
    # - supervisor remains is_shutdown == True
    assert supervisor.is_shutdown is True
    assert supervisor.is_alive is False


def test_concurrent_simultaneous_shutdown_with_real_process():
    """Simultaneous shutdown() calls against a real worker process do not raise or deadlock."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    exceptions = []

    def worker():
        try:
            barrier.wait(timeout=5.0)
            supervisor.shutdown()
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert exceptions == []
    assert all(not t.is_alive() for t in threads)
    assert supervisor._process is None
    assert supervisor.is_shutdown is True
    assert supervisor.is_alive is False


# Test 7 — Deterministic lock-boundary observation
def test_process_termination_never_executes_while_supervisor_lock_held():
    """Verify that proc.terminate(), proc.kill(), and proc.wait() are never called while self._lock is held."""
    supervisor = MathJaxProcessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 4321
    mock_proc.poll.return_value = None

    lock_states = {
        "terminate": [],
        "kill": [],
        "wait": [],
    }

    def spy_terminate():
        lock_states["terminate"].append(supervisor._lock.locked())

    def spy_kill():
        lock_states["kill"].append(supervisor._lock.locked())
        mock_proc.poll.return_value = -9

    def spy_wait(timeout=None):
        lock_states["wait"].append(supervisor._lock.locked())
        return -9

    mock_proc.terminate = MagicMock(side_effect=spy_terminate)
    mock_proc.kill = MagicMock(side_effect=spy_kill)
    mock_proc.wait = MagicMock(side_effect=spy_wait)

    supervisor._process = mock_proc
    supervisor._response_queue = queue.Queue()

    thread_join_lock_states = []
    mock_reader = MagicMock()
    mock_reader.is_alive.return_value = True

    def spy_join(timeout=None):
        thread_join_lock_states.append(supervisor._lock.locked())
        mock_reader.is_alive.return_value = False

    mock_reader.join = MagicMock(side_effect=spy_join)
    supervisor._stdout_reader_thread = mock_reader

    supervisor.shutdown()

    # Verify terminate was called
    assert len(lock_states["terminate"]) >= 1
    # Verify lock was NOT held during terminate
    assert all(held is False for held in lock_states["terminate"])

    # Verify wait was called
    assert len(lock_states["wait"]) >= 1
    # Verify lock was NOT held during wait
    assert all(held is False for held in lock_states["wait"])

    # Verify lock was NOT held during kill (if kill was called)
    assert all(held is False for held in lock_states["kill"])

    # Verify thread joins happened outside lock
    assert len(thread_join_lock_states) >= 1
    assert all(held is False for held in thread_join_lock_states)

    assert supervisor._process is None
    assert supervisor.is_shutdown is True


def test_cleanup_process_handles_locked_never_calls_terminate_kill_or_wait():
    """_cleanup_process_handles_locked must strictly not call terminate(), kill(), wait(), or thread.join()."""
    supervisor = MathJaxProcessSupervisor()
    mock_proc = MagicMock()
    mock_proc.pid = 1111
    mock_proc.poll.return_value = 0

    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = MagicMock()

    mock_reader = MagicMock()
    mock_drain = MagicMock()
    supervisor._stdout_reader_thread = mock_reader
    supervisor._stderr_drain_thread = mock_drain
    supervisor._process = mock_proc

    with supervisor._lock:
        supervisor._cleanup_process_handles_locked(reason="TEST")

    mock_proc.terminate.assert_not_called()
    mock_proc.kill.assert_not_called()
    mock_proc.wait.assert_not_called()
    mock_reader.join.assert_not_called()
    mock_drain.join.assert_not_called()


def test_rpc_failure_cannot_terminate_new_process_generation():
    """An RPC failure from generation N must not terminate or clear handles of generation N+1."""
    supervisor = MathJaxProcessSupervisor()

    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 1001
    mock_proc_gen1.poll.return_value = None
    mock_proc_gen1.terminate = MagicMock()
    mock_proc_gen1.kill = MagicMock()
    mock_proc_gen1.wait = MagicMock(return_value=0)

    mock_proc_gen2 = MagicMock()
    mock_proc_gen2.pid = 2002
    mock_proc_gen2.poll.return_value = None
    mock_proc_gen2.terminate = MagicMock()
    mock_proc_gen2.kill = MagicMock()
    mock_proc_gen2.wait = MagicMock()

    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()
    mock_reader_gen2 = MagicMock()
    mock_drain_gen2 = MagicMock()

    original_terminate = supervisor._terminate_process_outside_lock

    def interleave_respawn(proc, reason="CLEANUP"):
        original_terminate(proc, reason=reason)
        if proc is mock_proc_gen1:
            with supervisor._lock:
                supervisor._process = mock_proc_gen2
                supervisor._process_generation = 2
                supervisor._response_queue = queue.Queue()
                supervisor._stdout_reader_thread = mock_reader_gen2
                supervisor._stderr_drain_thread = mock_drain_gen2

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=interleave_respawn)

    timeout_err = MathRenderTimeoutError(code=-32603, message="Timeout on Gen 1")
    supervisor._handle_rpc_exception(timeout_err, proc=mock_proc_gen1, generation=1)

    # 1. Gen 1 was terminated
    mock_proc_gen1.terminate.assert_called_once()

    # 2. Gen 2 was NOT terminated or killed
    mock_proc_gen2.terminate.assert_not_called()
    mock_proc_gen2.kill.assert_not_called()
    mock_proc_gen2.wait.assert_not_called()

    # 3. Gen 2 process and generation are intact on supervisor
    assert supervisor._process is mock_proc_gen2
    assert supervisor._process_generation == 2
    assert supervisor._response_queue is not None
    assert supervisor._stdout_reader_thread is mock_reader_gen2
    assert supervisor._stderr_drain_thread is mock_drain_gen2


def test_dispatch_rpc_captures_generation_and_spares_new_generation():
    """Verify _dispatch_rpc captures failed process/generation under lock and spares generation N+1."""
    supervisor = MathJaxProcessSupervisor()

    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 1111
    mock_proc_gen1.poll.return_value = None
    mock_proc_gen1.terminate = MagicMock()
    mock_proc_gen1.kill = MagicMock()
    mock_proc_gen1.wait = MagicMock(return_value=0)

    mock_proc_gen2 = MagicMock()
    mock_proc_gen2.pid = 2222
    mock_proc_gen2.poll.return_value = None
    mock_proc_gen2.terminate = MagicMock()
    mock_proc_gen2.kill = MagicMock()
    mock_proc_gen2.wait = MagicMock()

    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    def mock_call_rpc(method, params=None):
        raise MathRenderTimeoutError(code=-32603, message="Timeout on Gen 1")

    supervisor._call_rpc_locked = MagicMock(side_effect=mock_call_rpc)

    original_terminate = supervisor._terminate_process_outside_lock

    def interleave_respawn(proc, reason="CLEANUP"):
        original_terminate(proc, reason=reason)
        if proc is mock_proc_gen1:
            with supervisor._lock:
                supervisor._process = mock_proc_gen2
                supervisor._process_generation = 2
                supervisor._response_queue = queue.Queue()

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=interleave_respawn)

    with pytest.raises(MathRenderTimeoutError):
        supervisor._dispatch_rpc("render", {"tex": "x"})

    # Gen 1 was terminated
    mock_proc_gen1.terminate.assert_called_once()
    # Gen 2 was NOT terminated
    mock_proc_gen2.terminate.assert_not_called()
    # Supervisor retained Gen 2
    assert supervisor._process is mock_proc_gen2
    assert supervisor._process_generation == 2


def test_respawn_cleanup_cannot_clear_new_process_generation():
    """Verify that respawn cleanup for generation N cannot clear generation N+1 state or handles."""
    supervisor = MathJaxProcessSupervisor()

    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 3001
    mock_proc_gen1.poll.return_value = -9
    mock_proc_gen1.terminate = MagicMock()
    mock_proc_gen1.kill = MagicMock()
    mock_proc_gen1.wait = MagicMock(return_value=0)

    mock_proc_gen2 = MagicMock()
    mock_proc_gen2.pid = 4002
    mock_proc_gen2.poll.return_value = None
    mock_proc_gen2.terminate = MagicMock()
    mock_proc_gen2.kill = MagicMock()
    mock_proc_gen2.wait = MagicMock()

    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()
    mock_reader_gen2 = MagicMock()
    mock_drain_gen2 = MagicMock()

    original_terminate = supervisor._terminate_process_outside_lock

    def interleave_replacement(proc, reason="CLEANUP"):
        original_terminate(proc, reason=reason)
        if proc is mock_proc_gen1:
            with supervisor._lock:
                supervisor._process = mock_proc_gen2
                supervisor._process_generation = 2
                supervisor._response_queue = queue.Queue()
                supervisor._stdout_reader_thread = mock_reader_gen2
                supervisor._stderr_drain_thread = mock_drain_gen2

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=interleave_replacement)

    with supervisor._lock:
        returned_proc = supervisor._ensure_process_locked()

    # Gen 1 termination was initiated during respawn
    supervisor._terminate_process_outside_lock.assert_called_once_with(mock_proc_gen1, reason="RESPAWN")

    # Gen 2 was NOT terminated or modified
    mock_proc_gen2.terminate.assert_not_called()
    mock_proc_gen2.kill.assert_not_called()
    mock_proc_gen2.wait.assert_not_called()

    # Supervisor returned Gen 2 and preserved all Gen 2 handles
    assert returned_proc is mock_proc_gen2
    assert supervisor._process is mock_proc_gen2
    assert supervisor._process_generation == 2
    assert supervisor._response_queue is not None
    assert supervisor._stdout_reader_thread is mock_reader_gen2
    assert supervisor._stderr_drain_thread is mock_drain_gen2


def test_concurrent_respawn_only_one_owner_terminates_stale_process():
    """Verify that concurrent callers observing a dead process coordinate via CV so only one terminates."""
    supervisor = MathJaxProcessSupervisor()

    mock_stale_proc = MagicMock()
    mock_stale_proc.pid = 5001
    mock_stale_proc.poll.return_value = -9
    mock_stale_proc.terminate = MagicMock()
    mock_stale_proc.kill = MagicMock()
    mock_stale_proc.wait = MagicMock(return_value=0)

    mock_new_proc = MagicMock()
    mock_new_proc.pid = 5002
    mock_new_proc.poll.return_value = None
    mock_new_proc.stdin = MagicMock()
    mock_new_proc.stdout = MagicMock()
    mock_new_proc.stdout.readline = MagicMock(return_value="")
    mock_new_proc.stderr = MagicMock()
    mock_new_proc.stderr.readline = MagicMock(return_value="")

    supervisor._process = mock_stale_proc
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    t1_in_terminate = threading.Event()
    t2_waiting_on_cv = threading.Event()
    t1_can_proceed = threading.Event()

    orig_terminate = supervisor._terminate_process_outside_lock

    def spy_terminate(proc, reason="CLEANUP"):
        if proc is mock_stale_proc:
            t1_in_terminate.set()
            t1_can_proceed.wait(timeout=5.0)
        return orig_terminate(proc, reason=reason)

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=spy_terminate)

    orig_wait = supervisor._state_cv.wait

    def spy_wait(*args, **kwargs):
        t2_waiting_on_cv.set()
        return orig_wait(*args, **kwargs)

    supervisor._state_cv.wait = spy_wait

    results = {}
    exceptions = []

    def caller_thread(thread_id: str):
        try:
            with supervisor._lock:
                proc = supervisor._ensure_process_locked()
            results[thread_id] = proc
        except Exception as e:
            exceptions.append((thread_id, e))

    with patch("subprocess.Popen", return_value=mock_new_proc) as mock_popen:
        with patch.object(supervisor, "_perform_startup_handshake_locked"):
            t1 = threading.Thread(target=caller_thread, args=("t1",))
            t2 = threading.Thread(target=caller_thread, args=("t2",))

            t1.start()
            # Wait until T1 has claimed the stale process and is inside terminate outside lock
            assert t1_in_terminate.wait(timeout=5.0), "T1 did not reach terminate"

            # Now start T2, which must see self._respawning=True and wait on CV
            t2.start()
            assert t2_waiting_on_cv.wait(timeout=5.0), "T2 did not wait on condition variable"

            # Allow T1 to proceed with termination and replacement spawn
            t1_can_proceed.set()

            t1.join(timeout=5.0)
            t2.join(timeout=5.0)

    assert exceptions == [], f"Unexpected exceptions: {exceptions}"
    assert results.get("t1") is mock_new_proc
    assert results.get("t2") is mock_new_proc

    # Stale process terminated exactly once
    supervisor._terminate_process_outside_lock.assert_called_once_with(mock_stale_proc, reason="RESPAWN")
    # Popen called exactly once to spawn replacement
    mock_popen.assert_called_once()

    # Supervisor state is consistent
    assert supervisor._process is mock_new_proc
    assert supervisor._process_generation == 2
    assert supervisor._respawning is False


def test_shutdown_wakes_waiting_thread_when_processless_respawn_active():
    """Verify that shutdown() wakes threads waiting on CV even when process=None and response_queue=None."""
    supervisor = MathJaxProcessSupervisor()
    assert supervisor._process is None
    assert supervisor._response_queue is None

    # Simulate an active respawn transition before Popen assigns process/queue
    supervisor._respawning = True

    waiting_on_cv = threading.Event()
    orig_wait = supervisor._state_cv.wait

    def spy_wait(*args, **kwargs):
        waiting_on_cv.set()
        return orig_wait(*args, **kwargs)

    supervisor._state_cv.wait = spy_wait

    thread_error = []

    def caller_thread():
        try:
            with supervisor._lock:
                supervisor._ensure_process_locked()
        except Exception as e:
            thread_error.append(e)

    t = threading.Thread(target=caller_thread)
    t.start()

    # Deterministically ensure caller_thread is waiting on state_cv
    assert waiting_on_cv.wait(timeout=5.0), "Thread did not enter state_cv.wait"

    # Invariants before shutdown:
    assert supervisor._respawning is True
    assert supervisor._process is None
    assert supervisor._response_queue is None

    # Trigger shutdown from a different thread
    supervisor.shutdown()

    # Caller thread must wake up and exit immediately
    t.join(timeout=2.0)
    assert not t.is_alive(), "Waiting thread was stranded after shutdown"
    assert len(thread_error) == 1
    assert isinstance(thread_error[0], MathSupervisorShutdownError)
    assert supervisor.is_shutdown is True


def test_rpc_failure_does_not_expose_terminating_process_as_healthy():
    """Verify that an RPC-failed worker is never treated as healthy while teardown is in progress."""
    supervisor = MathJaxProcessSupervisor()

    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 7001
    mock_proc_gen1.poll.return_value = None  # Process appears alive to OS poll()
    mock_proc_gen1.terminate = MagicMock()
    mock_proc_gen1.kill = MagicMock()
    mock_proc_gen1.wait = MagicMock(return_value=0)

    mock_proc_gen2 = MagicMock()
    mock_proc_gen2.pid = 7002
    mock_proc_gen2.poll.return_value = None
    mock_proc_gen2.stdin = MagicMock()
    mock_proc_gen2.stdout = MagicMock()
    mock_proc_gen2.stdout.readline = MagicMock(return_value="")
    mock_proc_gen2.stderr = MagicMock()
    mock_proc_gen2.stderr.readline = MagicMock(return_value="")

    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    t1_in_terminate = threading.Event()
    t2_waiting_on_cv = threading.Event()
    t1_can_proceed = threading.Event()

    orig_terminate = supervisor._terminate_process_outside_lock

    def spy_terminate(proc, reason="CLEANUP"):
        if proc is mock_proc_gen1 and reason == "REQUEST_TIMEOUT":
            t1_in_terminate.set()
            assert t1_can_proceed.wait(timeout=5.0), "Timeout waiting for t1_can_proceed"
            mock_proc_gen1.poll.return_value = -9
        return orig_terminate(proc, reason=reason)

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=spy_terminate)

    orig_wait = supervisor._state_cv.wait

    def spy_wait(*args, **kwargs):
        t2_waiting_on_cv.set()
        return orig_wait(*args, **kwargs)

    supervisor._state_cv.wait = spy_wait

    t1_error = []
    t2_result = []

    def failing_rpc_thread():
        try:
            def mock_call_rpc(method, params=None):
                timeout_exc = MathRenderTimeoutError(code=-32603, message="Timeout on Gen 1")
                object.__setattr__(timeout_exc, "_failed_proc", mock_proc_gen1)
                object.__setattr__(timeout_exc, "_failed_generation", 1)
                raise timeout_exc

            with patch.object(supervisor, "_call_rpc_locked", side_effect=mock_call_rpc):
                supervisor._dispatch_rpc("render", {"tex": "x"})
        except Exception as e:
            t1_error.append(e)

    def concurrent_caller_thread():
        try:
            with supervisor._lock:
                proc = supervisor._ensure_process_locked()
            t2_result.append(proc)
        except Exception as e:
            t2_result.append(e)

    with patch("subprocess.Popen", return_value=mock_proc_gen2) as mock_popen:
        with patch.object(supervisor, "_perform_startup_handshake_locked"):
            t1 = threading.Thread(target=failing_rpc_thread)
            t2 = threading.Thread(target=concurrent_caller_thread)

            t1.start()

            # 1. Wait until Gen 1 failure handler begins out-of-lock process termination
            assert t1_in_terminate.wait(timeout=5.0), "T1 did not reach terminate"

            # 2. Concurrent caller attempts _ensure_process_locked() while Gen 1 is terminating
            t2.start()

            # 3. Verify T2 does NOT receive mock_proc_gen1 as healthy, but instead waits on CV
            assert t2_waiting_on_cv.wait(timeout=5.0), "T2 did not wait on CV while Gen 1 was terminating"
            assert len(t2_result) == 0, "T2 must not have returned while Gen 1 is terminating"

            # 4. Now let T1 complete out-of-lock termination and cleanup
            t1_can_proceed.set()

            t1.join(timeout=5.0)
            t2.join(timeout=5.0)

    # 5. Verify T1 raised MathRenderTimeoutError
    assert len(t1_error) == 1
    assert isinstance(t1_error[0], MathRenderTimeoutError)

    # 6. Verify T2 did NOT receive mock_proc_gen1, but performed coordinated replacement and received Gen 2
    assert len(t2_result) == 1
    assert t2_result[0] is mock_proc_gen2
    assert t2_result[0] is not mock_proc_gen1

    # 7. Verify Gen 1 was terminated and Gen 2 remains intact
    supervisor._terminate_process_outside_lock.assert_called_once_with(mock_proc_gen1, reason="REQUEST_TIMEOUT")
    mock_popen.assert_called_once()
    assert supervisor._process is mock_proc_gen2
    assert supervisor._process_generation == 2
