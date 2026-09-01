# ============================================================
#  interfaces/desktop/bridge.py
#  Thread-Safe Qt Signal Event Bridge
# ============================================================

from typing import Any, Optional
from interfaces.desktop.qt_compat import QObject, Signal
from application.ports.notifier import IApplicationEventPublisher
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
    MissedScheduleDetectedEvent,
    ScheduleUpdatedEvent,
)


class QtSignalEventBridge(QObject):
    """
    Thread-safe Qt signal bridge adapting application events to Qt Queued Signals.
    Guarantees that background events from InMemoryEventBus are delivered to
    QObject controllers and ViewModels strictly on the Qt GUI Main Thread.
    """

    job_progress_received = Signal(int, int, int, float)       # job_id, processed, total, pct
    api_switch_received = Signal(int, str, str, str, int)      # job_id, old_label, new_label, reason, page
    job_completed_received = Signal(int, str)                  # job_id, output_uri
    job_failed_received = Signal(int, str, bool)               # job_id, error_message, is_retryable
    job_cancelled_received = Signal(int)                       # job_id
    job_state_changed_received = Signal(int, str, str)         # job_id, old_status, new_status
    missed_schedule_received = Signal(int, str, str, str)      # job_id, filename, sched_iso, policy
    schedule_updated_received = Signal(int, str)               # job_id, sched_iso

    def __init__(self, event_bus: Optional[IApplicationEventPublisher] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._event_bus: Optional[IApplicationEventPublisher] = None
        self._attached: bool = False
        if event_bus is not None:
            self.attach(event_bus)

    @property
    def is_attached(self) -> bool:
        return self._attached

    def attach(self, event_bus: IApplicationEventPublisher) -> None:
        """Subscribes the bridge to receive typed application events."""
        if self._attached:
            return
        self._event_bus = event_bus
        if hasattr(self._event_bus, "subscribe"):
            self._event_bus.subscribe(object, self._on_event)
        self._attached = True

    def detach(self) -> None:
        """Unsubscribes the bridge to prevent event delivery to tearing-down QObjects."""
        if not self._attached or not self._event_bus:
            return
        if hasattr(self._event_bus, "unsubscribe"):
            self._event_bus.unsubscribe(self._on_event)
        self._attached = False
        self._event_bus = None

    def _on_event(self, event: Any) -> None:
        """
        Receives events (potentially from background worker threads) and emits Qt signals.
        Qt meta-object system automatically routes to connected GUI thread slots via QueuedConnection.
        """
        if not self._attached:
            return

        if isinstance(event, JobProgressEvent):
            self.job_progress_received.emit(
                event.job_id, event.processed_pages, event.total_pages, float(event.percent)
            )
        elif isinstance(event, ApiSwitchEvent):
            self.api_switch_received.emit(
                event.job_id, event.old_label, event.new_label, event.reason, event.page
            )
        elif isinstance(event, JobCompletedEvent):
            self.job_completed_received.emit(event.job_id, event.output_artifact_uri)
        elif isinstance(event, JobFailedEvent):
            self.job_failed_received.emit(event.job_id, event.error_message, event.is_retryable)
        elif isinstance(event, JobCancelledEvent):
            self.job_cancelled_received.emit(event.job_id)
        elif isinstance(event, JobStateChangedEvent):
            old_st_val = event.old_status.value if hasattr(event.old_status, "value") else str(event.old_status)
            new_st_val = event.new_status.value if hasattr(event.new_status, "value") else str(event.new_status)
            self.job_state_changed_received.emit(event.job_id, old_st_val, new_st_val)
        elif isinstance(event, MissedScheduleDetectedEvent):
            sched_str = event.scheduled_at.isoformat() if event.scheduled_at else ""
            self.missed_schedule_received.emit(event.job_id, event.file_name, sched_str, event.policy)
        elif isinstance(event, ScheduleUpdatedEvent):
            sched_str = event.scheduled_at.isoformat() if event.scheduled_at else ""
            self.schedule_updated_received.emit(event.job_id, sched_str)
