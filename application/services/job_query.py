# ============================================================
#  application/services/job_query.py
#  Job Query Application Service (CQRS Query Boundary)
# ============================================================

import math
from typing import List, Optional, Tuple
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.dto.job_dto import JobResponseDTO, JobDetailDTO
from core.entities.job import Job, JobStatus
from core.exceptions.domain_exceptions import EntityNotFoundError


class JobQueryService:
    """
    سرویس تخصصی پرس‌وجوی وضعیت، تاریخچه و صف کارها در لایه کاربرد (جداسازی Query از Mutation).
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def list_user_jobs(self, user_id: int, limit: int = 50, offset: int = 0) -> List[JobResponseDTO]:
        with self.uow_factory.create() as uow:
            jobs = uow.jobs.list_by_user(user_id, limit, offset)
            return [self._to_response_dto(j) for j in jobs]

    def get_job_detail(self, job_id: int, user_id: int) -> JobDetailDTO:
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job or job.user_id != user_id:
                raise EntityNotFoundError("Job", job_id)
            return self._to_detail_dto(job)

    def get_user_pending_job_count(self, user_id: int) -> int:
        with self.uow_factory.create() as uow:
            return uow.jobs.count_by_user(user_id, status=JobStatus.PENDING)

    def get_user_job_count(self, user_id: int) -> int:
        with self.uow_factory.create() as uow:
            return uow.jobs.count_by_user(user_id)

    def get_queue_position(self, job_id: int) -> int:
        with self.uow_factory.create() as uow:
            return uow.jobs.get_queue_position(job_id)

    def get_today_stats(self) -> dict:
        with self.uow_factory.create() as uow:
            return uow.jobs.get_today_stats()


    def get_paginated_history(
        self, user_id: int, page: int = 1, per_page: int = 5
    ) -> Tuple[List[JobDetailDTO], int, int]:
        """
        دریافت تاریخچه صفحه‌بندی‌شده کارهای کاربر همراه با تعداد کل و صفحات.
        خروجی: (لیست_کارها, تعداد_کل, کل_صفحات)
        """
        page = max(1, page)
        offset = (page - 1) * per_page

        with self.uow_factory.create() as uow:
            total_jobs = uow.jobs.count_by_user(user_id)
            total_pages = max(1, math.ceil(total_jobs / per_page))
            jobs = uow.jobs.list_by_user(user_id, limit=per_page, offset=offset)

            dtos = [self._to_detail_dto(j) for j in jobs]
            return dtos, total_jobs, total_pages

    def _to_response_dto(self, job: Job) -> JobResponseDTO:
        return JobResponseDTO(
            id=job.id,
            user_id=job.user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            auto_pipeline2=job.auto_pipeline2,
            created_at=job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
        )

    def _to_detail_dto(self, job: Job) -> JobDetailDTO:
        active_label = job.current_api.label if job.current_api else None
        return JobDetailDTO(
            id=job.id,
            user_id=job.user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            prompt_id=job.prompt_id,
            prompt_text=job.prompt_text,
            active_api_label=active_label,
            api_switch_log=job.api_switch_log,
            output_path=job.output_path,
            error_message=job.error_message,
            auto_pipeline2=job.auto_pipeline2,
            created_at=job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
            updated_at=job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
        )
