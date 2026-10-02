"""Unit tests for MathJaxProcessSupervisor RPC core, reader thread, and timeouts (TICK-P07D).

Verifies:
1. Per-process companion daemon reader thread and unbounded queue (ADR-002 D04).
2. Monotonically increasing generation token isolating stale responses from killed workers.
3. JSON-RPC correlation ID verification and dropping of mismatched responses.
4. Synchronous cold-start health handshake ("ping" -> "pong") with timeout escalation (D02).
5. In-flight request timeout with two-phase termination escalation (D01, D03, D04).
6. Fast crash wake-up via _EofSentinel raising MathWorkerCrashedError immediately.
7. _AbortSentinel raising MathSupervisorShutdownError immediately.
8. Zero in-flight retries on failure (D05).
9. Architecture invariant: zero Qt imports in supervisor.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from application.ports.math_renderer import (
    MathRenderError,
    MathRenderTimeoutError,
    MathSupervisorShutdownError,
    MathWorkerCrashedError,
    MathWorkerStartupError,
)
from infrastructure.math.mathjax_supervisor import (
    MathJaxProcessSupervisor,
    _AbortSentinel,
    _EofSentinel,
    _Sentinel,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOOD_STDERR_WORKER = FIXTURES_DIR / "flood_stderr_worker.py"
HUNG_WORKER_STARTUP = FIXTURES_DIR / "hung_worker_startup.py"
HUNG_WORKER_REQUEST = FIXTURES_DIR / "hung_worker_request.py"
CRASH_WORKER_MID_REQUEST = FIXTURES_DIR / "crash_worker_mid_request.py"
LATE_WORKER_RESPONSE = FIXTURES_DIR / "late_worker_response.py"


# ==============================================================================
# 1. Startup Handshake Tests (ADR-002 D02)
# ==============================================================================

def test_startup_handshake_success():
    """Supervisor completes synchronous ping/pong handshake on startup and transitions to alive."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
        startup_timeout_seconds=5.0,
    )
    try:
        assert supervisor.is_alive is True
        assert supervisor._process_generation == 1
        assert supervisor._stdout_reader_thread is not None
        assert supervisor._stdout_reader_thread.is_alive()
        assert supervisor._stdout_reader_thread.daemon is True
        assert supervisor.ping() is True
    finally:
        supervisor.shutdown()

    assert supervisor.is_alive is False
    assert supervisor._process is None


def test_startup_handshake_timeout_escalates_and_raises(caplog: pytest.LogCaptureFixture):
    """Worker that hangs on startup times out after startup_timeout_seconds and escalates termination."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=HUNG_WORKER_STARTUP,
        startup_timeout_seconds=0.3,
    )

    start_time = time.monotonic()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(MathWorkerStartupError) as exc_info:
            supervisor.ping()
    elapsed = time.monotonic() - start_time

    assert "startup handshake timed out" in exc_info.value.message
    # Verify bounded execution (timeout 0.3s + 0.5s terminate wait + generous CI cushion)
    assert elapsed < 4.0
    assert supervisor.is_alive is False
    assert supervisor._process is None

    # Verify termination escalation logged
    term_logs = [
        rec.message for rec in caplog.records
        if "Terminating MathJax worker process" in rec.message and "STARTUP_TIMEOUT" in rec.message
    ]
    assert len(term_logs) >= 1

    supervisor.shutdown()


def test_startup_handshake_premature_crash_raises_startup_error():
    """Worker that exits prematurely during startup handshake raises MathWorkerStartupError."""
    # Using an empty script or a script that exits immediately
    exit_immediately_script = Path(__file__).resolve().parent.parent / "fixtures" / "sigterm_ignoring_worker.py"

    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=exit_immediately_script,
        startup_timeout_seconds=5.0,
    )

    # Mock the reader queue to immediately deliver _EofSentinel during handshake
    q: queue.Queue = queue.Queue()
    q.put((1, _EofSentinel()))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    mock_proc.stdin = MagicMock()
    supervisor._process = mock_proc

    with patch.object(supervisor, "_cleanup_process_handles_locked") as mock_cleanup:
        with pytest.raises(MathWorkerStartupError) as exc_info:
            supervisor._perform_startup_handshake_locked(expected_gen=1, q=q)
        assert "exited unexpectedly during startup handshake" in exc_info.value.message
        mock_cleanup.assert_called_once_with(reason="STARTUP_CRASH")


# ==============================================================================
# 2. In-Flight Request Timeout Tests (ADR-002 D01, D04)
# ==============================================================================

def test_hung_request_times_out_and_escalates_termination(caplog: pytest.LogCaptureFixture):
    """Hung worker on RPC request times out deterministically, reaps process, and raises MathRenderTimeoutError."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=HUNG_WORKER_REQUEST,
        request_timeout_seconds=0.3,
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    start_time = time.monotonic()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(MathRenderTimeoutError) as exc_info:
            supervisor.render("x + y = z")
    elapsed = time.monotonic() - start_time

    assert "timed out after 0.3s" in exc_info.value.message
    # Request timeout ~0.3s + 0.5s terminate wait < 4.0s generous bound
    assert elapsed < 4.0
    assert supervisor.is_alive is False
    assert supervisor._process is None

    # Verify termination escalation logged with reason REQUEST_TIMEOUT
    term_logs = [
        rec.message for rec in caplog.records
        if "Terminating MathJax worker process" in rec.message and "REQUEST_TIMEOUT" in rec.message
    ]
    assert len(term_logs) == 1

    supervisor.shutdown()


