"""Unit tests for MathJaxProcessSupervisor and MathJaxClient (TICK-009B).

Verifies process lifecycle, JSON-RPC communication, crash detection and recovery,
restart rate-limiting, buffer bounds, LRU cache integration, and batch rendering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Generator, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from application.ports.math_renderer import (
    MathBufferLimitExceededError,
    MathCircuitBreakerOpenError,
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
    MathRenderTimeoutError,
    MathSupervisorShutdownError,
    MathSyntaxError,
    MathWorkerCrashedError,
)
from infrastructure.math import (
    MathJaxClient,
    MathJaxProcessSupervisor,
    MathSvgCache,
)
from infrastructure.math.circuit_breaker import (
    CircuitBreakerState,
    MathCircuitBreaker,
)
from infrastructure.math.negative_memo import NegativeFailureMemo
from infrastructure.paths import get_runtime_resource_path

EXPECTED_NODE_VERSION = "22.23.2"


# ==============================================================================
# Helpers & Fixtures for Live Runtime Execution
# ==============================================================================

def _resolve_test_node_binary() -> Tuple[Optional[Path], Optional[str]]:
    env_node = os.environ.get("POLPO_NODE_PATH")
    candidate: Optional[Path] = None
    if env_node:
        p = Path(env_node).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            candidate = p

    if candidate is None:
        ext = ".exe" if os.name == "nt" else ""
        bundled = get_runtime_resource_path(f"resources/mathjax/node{ext}")
        if bundled.is_file() and os.access(bundled, os.X_OK):
            candidate = bundled.resolve()

    if candidate is None:
        ext = ".exe" if os.name == "nt" else ""
        system_node = shutil.which(f"node{ext}") or shutil.which("node")
        if system_node:
            candidate = Path(system_node).resolve()

    if candidate is None:
        return None, None

    try:
        proc = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        version = proc.stdout.strip().lstrip("v")
        return candidate, version
    except Exception:
        return candidate, None


def _can_node_resolve_mathjax(node_path: Path) -> bool:
    worker_dir = get_runtime_resource_path("resources/mathjax")
    try:
        proc = subprocess.run(
            [str(node_path), "-e", "require.resolve('mathjax-full/js/mathjax.js')"],
            cwd=str(worker_dir),
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module")
def live_node_path() -> Path:
    node_path, version = _resolve_test_node_binary()
    if node_path is None:
        pytest.skip("Node.js runtime not found.")
    if version != EXPECTED_NODE_VERSION:
        pytest.skip(f"Host Node version {version} != {EXPECTED_NODE_VERSION}.")
    if not _can_node_resolve_mathjax(node_path):
        pytest.skip("mathjax-full package not resolved in resources/mathjax.")
    return node_path


@pytest.fixture
def live_supervisor(live_node_path: Path) -> Generator[MathJaxProcessSupervisor, None, None]:
    supervisor = MathJaxProcessSupervisor(node_path=live_node_path)
    try:
        yield supervisor
    finally:
        supervisor.shutdown()


# ==============================================================================
# Hermetic Unit Tests (No Live Node Required)
# ==============================================================================

def test_supervisor_rejects_oversized_tex_buffer():
    """Supervisor immediately rejects TeX strings exceeding 16 KB with code -32600."""
    supervisor = MathJaxProcessSupervisor()
    huge_tex = "x + " * 6000  # > 16 KB
    with pytest.raises(MathBufferLimitExceededError) as exc_info:
        supervisor.render(huge_tex)
    assert isinstance(exc_info.value, MathRenderError)
    assert exc_info.value.code == -32600
    assert "Buffer limit exceeded" in str(exc_info.value)


def test_supervisor_rate_limiting_prevents_crash_loop():
    """Supervisor raises circuit breaker error when worker crashes more than 3 times in 1 minute."""
    supervisor = MathJaxProcessSupervisor()
    # Trip circuit breaker by recording 3 recent failures
    for _ in range(3):
        supervisor.circuit_breaker.record_failure()

    with pytest.raises(MathCircuitBreakerOpenError) as exc_info:
        supervisor.render("x^2")
    assert isinstance(exc_info.value, MathRenderError)
    assert exc_info.value.code == -32603
    assert "circuit breaker is OPEN" in str(exc_info.value)


def test_client_cache_hit_bypasses_supervisor():
    """Client returns cached result directly without calling supervisor."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    cache = MathSvgCache(capacity=10)
    client = MathJaxClient(supervisor=mock_supervisor, cache=cache)

    req = MathRenderRequest(tex="x^2", display=False)
    cache_key = req.compute_hash()
    cached_result = MathRenderResult(
        hash=cache_key,
        svg_xml="<svg>cached</svg>",
        width="1ex",
        height="1ex",
        vertical_align="0ex",
    )
    cache.put(cache_key, cached_result)

    result = client.render(req)
    assert result == cached_result
    mock_supervisor.render.assert_not_called()


