# ============================================================
#  application/services/schedule_service.py
# ============================================================

from datetime import datetime, timezone
from typing import Callable, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.notifier import IApplicationEventPublisher
from application.events import JobStateChangedEvent, ScheduleUpdatedEvent
from application.dto.job_dto import JobResponseDTO
from core.entities.job import JobStatus
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError


class ScheduleService:
    """
    Application service managing document job schedules.
    Provides use cases for rescheduling, clearing schedules, and acknowledging missed schedules.
    Delegates cancellation to JobExecutionService to ensure single-owner cancellation semantics.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        event_publisher: Optional[IApplicationEventPublisher] = None,
        runtime_wake_fn: Optional[Callable[[], None]] = None,
        execution_service: Optional[object] = None,
    ):
        self.uow_factory = uow_factory
        self.event_publisher = event_publisher
        self.runtime_wake_fn = runtime_wake_fn
        self.execution_service = execution_service

    def reschedule_job(
        self,
        job_id: int,
        new_scheduled_at: Optional[datetime],
        user_id: int = 1,
    ) -> JobResponseDTO:
        """
        Reschedules a pending or paused job to a new UTC execution time (or None for immediate queueing).
        If the job was paused, transitions it back to PENDING.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if job.status not in (JobStatus.PENDING, JobStatus.PAUSED):
                raise DomainError(f"Job in state '{job.status.value}' cannot be rescheduled.")

            # Validate UTC timestamp
            if new_scheduled_at is not None and new_scheduled_at.tzinfo is None:
                new_scheduled_at = new_scheduled_at.replace(tzinfo=timezone.utc)

            old_st = job.status
            job.scheduled_at = new_scheduled_at

            # If job was paused (e.g. from missed schedule), unpause back to PENDING
            if job.status == JobStatus.PAUSED:
                JobStateTransitionPolicy.validate_transition(old_st, JobStatus.PENDING)
                job.status = JobStatus.PENDING
                job.error_message = None

            uow.jobs.save(job)
            uow.commit()

        if old_st == JobStatus.PAUSED and self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(job_id=job.id, old_status=JobStatus.PAUSED, new_status=JobStatus.PENDING)
            )

        if self.event_publisher:
            self.event_publisher.publish(
                ScheduleUpdatedEvent(job_id=job.id, scheduled_at=new_scheduled_at)
            )

        # If rescheduled to immediate or due now, wake runtime dispatcher
        now_utc = datetime.now(timezone.utc)
        if (new_scheduled_at is None or new_scheduled_at <= now_utc) and self.runtime_wake_fn:
            self.runtime_wake_fn()

        return JobResponseDTO(
            id=job.id,
            user_id=user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            auto_pipeline2=job.auto_pipeline2,
        )

    def acknowledge_missed_schedule(
        self,
        job_id: int,
        action: str,
        new_scheduled_at: Optional[datetime] = None,
        user_id: int = 1,
    ) -> JobResponseDTO:
        """
        Processes user resolution for an unresolved missed schedule:
        - 'run_now': Clears scheduled_at, unpauses if paused, and queues for immediate execution.
        - 'reschedule': Sets new_scheduled_at and unpauses if paused.
        - 'cancel': Cooperatively cancels the job via JobExecutionService.
        """
        if action == "run_now":
            return self.reschedule_job(job_id, new_scheduled_at=None, user_id=user_id)
        elif action == "reschedule":
            if new_scheduled_at is None:
                raise DomainError("Action 'reschedule' requires new_scheduled_at parameter.")
            return self.reschedule_job(job_id, new_scheduled_at=new_scheduled_at, user_id=user_id)
        elif action == "cancel":
            # Canonical delegation to JobExecutionService to avoid duplicate cancellation logic
            if self.execution_service and hasattr(self.execution_service, "cancel_job"):
                self.execution_service.cancel_job(job_id)
            else:
                with self.uow_factory.create() as uow:
                    job = uow.jobs.get_by_id(job_id)
                    if not job:
                        raise EntityNotFoundError("Job", job_id)
                    if hasattr(uow.jobs, "request_cancellation"):
                        uow.jobs.request_cancellation(job_id)
                        uow.commit()

            with self.uow_factory.create() as uow:
                updated_job = uow.jobs.get_by_id(job_id)
                if not updated_job:
                    raise EntityNotFoundError("Job", job_id)

            return JobResponseDTO(
                id=updated_job.id,
                user_id=user_id,
                file_name=updated_job.file_name,
                status=updated_job.status.value,
                total_pages=updated_job.total_pages,
                processed_pages=updated_job.processed_pages,
                auto_pipeline2=updated_job.auto_pipeline2,
            )
        else:
            raise DomainError(f"Invalid missed schedule action '{action}'. Must be 'run_now', 'reschedule', or 'cancel'.")
