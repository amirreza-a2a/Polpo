# ============================================================
#  application/services/job_submission.py
# ============================================================

from typing import List, Optional
from application.dto.job_dto import SubmitJobCommand, JobResponseDTO, JobDetailDTO
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from application.ports.document_processor import IDocumentProcessor
from core.entities.job import Job, JobStatus
from core.entities.prompt import PromptType
from core.entities.artifact import ArtifactType
from core.policies.quota_policy import QuotaPolicy
from core.exceptions.domain_exceptions import (
    EntityNotFoundError,
    QuotaExceededError,
    DomainError,
)
from core.ai.exceptions import AIChainExhaustedError


class JobSubmissionService:
    """
    سرویس ثبت و پرس‌وجوی کارهای پردازش سند PDF.
    مرز کامل استفاده از Use Caseها را جهت استقلال کامل لایه REST فراهم می‌کند.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor

    def submit_job(self, cmd: SubmitJobCommand) -> JobResponseDTO:
        # 1. محاسبه صفحات PDF
        try:
            total_pages = self.doc_processor.get_page_count(cmd.file_bytes)
        except Exception as e:
            raise DomainError(f"Failed to parse PDF document: {e}")

        if total_pages <= 0:
            raise DomainError("PDF document contains no renderable pages.")

        job_id_created: Optional[int] = None

        try:
            with self.uow_factory.create() as uow:
                # 2. بررسی کاربر و سهمیه
                user = uow.users.get_by_id(cmd.user_id)
                if not user:
                    raise EntityNotFoundError("User", cmd.user_id)

                if QuotaPolicy.should_reset_quota(user.quota.last_active_date):
                    uow.users.reset_daily_quota(user.id)
                    user.quota.daily_pages_used = 0

                if not QuotaPolicy.can_consume(user.quota, 1):
                    raise QuotaExceededError("Daily page limit reached.")

                # 3. حل پرامپت
                prompt_text = cmd.prompt_text
                prompt_id = cmd.prompt_id
                if not prompt_text:
                    if prompt_id:
                        p_entity = uow.prompts.get_by_id(prompt_id)
                        if p_entity:
                            prompt_text = p_entity.text
                    else:
                        def_prompt = uow.prompts.get_default(PromptType.PIPELINE_1)
                        if def_prompt:
                            prompt_id = def_prompt.id
                            prompt_text = def_prompt.text

                if not prompt_text:
                    raise DomainError("No conversion prompt available.")

                # 4. حل زنجیره API
                chain = []
                if cmd.api_chain_ids:
                    for aid in cmd.api_chain_ids:
                        slot = uow.apis.get_by_id(aid, "private") or uow.apis.get_by_id(aid, "public")
                        if slot:
                            chain.append(slot)
                else:
                    chain = uow.apis.list_by_user(user.id, include_public=user.preferences.use_public_fallback)

                if not chain:
                    raise AIChainExhaustedError("No API slots configured or available for this user.")

                # 5. ایجاد رکورد اولیه در دیتابیس
                job = Job(
                    id=None,
                    user_id=user.id,
                    file_name=cmd.filename,
                    file_path="",
                    total_pages=total_pages,
                    processed_pages=0,
                    status=JobStatus.PENDING,
                    prompt_id=prompt_id,
                    prompt_text=prompt_text,
                    api_chain=chain,
                    current_api_index=0,
                    api_switch_log=[],
                    auto_pipeline2=cmd.auto_pipeline2 or user.preferences.auto_pipeline2,
                    pipeline2_prompt_id=cmd.pipeline2_prompt_id or user.preferences.default_pipeline2_prompt_id,
                )
                saved_job = uow.jobs.save(job)
                job_id_created = saved_job.id

                # 6. ذخیره‌سازی فایل مبدأ در مخزن آرتیفکت
                artifact_handle = self.storage.store(
                    job_id=saved_job.id,
                    artifact_type=ArtifactType.SOURCE_PDF,
                    filename=cmd.filename,
                    data=cmd.file_bytes,
                    mime_type="application/pdf",
                )

                saved_job.file_path = artifact_handle.uri
                uow.jobs.save(saved_job)
                uow.commit()

                return self._to_response_dto(saved_job)

        except Exception as e:
            if job_id_created is not None:
                self.storage.cleanup_job_artifacts(job_id_created)
            raise e

    def list_user_jobs(self, user_id: int, limit: int = 50, offset: int = 0) -> List[JobResponseDTO]:
        with self.uow_factory.create() as uow:
            jobs = uow.jobs.list_by_user(user_id, limit, offset)
            return [self._to_response_dto(j) for j in jobs]

    def get_job_detail(self, job_id: int, user_id: int) -> JobDetailDTO:
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job or job.user_id != user_id:
                raise EntityNotFoundError("Job", job_id)

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