def test_client_cache_miss_calls_supervisor_and_caches():
    """Client delegates to supervisor on cache miss and populates cache."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>rendered</svg>",
        "width": "2ex",
        "height": "1.5ex",
        "vertical_align": "-0.2ex",
    }

    cache = MathSvgCache(capacity=10)
    client = MathJaxClient(supervisor=mock_supervisor, cache=cache)

    req = MathRenderRequest(tex="y = mx + b", display=True)
    result = client.render(req)

    assert result.svg_xml == "<svg>rendered</svg>"
    assert result.width == "2ex"
    assert result.height == "1.5ex"
    mock_supervisor.render.assert_called_once_with(
        tex="y = mx + b", display=True, em=16, ex=8
    )

    # Subsequent render hits cache
    mock_supervisor.reset_mock()
    second_result = client.render(req)
    assert second_result == result
    mock_supervisor.render.assert_not_called()


def test_client_render_batch_handles_mixed_cache_hits():
    """Client batch rendering uses cache when available and queries supervisor for misses."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>new</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }

    cache = MathSvgCache(capacity=10)
    client = MathJaxClient(supervisor=mock_supervisor, cache=cache)

    req1 = MathRenderRequest(tex="a", display=False)
    req2 = MathRenderRequest(tex="b", display=False)

    # Pre-cache req1
    cache.put(
        req1.compute_hash(),
        MathRenderResult(
            hash=req1.compute_hash(),
            svg_xml="<svg>a</svg>",
            width="1ex",
            height="1ex",
            vertical_align="0ex",
        ),
    )

    batch_results = client.render_batch([req1, req2])
    assert len(batch_results) == 2
    assert batch_results[0].svg_xml == "<svg>a</svg>"
    assert batch_results[1].svg_xml == "<svg>new</svg>"
    mock_supervisor.render.assert_called_once_with(tex="b", display=False, em=16, ex=8)


def test_client_negative_memo_hit_bypasses_supervisor():
    """Client returns cached terminal error from negative memo without calling supervisor."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    memo = NegativeFailureMemo(capacity=10)
    req = MathRenderRequest(tex=r"\pathological", display=False)
    cache_key = req.compute_hash()
    memo.put(cache_key, MathRenderTimeoutError(code=-32603, message="Timeout occurred"))

    client = MathJaxClient(supervisor=mock_supervisor, negative_memo=memo)

    with pytest.raises(MathRenderTimeoutError) as exc_info:
        client.render(req)

    assert "Timeout occurred" in exc_info.value.message
    # Assert supervisor was called zero times (no timing assertions)
    mock_supervisor.render.assert_not_called()


def test_client_records_timeout_in_negative_memo():
    """Pathological formula timing out records hash in memo; subsequent calls fail fast."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.side_effect = MathRenderTimeoutError(code=-32603, message="Timed out")

    client = MathJaxClient(supervisor=mock_supervisor)
    req = MathRenderRequest(tex=r"\infinite_macro", display=True)
    cache_key = req.compute_hash()

    with pytest.raises(MathRenderTimeoutError):
        client.render(req)

    assert mock_supervisor.render.call_count == 1
    cached_err = client.negative_memo.get(cache_key)
    assert isinstance(cached_err, MathRenderTimeoutError)

    # Subsequent render hits negative memo; supervisor is called zero additional times
    mock_supervisor.render.reset_mock()
    with pytest.raises(MathRenderTimeoutError):
        client.render(req)

    mock_supervisor.render.assert_not_called()