def test_zero_in_flight_retries_on_timeout():
    """A timed-out request fails fast immediately with zero transparent in-flight retries."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=HUNG_WORKER_REQUEST,
        request_timeout_seconds=0.2,
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    initial_gen = supervisor._process_generation

    with pytest.raises(MathRenderTimeoutError):
        supervisor.render("x^2")

    # Worker was terminated; no auto-respawn occurred within the failed render call
    assert supervisor._process is None
    assert supervisor._process_generation == initial_gen

    supervisor.shutdown()


# ==============================================================================
# 3. Crash Mid-Request Fast Wake-Up Tests (ADR-002 D04)
# ==============================================================================

def test_crash_mid_request_fast_wake_up_raises_math_worker_crashed_error():
    """Worker crashing mid-request emits _EofSentinel and immediately raises MathWorkerCrashedError."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=CRASH_WORKER_MID_REQUEST,
        request_timeout_seconds=10.0,  # Long timeout to prove wake-up does NOT wait for timeout
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    start_time = time.monotonic()
    with pytest.raises(MathWorkerCrashedError) as exc_info:
        supervisor.render("x = y")
    elapsed = time.monotonic() - start_time

    # Must wake up almost immediately (well under the 10.0s request timeout)
    assert elapsed < 3.0
    assert "exited unexpectedly" in exc_info.value.message
    assert supervisor.is_alive is False
    assert supervisor._process is None

    supervisor.shutdown()


# ==============================================================================
# 4. Generation Token & Stale Response Isolation (ADR-002 D04)
# ==============================================================================

def test_stale_response_from_earlier_generation_is_discarded(caplog: pytest.LogCaptureFixture):
    """Responses tagged with an obsolete generation token are logged and discarded."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 2  # Current generation is 2

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    # Enqueue a stale response from generation 1, followed by a valid response for generation 2
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 1, "result": {"svg": "<svg>stale-gen1</svg>"}}\n',
    ))
    q.put((
        2,
        '{"jsonrpc": "2.0", "id": 1, "result": {"svg": "<svg>fresh-gen2</svg>"}}\n',
    ))

    with caplog.at_level(logging.WARNING):
        with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
            res = supervisor._call_rpc_locked("render", {"tex": "a"})

    assert res == {"svg": "<svg>fresh-gen2</svg>"}

    # Verify stale generation warning was logged
    stale_logs = [
        rec.message for rec in caplog.records
        if "Discarding response from stale generation 1 (expected 2)" in rec.message
    ]
    assert len(stale_logs) == 1


def test_late_worker_response_times_out_and_reaps_generation(caplog: pytest.LogCaptureFixture):
    """Worker responding after request_timeout_seconds triggers timeout, reaping generation."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=LATE_WORKER_RESPONSE,
        request_timeout_seconds=0.2,
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    with caplog.at_level(logging.WARNING):
        with pytest.raises(MathRenderTimeoutError) as exc_info:
            supervisor.render("x + 1")

    assert "timed out after 0.2s" in exc_info.value.message
    assert supervisor.is_alive is False
    assert supervisor._process is None

    supervisor.shutdown()


