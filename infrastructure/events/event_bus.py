# ============================================================
#  infrastructure/events/event_bus.py
# ============================================================

import uuid
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple, Type

from application.ports.notifier import IApplicationEventPublisher
from application.sanitizer import sanitize_error_message

logger = logging.getLogger("infrastructure.events.event_bus")


class InMemoryEventBus(IApplicationEventPublisher):
    """
    Thread-safe in-process application event bus.
    Dispatches typed events to subscribers with lock-free callback execution,
    subscriber error isolation, and secret-redacted logging.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Mapping: event_type -> List of (subscription_id, handler_callable)
        self._subscribers: Dict[Type, List[Tuple[str, Callable[[Any], None]]]] = {}

    def subscribe(self, event_type: Type, handler: Callable[[Any], None]) -> str:
        """
        Registers a callback handler for a specific event type.
        Returns a unique subscription ID for unregistering.
        """
        if not callable(handler):
            raise ValueError("Event handler must be callable.")

        sub_id = uuid.uuid4().hex
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append((sub_id, handler))
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """
        Unregisters a subscriber by its subscription ID.
        Returns True if the subscriber was found and removed, False otherwise.
        """
        with self._lock:
            for event_type, subs in self._subscribers.items():
                for idx, (sid, handler) in enumerate(subs):
                    if sid == sub_id:
                        subs.pop(idx)
                        return True
        return False

    def publish(self, event: Any) -> None:
        """
        Publishes an event to all matching subscribers.
        Takes a shallow snapshot under the registry lock and executes callbacks outside the lock.
        Exceptions in subscriber callbacks are sanitized, logged, and isolated.
        """
        event_cls = type(event)
        handlers_to_invoke: List[Callable[[Any], None]] = []

        with self._lock:
            # Collect subscribers for this exact event class and any parent classes registered
            for registered_type, subs in self._subscribers.items():
                if issubclass(event_cls, registered_type):
                    for _, handler in subs:
                        handlers_to_invoke.append(handler)

        # Execute callbacks outside the lock
        for handler in handlers_to_invoke:
            try:
                handler(event)
            except Exception as e:
                clean_err = sanitize_error_message(str(e))
                clean_handler = sanitize_error_message(getattr(handler, "__name__", str(handler)))
                logger.error(
                    "Error executing subscriber %s for event %s: %s",
                    clean_handler,
                    event_cls.__name__,
                    clean_err,
                )