def test_client_records_crash_in_negative_memo():
    """Worker crash records hash in negative memo; subsequent calls fail fast."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.side_effect = MathWorkerCrashedError(code=-32603, message="Worker died")

    client = MathJaxClient(supervisor=mock_supervisor)
    req = MathRenderRequest(tex=r"\segfault_formula", display=False)
    cache_key = req.compute_hash()

    with pytest.raises(MathWorkerCrashedError):
        client.render(req)

    assert mock_supervisor.render.call_count == 1
    cached_err = client.negative_memo.get(cache_key)
    assert isinstance(cached_err, MathWorkerCrashedError)

    mock_supervisor.render.reset_mock()
    with pytest.raises(MathWorkerCrashedError):
        client.render(req)

    mock_supervisor.render.assert_not_called()


def test_client_never_records_shutdown_error_in_memo_or_cache():
    """MathSupervisorShutdownError is never recorded in negative memo or positive cache."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.side_effect = MathSupervisorShutdownError(
        code=-32603, message="MathJaxProcessSupervisor has been shut down."
    )

    client = MathJaxClient(supervisor=mock_supervisor)
    req = MathRenderRequest(tex="x + y", display=False)
    cache_key = req.compute_hash()

    with pytest.raises(MathSupervisorShutdownError):
        client.render(req)

    assert client.negative_memo.get(cache_key) is None
    assert cache_key not in client.negative_memo
    assert client.cache.get(cache_key) is None
    assert cache_key not in client.cache


def test_client_negative_memo_hit_neutral_to_circuit_breaker():
    """Negative memo hit leaves HALF_OPEN probe slot unclaimed and breaker state untouched."""
    simulated_time = [100.0]
    breaker = MathCircuitBreaker(time_provider=lambda: simulated_time[0])
    # Trip breaker to OPEN
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitBreakerState.OPEN

    # Transition to HALF_OPEN by advancing time past cooldown (30.0s)
    simulated_time[0] += 35.0
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    assert breaker.is_probe_in_flight is False

    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.circuit_breaker = breaker

    memo = NegativeFailureMemo(capacity=10)
    req = MathRenderRequest(tex=r"\bad", display=False)
    cache_key = req.compute_hash()
    memo.put(cache_key, MathRenderTimeoutError(code=-32603, message="Timeout"))

    client = MathJaxClient(supervisor=mock_supervisor, negative_memo=memo)

    with pytest.raises(MathRenderTimeoutError):
        client.render(req)

    mock_supervisor.render.assert_not_called()
    # Circuit breaker remains in HALF_OPEN with NO probe claimed
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    assert breaker.is_probe_in_flight is False

    # Verify a subsequent valid probe can still claim the permit slot
    with breaker.probe_permit() as permit:
        assert breaker.is_probe_in_flight is True
        permit.record_success()

    assert breaker.state == CircuitBreakerState.CLOSED


