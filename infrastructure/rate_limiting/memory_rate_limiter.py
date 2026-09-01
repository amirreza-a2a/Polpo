# ============================================================
#  infrastructure/rate_limiting/memory_rate_limiter.py
# ============================================================

import time
import threading
from collections import deque
from typing import Dict, Optional, Deque

from application.ports.rate_limiter import IRateLimiter
from core.entities.api_slot import ApiSlot


class ThreadSafeMemoryRateLimiter(IRateLimiter):
    """
    Thread-safe in-memory sliding-window rate limiter with atomic slot reservation.
    Enforces Requests-Per-Minute (RPM) limits per API slot using monotonic time
    and fine-grained per-slot mutex synchronization without holding locks during sleep.
    """

    # Configurable default RPM values (not authoritative constants).
    DEFAULT_PROVIDER_RPMS: Dict[str, int] = {
        "google": 15,
        "openai": 60,
        "openrouter": 30,
        "custom": 60,
    }

    def __init__(
        self,
        default_rpms: Optional[Dict[str, int]] = None,
        fallback_rpm: int = 60,
    ):
        self._provider_rpms = dict(self.DEFAULT_PROVIDER_RPMS)
        if default_rpms:
            self._provider_rpms.update(default_rpms)
        self._fallback_rpm = fallback_rpm

        self._meta_lock = threading.Lock()
        self._slot_locks: Dict[str, threading.Lock] = {}
        self._history: Dict[str, Deque[float]] = {}

    def _get_key(self, slot: ApiSlot) -> str:
        if slot.credential_ref and slot.credential_ref.identifier:
            return slot.credential_ref.identifier
        if slot.id is not None:
            return f"{slot.provider}_{slot.id}"
        return (slot.provider or "unknown").lower().strip()

    def _get_slot_lock(self, key: str) -> threading.Lock:
        with self._meta_lock:
            if key not in self._slot_locks:
                self._slot_locks[key] = threading.Lock()
            return self._slot_locks[key]

    def _get_rpm(self, slot: ApiSlot) -> int:
        provider = (slot.provider or "").lower().strip()
        return self._provider_rpms.get(provider, self._fallback_rpm)

    def wait_if_needed(self, slot: ApiSlot) -> None:
        """
        Atomically checks and reserves dispatch capacity in the sliding 60.0s window.
        Calculates necessary wait time under per-slot lock, then performs the sleep
        outside the lock to prevent blocking concurrent callers of other slots or calls.
        """
        rpm = self._get_rpm(slot)
        if rpm <= 0:
            return

        key = self._get_key(slot)
        slot_lock = self._get_slot_lock(key)
        sleep_duration = 0.0

        with slot_lock:
            if key not in self._history:
                self._history[key] = deque()
            timestamps = self._history[key]
            now = time.monotonic()

            # Evict timestamps older than 60.0s
            while timestamps and (now - timestamps[0]) >= 60.0:
                timestamps.popleft()

            if len(timestamps) < rpm:
                target_time = now
                timestamps.append(target_time)
                sleep_duration = 0.0
            else:
                # Saturation reached: the next allowed slot is 60.0s after the oldest active request
                earliest = timestamps[-rpm]
                target_time = max(now, earliest + 60.0)
                timestamps.append(target_time)
                sleep_duration = max(0.0, target_time - now)

        # Sleep outside the lock so other threads or slots are not blocked
        if sleep_duration > 0.0:
            time.sleep(sleep_duration)

    def mark_request_sent(self, slot: ApiSlot) -> None:
        """
        Records the actual dispatch of a request. If wait_if_needed was already called,
        the reservation is preserved in the queue.
        """
        key = self._get_key(slot)
        slot_lock = self._get_slot_lock(key)
        with slot_lock:
            if key not in self._history:
                self._history[key] = deque()
            timestamps = self._history[key]
            now = time.monotonic()

            # Evict old timestamps
            while timestamps and (now - timestamps[0]) >= 60.0:
                timestamps.popleft()

            # If no reservation was made prior (caller called mark_request_sent directly), record now
            if not timestamps:
                timestamps.append(now)

    def get_history_count(self, slot: ApiSlot) -> int:
        """
        Diagnostic helper returning the active request count in the current window.
        """
        key = self._get_key(slot)
        slot_lock = self._get_slot_lock(key)
        with slot_lock:
            if key not in self._history:
                return 0
            timestamps = self._history[key]
            now = time.monotonic()
            while timestamps and (now - timestamps[0]) >= 60.0:
                timestamps.popleft()
            return len(timestamps)
