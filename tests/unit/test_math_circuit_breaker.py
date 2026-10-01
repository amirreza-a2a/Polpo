"""Unit tests for pure thread-safe MathCircuitBreaker state machine (TICK-P07B)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import threading
from typing import List

import pytest

from application.ports.math_renderer import MathCircuitBreakerOpenError
import infrastructure.math.circuit_breaker as cb_module
from infrastructure.math.circuit_breaker import (
    INITIAL_COOLDOWN_SECONDS,
    MAX_COOLDOWN_SECONDS,
    MAX_RESTARTS_PER_MINUTE,
    RESTART_WINDOW_SECONDS,
    CircuitBreakerState,
    MathCircuitBreaker,
)


class SyntheticClock:
    """Deterministic synthetic clock provider for circuit breaker tests."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._current_time: float = initial_time

    def __call__(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        """Advance the synthetic clock by a given delta."""
        self._current_time += seconds

    @property
    def current_time(self) -> float:
        return self._current_time


# ==============================================================================
# 1. Initial State & Clean Startup (P10 Invariant)
# ==============================================================================

def test_circuit_breaker_initial_state():
    """Breaker initializes in CLOSED state with empty history and default cooldown."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS
    assert breaker.is_probe_in_flight is False


def test_clean_boot_does_not_record_failure():
    """Initial process startup does not invoke record_failure, leaving 0 failures."""
    clock = SyntheticClock(initial_time=100.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    with breaker.probe_permit():
        pass

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0


# ==============================================================================
# 2. CLOSED State & Sliding Window Pruning
# ==============================================================================

def test_single_failures_remain_closed():
    """1 and 2 failures leave the breaker in CLOSED state."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    breaker.record_failure()
    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 1

    clock.advance(10.0)
    breaker.record_failure()
    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 2


def test_sliding_window_expiration_does_not_trip():
    """Failures outside the 60s window expire; 3rd failure at t=70 does not trip."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    breaker.record_failure()  # t=0
    assert breaker.failure_count == 1

    clock.advance(20.0)
    breaker.record_failure()  # t=20
    assert breaker.failure_count == 2

    # Advance to t=70; t=0 failure has expired (> 60s ago)
    clock.advance(50.0)
    breaker.record_failure()  # t=70

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 2  # Only t=20 and t=70 retained


def test_alternating_failures_and_successes_trips_to_open():
    """record_success() in CLOSED is a strict no-op; 3 failures in 60s trip the breaker."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    breaker.record_failure()  # t=0, fail 1
    clock.advance(5.0)
    breaker.record_success()  # t=5, success (no-op)
    assert breaker.failure_count == 1

    clock.advance(5.0)
    breaker.record_failure()  # t=10, fail 2
    clock.advance(5.0)
    breaker.record_success()  # t=15, success (no-op)
    assert breaker.failure_count == 2

    clock.advance(5.0)
    breaker.record_failure()  # t=20, fail 3

    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS


# ==============================================================================
# 3. OPEN State & Cooldown Expiration
# ==============================================================================

def test_open_state_fast_fails_permit_acquisition():
    """Entering probe_permit while OPEN immediately raises MathCircuitBreakerOpenError."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    clock.advance(10.0)  # Cooldown is 30s, only 10s passed
    with pytest.raises(MathCircuitBreakerOpenError) as exc_info:
        with breaker.probe_permit():
            pytest.fail("Should not execute inside OPEN permit")

    assert exc_info.value.code == -32603
    assert "OPEN" in exc_info.value.message


def test_cooldown_expiry_transitions_open_to_half_open():
    """Advancing past cooldown_deadline transitions breaker from OPEN to HALF_OPEN."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    # Exactly at cooldown deadline (t=30.0s)
    clock.advance(INITIAL_COOLDOWN_SECONDS)
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    assert breaker.is_probe_in_flight is False


# ==============================================================================
# 4. HALF_OPEN State & Probe Permit Management
# ==============================================================================

def test_half_open_allows_single_probe_and_rejects_concurrent_probes():
    """HALF_OPEN permits exactly 1 probe; subsequent concurrent attempts raise MathCircuitBreakerOpenError."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    with breaker.probe_permit() as probe:
        assert breaker.is_probe_in_flight is True

        # Second attempt while probe is in flight
        with pytest.raises(MathCircuitBreakerOpenError) as exc_info:
            with breaker.probe_permit():
                pytest.fail("Second probe must be rejected")
        assert "HALF_OPEN" in exc_info.value.message
        assert "in flight" in exc_info.value.message


def test_successful_probe_closes_breaker_and_resets_history_and_cooldown():
    """Probe success transitions to CLOSED, clears failures, and resets cooldown to 30s."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)

    with breaker.probe_permit() as probe:
        probe.record_success()

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.failure_count == 0
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS
    assert breaker.is_probe_in_flight is False

    # Normal requests now execute in CLOSED
    with breaker.probe_permit():
        pass
    assert breaker.state == CircuitBreakerState.CLOSED


def test_failed_probe_reopens_breaker_and_doubles_cooldown_sequence():
    """Failed probes double cooldown: 30s -> 60s -> 120s -> 240s -> 240s."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    # 1. Trip breaker at t=0
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == 30.0

    # 2. Probe 1 at t=30 fails -> cooldown becomes 60s
    clock.advance(30.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    with breaker.probe_permit() as probe:
        probe.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == 60.0

    # 3. Probe 2 at t=30+60=90 fails -> cooldown becomes 120s
    clock.advance(60.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    with breaker.probe_permit() as probe:
        probe.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == 120.0

    # 4. Probe 3 at t=90+120=210 fails -> cooldown becomes 240s
    clock.advance(120.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    with breaker.probe_permit() as probe:
        probe.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == 240.0

    # 5. Probe 4 at t=210+240=450 fails -> cooldown capped at 240s
    clock.advance(240.0)
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    with breaker.probe_permit() as probe:
        probe.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.current_cooldown == MAX_COOLDOWN_SECONDS  # 240.0s


def test_probe_exits_without_outcome_returns_to_half_open_with_slot_free():
    """Exiting probe permit without outcome leaves breaker HALF_OPEN with slot free."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    # Context completes without recording success or failure
    with breaker.probe_permit():
        assert breaker.is_probe_in_flight is True

    # Exited without outcome
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    assert breaker.is_probe_in_flight is False
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS

    # Subsequent probe can now be claimed
    with breaker.probe_permit() as probe:
        assert breaker.is_probe_in_flight is True
        probe.record_success()
    assert breaker.state == CircuitBreakerState.CLOSED


def test_probe_raising_exception_releases_slot_and_preserves_half_open():
    """Exception inside probe context bubbles up, releases slot, and keeps HALF_OPEN."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)

    with pytest.raises(RuntimeError, match="Simulation error"):
        with breaker.probe_permit():
            raise RuntimeError("Simulation error")

    assert breaker.state == CircuitBreakerState.HALF_OPEN
    assert breaker.is_probe_in_flight is False
    assert breaker.current_cooldown == INITIAL_COOLDOWN_SECONDS


def test_probe_outcome_called_on_breaker_directly_works():
    """Calling record_success / record_failure directly on breaker also resolves probe."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)

    with breaker.probe_permit():
        breaker.record_success()

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.is_probe_in_flight is False


def test_probe_repeated_outcome_calls_do_not_corrupt_state():
    """Calling record_success multiple times does not corrupt state."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)

    with breaker.probe_permit() as probe:
        probe.record_success()
        probe.record_success()  # Duplicate call

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.is_probe_in_flight is False
    assert breaker.failure_count == 0


# ==============================================================================
# 5. Multi-Threaded Concurrency (20-Thread Race Test)
# ==============================================================================

def test_multithreaded_twenty_thread_half_open_probe_race():
    """20 threads race to claim a HALF_OPEN probe: exactly 1 succeeds, 19 fail fast."""
    clock = SyntheticClock(initial_time=0.0)
    breaker = MathCircuitBreaker(time_provider=clock)

    # Trip breaker and advance to HALF_OPEN
    for _ in range(3):
        breaker.record_failure()
    clock.advance(INITIAL_COOLDOWN_SECONDS)
    assert breaker.state == CircuitBreakerState.HALF_OPEN

    num_threads = 20
    barrier = threading.Barrier(num_threads)
    probe_acquired = threading.Event()
    probe_release = threading.Event()
    all_failed = threading.Event()

    successes: List[int] = []
    failures: List[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()  # Deterministic synchronized start
        try:
            with breaker.probe_permit() as probe:
                with lock:
                    successes.append(1)
                probe_acquired.set()
                probe_release.wait(timeout=5.0)
                probe.record_success()
        except MathCircuitBreakerOpenError:
            with lock:
                failures.append(1)
                if len(failures) == num_threads - 1:
                    all_failed.set()

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()

    # Wait until the 1 lucky thread has acquired the probe
    assert probe_acquired.wait(timeout=5.0), "Probe was not acquired in time"

    # Wait until all other 19 concurrent threads have failed fast while probe is in flight
    assert all_failed.wait(timeout=5.0), "Not all concurrent threads failed fast in time"

    # Allow the probe to complete
    probe_release.set()

    for t in threads:
        t.join(timeout=5.0)

    assert len(successes) == 1, f"Expected exactly 1 probe success, got {len(successes)}"
    assert len(failures) == 19, f"Expected exactly 19 fast-fails, got {len(failures)}"
    assert breaker.state == CircuitBreakerState.CLOSED


# ==============================================================================
# 6. Architecture & Port Isolation Checks
# ==============================================================================

def test_circuit_breaker_has_no_qt_or_subprocess_or_supervisor_imports():
    """Architecture invariant: circuit_breaker.py must have zero Qt/subprocess/supervisor imports."""
    source_path = Path(inspect.getfile(cb_module))
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    disallowed_prefixes = (
        "PySide6",
        "PyQt5",
        "PyQt6",
        "subprocess",
        "infrastructure.math.mathjax_supervisor",
        "infrastructure.math.mathjax_client",
        "infrastructure.math.lru_cache",
        "interfaces",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in disallowed_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"Forbidden import '{alias.name}' in {source_path}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for prefix in disallowed_prefixes:
                assert not module_name.startswith(prefix), (
                    f"Forbidden from-import '{module_name}' in {source_path}"
                )
