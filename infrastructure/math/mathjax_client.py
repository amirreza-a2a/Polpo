"""MathJax renderer client adapting the persistent worker supervisor to IMathRenderer (TICK-009B).

Coordinates the in-memory MathSvgCache and MathJaxProcessSupervisor to provide
cached, high-throughput formula rendering.
"""

from __future__ import annotations

from typing import List, Optional

from application.ports.math_renderer import (
    IMathRenderer,
    MathRenderRequest,
    MathRenderResult,
)
from infrastructure.math.lru_cache import MathSvgCache
from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor


class MathJaxClient(IMathRenderer):
    """Client implementing IMathRenderer backed by a supervisor and an LRU cache."""

    def __init__(
        self,
        supervisor: MathJaxProcessSupervisor,
        cache: Optional[MathSvgCache] = None,
    ) -> None:
        """Initialize the client.

        Args:
            supervisor: Process supervisor managing the headless Node.js daemon.
            cache: Thread-safe LRU cache instance. Defaults to a fresh 1,000-item cache.
        """
        self._supervisor = supervisor
        self._cache = cache if cache is not None else MathSvgCache(capacity=1000)

    @property
    def supervisor(self) -> MathJaxProcessSupervisor:
        """Return the underlying process supervisor."""
        return self._supervisor

    @property
    def cache(self) -> MathSvgCache:
        """Return the in-memory SVG cache."""
        return self._cache

    def render(self, request: MathRenderRequest) -> MathRenderResult:
        """Render a single TeX math expression to SVG, utilizing the LRU cache on hit.

        Args:
            request: Formula rendering specifications.

        Returns:
            MathRenderResult containing SVG XML and layout metrics.

        Raises:
            MathRenderError: If formula syntax is invalid or worker execution fails.
        """
        cache_key = request.compute_hash()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        render_data = self._supervisor.render(
            tex=request.tex,
            display=request.display,
            em=request.em,
            ex=request.ex,
        )

        result = MathRenderResult(
            hash=cache_key,
            svg_xml=render_data["svg_xml"],
            width=render_data["width"],
            height=render_data["height"],
            vertical_align=render_data["vertical_align"],
        )

        self._cache.put(cache_key, result)
        return result

    def render_batch(self, requests: List[MathRenderRequest]) -> List[MathRenderResult]:
        """Render multiple TeX math expressions in a batch.

        Args:
            requests: Sequence of formula rendering specifications.

        Returns:
            List of rendered SVG results corresponding to inputs.

        Raises:
            MathRenderError: If formula rendering encounters an unrecoverable error.
        """
        results: List[MathRenderResult] = []
        for req in requests:
            results.append(self.render(req))
        return results
