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


class OwnerTrackingLock:
    """Wrapper around threading.Lock tracking whether the calling thread holds the lock."""

    def __init__(self, real_lock: threading.Lock) -> None:
        self._real_lock = real_lock
        self._owner_ident: int | None = None
        self._meta_lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        res = self._real_lock.acquire(blocking, timeout)
        if res:
            with self._meta_lock:
                self._owner_ident = threading.get_ident()
        return res

    def release(self) -> None:
        with self._meta_lock:
            self._owner_ident = None
        self._real_lock.release()

    def __enter__(self) -> OwnerTrackingLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()

    def _is_owned(self) -> bool:
        with self._meta_lock:
            return self._owner_ident == threading.get_ident()

    def is_owned_by_current_thread(self) -> bool:
        return self._is_owned()

    def locked(self) -> bool:
        return self._real_lock.locked()


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

        proc.terminate = MagicMock()
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
    assert new_proc.terminate.called or new_proc.kill.called
    assert new_proc.poll() is not None
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


def test_spawn_shutdown_race_termination_occurs_outside_supervisor_lock():
    """Verify that when shutdown occurs during Popen, post-Popen termination executes outside supervisor lock."""
    supervisor = MathJaxProcessSupervisor()
    tracking_lock = OwnerTrackingLock(supervisor._lock)
    supervisor._lock = tracking_lock
    supervisor._state_cv = threading.Condition(tracking_lock)

    mock_proc = MagicMock()
    mock_proc.pid = 9999

    lock_states = {
        "terminate": [],
        "kill": [],
        "wait": [],
    }

    reaped = False

    def mock_poll():
        if reaped:
            return -9
        return None

    mock_proc.poll = MagicMock(side_effect=mock_poll)

    def spy_terminate():
        lock_states["terminate"].append(tracking_lock.is_owned_by_current_thread())

    def spy_kill():
        nonlocal reaped
        lock_states["kill"].append(tracking_lock.is_owned_by_current_thread())
        reaped = True

    def spy_wait(timeout=None):
        lock_states["wait"].append(tracking_lock.is_owned_by_current_thread())
        if reaped:
            return -9
        return None

    mock_proc.terminate = MagicMock(side_effect=spy_terminate)
    mock_proc.kill = MagicMock(side_effect=spy_kill)
    mock_proc.wait = MagicMock(side_effect=spy_wait)

    popen_executed = threading.Event()
    shutdown_started = threading.Event()
    shutdown_thread_holder = []

    def racing_popen(*args, **kwargs):
        # 1. supervisor._lock is held by the spawning thread
        assert tracking_lock.is_owned_by_current_thread() is True
        popen_executed.set()

        # 3. shutdown starts during Popen from another thread
        def run_shutdown():
            shutdown_started.set()
            supervisor.shutdown()

        shutdown_thread = threading.Thread(target=run_shutdown)
        shutdown_thread_holder.append(shutdown_thread)
        shutdown_thread.start()

        # Wait until shutdown starts and sets _is_shutdown
        assert shutdown_started.wait(timeout=5.0)
        while not supervisor._is_shutdown:
            time.sleep(0.001)

        # 4. Popen returns
        return mock_proc

    with patch("subprocess.Popen", side_effect=racing_popen) as mock_popen:
        with pytest.raises(MathSupervisorShutdownError) as exc_info:
            with supervisor._lock:
                supervisor._ensure_process_locked()

    if shutdown_thread_holder:
        shutdown_thread_holder[0].join(timeout=5.0)

    assert "shut down during spawn" in exc_info.value.message

    # Verify terminate, wait, and kill were all called
    assert len(lock_states["terminate"]) >= 1, "proc.terminate() was not called"
    assert len(lock_states["kill"]) >= 1, "proc.kill() was not called"
    assert len(lock_states["wait"]) >= 1, "proc.wait() was not called"

    # Assert that for EVERY call, supervisor._lock was NOT held by terminating thread
    assert all(held is False for held in lock_states["terminate"]), "Lock was held by terminating thread during proc.terminate()"
    assert all(held is False for held in lock_states["kill"]), "Lock was held by terminating thread during proc.kill()"
    assert all(held is False for held in lock_states["wait"]), "Lock was held by terminating thread during proc.wait()"

    # Verify spawned process is reaped
    assert mock_proc.poll() is not None, "Process was not reaped"

    # Verify supervisor._process is None after cleanup
    assert supervisor._process is None
    assert supervisor.is_alive is False
    assert supervisor.is_shutdown is True

    # Verify mock_popen was called exactly once (no replacement worker created)
    mock_popen.assert_called_once()


