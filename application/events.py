# ============================================================
#  application/events.py
# ============================================================

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from core.entities.job import JobStatus


@dataclass(frozen=True)
class JobProgressEvent:
    """Event emitted when document processing progress updates."""
    job_id: int
    processed_pages: int
    total_pages: int
    percent: float


@dataclass(frozen=True)
class ApiSwitchEvent:
    """Event emitted when AI provider fallback switches to another API slot."""
    job_id: int
    old_label: str
    new_label: str
    reason: str
    page: int


@dataclass(frozen=True)
class JobCompletedEvent:
    """Event emitted when document processing completes and final output artifact is stored."""
    job_id: int
    output_artifact_uri: str


@dataclass(frozen=True)
class JobFailedEvent:
    """Event emitted when a job encounters a terminal failure."""
    job_id: int
    error_message: str
    is_retryable: bool = False


@dataclass(frozen=True)
class JobCancelledEvent:
    """Event emitted when a job is cooperatively cancelled."""
    job_id: int


@dataclass(frozen=True)
class JobStateChangedEvent:
    """Event emitted when a job transitions between lifecycle states."""
    job_id: int
    old_status: Optional[JobStatus]
    new_status: JobStatus


@dataclass(frozen=True)
class MissedScheduleDetectedEvent:
    """Event emitted when a job's scheduled execution time passed while the application was closed."""
    job_id: int
    file_name: str
    scheduled_at: Optional[datetime]
    policy: str


@dataclass(frozen=True)
class ScheduleUpdatedEvent:
    """Event emitted when a job's scheduled execution time is set, updated, or cleared."""
    job_id: int
    scheduled_at: Optional[datetime]