def test_generation_isolation_with_real_late_worker_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Real subprocess lifecycle test: late response from generation 1 cannot satisfy generation 2.

    Demonstrates:
    Generation 1
        ↓
    send request
        ↓
    worker intentionally responds late (sleeps 0.35s > 0.2s timeout)
        ↓
    request timeout triggers MathRenderTimeoutError
        ↓
    P07C cleanup initiates termination escalation and reaps generation 1
        ↓
    Generation 2 is created (queue_2, reader_2, process_generation=2)
        ↓
    send request on generation 2 -> returns prompt Generation 2 response
        ↓
    stale response from generation 1 remains in q_gen1 and is observed after Generation 2 is active
        ↓
    late response from generation 1 must not satisfy request on generation 2 or enter q_gen2.
    """
    marker_file = tmp_path / "gen1_marker.txt"
    monkeypatch.setenv("POLPO_LATE_WORKER_MARKER_FILE", str(marker_file))
    monkeypatch.setenv("POLPO_TEST_LATE_WORKER_DELAY", "0.35")

    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=LATE_WORKER_RESPONSE,
        request_timeout_seconds=0.2,
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    assert supervisor.is_alive is True
    assert supervisor._process_generation == 1
    q_gen1 = supervisor._response_queue
    proc_gen1 = supervisor._process
    reader_gen1 = supervisor._stdout_reader_thread
    assert q_gen1 is not None
    assert proc_gen1 is not None
    assert reader_gen1 is not None

    # On Windows, TerminateProcess is non-catchable; prevent immediate termination
    # during Phase 1 cleanup so the real worker subprocess completes its delay
    # and emits its late response to stdout before exiting cleanly.
    if sys.platform == "win32":
        monkeypatch.setattr(proc_gen1, "terminate", lambda: None)

    # Step 1: Request on Generation 1 times out because worker sleeps 0.35s > 0.2s
    with caplog.at_level(logging.WARNING):
        with pytest.raises(MathRenderTimeoutError) as exc_info:
            supervisor.render("formula_1")

    assert "timed out after 0.2s" in exc_info.value.message
    # Generation 1 is cleaned up and reaped
    assert supervisor.is_alive is False
    assert supervisor._process is None

    # Step 2: Immediately start Generation 2 and verify its properties and response
    result_gen2 = supervisor.render("formula_2")

    # Assert Generation 2 properties
    assert supervisor.is_alive is True
    assert supervisor._process_generation == 2
    q_gen2 = supervisor._response_queue
    proc_gen2 = supervisor._process
    reader_gen2 = supervisor._stdout_reader_thread
    assert q_gen2 is not None
    assert q_gen2 is not q_gen1, "Generation 2 must allocate its own dedicated response queue"
    assert proc_gen2 is not proc_gen1, "Generation 2 must be a distinct process instance"
    assert reader_gen2 is not reader_gen1, "Generation 2 must have its own reader thread"

    # Verify the result was produced by Generation 2's request (ID 4), NOT Generation 1 (ID 2)
    assert "response-id-4" in result_gen2["svg_xml"]
    assert "response-id-2" not in result_gen2["svg_xml"]

    # Step 3: ONLY AFTER Generation 2 exists and is active, inspect q_gen1 for the stale response
    observed_gen1_response = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            item_gen, item_payload = q_gen1.get(timeout=0.1)
            if item_gen == 1 and isinstance(item_payload, str) and "response-id-2" in item_payload:
                observed_gen1_response = item_payload
                break
        except queue.Empty:
            continue

    assert observed_gen1_response is not None, (
        "Generation 1 delayed response with 'response-id-2' was never observed in q_gen1 after Generation 2 started"
    )

    # Step 4: Verify queue isolation — q_gen2 contains only generation-2 items, never generation-1
    while not q_gen2.empty():
        item_gen, item_payload = q_gen2.get_nowait()
        assert item_gen == 2, f"Item from generation {item_gen} leaked into generation 2 queue!"
        if isinstance(item_payload, str):
            assert "response-id-2" not in item_payload, (
                "Generation 1 response leaked into generation 2 queue!"
            )

    supervisor.shutdown()


def test_stale_reader_and_eof_sentinel_cannot_corrupt_new_generation():
    """Old generation reader and its _EofSentinel never pollute or terminate the new generation."""
    supervisor = MathJaxProcessSupervisor()

    # Simulate Generation 1
    q1: queue.Queue = queue.Queue()
    supervisor._response_queue = q1
    supervisor._process_generation = 1

    # Simulate Generation 2 starting
    q2: queue.Queue = queue.Queue()
    supervisor._response_queue = q2
    supervisor._process_generation = 2

    # Old reader for generation 1 pushes late response and EOF sentinel into q1
    q1.put((1, '{"jsonrpc": "2.0", "id": 1, "result": "stale-gen1"}\n'))
    q1.put((1, _EofSentinel()))

    # Generation 2's queue q2 receives its own response
    q2.put((2, '{"jsonrpc": "2.0", "id": 1, "result": "fresh-gen2"}\n'))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        result = supervisor._call_rpc_locked("render", {"tex": "x"})

    assert result == "fresh-gen2"
    assert q1.qsize() == 2


def test_stale_eof_sentinel_in_current_queue_is_discarded(caplog: pytest.LogCaptureFixture):
    """Stale _EofSentinel from an earlier generation is safely discarded and does not crash."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 2

    # Stale EOF sentinel from generation 1 in queue, followed by valid generation 2 response
    q.put((1, _EofSentinel()))
    q.put((2, '{"jsonrpc": "2.0", "id": 1, "result": "gen2-ok"}\n'))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    with caplog.at_level(logging.WARNING):
        with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
            result = supervisor._call_rpc_locked("render", {"tex": "x"})

    assert result == "gen2-ok"
    stale_logs = [
        rec.message for rec in caplog.records
        if "Discarding response from stale generation 1 (expected 2)" in rec.message
    ]
    assert len(stale_logs) == 1


