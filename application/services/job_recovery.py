# ============================================================
#  application/services/job_recovery.py
# ============================================================

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.notifier import IApplicationEventPublisher
from application.events import (
    JobStateChangedEvent,
    MissedScheduleDetectedEvent,
    ScheduleUpdatedEvent,
)
from application.dto.job_dto import JobResponseDTO
from core.entities.job import Job, JobStatus
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.policies.retry_policy import RetryPolicy
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError


class JobRecoveryService:
    """
    Application service for startup crash reconciliation, missed-schedule reconciliation,
    and resuming/retrying interrupted jobs.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        event_publisher: Optional[IApplicationEventPublisher] = None,
    ):
        self.uow_factory = uow_factory
        self.event_publisher = event_publisher

    def reconcile_stale_jobs(self) -> int:
        """
        Startup crash recovery method. Finds all jobs left in PROCESSING state from previous
        interrupted runs, transitions them to PAUSED, and publishes JobStateChangedEvents
        after successful database commit.
        """
        recovered_job_ids: List[int] = []
        with self.uow_factory.create() as uow:
            if hasattr(uow.jobs, "list"):
                stale_jobs = uow.jobs.list(status=JobStatus.PROCESSING, limit=1000)
                recovered_job_ids = [j.id for j in stale_jobs]
            count = uow.jobs.reconcile_stale_jobs()
            uow.commit()

        if self.event_publisher and recovered_job_ids:
            for jid in recovered_job_ids:
                self.event_publisher.publish(
                    JobStateChangedEvent(
                        job_id=jid,
                        old_status=JobStatus.PROCESSING,
                        new_status=JobStatus.PAUSED,
                    )
                )

        return count

    def reconcile_missed_schedules(
        self,
        startup_time: Optional[datetime] = None,
        policy: Optional[str] = None,
    ) -> List[Job]:
        """
        Startup reconciliation for jobs whose scheduled_at passed while the application was closed.
        Applies AppSettings.missed_schedule_policy ('run_immediately', 'mark_paused', or 'prompt').
        """
        as_of = startup_time or datetime.now(timezone.utc)
        reconciled_records: List[Tuple[Job, Optional[datetime]]] = []
        effective_policy: str = "prompt"

        with self.uow_factory.create() as uow:
            configured_policy = policy
            if not configured_policy and hasattr(uow, "settings"):
                try:
                    configured_policy = uow.settings.get().missed_schedule_policy
                except Exception:
                    pass
            effective_policy = configured_policy or "prompt"

            if hasattr(uow.jobs, "get_missed_schedules"):
                missed_jobs = uow.jobs.get_missed_schedules(as_of=as_of)
            else:
                missed_jobs = []

            for job in missed_jobs:
                orig_sched = job.scheduled_at
                if effective_policy == "run_immediately":
                    job.scheduled_at = None
                    uow.jobs.save(job)
                    reconciled_records.append((job, orig_sched))
                elif effective_policy == "mark_paused":
                    JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
                    job.status = JobStatus.PAUSED
                    job.error_message = "Missed schedule while application was closed"
                    uow.jobs.save(job)
                    reconciled_records.append((job, orig_sched))
                elif effective_policy == "prompt":
                    # Remains PENDING in database, event emitted post-commit
                    reconciled_records.append((job, orig_sched))

            uow.commit()

        # Emit events strictly post-commit outside the transaction context
        if self.event_publisher and reconciled_records:
            for job, orig_sched in reconciled_records:
                if effective_policy == "run_immediately":
                    self.event_publisher.publish(
                        MissedScheduleDetectedEvent(
                            job_id=job.id,
                            file_name=job.file_name,
                            scheduled_at=orig_sched,
                            policy="run_immediately",
                        )
                    )
                    self.event_publisher.publish(
                        ScheduleUpdatedEvent(job_id=job.id, scheduled_at=None)
                    )
                elif effective_policy == "mark_paused":
                    self.event_publisher.publish(
                        JobStateChangedEvent(
                            job_id=job.id,
                            old_status=JobStatus.PENDING,
                            new_status=JobStatus.PAUSED,
                        )
                    )
                    self.event_publisher.publish(
                        MissedScheduleDetectedEvent(
                            job_id=job.id,
                            file_name=job.file_name,
                            scheduled_at=orig_sched,
                            policy="mark_paused",
                        )
                    )
                elif effective_policy == "prompt":
                    self.event_publisher.publish(
                        MissedScheduleDetectedEvent(
                            job_id=job.id,
                            file_name=job.file_name,
                            scheduled_at=orig_sched,
                            policy="prompt",
                        )
                    )

        return [job for job, _ in reconciled_records]

    def resume_job(self, job_id: int, user_id: int = 1, new_chain_ids: Optional[List[int]] = None) -> JobResponseDTO:
        """
        Resumes a PAUSED, FAILED, or CANCELLED job, re-queuing it as PENDING.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if job.status not in (JobStatus.PAUSED, JobStatus.FAILED, JobStatus.CANCELLED):
                raise DomainError(f"Job is in state '{job.status.value}' and cannot be resumed.")

            if new_chain_ids:
                chain = []
                for aid in new_chain_ids:
                    slot = uow.apis.get_by_id(aid)
                    if slot:
                        chain.append(slot)
                if chain:
                    job.api_chain = chain
                    job.current_api_index = 0

            old_st = job.status
            JobStateTransitionPolicy.validate_transition(old_st, JobStatus.PENDING)
            job.status = JobStatus.PENDING
            job.error_message = None
            job.cancel_requested = False

            uow.jobs.save(job)
            uow.commit()

        if self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(job_id=job.id, old_status=old_st, new_status=JobStatus.PENDING)
            )

        return JobResponseDTO(
            id=job.id,
            user_id=user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            auto_pipeline2=job.auto_pipeline2,
        )

    def retry_job(self, job_id: int, user_id: int = 1) -> JobResponseDTO:
        """
        Retries a failed job if retry limit has not been exceeded.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if not RetryPolicy.is_eligible_for_retry(job.retry_count):
                raise DomainError(f"Maximum retry attempts ({RetryPolicy.MAX_AUTO_RETRIES}) reached.")

            old_st = job.status
            JobStateTransitionPolicy.validate_transition(old_st, JobStatus.PENDING)
            job.status = JobStatus.PENDING
            job.retry_count += 1
            job.error_message = None
            job.cancel_requested = False

            uow.jobs.save(job)
            uow.commit()

        if self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(job_id=job.id, old_status=old_st, new_status=JobStatus.PENDING)
            )

        return JobResponseDTO(
            id=job.id,
            user_id=user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            auto_pipeline2=job.auto_pipeline2,
        )
