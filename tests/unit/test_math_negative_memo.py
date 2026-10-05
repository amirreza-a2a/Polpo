"""Unit tests for NegativeFailureMemo (TICK-P07F / ADR-002 section D05).

Verifies LRU eviction, thread-safety under concurrency, session-scoped clear,
supported error types, and exclusion of MathSupervisorShutdownError.
"""

from __future__ import annotations

import threading
import pytest

from application.ports.math_renderer import (
    MathCircuitBreakerOpenError,
    MathRenderTimeoutError,
    MathSupervisorShutdownError,
    MathSyntaxError,
    MathWorkerCrashedError,
)
from infrastructure.math.negative_memo import NegativeFailureMemo


def test_negative_memo_initialization():
    """NegativeFailureMemo defaults to 1,000 capacity and validates positive integer."""
    memo = NegativeFailureMemo()
    assert memo.capacity == 1000
    assert len(memo) == 0

    custom = NegativeFailureMemo(capacity=50)
    assert custom.capacity == 50

    with pytest.raises(ValueError, match="Capacity must be a positive integer"):
        NegativeFailureMemo(capacity=0)

    with pytest.raises(ValueError, match="Capacity must be a positive integer"):
        NegativeFailureMemo(capacity=-10)


def test_negative_memo_put_and_get():
    """NegativeFailureMemo stores terminal errors and retrieves them by formula hash."""
    memo = NegativeFailureMemo(capacity=10)
    timeout_err = MathRenderTimeoutError(code=-32603, message="Timed out")
    crashed_err = MathWorkerCrashedError(code=-32603, message="Worker died")

    assert memo.get("nonexistent") is None
    assert "nonexistent" not in memo

    memo.put("hash_timeout", timeout_err)
    assert memo.get("hash_timeout") is timeout_err
    assert "hash_timeout" in memo
    assert len(memo) == 1

    memo.put("hash_crashed", crashed_err)
    assert memo.get("hash_crashed") is crashed_err
    assert len(memo) == 2

    # Updating existing key moves it to most recently used and updates value
    updated_err = MathRenderTimeoutError(code=-32603, message="Timed out again")
    memo.put("hash_timeout", updated_err)
    assert memo.get("hash_timeout") is updated_err
    assert len(memo) == 2


def test_negative_memo_lru_eviction():
    """NegativeFailureMemo bounds storage and evicts least recently used entries."""
    memo = NegativeFailureMemo(capacity=3)
    e1 = MathRenderTimeoutError(code=-32603, message="err 1")
    e2 = MathRenderTimeoutError(code=-32603, message="err 2")
    e3 = MathRenderTimeoutError(code=-32603, message="err 3")
    e4 = MathRenderTimeoutError(code=-32603, message="err 4")

    memo.put("k1", e1)
    memo.put("k2", e2)
    memo.put("k3", e3)
    assert len(memo) == 3

    # Access k1 so k2 becomes the least recently used
    assert memo.get("k1") is e1

    # Insert k4 -> should evict k2
    memo.put("k4", e4)
    assert len(memo) == 3
    assert memo.get("k2") is None
    assert "k2" not in memo
    assert memo.get("k1") is e1
    assert memo.get("k3") is e3
    assert memo.get("k4") is e4


def test_negative_memo_bounded_to_1000_entries():
    """NegativeFailureMemo strictly enforces 1,000 item capacity bound under volume."""
    memo = NegativeFailureMemo(capacity=1000)
    for i in range(1050):
        err = MathRenderTimeoutError(code=-32603, message=f"error {i}")
        memo.put(f"hash_{i}", err)

    assert len(memo) == 1000
    # First 50 should be evicted
    for i in range(50):
        assert memo.get(f"hash_{i}") is None
    # Last 1000 should be present
    for i in range(50, 1050):
        assert memo.get(f"hash_{i}") is not None


def test_negative_memo_clear_on_reload():
    """Session-scoped memo can be cleared on document reload or app restart."""
    memo = NegativeFailureMemo(capacity=10)
    memo.put("k1", MathRenderTimeoutError(code=-32603, message="err 1"))
    memo.put("k2", MathWorkerCrashedError(code=-32603, message="err 2"))
    assert len(memo) == 2

    memo.clear()
    assert len(memo) == 0
    assert memo.get("k1") is None
    assert memo.get("k2") is None
    assert "k1" not in memo


def test_negative_memo_shutdown_error_never_recorded():
    """MathSupervisorShutdownError and non-terminal errors are never stored in memo."""
    memo = NegativeFailureMemo(capacity=10)
    shutdown_err = MathSupervisorShutdownError(
        code=-32603, message="MathJaxProcessSupervisor has been shut down."
    )
    breaker_err = MathCircuitBreakerOpenError(
        code=-32603, message="MathJax circuit breaker is OPEN"
    )
    syntax_err = MathSyntaxError(code=-32602, message="Invalid LaTeX syntax")

    memo.put("shutdown_hash", shutdown_err)
    memo.put("breaker_hash", breaker_err)
    memo.put("syntax_hash", syntax_err)

    assert memo.get("shutdown_hash") is None
    assert memo.get("breaker_hash") is None
    assert memo.get("syntax_hash") is None
    assert "shutdown_hash" not in memo
    assert "breaker_hash" not in memo
    assert "syntax_hash" not in memo
    assert len(memo) == 0


def test_negative_memo_thread_safety_concurrent_access():
    """Concurrent reads, writes, and clears do not corrupt state or cause race conditions."""
    memo = NegativeFailureMemo(capacity=100)
    errors = [
        MathRenderTimeoutError(code=-32603, message=f"Timeout {i}")
        for i in range(200)
    ]
    barrier = threading.Barrier(8)

    def worker_write(start_idx: int) -> None:
        barrier.wait()
        for i in range(start_idx, start_idx + 50):
            memo.put(f"key_{i}", errors[i % len(errors)])

    def worker_read() -> None:
        barrier.wait()
        for i in range(200):
            _ = memo.get(f"key_{i}")
            _ = f"key_{i}" in memo

    threads = []
    for t_idx in range(4):
        t = threading.Thread(target=worker_write, args=(t_idx * 50,))
        threads.append(t)
    for _ in range(4):
        t = threading.Thread(target=worker_read)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(memo) <= 100
