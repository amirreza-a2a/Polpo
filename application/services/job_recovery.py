# ============================================================
#  application/services/job_recovery.py
# ============================================================

from typing import List, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.dto.job_dto import JobResponseDTO
from core.entities.job import JobStatus
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.policies.retry_policy import RetryPolicy
from core.exceptions.domain_exceptions import (
    EntityNotFoundError,
    DomainError,
)


class JobRecoveryService:
    """
    Application service for resuming and retrying paused or failed jobs.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def resume_job(self, job_id: int, user_id: int = 1, new_chain_ids: Optional[List[int]] = None) -> JobResponseDTO:
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if job.status not in (JobStatus.PAUSED, JobStatus.FAILED):
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

            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PENDING)
            job.status = JobStatus.PENDING
            job.error_message = None

            uow.jobs.save(job)
            uow.commit()

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
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if not RetryPolicy.is_eligible_for_retry(job.retry_count):
                raise DomainError(f"Maximum retry attempts ({RetryPolicy.MAX_AUTO_RETRIES}) reached.")

            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PENDING)
            job.status = JobStatus.PENDING
            job.retry_count += 1
            job.error_message = None

            uow.jobs.save(job)
            uow.commit()

            return JobResponseDTO(
                id=job.id,
                user_id=user_id,
                file_name=job.file_name,
                status=job.status.value,
                total_pages=job.total_pages,
                processed_pages=job.processed_pages,
                auto_pipeline2=job.auto_pipeline2,
            )