# ==============================================================================
# 5. JSON-RPC Correlation ID Verification (ADR-002 D04)
# ==============================================================================

def test_mismatched_response_id_is_dropped_and_matching_response_accepted(caplog: pytest.LogCaptureFixture):
    """Mismatched JSON-RPC response IDs are dropped with a warning, and matching responses accepted."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 1
    supervisor._next_id = 5

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    # Enqueue response with ID 999 (mismatched, expected 5), then response with ID 5
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 999, "result": {"svg": "<svg>unrelated</svg>"}}\n',
    ))
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 5, "result": {"svg": "<svg>matched</svg>"}}\n',
    ))

    with caplog.at_level(logging.WARNING):
        with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
            res = supervisor._call_rpc_locked("render", {"tex": "b"})

    assert res == {"svg": "<svg>matched</svg>"}

    mismatch_logs = [
        rec.message for rec in caplog.records
        if "Dropped mismatched JSON-RPC response ID (expected 5, got 999)" in rec.message
    ]
    assert len(mismatch_logs) == 1


# ==============================================================================
# 6. Sentinel & Shutdown Wake-Up Tests (ADR-002 D04, D06)
# ==============================================================================

def test_dequeuing_abort_sentinel_raises_math_supervisor_shutdown_error():
    """Dequeuing _AbortSentinel raises MathSupervisorShutdownError immediately without timeout."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 1

    # Put AbortSentinel into queue
    q.put((1, _AbortSentinel()))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathSupervisorShutdownError) as exc_info:
            supervisor._call_rpc_locked("ping")

    assert "shut down" in exc_info.value.message


