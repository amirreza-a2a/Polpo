"""In-memory thread-safe LRU cache for rendered MathJax SVG artifacts.

Provides O(1) retrieval for warm math rendering, avoiding redundant worker
calls during interactive editor typing and preview rendering.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from application.ports.math_renderer import MathRenderRequest, MathRenderResult


class MathSvgCache:
    """Thread-safe in-memory Least Recently Used (LRU) cache for rendered math SVGs."""

    def __init__(self, capacity: int = 1000) -> None:
        """Initialize the LRU cache with a bounded capacity.

        Args:
            capacity: Maximum number of entries retained before evicting the
                      oldest entry. Default is 1,000 items (~2 MB RAM).
        """
        if capacity <= 0:
            raise ValueError(f"Capacity must be a positive integer, got {capacity}")
        self._capacity: int = capacity
        self._cache: OrderedDict[str, MathRenderResult] = OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Maximum number of entries allowed in the cache."""
        return self._capacity

    def get(self, key: str) -> Optional[MathRenderResult]:
        """Retrieve a cached MathRenderResult by key, marking it as most recently used.

        Args:
            key: Deterministic SHA-256 hash key.

        Returns:
            MathRenderResult if present, None otherwise.
        """
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def put(self, key: str, result: MathRenderResult) -> None:
        """Store a MathRenderResult in the cache, evicting the oldest if capacity is exceeded.

        Args:
            key: Deterministic SHA-256 hash key.
            result: Rendered SVG data and metrics.
        """
        with self._lock:
            if key in self._cache:
                self._cache[key] = result
                self._cache.move_to_end(key)
                return

            if len(self._cache) >= self._capacity:
                self._cache.popitem(last=False)

            self._cache[key] = result

    def clear(self) -> None:
        """Removes all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        """Return the current number of cached items."""
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the cache without updating its LRU position."""
        with self._lock:
            return key in self._cache

    @staticmethod
    def compute_key(tex: str, display: bool, em: int = 16, ex: int = 8) -> str:
        """Generate the canonical deterministic SHA-256 cache key for a formula."""
        return MathRenderRequest(tex=tex, display=display, em=em, ex=ex).compute_hash()
