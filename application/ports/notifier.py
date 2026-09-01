# ============================================================
#  application/ports/notifier.py
# ============================================================

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IApplicationEventPublisher(Protocol):
    """
    Application event publisher port.
    Desktop ViewModels, controllers, or event sinks subscribe to this boundary.
    """
    def publish(self, event: Any) -> None:
        ...


class IProgressNotifier(ABC):
    """
    Progress notification compatibility port for legacy notification flows.
    """

    @abstractmethod
    def notify_progress(self, user_id: int, job_id: int, processed_pages: int, total_pages: int) -> None:
        pass

    @abstractmethod
    def notify_api_switch(
        self,
        user_id: int,
        job_id: int,
        old_label: str,
        new_label: str,
        reason: str,
        page: int,
    ) -> None:
        pass

    @abstractmethod
    def notify_job_completed(self, user_id: int, job_id: int, output_handle_uri: str) -> None:
        pass

    @abstractmethod
    def notify_job_failed(self, user_id: int, job_id: int, error_message: str) -> None:
        pass


class ProgressNotifierEventAdapter(IProgressNotifier):
    """
    Compatibility adapter mapping legacy IProgressNotifier calls to typed IApplicationEventPublisher events.
    """

    def __init__(self, publisher: IApplicationEventPublisher):
        self.publisher = publisher

    def notify_progress(self, user_id: int, job_id: int, processed_pages: int, total_pages: int) -> None:
        from application.events import JobProgressEvent
        pct = (processed_pages / total_pages * 100.0) if total_pages > 0 else 0.0
        self.publisher.publish(JobProgressEvent(job_id=job_id, processed_pages=processed_pages, total_pages=total_pages, percent=pct))

    def notify_api_switch(self, user_id: int, job_id: int, old_label: str, new_label: str, reason: str, page: int) -> None:
        from application.events import ApiSwitchEvent
        self.publisher.publish(ApiSwitchEvent(job_id=job_id, old_label=old_label, new_label=new_label, reason=reason, page=page))

    def notify_job_completed(self, user_id: int, job_id: int, output_handle_uri: str) -> None:
        from application.events import JobCompletedEvent
        self.publisher.publish(JobCompletedEvent(job_id=job_id, output_artifact_uri=output_handle_uri))

    def notify_job_failed(self, user_id: int, job_id: int, error_message: str) -> None:
        from application.events import JobFailedEvent
        self.publisher.publish(JobFailedEvent(job_id=job_id, error_message=error_message))


class EventPublisherNotifierAdapter:
    """
    Boundary adapter mapping typed IApplicationEventPublisher events to legacy IProgressNotifier calls.
    """

    def __init__(self, notifier: IProgressNotifier):
        self.notifier = notifier

    def publish(self, event: Any) -> None:
        from application.events import (
            JobProgressEvent,
            ApiSwitchEvent,
            JobCompletedEvent,
            JobFailedEvent,
        )
        if isinstance(event, JobProgressEvent):
            if hasattr(self.notifier, "notify_progress"):
                self.notifier.notify_progress(1, event.job_id, event.processed_pages, event.total_pages)
        elif isinstance(event, ApiSwitchEvent):
            if hasattr(self.notifier, "notify_api_switch"):
                self.notifier.notify_api_switch(1, event.job_id, event.old_label, event.new_label, event.reason, event.page)
        elif isinstance(event, JobCompletedEvent):
            if hasattr(self.notifier, "notify_job_completed"):
                self.notifier.notify_job_completed(1, event.job_id, event.output_artifact_uri)
        elif isinstance(event, JobFailedEvent):
            if hasattr(self.notifier, "notify_job_failed"):
                self.notifier.notify_job_failed(1, event.job_id, event.error_message)
