# ============================================================
#  tests/unit/test_diagram_renderer.py
#  Unit Tests for Technical Diagram Rendering Port & Adapters
# ============================================================

import os
import shutil
import tempfile
import threading
import time

import pytest

from application.ports.diagram_renderer import (
    DiagramCancelledError,
    DiagramRenderRequest,
    DiagramRenderResult,
    DiagramRendererUnavailableError,
    DiagramSyntaxError,
    DiagramTimeoutError,
)
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer


def test_diagram_render_request_hash_deterministic():
    req1 = DiagramRenderRequest(source=r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}")
    req2 = DiagramRenderRequest(source=r" \begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture} ")
    assert req1.compute_hash() == req2.compute_hash()

    req_diff = DiagramRenderRequest(source=r"\begin{tikzpicture}\draw(0,0)--(2,2);\end{tikzpicture}")
    assert req1.compute_hash() != req_diff.compute_hash()


def test_diagram_svg_cache_lru_eviction():
    cache = DiagramSvgCache(capacity=2)
    res1 = DiagramRenderResult(hash="h1", svg_xml="<svg>1</svg>", width=10.0, height=10.0)
    res2 = DiagramRenderResult(hash="h2", svg_xml="<svg>2</svg>", width=20.0, height=20.0)
    res3 = DiagramRenderResult(hash="h3", svg_xml="<svg>3</svg>", width=30.0, height=30.0)

    cache.put(res1)
    cache.put(res2)
    assert len(cache) == 2
    assert cache.get("h1") is not None

    # Accessing h1 makes it MRU; h2 is now LRU
    cache.put(res3)
    assert len(cache) == 2
    assert cache.get("h2") is None  # Evicted
    assert cache.get("h1") is not None
    assert cache.get("h3") is not None


def test_diagram_svg_cache_thread_safety():
    cache = DiagramSvgCache(capacity=100)
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(50):
                h = f"h_{worker_id}_{i}"
                cache.put(DiagramRenderResult(hash=h, svg_xml="<svg/>", width=1.0, height=1.0))
                _ = cache.get(h)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(cache) <= 100


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_compiles_tikz_to_svg():
    cache = DiagramSvgCache(capacity=10)
    renderer = StandaloneTeXDiagramRenderer(cache=cache)
    assert renderer.probe_readiness() is True

    req = DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \draw[thick] (0,0) -- (2,1);
\end{tikzpicture}"""
    )
    result = renderer.render(req)
    assert result.hash == req.compute_hash()
    assert "<svg" in result.svg_xml
    assert result.width > 0
    assert result.height > 0
    assert result.status == "rendered"

    # Verify cache hit
    hit = cache.get(result.hash)
    assert hit is not None
    assert hit.svg_xml == result.svg_xml


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_compiles_pgfplots():
    renderer = StandaloneTeXDiagramRenderer()
    req = DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \begin{axis}[width=5cm, height=4cm]
        \addplot[domain=0:2] {x^2};
    \end{axis}
\end{tikzpicture}"""
    )
    result = renderer.render(req)
    assert "<svg" in result.svg_xml
    assert result.width > 0


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_compiles_circuitikz():
    renderer = StandaloneTeXDiagramRenderer()
    req = DiagramRenderRequest(
        source=r"""\begin{circuitikz}[american]
    \draw (0,0) to[R=10] (2,0);
\end{circuitikz}"""
    )
    result = renderer.render(req)
    assert "<svg" in result.svg_xml
    assert result.width > 0


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_syntax_error():
    renderer = StandaloneTeXDiagramRenderer()
    req = DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \draw[completelyInvalidOptionXYZ] (0,0) -- (1,1);
\end{tikzpicture}"""
    )
    with pytest.raises(DiagramSyntaxError) as exc_info:
        renderer.render(req)
    assert exc_info.value.code == "syntax"


def test_standalone_renderer_cancellation():
    renderer = StandaloneTeXDiagramRenderer()
    req = DiagramRenderRequest(source=r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}")
    with pytest.raises(DiagramCancelledError):
        renderer.render(req, cancel_fn=lambda: True)


def test_standalone_renderer_unavailable_compiler():
    renderer = StandaloneTeXDiagramRenderer(compiler_path="/nonexistent/tex/binary")
    req = DiagramRenderRequest(source=r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}")
    with pytest.raises(DiagramRendererUnavailableError):
        renderer.render(req)


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_batch():
    renderer = StandaloneTeXDiagramRenderer()
    req1 = DiagramRenderRequest(source=r"\begin{tikzpicture}\draw (0,0) -- (1,0);\end{tikzpicture}")
    req2 = DiagramRenderRequest(source=r"\begin{tikzpicture}\invalidCmdFoo\end{tikzpicture}")

    outcomes = renderer.render_batch([req1, req2])
    h1, h2 = req1.compute_hash(), req2.compute_hash()

    assert isinstance(outcomes[h1], DiagramRenderResult)
    assert isinstance(outcomes[h2], DiagramSyntaxError)


@pytest.mark.skipif(shutil.which("xelatex") is None and shutil.which("pdflatex") is None, reason="No TeX compiler on host")
def test_standalone_renderer_compiles_tikzcd_to_svg():
    renderer = StandaloneTeXDiagramRenderer()
    req = DiagramRenderRequest(
        source=r"""\begin{tikzcd}
    A \arrow[r] & B
\end{tikzcd}"""
    )
    res = renderer.render(req)
    assert res.status == "rendered"
    assert "<svg" in res.svg_xml
    assert res.width > 0
    assert res.height > 0