def test_shutdown_unblocks_in_flight_rpc_immediately():
    """Shutdown unblocks in-flight RPC immediately with MathSupervisorShutdownError without waiting out timeout."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=HUNG_WORKER_REQUEST,
        request_timeout_seconds=5.0,
        startup_timeout_seconds=5.0,
        auto_start=True,
    )
    assert supervisor.is_alive is True

    caught_exception: list[MathSupervisorShutdownError] = []
    rpc_start_time: float = 0.0
    rpc_end_time: float = 0.0

    def call_render() -> None:
        nonlocal rpc_start_time, rpc_end_time
        rpc_start_time = time.monotonic()
        try:
            supervisor.render("x + y")
        except MathSupervisorShutdownError as exc:
            caught_exception.append(exc)
        finally:
            rpc_end_time = time.monotonic()

    t = threading.Thread(target=call_render, daemon=True)
    t.start()

    # Wait briefly for render() to acquire lock, send request, and block in q.get()
    time.sleep(0.2)
    assert t.is_alive()

    shutdown_start = time.monotonic()
    supervisor.shutdown()
    shutdown_duration = time.monotonic() - shutdown_start

    t.join(timeout=2.0)
    assert not t.is_alive(), "In-flight RPC thread must unblock and terminate immediately upon shutdown"

    assert len(caught_exception) == 1
    assert "shut down" in caught_exception[0].message
    total_rpc_time = rpc_end_time - rpc_start_time
    # Must unblock well before the 5.0s request timeout (typically < 1.0s)
    assert total_rpc_time < 3.0
    assert shutdown_duration < 2.0
    assert supervisor._is_shutdown is True
    assert supervisor._process is None


def test_reader_thread_joined_on_shutdown():
    """Stdout reader daemon thread terminates and is joined within 0.2s on supervisor shutdown."""
    supervisor = MathJaxProcessSupervisor(
        node_path=sys.executable,
        worker_script_path=FLOOD_STDERR_WORKER,
        auto_start=True,
    )
    assert supervisor.is_alive is True
    reader_thread = supervisor._stdout_reader_thread
    assert reader_thread is not None
    assert reader_thread.is_alive() is True

    start_time = time.monotonic()
    supervisor.shutdown()
    elapsed = time.monotonic() - start_time

    assert reader_thread.is_alive() is False
    assert elapsed < 1.0


def test_cleanup_bounded_reader_thread_join():
    """Cleanup joins stdout reader thread with a bounded 0.2s timeout."""
    supervisor = MathJaxProcessSupervisor()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    supervisor._process = mock_proc

    mock_reader = MagicMock()
    mock_reader.is_alive.return_value = True
    supervisor._stdout_reader_thread = mock_reader

    with supervisor._lock:
        supervisor._cleanup_process_handles_locked(reason="TEST")

    mock_reader.join.assert_called_once_with(timeout=0.2)


# ==============================================================================
# 7. Architecture Invariant: No Qt Imports
# ==============================================================================

def test_supervisor_has_no_qt_imports():
    """Clean Architecture invariant: MathJax supervisor must not import Qt/PySide6."""
    supervisor_path = Path(__file__).resolve().parent.parent.parent / "infrastructure" / "math" / "mathjax_supervisor.py"
    tree = ast.parse(supervisor_path.read_text(encoding="utf-8"))

    qt_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(x in alias.name.lower() for x in ("qt", "pyside", "pyqt")):
                    qt_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(x in node.module.lower() for x in ("qt", "pyside", "pyqt")):
                qt_imports.append(node.module)

    assert not qt_imports, f"Found prohibited Qt imports in mathjax_supervisor.py: {qt_imports}"