def test_respawn_shutdown_race_terminates_both_generations_cleanly():
    """Verify that when shutdown occurs during respawn Popen, both generations are reaped without lock."""
    supervisor = MathJaxProcessSupervisor()
    tracking_lock = OwnerTrackingLock(supervisor._lock)
    supervisor._lock = tracking_lock
    supervisor._state_cv = threading.Condition(tracking_lock)

    # Generation 1 mock process (stale)
    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 1111
    gen1_state = {"reaped": False}

    def poll_gen1():
        if gen1_state["reaped"]:
            return -9
        return None

    mock_proc_gen1.poll = MagicMock(side_effect=poll_gen1)

    # Generation 2 mock process (replacement spawned during respawn)
    mock_proc_gen2 = MagicMock()
    mock_proc_gen2.pid = 2222
    gen2_state = {"reaped": False}

    def poll_gen2():
        if gen2_state["reaped"]:
            return -9
        return None

    mock_proc_gen2.poll = MagicMock(side_effect=poll_gen2)

    gen1_lock_states = []
    gen2_lock_states = []

    def spy_term_gen1():
        gen1_lock_states.append(tracking_lock.is_owned_by_current_thread())
        gen1_state["reaped"] = True

    def spy_term_gen2():
        gen2_lock_states.append(tracking_lock.is_owned_by_current_thread())
        gen2_state["reaped"] = True

    mock_proc_gen1.terminate = MagicMock(side_effect=spy_term_gen1)
    mock_proc_gen1.kill = MagicMock(side_effect=spy_term_gen1)
    mock_proc_gen1.wait = MagicMock(return_value=-9)

    mock_proc_gen2.terminate = MagicMock(side_effect=spy_term_gen2)
    mock_proc_gen2.kill = MagicMock(side_effect=spy_term_gen2)
    mock_proc_gen2.wait = MagicMock(return_value=-9)

    # Set up supervisor with an existing (stale) Gen 1 process
    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    shutdown_started = threading.Event()
    shutdown_thread_holder = []

    def racing_replacement_popen(*args, **kwargs):
        # Gen 1 was already terminated outside lock; replacement Popen holds lock
        assert tracking_lock.is_owned_by_current_thread() is True

        def run_shutdown():
            shutdown_started.set()
            supervisor.shutdown()

        t = threading.Thread(target=run_shutdown)
        shutdown_thread_holder.append(t)
        t.start()

        assert shutdown_started.wait(timeout=5.0)
        while not supervisor._is_shutdown:
            time.sleep(0.001)

        return mock_proc_gen2

    with patch("subprocess.Popen", side_effect=racing_replacement_popen):
        with pytest.raises(MathSupervisorShutdownError) as exc_info:
            with supervisor._lock:
                # Trigger respawn: mark mock_proc_gen1 as stale
                gen1_state["reaped"] = True
                supervisor._ensure_process_locked()

    if shutdown_thread_holder:
        shutdown_thread_holder[0].join(timeout=5.0)

    assert "shut down during spawn" in exc_info.value.message
    # Both Gen 1 and Gen 2 must be reaped
    assert mock_proc_gen1.poll() is not None, "Gen 1 was not reaped"
    assert mock_proc_gen2.poll() is not None, "Gen 2 was not reaped"
    # Neither termination held lock in the terminating thread
    assert all(held is False for held in gen1_lock_states), "Lock held during Gen 1 termination"
    assert all(held is False for held in gen2_lock_states), "Lock held during Gen 2 termination"
    # Clean supervisor state
    assert supervisor._process is None
    assert supervisor.is_shutdown is True
    assert supervisor.is_alive is False


