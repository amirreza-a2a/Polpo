# ============================================================
#  core/policies/job_state_policy.py
# ============================================================

from typing import Set
from core.entities.job import JobStatus


class InvalidStateTransitionError(Exception):
    """Raised when an invalid job lifecycle state transition is attempted."""
    pass


class JobStateTransitionPolicy:
    """
    Formal state transition policy for document processing jobs.
    Defines permitted state transitions in the domain layer.
    """

    ALLOWED_TRANSITIONS: dict[JobStatus, Set[JobStatus]] = {
        JobStatus.PENDING: {JobStatus.PROCESSING, JobStatus.FAILED, JobStatus.CANCELLED},
        JobStatus.PROCESSING: {JobStatus.DONE, JobStatus.PAUSED, JobStatus.FAILED, JobStatus.CANCELLED},
        JobStatus.PAUSED: {JobStatus.PENDING, JobStatus.FAILED, JobStatus.CANCELLED},
        JobStatus.FAILED: {JobStatus.PENDING, JobStatus.CANCELLED},
        JobStatus.CANCELLED: {JobStatus.PENDING},
        JobStatus.DONE: set(),  # DONE is a terminal state with no outgoing transitions.
    }

    @classmethod
    def validate_transition(cls, current_status: JobStatus, target_status: JobStatus) -> None:
        """Validates whether a state transition is legal, raising InvalidStateTransitionError if not."""
        if current_status == target_status:
            return

        allowed = cls.ALLOWED_TRANSITIONS.get(current_status, set())
        if target_status not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid job state transition from '{current_status.value}' to '{target_status.value}'"
            )

    @classmethod
    def can_transition(cls, current_status: JobStatus, target_status: JobStatus) -> bool:
        """Checks whether a state transition is legal without raising an exception."""
        if current_status == target_status:
            return True
        return target_status in cls.ALLOWED_TRANSITIONS.get(current_status, set())
