# ============================================================
#  infrastructure/diagram/cache.py
#  Bounded Content-Addressed LRU Cache for Rendered Diagram SVGs
# ============================================================

from __future__ import annotations

from collections import OrderedDict
import threading
from typing import Optional

from application.ports.diagram_renderer import DiagramRenderResult


class DiagramSvgCache:
    """Thread-safe bounded in-memory LRU cache for rendered diagram SVGs.

    Maps deterministic SHA-256 diagram hashes to their DiagramRenderResult instances.
    Guarantees thread-safe access and O(1) eviction of least-recently-used results.
    """

    def __init__(self, capacity: int = 500) -> None:
        if capacity <= 0:
            raise ValueError(f"Cache capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, DiagramRenderResult] = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    def get(self, key: str) -> Optional[DiagramRenderResult]:
        """Retrieves a cached result by its deterministic SHA-256 hash.

        Marks the retrieved entry as most-recently-used upon a hit.
        """
        if not key:
            return None
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def put(self, result: DiagramRenderResult) -> None:
        """Stores a rendered result in the cache, evicting the oldest item if full."""
        if not result or not result.hash:
            return
        with self._lock:
            if result.hash in self._cache:
                self._cache.move_to_end(result.hash)
                self._cache[result.hash] = result
            else:
                if len(self._cache) >= self._capacity:
                    self._cache.popitem(last=False)
                self._cache[result.hash] = result

    def clear(self) -> None:
        """Flushes all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache
