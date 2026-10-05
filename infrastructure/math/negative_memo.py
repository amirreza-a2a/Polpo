"""In-memory thread-safe LRU memo for terminal MathJax formula render failures (TICK-P07F).

Provides O(1) retrieval for failed formula hashes, fast-failing repeated renders
of pathological formulas to prevent worker restart budget exhaustion during live typing.
"""

from __future__ import annotations

from collections import OrderedDict
import threading
from typing import Optional

from application.ports.math_renderer import (
    MathRenderError,
    MathRenderTimeoutError,
    MathWorkerCrashedError,
)


class NegativeFailureMemo:
    """Thread-safe in-memory Least Recently Used (LRU) memo for terminal math failures."""

    # Known limitation: A worker killed for reasons unrelated to the in-flight
    # formula leaves that formula in the negative memo for the session; a TTL /
    # expiration policy is deferred to P08.

    def __init__(self, capacity: int = 1000) -> None:
        """Initialize the LRU negative memo with a bounded capacity.

        Args:
            capacity: Maximum number of entries retained before evicting the
                      oldest entry. Default is 1,000 items.
        """
        if capacity <= 0:
            raise ValueError(f"Capacity must be a positive integer, got {capacity}")
        self._capacity: int = capacity
        self._memo: OrderedDict[str, MathRenderError] = OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Maximum number of entries allowed in the memo."""
        return self._capacity

    def get(self, key: str) -> Optional[MathRenderError]:
        """Retrieve a cached terminal MathRenderError by formula hash.

        Marks the entry as most recently used on hit.

        Args:
            key: Deterministic SHA-256 hash of the formula request.

        Returns:
            MathRenderError if present, None otherwise.
        """
        with self._lock:
            if key not in self._memo:
                return None
            self._memo.move_to_end(key)
            return self._memo[key]

    def put(self, key: str, error: MathRenderError) -> None:
        """Store a terminal MathRenderError in the memo, evicting the oldest if at capacity.

        Per ADR-002 section D05 and D07, only terminal formula rendering failures
        (MathRenderTimeoutError, MathWorkerCrashedError) are recorded in the negative
        memo. Transient errors (MathSupervisorShutdownError, MathCircuitBreakerOpenError)
        and non-terminal errors are never stored.

        Args:
            key: Deterministic SHA-256 hash of the formula request.
            error: Terminal render failure error instance.
        """
        if not isinstance(error, (MathRenderTimeoutError, MathWorkerCrashedError)):
            return

        with self._lock:
            if key in self._memo:
                self._memo[key] = error
                self._memo.move_to_end(key)
                return

            if len(self._memo) >= self._capacity:
                self._memo.popitem(last=False)

            self._memo[key] = error

    def clear(self) -> None:
        """Remove all entries from the memo (e.g. on document reload or session restart)."""
        with self._lock:
            self._memo.clear()

    def __len__(self) -> int:
        """Return the current number of memoized failure entries."""
        with self._lock:
            return len(self._memo)

    def __contains__(self, key: str) -> bool:
        """Check if a formula hash exists in the memo without updating LRU position."""
        with self._lock:
            return key in self._memo