def test_shutdown_delayed_respawn_owner_does_not_corrupt_handles_or_hang():
    """Verify that if respawn owner does not settle before shutdown deadline, shutdown is bounded and handles are not corrupted."""
    supervisor = MathJaxProcessSupervisor()
    tracking_lock = OwnerTrackingLock(supervisor._lock)
    supervisor._lock = tracking_lock
    supervisor._state_cv = threading.Condition(tracking_lock)

    # Generation 1 mock process (stale)
    mock_proc_gen1 = MagicMock()
    mock_proc_gen1.pid = 1111
    stale_state = {"reaped": False}

    def poll_gen1():
        if stale_state["reaped"]:
            return -9
        return None

    mock_proc_gen1.poll = MagicMock(side_effect=poll_gen1)

    owner_reached_termination = threading.Event()
    owner_can_proceed = threading.Event()
    owner_finished = threading.Event()
    owner_exceptions = []

    orig_terminate = supervisor._terminate_process_outside_lock

    def delayed_terminate(proc, reason="CLEANUP"):
        if proc is mock_proc_gen1 and reason == "RESPAWN":
            # The owner thread released supervisor._lock before calling _terminate_process_outside_lock
            assert tracking_lock.is_owned_by_current_thread() is False
            owner_reached_termination.set()
            assert owner_can_proceed.wait(timeout=5.0), "Timeout waiting for owner_can_proceed"
            stale_state["reaped"] = True
            return
        return orig_terminate(proc, reason=reason)

    supervisor._terminate_process_outside_lock = MagicMock(side_effect=delayed_terminate)

    # Set up supervisor with an existing (stale) Gen 1 process
    supervisor._process = mock_proc_gen1
    supervisor._process_generation = 1
    supervisor._response_queue = queue.Queue()

    def owner_thread_worker():
        try:
            with supervisor._lock:
                stale_state["reaped"] = True
                supervisor._ensure_process_locked()
        except Exception as e:
            owner_exceptions.append(e)
        finally:
            owner_finished.set()

    owner_t = threading.Thread(target=owner_thread_worker)
    owner_t.start()

    # 1. Wait until owner has claimed respawn and reached out-of-lock termination
    assert owner_reached_termination.wait(timeout=5.0)

    # 2. Call shutdown with a tiny settle timeout (0.01s)
    # Shutdown enters with self._state_cv: (which succeeds because owner released lock!),
    # and waits on state_cv with timeout=0.01s.
    # Shutdown must complete within bounded time (< 1.0s) without deadlocking or hanging
    t0 = time.monotonic()
    supervisor.shutdown(settle_timeout=0.01)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"Shutdown took too long: {elapsed:.2f}s"
    assert supervisor.is_shutdown is True

    # 3. Because owner was still active at timeout, shutdown did NOT prematurely clear handles
    # Now unblock owner to finish
    owner_can_proceed.set()
    assert owner_finished.wait(timeout=5.0), "Owner did not finish"
    owner_t.join(timeout=5.0)

    # 4. Verify owner caught shutdown exception and cleaned up safely
    assert len(owner_exceptions) == 1
    assert isinstance(owner_exceptions[0], MathSupervisorShutdownError)

    # 5. In the end, process was reaped, supervisor state is cleanly None
    assert mock_proc_gen1.poll() is not None, "Gen 1 was not reaped"
    assert supervisor._process is None
    assert supervisor._respawning is False
    assert supervisor._respawn_owner_thread_id is None
    assert supervisor.is_alive is False
