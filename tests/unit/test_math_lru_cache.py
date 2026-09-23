"""Unit tests for Math Renderer port, models, and LRU cache (TICK-009A)."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import List

import pytest

from application.ports.math_renderer import (
    IMathRenderer,
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
)
from infrastructure.math.lru_cache import MathSvgCache


def test_math_render_request_defaults_and_hash():
    """Verify MathRenderRequest defaults and deterministic sha256 computation."""
    req = MathRenderRequest(tex="E = mc^2", display=True)
    assert req.tex == "E = mc^2"
    assert req.display is True
    assert req.em == 16
    assert req.ex == 8

    expected_hash = hashlib.sha256("3.2.2|True|16|8|E = mc^2".encode("utf-8")).hexdigest()
    assert req.compute_hash() == expected_hash


def test_math_render_result_and_error():
    """Verify MathRenderResult structure and MathRenderError exception behavior."""
    res = MathRenderResult(
        hash="abc123hash",
        svg_xml="<svg></svg>",
        width="2.5ex",
        height="1.2ex",
        vertical_align="-0.3ex",
    )
    assert res.hash == "abc123hash"
    assert res.svg_xml == "<svg></svg>"
    assert res.width == "2.5ex"
    assert res.height == "1.2ex"
    assert res.vertical_align == "-0.3ex"

    err = MathRenderError(code=-32602, message="Syntax error in LaTeX formula")
    assert err.code == -32602
    assert err.message == "Syntax error in LaTeX formula"
    assert err.args == ("Syntax error in LaTeX formula",)
    assert "Syntax error" in str(err)
    assert isinstance(err, Exception)


def test_imath_renderer_is_abstract_and_subclassable():
    """IMathRenderer cannot be instantiated directly, but can be concretely subclassed."""
    with pytest.raises(TypeError):
        IMathRenderer()  # type: ignore[abstract]

    class ConcreteRenderer(IMathRenderer):
        def render(self, request: MathRenderRequest) -> MathRenderResult:
            return MathRenderResult(
                hash=request.compute_hash(),
                svg_xml="<svg>rendered</svg>",
                width="1.0ex",
                height="1.0ex",
                vertical_align="0.0ex",
            )

        def render_batch(self, requests: List[MathRenderRequest]) -> List[MathRenderResult]:
            return [self.render(r) for r in requests]

    renderer = ConcreteRenderer()
    req = MathRenderRequest(tex="x", display=False)
    single = renderer.render(req)
    assert single.svg_xml == "<svg>rendered</svg>"

    batch = renderer.render_batch([req, req])
    assert len(batch) == 2


def test_lru_cache_capacity_validation_and_default():
    """Verify default capacity and positive capacity validation."""
    default_cache = MathSvgCache()
    assert default_cache.capacity == 1000

    with pytest.raises(ValueError, match="positive integer"):
        MathSvgCache(capacity=0)

    with pytest.raises(ValueError, match="positive integer"):
        MathSvgCache(capacity=-5)


def test_lru_cache_compute_key():
    """Verify MathSvgCache.compute_key delegates deterministically to MathRenderRequest."""
    key = MathSvgCache.compute_key(tex="y = f(x)", display=True, em=16, ex=8)
    req = MathRenderRequest(tex="y = f(x)", display=True, em=16, ex=8)
    assert key == req.compute_hash()


def test_lru_cache_insertion_retrieval_and_miss():
    """Verify standard cache insertion, lookup hit, and lookup miss."""
    cache = MathSvgCache(capacity=10)
    assert len(cache) == 0

    req = MathRenderRequest(tex="x^2", display=False)
    key = req.compute_hash()
    res = MathRenderResult(
        hash=key,
        svg_xml="<svg>x2</svg>",
        width="1.0ex",
        height="1.0ex",
        vertical_align="0.0ex",
    )

    assert cache.get(key) is None
    assert key not in cache

    cache.put(key, res)
    assert len(cache) == 1
    assert key in cache

    cached = cache.get(key)
    assert cached is not None
    assert cached.svg_xml == "<svg>x2</svg>"
    assert cached.hash == key


def test_lru_cache_overwrite_updates_value_and_promotes_mru():
    """Overwriting an existing key updates its value and promotes it without growing size."""
    cache = MathSvgCache(capacity=2)
    res_v1 = MathRenderResult("k1", "<svg>v1</svg>", "1", "1", "0")
    res_v2 = MathRenderResult("k1", "<svg>v2</svg>", "1", "1", "0")
    res_b = MathRenderResult("k2", "<svg>b</svg>", "1", "1", "0")

    cache.put("k1", res_v1)
    cache.put("k2", res_b)
    assert len(cache) == 2

    # Overwrite k1 with v2
    cache.put("k1", res_v2)
    assert len(cache) == 2
    assert cache.get("k1") == res_v2

    # Now k1 is MRU, k2 is LRU. Adding k3 should evict k2, not k1.
    res_c = MathRenderResult("k3", "<svg>c</svg>", "1", "1", "0")
    cache.put("k3", res_c)
    assert len(cache) == 2
    assert "k2" not in cache
    assert "k1" in cache
    assert "k3" in cache


def test_lru_cache_eviction_policy():
    """Cache evicts least-recently-used item when capacity is exceeded."""
    cache = MathSvgCache(capacity=3)

    items = [
        MathRenderResult(hash=f"k{i}", svg_xml=f"<svg>{i}</svg>", width="1", height="1", vertical_align="0")
        for i in range(5)
    ]

    cache.put("k0", items[0])
    cache.put("k1", items[1])
    cache.put("k2", items[2])
    assert len(cache) == 3

    # Access k0 (makes k0 most recently used; order: k1, k2, k0)
    assert cache.get("k0") is not None

    # Insert k3 -> exceeds capacity (3), should evict oldest (k1)
    cache.put("k3", items[3])
    assert len(cache) == 3
    assert "k1" not in cache
    assert "k0" in cache
    assert "k2" in cache
    assert "k3" in cache

    # Order in cache is currently [k2, k0, k3]. Oldest is k2.
    # Insert k4 -> should evict oldest (k2). Order becomes [k0, k3, k4].
    cache.put("k4", items[4])
    assert len(cache) == 3
    assert "k2" not in cache
    assert "k0" in cache
    assert "k3" in cache
    assert "k4" in cache


def test_lru_cache_clear():
    """Clear empties the cache completely."""
    cache = MathSvgCache(capacity=10)
    cache.put("k1", MathRenderResult("k1", "<svg>1</svg>", "1", "1", "0"))
    cache.put("k2", MathRenderResult("k2", "<svg>2</svg>", "1", "1", "0"))
    assert len(cache) == 2

    cache.clear()
    assert len(cache) == 0
    assert cache.get("k1") is None
    assert cache.get("k2") is None


def test_lru_cache_thread_safety():
    """Cache operations must be thread-safe across concurrent reads and writes."""
    cache = MathSvgCache(capacity=50)

    def worker(worker_id: int):
        for i in range(100):
            key = f"key_{worker_id}_{i % 20}"
            item = MathRenderResult(
                hash=key,
                svg_xml=f"<svg>{worker_id}_{i}</svg>",
                width="1",
                height="1",
                vertical_align="0",
            )
            cache.put(key, item)
            _ = cache.get(key)
            _ = len(cache)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, wid) for wid in range(8)]
        for f in futures:
            f.result()

    assert len(cache) <= 50