def test_client_canonical_5_stage_evaluation_order():
    """Verifies strict 5-stage evaluation order defined in ADR-002 section D05:
    1. Positive SVG Cache Lookup -> returns immediately.
    2. Negative Memo Check -> raises cached error immediately without calling supervisor.
    3. Pre-flight input validation -> supervisor rejects oversized TeX (>16 KB).
    4. Circuit breaker evaluation -> supervisor rejects if OPEN.
    5. Post-response validation -> supervisor checks SVG length, caches success in positive cache.
    """
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>ok</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }

    client = MathJaxClient(supervisor=mock_supervisor)
    req = MathRenderRequest(tex="x = 1", display=False)
    cache_key = req.compute_hash()

    # Stage 1: Positive cache hit returns immediately without supervisor call
    cached_res = MathRenderResult(cache_key, "<svg>hit</svg>", "1ex", "1ex", "0ex")
    client.cache.put(cache_key, cached_res)
    assert client.render(req) == cached_res
    mock_supervisor.render.assert_not_called()

    # Stage 2: When positive cache misses, negative memo hit raises immediately without supervisor call
    client.cache.clear()
    client.negative_memo.put(cache_key, MathRenderTimeoutError(-32603, "Cached timeout"))
    with pytest.raises(MathRenderTimeoutError):
        client.render(req)
    mock_supervisor.render.assert_not_called()

    # Stage 3: Pre-flight input validation (TeX > 16 KB rejected before contacting worker/breaker)
    client.negative_memo.clear()
    huge_req = MathRenderRequest(tex="a + " * 6000, display=False)
    mock_supervisor.render.side_effect = MathBufferLimitExceededError(
        code=-32600, message="Buffer limit exceeded: TeX length exceeds 16384 bytes"
    )
    with pytest.raises(MathBufferLimitExceededError) as exc_info:
        client.render(huge_req)
    assert "Buffer limit exceeded" in str(exc_info.value)

    # Direct verification with real supervisor instances for Stage 3 & 4
    real_supervisor = MathJaxProcessSupervisor()
    real_client = MathJaxClient(supervisor=real_supervisor)

    # Stage 3 end-to-end: Pre-flight TeX length rejected before worker or breaker
    with pytest.raises(MathBufferLimitExceededError):
        real_client.render(MathRenderRequest(tex="x + " * 6000, display=False))

    # Stage 4 end-to-end: Circuit breaker in OPEN state fast-fails before worker
    for _ in range(3):
        real_supervisor.circuit_breaker.record_failure()
    with pytest.raises(MathCircuitBreakerOpenError):
        real_client.render(MathRenderRequest(tex="x = 1", display=False))

    # Stage 5: Successful render -> result validated and cached in positive MathSvgCache
    mock_supervisor.render.side_effect = None
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>ok</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }
    res = client.render(req)
    assert res.svg_xml == "<svg>ok</svg>"
    assert client.cache.get(cache_key) is not None


def test_client_render_batch_evaluation_order_and_negative_memo():
    """render_batch applies canonical 5-stage order per request, using cache and negative memo."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>normal</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }

    client = MathJaxClient(supervisor=mock_supervisor)

    req_cached = MathRenderRequest(tex="cached", display=False)
    req_bad = MathRenderRequest(tex="bad", display=False)
    req_fresh = MathRenderRequest(tex="fresh", display=False)

    # Warm positive cache for req_cached
    client.cache.put(
        req_cached.compute_hash(),
        MathRenderResult(req_cached.compute_hash(), "<svg>cached</svg>", "1ex", "1ex", "0ex"),
    )
    # Warm negative memo for req_bad
    client.negative_memo.put(
        req_bad.compute_hash(),
        MathRenderTimeoutError(-32603, "Timeout on bad"),
    )

    # Batch with cached and fresh succeeds
    batch_res = client.render_batch([req_cached, req_fresh])
    assert len(batch_res) == 2
    assert batch_res[0].svg_xml == "<svg>cached</svg>"
    assert batch_res[1].svg_xml == "<svg>normal</svg>"
    # Supervisor only called for req_fresh
    mock_supervisor.render.assert_called_once_with(tex="fresh", display=False, em=16, ex=8)

    # Batch containing negative memo entry raises without calling supervisor again
    mock_supervisor.render.reset_mock()
    with pytest.raises(MathRenderTimeoutError):
        client.render_batch([req_bad, req_fresh])
    mock_supervisor.render.assert_not_called()


def test_client_render_batch_isolated_all_success():
    """render_batch_isolated returns dictionary mapping hash to MathRenderResult when all succeed."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>ok</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }
    client = MathJaxClient(supervisor=mock_supervisor)
    req1 = MathRenderRequest(tex="x+1", display=False)
    req2 = MathRenderRequest(tex="x+2", display=True)

    results = client.render_batch_isolated([req1, req2])
    assert len(results) == 2
    assert req1.compute_hash() in results
    assert req2.compute_hash() in results
    assert isinstance(results[req1.compute_hash()], MathRenderResult)
    assert isinstance(results[req2.compute_hash()], MathRenderResult)
    assert results[req1.compute_hash()].svg_xml == "<svg>ok</svg>"
    # Positive cache is populated
    assert client.cache.get(req1.compute_hash()) is not None
    assert client.cache.get(req2.compute_hash()) is not None


