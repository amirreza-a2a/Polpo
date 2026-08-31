# ============================================================
#  core/policies/job_state_policy.py
# ============================================================

from typing import Set
from core.entities.job import JobStatus


class InvalidStateTransitionError(Exception):
    """خطای تغییر وضعیت نامعتبر کار."""
    pass


class JobStateTransitionPolicy:
    """
    سیاست رسمی ماشین وضعیت کارها (State Machine).
    قوانین مجاز بودن گذارهای وضعیت را در لایه دامنه تعریف می‌کند.
    """

    ALLOWED_TRANSITIONS: dict[JobStatus, Set[JobStatus]] = {
        JobStatus.PENDING: {JobStatus.PROCESSING, JobStatus.FAILED},
        JobStatus.PROCESSING: {JobStatus.DONE, JobStatus.PAUSED, JobStatus.FAILED},
        JobStatus.PAUSED: {JobStatus.PENDING, JobStatus.FAILED},
        JobStatus.FAILED: {JobStatus.PENDING},
        JobStatus.DONE: set(),  # Done یک وضعیت نهایی و غیرقابل تغییر برای همان Job ID است.
    }

    @classmethod
    def validate_transition(cls, current_status: JobStatus, target_status: JobStatus) -> None:
        """بررسی مجاز بودن تغییر وضعیت، در صورت نامعتبر بودن استثنا پرتاب می‌شود."""
        if current_status == target_status:
            return

        allowed = cls.ALLOWED_TRANSITIONS.get(current_status, set())
        if target_status not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid job state transition from '{current_status.value}' to '{target_status.value}'"
            )

    @classmethod
    def can_transition(cls, current_status: JobStatus, target_status: JobStatus) -> bool:
        """بررسی مجاز بودن تغییر وضعیت بدون پرتاب استثنا."""
        if current_status == target_status:
            return True
        return target_status in cls.ALLOWED_TRANSITIONS.get(current_status, set())
