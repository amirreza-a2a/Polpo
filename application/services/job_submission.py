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
from core.exceptions.domain_exceptions import (
    EntityNotFoundError,
    DomainError,
)
from core.ai.exceptions import AIChainExhaustedError


class JobSubmissionService:
    """
    Application service for validating and submitting PDF document conversion jobs.
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
        # 1. Calculate PDF page count
        try:
            total_pages = self.doc_processor.get_page_count(cmd.file_bytes)
        except Exception as e:
            raise DomainError(f"Failed to parse PDF document: {e}")

        if total_pages <= 0:
            raise DomainError("PDF document contains no renderable pages.")

        job_id_created: Optional[int] = None

        try:
            with self.uow_factory.create() as uow:
                # 2. Optional user resolution for legacy callers
                user = uow.users.get_by_id(cmd.user_id) if hasattr(uow, "users") and uow.users else None

                # 3. Prompt resolution
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

                # 4. API Chain resolution
                chain = []
                if cmd.api_chain_ids:
                    for aid in cmd.api_chain_ids:
                        slot = uow.apis.get_by_id(aid)
                        if slot:
                            chain.append(slot)
                else:
                    if hasattr(uow.apis, "list_all"):
                        chain = uow.apis.list_all()
                    else:
                        chain = uow.apis.list_by_user(cmd.user_id, include_public=True)

                if not chain:
                    raise AIChainExhaustedError("No API slots configured or available.")

                # 5. Create initial job entity
                job = Job(
                    id=None,
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
                    auto_pipeline2=cmd.auto_pipeline2 or (user.preferences.auto_pipeline2 if user and hasattr(user, "preferences") else False),
                    pipeline2_prompt_id=cmd.pipeline2_prompt_id or (user.preferences.default_pipeline2_prompt_id if user and hasattr(user, "preferences") else None),
                )
                saved_job = uow.jobs.save(job)
                job_id_created = saved_job.id

                # 6. Ingest source document into artifact storage
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

                return self._to_response_dto(saved_job, user_id=cmd.user_id)

        except Exception as e:
            if job_id_created is not None:
                self.storage.cleanup_job_artifacts(job_id_created)
            raise e

    def submit_pipeline2_job(
        self,
        source_job_id: int,
        user_id: int = 1,
        prompt_id: Optional[int] = None,
        api_chain_ids: Optional[List[int]] = None,
    ) -> int:
        with self.uow_factory.create() as uow:
            source_job = uow.jobs.get_by_id(source_job_id)
            if not source_job:
                raise EntityNotFoundError("Job", source_job_id)

            if source_job.status != JobStatus.DONE:
                raise DomainError("Source job is not completed yet.")

            # Resolve Pipeline 2 prompt
            prompt_text = ""
            if prompt_id:
                p = uow.prompts.get_by_id(prompt_id)
                if p:
                    prompt_text = p.text
            if not prompt_text:
                default_p = uow.prompts.get_default(PromptType.PIPELINE_2)
                prompt_text = default_p.text if default_p else "Refine and structure markdown content."

            # Resolve API chain
            if api_chain_ids:
                chain = []
                for aid in api_chain_ids:
                    slot = uow.apis.get_by_id(aid)
                    if slot:
                        chain.append(slot)
            else:
                if hasattr(uow.apis, "list_all"):
                    chain = uow.apis.list_all()
                else:
                    chain = uow.apis.list_by_user(user_id, include_public=True)

            if not chain:
                raise AIChainExhaustedError("No API slots available for Pipeline 2.")

            from core.entities.job import Pipeline2Job
            p2_job = Pipeline2Job(
                id=None,
                source_job_id=source_job_id,
                prompt_id=prompt_id,
                prompt_text=prompt_text,
                api_chain=chain,
                status=JobStatus.PENDING,
                input_path=source_job.output_path,
            )
            saved_p2 = uow.pipeline2_jobs.save(p2_job)
            uow.commit()
            return saved_p2.id

    def _to_response_dto(self, job: Job, user_id: int = 1) -> JobResponseDTO:
        return JobResponseDTO(
            id=job.id,
            user_id=user_id,
            file_name=job.file_name,
            status=job.status.value,
            total_pages=job.total_pages,
            processed_pages=job.processed_pages,
            auto_pipeline2=job.auto_pipeline2,
            created_at=job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
        )
