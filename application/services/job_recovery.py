# ============================================================
#  application/services/job_recovery.py
# ============================================================

from typing import List, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.notifier import IApplicationEventPublisher
from application.events import JobStateChangedEvent
from application.dto.job_dto import JobResponseDTO
from core.entities.job import JobStatus
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.policies.retry_policy import RetryPolicy
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError


class JobRecoveryService:
    """
    Application service for startup crash reconciliation and resuming/retrying interrupted jobs.
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
