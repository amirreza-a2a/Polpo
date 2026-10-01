"""Pure thread-safe MathCircuitBreaker state machine for process supervision.

Safeguards the PolpoT application against crash-looping MathJax worker processes
by governing process restarts with a 3-state circuit breaker (CLOSED, OPEN, HALF_OPEN)
and exponential cooldown backoff.
"""

from __future__ import annotations

from enum import Enum
import threading
import time
from typing import Callable, List, Optional

from application.ports.math_renderer import MathCircuitBreakerOpenError

# Explicit timing and threshold constants per ADR-002 section D05
RESTART_WINDOW_SECONDS: float = 60.0
MAX_RESTARTS_PER_MINUTE: int = 3
INITIAL_COOLDOWN_SECONDS: float = 30.0
MAX_COOLDOWN_SECONDS: float = 240.0


class CircuitBreakerState(Enum):
    """Operational states for the MathJax process supervisor circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ProbePermit:
    """Context-manager permit handle yielded by MathCircuitBreaker.probe_permit().

    Allows callers to record the outcome of a request or probe execution, and
    guarantees that probe slots are safely released if execution exits without
    recording an explicit outcome.
    """

    def __init__(self, breaker: MathCircuitBreaker) -> None:
        self._breaker = breaker
        self._is_probe: bool = False
        self._entered: bool = False
        self._outcome_recorded: bool = False

    def __enter__(self) -> ProbePermit:
        self._breaker._acquire_permit(self)
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        if self._entered:
            self._breaker._release_permit(
                is_probe=self._is_probe,
                outcome_recorded=self._outcome_recorded,
            )
        return False

    def record_success(self) -> None:
        """Record successful execution of the permitted operation."""
        if not self._outcome_recorded:
            self._outcome_recorded = True
            self._breaker.record_success(from_probe=self._is_probe)

    def record_failure(self) -> None:
        """Record unrecoverable failure of the permitted operation."""
        if not self._outcome_recorded:
            self._outcome_recorded = True
            self._breaker.record_failure(from_probe=self._is_probe)


class MathCircuitBreaker:
    """Thread-safe circuit breaker governing worker process restarts.

    Implements a 3-state machine (CLOSED, OPEN, HALF_OPEN) with a rolling restart
    window, exponential cooldown backoff, and atomic probe reservation.
    """

    def __init__(
        self,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the circuit breaker in CLOSED state.

        Args:
            time_provider: Injectable monotonic clock function returning seconds.
        """
        self._time_provider: Callable[[], float] = time_provider
        self._lock: threading.Lock = threading.Lock()
        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._restart_timestamps: List[float] = []
        self._current_cooldown: float = INITIAL_COOLDOWN_SECONDS
        self._cooldown_deadline: Optional[float] = None
        self._probe_in_flight: bool = False

    def _evaluate_state_locked(self, current_time: Optional[float] = None) -> None:
        """Lazily transitions OPEN to HALF_OPEN when cooldown deadline expires."""
        if self._state == CircuitBreakerState.OPEN:
            now = self._time_provider() if current_time is None else current_time
            if self._cooldown_deadline is not None and now >= self._cooldown_deadline:
                self._state = CircuitBreakerState.HALF_OPEN
                self._cooldown_deadline = None
                self._probe_in_flight = False

    @property
    def state(self) -> CircuitBreakerState:
        """Current operational state of the circuit breaker."""
        with self._lock:
            self._evaluate_state_locked()
            return self._state

    @property
    def current_cooldown(self) -> float:
        """Active cooldown duration in seconds."""
        with self._lock:
            return self._current_cooldown

    @property
    def is_probe_in_flight(self) -> bool:
        """Whether a probe request is currently executing in HALF_OPEN state."""
        with self._lock:
            self._evaluate_state_locked()
            return self._probe_in_flight

    @property
    def failure_count(self) -> int:
        """Number of failures recorded within the rolling 60-second window."""
        with self._lock:
            self._evaluate_state_locked()
            now = self._time_provider()
            cutoff = now - RESTART_WINDOW_SECONDS
            return sum(1 for t in self._restart_timestamps if t >= cutoff)

    def probe_permit(self) -> ProbePermit:
        """Create a permit context manager for executing a request or probe.

        Returns:
            ProbePermit context manager handle.
        """
        return ProbePermit(self)

    def _acquire_permit(self, permit: ProbePermit) -> None:
        """Atomically validates state and claims execution or probe permit.

        Raises:
            MathCircuitBreakerOpenError: If breaker is OPEN, or HALF_OPEN with
                a probe already in flight.
        """
        with self._lock:
            self._evaluate_state_locked()

            if self._state == CircuitBreakerState.OPEN:
                deadline_str = (
                    f"{self._cooldown_deadline:.1f}"
                    if self._cooldown_deadline is not None
                    else "unknown"
                )
                raise MathCircuitBreakerOpenError(
                    code=-32603,
                    message=(
                        f"MathJax circuit breaker is OPEN (cooldown deadline: {deadline_str}s)"
                    ),
                )

            if self._state == CircuitBreakerState.HALF_OPEN:
                if self._probe_in_flight:
                    raise MathCircuitBreakerOpenError(
                        code=-32603,
                        message=(
                            "MathJax circuit breaker is HALF_OPEN and a probe request "
                            "is already in flight"
                        ),
                    )
                self._probe_in_flight = True
                permit._is_probe = True
            else:
                permit._is_probe = False

    def _release_permit(self, is_probe: bool, outcome_recorded: bool) -> None:
        """Releases the probe slot when the permit context block exits."""
        with self._lock:
            if is_probe and not outcome_recorded:
                # If the probe exited without an outcome, release the slot and remain HALF_OPEN
                if self._probe_in_flight:
                    self._probe_in_flight = False
                    self._state = CircuitBreakerState.HALF_OPEN

    def record_failure(
        self,
        now: Optional[float] = None,
        from_probe: bool = False,
    ) -> None:
        """Record an unrecoverable failure event.

        In CLOSED:
            Appends failure timestamp and prunes entries older than 60s.
            If rolling failure count >= 3, trips to OPEN with INITIAL_COOLDOWN_SECONDS (30s).
        In HALF_OPEN:
            Fails the probe, transitions back to OPEN, appends failure timestamp,
            doubles cooldown up to MAX_COOLDOWN_SECONDS (240s), and computes new deadline.
        In OPEN:
            Already OPEN; active cooldown deadline is preserved.
        """
        with self._lock:
            current_time = self._time_provider() if now is None else now
            self._evaluate_state_locked(current_time)

            if self._state == CircuitBreakerState.HALF_OPEN or (from_probe and self._probe_in_flight):
                self._state = CircuitBreakerState.OPEN
                self._probe_in_flight = False
                self._restart_timestamps.append(current_time)
                self._current_cooldown = min(self._current_cooldown * 2.0, MAX_COOLDOWN_SECONDS)
                self._cooldown_deadline = current_time + self._current_cooldown
            elif self._state == CircuitBreakerState.CLOSED:
                cutoff = current_time - RESTART_WINDOW_SECONDS
                self._restart_timestamps = [t for t in self._restart_timestamps if t >= cutoff]
                self._restart_timestamps.append(current_time)
                if len(self._restart_timestamps) >= MAX_RESTARTS_PER_MINUTE:
                    self._state = CircuitBreakerState.OPEN
                    self._current_cooldown = INITIAL_COOLDOWN_SECONDS
                    self._cooldown_deadline = current_time + self._current_cooldown
            elif self._state == CircuitBreakerState.OPEN:
                pass

    def record_success(self, from_probe: bool = False) -> None:
        """Record a successful operation.

        In HALF_OPEN:
            Probe succeeded; transitions to CLOSED, clears failure history,
            resets cooldown to INITIAL_COOLDOWN_SECONDS (30s), and clears deadline.
        In CLOSED:
            Strict NO-OP to preserve rolling failure window against alternating patterns.
        In OPEN:
            NO-OP.
        """
        with self._lock:
            self._evaluate_state_locked()

            if self._state == CircuitBreakerState.HALF_OPEN or (from_probe and self._probe_in_flight):
                self._state = CircuitBreakerState.CLOSED
                self._restart_timestamps.clear()
                self._current_cooldown = INITIAL_COOLDOWN_SECONDS
                self._cooldown_deadline = None
                self._probe_in_flight = False
            elif self._state == CircuitBreakerState.CLOSED:
                # Per ADR-002 D05, record_success() in CLOSED is a strict no-op
                pass

    def reset(self) -> None:
        """Reset the breaker to initial CLOSED state with clear history."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._restart_timestamps.clear()
            self._current_cooldown = INITIAL_COOLDOWN_SECONDS
            self._cooldown_deadline = None
            self._probe_in_flight = False