def test_client_render_batch_isolated_mixed_partial_failures():
    """render_batch_isolated isolates failures and continues processing subsequent formulas."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)

    def fake_render(tex, display, em, ex):
        if tex == "valid1":
            return {"svg_xml": "<svg>v1</svg>", "width": "1ex", "height": "1ex", "vertical_align": "0ex"}
        elif tex == "syntax_err":
            raise MathSyntaxError(-32602, "Syntax error")
        elif tex == "timeout_err":
            raise MathRenderTimeoutError(-32603, "Timeout error")
        elif tex == "valid2":
            return {"svg_xml": "<svg>v2</svg>", "width": "2ex", "height": "2ex", "vertical_align": "0ex"}
        raise RuntimeError("Unexpected tex")

    mock_supervisor.render.side_effect = fake_render
    client = MathJaxClient(supervisor=mock_supervisor)

    req1 = MathRenderRequest(tex="valid1", display=False)
    req2 = MathRenderRequest(tex="syntax_err", display=False)
    req3 = MathRenderRequest(tex="timeout_err", display=True)
    req4 = MathRenderRequest(tex="valid2", display=True)

    results = client.render_batch_isolated([req1, req2, req3, req4])

    assert len(results) == 4
    # Valid 1 succeeded
    assert isinstance(results[req1.compute_hash()], MathRenderResult)
    assert results[req1.compute_hash()].svg_xml == "<svg>v1</svg>"
    assert client.cache.get(req1.compute_hash()) is not None

    # Syntax err captured
    assert isinstance(results[req2.compute_hash()], MathSyntaxError)
    assert results[req2.compute_hash()].message == "Syntax error"
    assert client.cache.get(req2.compute_hash()) is None

    # Timeout err captured and recorded in negative memo
    assert isinstance(results[req3.compute_hash()], MathRenderTimeoutError)
    assert results[req3.compute_hash()].message == "Timeout error"
    assert client.negative_memo.get(req3.compute_hash()) is not None

    # Valid 2 succeeded despite previous errors
    assert isinstance(results[req4.compute_hash()], MathRenderResult)
    assert results[req4.compute_hash()].svg_xml == "<svg>v2</svg>"
    assert client.cache.get(req4.compute_hash()) is not None


def test_client_render_batch_isolated_negative_memo_hit_does_not_call_supervisor():
    """Negative memo hit returns cached error without calling supervisor."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg>fresh</svg>",
        "width": "1ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }
    client = MathJaxClient(supervisor=mock_supervisor)

    req_cached_err = MathRenderRequest(tex="memo_bad", display=False)
    req_fresh = MathRenderRequest(tex="fresh_good", display=False)

    client.negative_memo.put(
        req_cached_err.compute_hash(),
        MathRenderTimeoutError(-32603, "Earlier timeout"),
    )

    results = client.render_batch_isolated([req_cached_err, req_fresh])

    assert isinstance(results[req_cached_err.compute_hash()], MathRenderTimeoutError)
    assert isinstance(results[req_fresh.compute_hash()], MathRenderResult)
    # Supervisor was only called once for req_fresh, never for req_cached_err
    mock_supervisor.render.assert_called_once_with(tex="fresh_good", display=False, em=16, ex=8)


