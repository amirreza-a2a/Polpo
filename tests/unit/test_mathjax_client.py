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
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
)
from infrastructure.math import (
    MathJaxClient,
    MathJaxProcessSupervisor,
    MathSvgCache,
)
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
    with pytest.raises(MathRenderError) as exc_info:
        supervisor.render(huge_tex)
    assert exc_info.value.code == -32600
    assert "Buffer limit exceeded" in str(exc_info.value)


def test_supervisor_rate_limiting_prevents_crash_loop():
    """Supervisor raises rate limit error when worker crashes more than 3 times in 1 minute."""
    supervisor = MathJaxProcessSupervisor()
    # Simulate 3 recent restarts
    supervisor._restart_timestamps = [
        time.monotonic() - 10,
        time.monotonic() - 5,
        time.monotonic() - 1,
    ]

    with pytest.raises(MathRenderError) as exc_info:
        supervisor.render("x^2")
    assert exc_info.value.code == -32603
    assert "Restart rate limit exceeded" in str(exc_info.value)


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
    """Invalid TeX produces structured MathRenderError without crashing supervisor."""
    client = MathJaxClient(supervisor=live_supervisor)
    req = MathRenderRequest(tex=r"\frac{1}{", display=False)

    with pytest.raises(MathRenderError) as exc_info:
        client.render(req)
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
