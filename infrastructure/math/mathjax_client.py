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
    MathRenderTimeoutError,
    MathWorkerCrashedError,
)
from infrastructure.math.lru_cache import MathSvgCache
from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor
from infrastructure.math.negative_memo import NegativeFailureMemo


class MathJaxClient(IMathRenderer):
    """Client implementing IMathRenderer backed by a supervisor and an LRU cache."""

    def __init__(
        self,
        supervisor: MathJaxProcessSupervisor,
        cache: Optional[MathSvgCache] = None,
        negative_memo: Optional[NegativeFailureMemo] = None,
    ) -> None:
        """Initialize the client.

        Args:
            supervisor: Process supervisor managing the headless Node.js daemon.
            cache: Thread-safe LRU cache instance. Defaults to a fresh 1,000-item cache.
            negative_memo: Thread-safe session-scoped negative failure memo.
                           Defaults to a fresh 1,000-item memo.
        """
        self._supervisor = supervisor
        self._cache = cache if cache is not None else MathSvgCache(capacity=1000)
        self._negative_memo = (
            negative_memo if negative_memo is not None else NegativeFailureMemo(capacity=1000)
        )

    @property
    def supervisor(self) -> MathJaxProcessSupervisor:
        """Return the underlying process supervisor."""
        return self._supervisor

    @property
    def cache(self) -> MathSvgCache:
        """Return the in-memory SVG cache."""
        return self._cache

    @property
    def negative_memo(self) -> NegativeFailureMemo:
        """Return the session-scoped negative failure memo."""
        return self._negative_memo

    def render(self, request: MathRenderRequest) -> MathRenderResult:
        """Render a single TeX math expression to SVG following canonical 5-stage order.

        Canonical evaluation order (ADR-002 section D05):
        1. Positive SVG cache lookup (MathSvgCache): return cached markup immediately.
        2. Negative memo check: if hash was previously recorded as timeout/crashed,
           fail fast in O(1) without touching worker or claiming probe permits.
        3. Pre-flight input validation (handled by supervisor.render(): MAX_TEX_LENGTH).
        4. Circuit breaker & supervisor invocation (claims probe permit if HALF_OPEN).
        5. Post-response validation (MAX_SVG_LENGTH) & caching in positive cache.

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

        cached_err = self._negative_memo.get(cache_key)
        if cached_err is not None:
            raise cached_err

        try:
            render_data = self._supervisor.render(
                tex=request.tex,
                display=request.display,
                em=request.em,
                ex=request.ex,
            )
        except (MathRenderTimeoutError, MathWorkerCrashedError) as exc:
            # Known limitation: A worker killed for reasons unrelated to the
            # in-flight formula leaves that formula in the negative memo for
            # the session; a TTL / expiration policy is deferred to P08.
            self._negative_memo.put(cache_key, exc)
            raise
        # Note: MathSupervisorShutdownError is never recorded in negative memo or positive cache
        # per ADR-002 section D07, and naturally propagates uncaught.

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

        Employs identical canonical 5-stage evaluation order per request.

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
