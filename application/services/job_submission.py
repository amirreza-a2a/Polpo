# ============================================================
#  application/services/job_submission.py
# ============================================================

import logging
from typing import List, Optional
from application.dto.job_dto import SubmitJobCommand, JobResponseDTO
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IApplicationEventPublisher
from application.events import JobStateChangedEvent
from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.prompt import PromptType
from core.entities.artifact import ArtifactType
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError
from core.ai.exceptions import AIChainExhaustedError

logger = logging.getLogger("application.services.job_submission")


class JobSubmissionService:
    """
    Application service for validating, ingesting, and submitting PDF conversion jobs.
    Guarantees immutable local source document storage before committing PENDING job records.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
        event_publisher: Optional[IApplicationEventPublisher] = None,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor
        self.event_publisher = event_publisher

    def submit_job(self, cmd: SubmitJobCommand) -> JobResponseDTO:
        # 1. Calculate PDF page count
        try:
            total_pages = self.doc_processor.get_page_count(cmd.file_bytes)
        except Exception as e:
            raise DomainError(f"Failed to parse PDF document: {e}")

        if total_pages <= 0:
            raise DomainError("PDF document contains no renderable pages.")

        # 2. Ingest source document into immutable artifact storage first
        artifact_handle = None
        try:
            artifact_handle = self.storage.store(
                job_id=None,
                artifact_type=ArtifactType.SOURCE_PDF,
                filename=cmd.filename,
                data=cmd.file_bytes,
                mime_type="application/pdf",
            )
        except Exception as e:
            raise DomainError(f"Failed to ingest source artifact into storage: {e}")

        # 3. Resolve prompts and API chain and persist Job in SQLite
        try:
            with self.uow_factory.create() as uow:
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

                job = Job(
                    id=None,
                    file_name=cmd.filename,
                    file_path=artifact_handle.uri,
                    total_pages=total_pages,
                    processed_pages=0,
                    status=JobStatus.PENDING,
                    prompt_id=prompt_id,
                    prompt_text=prompt_text,
                    api_chain=chain,
                    current_api_index=0,
                    api_switch_log=[],
                    auto_pipeline2=cmd.auto_pipeline2,
                    pipeline2_prompt_id=cmd.pipeline2_prompt_id,
                    scheduled_at=getattr(cmd, "scheduled_at", None),
                )
                saved_job = uow.jobs.save(job)
                uow.commit()

        except Exception:
            # Clean up the exact ingested artifact file if database persistence failed
            if artifact_handle:
                try:
                    self.storage.delete(artifact_handle)
                except Exception as clean_err:
                    logger.warning("Failed to clean up source artifact on failed job commit: %s", clean_err)
            raise

        # 4. Emit event outside active database transaction
        if self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(
                    job_id=saved_job.id,
                    old_status=None,
                    new_status=JobStatus.PENDING,
                )
            )

        return self._to_response_dto(saved_job, user_id=cmd.user_id)

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

            prompt_text = ""
            if prompt_id:
                p = uow.prompts.get_by_id(prompt_id)
                if p:
                    prompt_text = p.text
            if not prompt_text:
                default_p = uow.prompts.get_default(PromptType.PIPELINE_2)
                prompt_text = default_p.text if default_p else "Refine and structure markdown content."

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

        if self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(
                    job_id=saved_p2.id,
                    old_status=None,
                    new_status=JobStatus.PENDING,
                )
            )

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