def test_client_render_batch_isolated_propagates_fatal_shutdown():
    """render_batch_isolated allows fatal MathSupervisorShutdownError to propagate immediately."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)

    def fake_render(tex, display, em, ex):
        if tex == "ok":
            return {"svg_xml": "<svg>ok</svg>", "width": "1ex", "height": "1ex", "vertical_align": "0ex"}
        elif tex == "shutdown":
            raise MathSupervisorShutdownError(-32000, "Supervisor is shut down")
        return {"svg_xml": "<svg>unreached</svg>", "width": "1ex", "height": "1ex", "vertical_align": "0ex"}

    mock_supervisor.render.side_effect = fake_render
    client = MathJaxClient(supervisor=mock_supervisor)

    req1 = MathRenderRequest(tex="ok", display=False)
    req2 = MathRenderRequest(tex="shutdown", display=False)
    req3 = MathRenderRequest(tex="after_shutdown", display=False)

    with pytest.raises(MathSupervisorShutdownError) as exc_info:
        client.render_batch_isolated([req1, req2, req3])

    assert "Supervisor is shut down" in str(exc_info.value)
    # req3 should never have been dispatched
    assert mock_supervisor.render.call_count == 2



# ==============================================================================
# Live Integration Tests with Headless Daemon
# ==============================================================================

def test_live_supervisor_ping_and_version(live_supervisor: MathJaxProcessSupervisor):
    """Live supervisor ping and version RPC calls succeed."""
    assert live_supervisor.ping() is True
    assert live_supervisor.is_alive is True

    ver = live_supervisor.version()
    assert ver.get("node") == EXPECTED_NODE_VERSION
    assert ver.get("mathjax") == "3.2.2"


def test_live_client_render_and_metrics(live_supervisor: MathJaxProcessSupervisor):
    """Live client converts TeX into valid SVG XML with metrics."""
    client = MathJaxClient(supervisor=live_supervisor)
    req = MathRenderRequest(tex=r"\sum_{i=1}^n i", display=True)
    res = client.render(req)

    assert res.hash == req.compute_hash()
    assert res.svg_xml.startswith("<svg")
    assert res.svg_xml.endswith("</svg>")
    assert "<defs><path" in res.svg_xml
    assert res.width.endswith("ex")
    assert res.height.endswith("ex")


def test_live_client_syntax_error_handling(live_supervisor: MathJaxProcessSupervisor):
    """Invalid TeX produces structured MathSyntaxError without crashing supervisor."""
    client = MathJaxClient(supervisor=live_supervisor)
    req = MathRenderRequest(tex=r"\frac{1}{", display=False)

    with pytest.raises(MathSyntaxError) as exc_info:
        client.render(req)
    assert isinstance(exc_info.value, MathRenderError)
    assert exc_info.value.code == -32602
    assert "Missing close brace" in exc_info.value.message
    assert live_supervisor.is_alive is True

    # Subsequent valid formula renders normally
    valid_req = MathRenderRequest(tex="x + 1", display=False)
    valid_res = client.render(valid_req)
    assert valid_res.svg_xml.startswith("<svg")


def test_live_supervisor_crash_recovery(live_supervisor: MathJaxProcessSupervisor):
    """Supervisor automatically restarts worker if process dies."""
    client = MathJaxClient(supervisor=live_supervisor)
    res1 = client.render(MathRenderRequest(tex="x_1", display=False))
    assert res1.svg_xml.startswith("<svg")

    # Forcibly kill the worker process
    assert live_supervisor._process is not None
    live_supervisor._process.kill()
    live_supervisor._process.wait()

    assert live_supervisor.is_alive is False

    # Next render request should detect dead process, restart it, and succeed
    res2 = client.render(MathRenderRequest(tex="x_2", display=False))
    assert res2.svg_xml.startswith("<svg")
    assert live_supervisor.is_alive is True
