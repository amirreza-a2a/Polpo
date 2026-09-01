# ============================================================
#  application/services/job_execution.py
# ============================================================

import logging
from datetime import datetime
from typing import Any, List, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IApplicationEventPublisher, IProgressNotifier
from application.ports.ai_executor import IAIExecutionService
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobCompletedEvent,
    JobFailedEvent,
)
from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.exceptions.domain_exceptions import ArtifactNotFoundError

logger = logging.getLogger("polpot.execution")


class JobExecutionService:
    """
    Application orchestrator for executing document processing jobs.
    Coordinates page rendering, AI OCR vision execution, image cropping, and Markdown artifact generation.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
        ai_executor: IAIExecutionService,
        event_publisher: Optional[Any] = None,
        notifier: Optional[Any] = None,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor
        self.ai_executor = ai_executor

        pub_candidate = event_publisher if event_publisher is not None else notifier
        if pub_candidate is not None:
            if hasattr(pub_candidate, "notify_progress") or hasattr(pub_candidate, "notify_job_completed"):
                from application.ports.notifier import EventPublisherNotifierAdapter
                self.publisher: Optional[IApplicationEventPublisher] = EventPublisherNotifierAdapter(pub_candidate)
            else:
                self.publisher = pub_candidate
        else:
            self.publisher = None

    def execute_next_job(self) -> Optional[Job]:
        # 1. Fetch next pending job from queue
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_next_pending()
            if not job:
                return None

            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PROCESSING)
            job.status = JobStatus.PROCESSING
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        logger.info(f"[Job {job.id}] Started execution. Total pages: {job.total_pages}")

        # 2. Retrieve source file from artifact storage
        try:
            source_handle = ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri=job.file_path,
                artifact_type=ArtifactType.SOURCE_PDF,
                job_id=job.id,
                filename=job.file_name,
            )
            pdf_bytes = self.storage.retrieve(source_handle)
        except (ArtifactNotFoundError, IOError, OSError) as e:
            logger.error(f"[Job {job.id}] Failed to load source artifact: {e}")
            with self.uow_factory.create() as uow:
                uow.jobs.update_status(job.id, JobStatus.FAILED, error_message=f"Source artifact missing: {e}")
                uow.commit()
            self._publish_failed(job.id, f"Source artifact missing: {e}")
            return job

        # 3. Page processing loop
        accumulated_markdown: List[str] = []
        start_page = job.processed_pages + 1

        for page_num in range(start_page, job.total_pages + 1):
            # Render page to JPEG
            try:
                page_jpeg_bytes = self.doc_processor.render_page_to_jpeg(pdf_bytes, page_num)
                self.storage.store(
                    job_id=job.id,
                    artifact_type=ArtifactType.PAGE_IMAGE,
                    filename=f"page_{page_num}.jpg",
                    data=page_jpeg_bytes,
                    mime_type="image/jpeg",
                )
            except (IOError, OSError, IndexError) as e:
                logger.error(f"[Job {job.id}] Rendering failed at page {page_num}: {e}")
                self._fail_job(job, f"Page {page_num} render failed: {e}")
                return job

            # Callback for recording API switch events
            def on_switch_callback(old_label: str, new_label: str, reason: str, page: int):
                event = {
                    "from_api": old_label,
                    "to_api": new_label,
                    "page": page,
                    "reason": reason,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                job.api_switch_log.append(event)
                with self.uow_factory.create() as uow:
                    uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log)
                    uow.commit()
                self._publish_api_switch(job, old_label, new_label, reason, page)

            # Send to AI executor with fallback chain starting at active slot
            active_chain = job.api_chain[job.current_api_index:]
            raw_page_md, used_slot = self.ai_executor.execute_vision_with_fallback(
                chain=active_chain,
                image_bytes=page_jpeg_bytes,
                prompt=job.prompt_text or "Convert page to markdown",
                at_page=page_num,
                mime_type="image/jpeg",
                on_switch=on_switch_callback,
            )

            if raw_page_md is None:
                self._pause_job(job, f"All APIs exhausted at page {page_num}.")
                return job

            # Update active slot index on job
            if used_slot:
                for idx, slot in enumerate(job.api_chain):
                    if slot.id == used_slot.id and slot.slot_type == used_slot.slot_type:
                        job.current_api_index = idx
                        break

            # Extract and crop images
            final_page_md, cropped_images = self.doc_processor.extract_and_crop_images(
                markdown_text=raw_page_md,
                page_jpeg_bytes=page_jpeg_bytes,
                job_id=job.id,
            )
            for crop_name, crop_bytes in cropped_images:
                self.storage.store(
                    job_id=job.id,
                    artifact_type=ArtifactType.CROPPED_IMAGE,
                    filename=crop_name,
                    data=crop_bytes,
                    mime_type="image/jpeg",
                )

            accumulated_markdown.append(f"<!-- Page {page_num} -->\n{final_page_md}\n")
            job.processed_pages = page_num

            # Save progress in database
            with self.uow_factory.create() as uow:
                uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log)
                if used_slot:
                    uow.apis.report_pages_used(used_slot.id, used_slot.slot_type, 1)
                uow.commit()

            self._publish_progress(job)

        # 4. Finalize job and store integrated Markdown artifact
        full_document = "\n\n".join(accumulated_markdown)
        output_handle = self.storage.store(
            job_id=job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=f"output_{job.id}.md",
            data=full_document.encode("utf-8"),
            mime_type="text/markdown",
        )

        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.DONE)
            job.status = JobStatus.DONE
            job.output_path = output_handle.uri
            uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log, output_path=output_handle.uri)
            uow.jobs.update_status(job.id, JobStatus.DONE)

            if job.auto_pipeline2:
                p2_job = Pipeline2Job(
                    id=None,
                    source_job_id=job.id,
                    prompt_id=job.pipeline2_prompt_id,
                    status=JobStatus.PENDING,
                    input_path=output_handle.uri,
                    api_chain=job.api_chain,
                    current_api_index=0,
                )
                uow.pipeline2_jobs.save(p2_job)

            uow.commit()

        self._publish_completed(job.id, output_handle.uri)
        return job

    def _pause_job(self, job: Job, error_msg: str) -> None:
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
            job.status = JobStatus.PAUSED
            job.error_message = error_msg
            uow.jobs.update_status(job.id, JobStatus.PAUSED, error_message=error_msg)
            uow.commit()
        self._publish_failed(job.id, error_msg)

    def _fail_job(self, job: Job, error_msg: str) -> None:
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.FAILED)
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            uow.jobs.update_status(job.id, JobStatus.FAILED, error_message=error_msg)
            uow.commit()
        self._publish_failed(job.id, error_msg)

    def execute_next_pipeline2_job(self) -> Optional[Pipeline2Job]:
        with self.uow_factory.create() as uow:
            p2_job = uow.pipeline2_jobs.get_next_pending()
            if not p2_job:
                return None

            JobStateTransitionPolicy.validate_transition(p2_job.status, JobStatus.PROCESSING)
            p2_job.status = JobStatus.PROCESSING
            uow.pipeline2_jobs.update_status(p2_job.id, JobStatus.PROCESSING)
            uow.commit()

        logger.info(f"[Pipeline2 Job {p2_job.id}] Started execution.")

        try:
            # Load input text from source markdown artifact
            source_handle = ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri=p2_job.input_path,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                job_id=p2_job.source_job_id,
                filename="input.md",
            )
            raw_text = self.storage.retrieve(source_handle).decode("utf-8")
        except Exception as e:
            logger.error(f"[Pipeline2 Job {p2_job.id}] Failed to load input text: {e}")
            with self.uow_factory.create() as uow:
                uow.pipeline2_jobs.update_status(p2_job.id, JobStatus.FAILED, error_message=f"Input artifact missing: {e}")
                uow.commit()
            return p2_job

        # Invoke AI executor for text refinement
        result, used_slot = self.ai_executor.execute_text_with_fallback(
            chain=p2_job.api_chain,
            prompt=p2_job.prompt_text or "Refine and structure markdown content",
            input_text=raw_text,
        )

        if result is None:
            with self.uow_factory.create() as uow:
                uow.pipeline2_jobs.update_status(p2_job.id, JobStatus.PAUSED, error_message="All APIs exhausted for Pipeline 2.")
                uow.commit()
            return p2_job

        # Store refined output
        output_handle = self.storage.store(
            job_id=p2_job.source_job_id,
            artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
            filename=f"p2_output_{p2_job.id}.md",
            data=result.encode("utf-8"),
            mime_type="text/markdown",
        )

        with self.uow_factory.create() as uow:
            uow.pipeline2_jobs.update_output_path(p2_job.id, output_handle.uri)
            uow.pipeline2_jobs.update_status(p2_job.id, JobStatus.DONE)
            uow.commit()

        self._publish_completed(p2_job.source_job_id, output_handle.uri)
        return p2_job

    def _publish_progress(self, job: Job) -> None:
        if not self.publisher:
            return
        pct = (job.processed_pages / job.total_pages * 100.0) if job.total_pages > 0 else 0.0
        self.publisher.publish(
            JobProgressEvent(
                job_id=job.id,
                processed_pages=job.processed_pages,
                total_pages=job.total_pages,
                percent=pct,
            )
        )

    def _publish_api_switch(self, job: Job, old_label: str, new_label: str, reason: str, page: int) -> None:
        if not self.publisher:
            return
        self.publisher.publish(
            ApiSwitchEvent(
                job_id=job.id,
                old_label=old_label,
                new_label=new_label,
                reason=reason,
                page=page,
            )
        )

    def _publish_completed(self, job_id: int, uri: str) -> None:
        if not self.publisher:
            return
        self.publisher.publish(JobCompletedEvent(job_id=job_id, output_artifact_uri=uri))

    def _publish_failed(self, job_id: int, error_msg: str, is_retryable: bool = False) -> None:
        if not self.publisher:
            return
        self.publisher.publish(JobFailedEvent(job_id=job_id, error_message=error_msg, is_retryable=is_retryable))
