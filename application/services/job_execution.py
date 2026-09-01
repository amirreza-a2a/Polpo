import logging
import threading
from datetime import datetime
from typing import Any, List, Optional

from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IApplicationEventPublisher
from application.ports.ai_executor import IAIExecutionService
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
)
from application.sanitizer import sanitize_error_message
from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
from core.entities.bounding_box_parser import BoundingBoxParser
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.exceptions.domain_exceptions import ArtifactNotFoundError, EntityNotFoundError, DomainError

logger = logging.getLogger("polpot.execution")


class JobExecutionService:
    """
    Application orchestrator for executing document processing jobs.
    Executes a single pre-claimed job, enforces cooperative pause and cancellation checkpoints,
    and publishes typed transport-neutral application events.
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
        self._lock = threading.Lock()
        self._pause_requested_jobs: set[int] = set()

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
        """
        Legacy compatibility helper. Atomically claims the next pending job
        and delegates to execute_claimed_job. Canonical desktop runtime uses
        execute_claimed_job() directly.
        """
        with self.uow_factory.create() as uow:
            job = None
            if hasattr(uow.jobs, "claim_next_pending"):
                job = uow.jobs.claim_next_pending()
            elif hasattr(uow.jobs, "get_next_pending"):
                job = uow.jobs.get_next_pending()
                if job and isinstance(getattr(job, "status", None), JobStatus):
                    job.status = JobStatus.PROCESSING
                    uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        if not job:
            return None

        self._publish_state_changed(job.id, JobStatus.PENDING, JobStatus.PROCESSING)
        return self.execute_claimed_job(job.id)

    def claim_and_execute_next(self) -> Optional[Job]:
        """Atomically claims and executes the next pending job."""
        return self.execute_next_job()

    def execute_claimed_job(self, job_id: int) -> Optional[Job]:
        """
        Canonical execution entry point for pre-claimed jobs.
        Does NOT perform an independent claim; operates strictly on the pre-claimed job_id.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if isinstance(getattr(job, "status", None), JobStatus) and job.status != JobStatus.PROCESSING:
                raise DomainError(f"Cannot execute job {job_id} in status '{job.status.value}'. Expected PROCESSING.")

        logger.info(f"[Job {job.id}] Executing pipeline. Total pages: {job.total_pages}")

        try:
            # 1. Retrieve source document from immutable artifact storage
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
                error_msg = sanitize_error_message(f"Source artifact missing: {e}")
                logger.error(f"[Job {job.id}] {error_msg}")
                with self.uow_factory.create() as uow:
                    uow.jobs.update_status(job.id, JobStatus.FAILED, error_message=error_msg)
                    uow.commit()
                self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.FAILED)
                self._publish_failed(job.id, error_msg, is_retryable=False)
                return job

            # 2. Page processing loop with deterministic checkpoint restoration
            accumulated_markdown: List[str] = []
            start_page = job.processed_pages + 1

            # Restore previously processed page markdown snippets if resuming/retrying from checkpoint
            for p in range(1, start_page):
                try:
                    p_handle = ArtifactHandle(
                        storage_backend=StorageBackendType.LOCAL_FS,
                        uri=f"job_{job.id}/page_{p}.md",
                        artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                        job_id=job.id,
                        filename=f"page_{p}.md",
                    )
                    p_bytes = self.storage.retrieve(p_handle)
                    accumulated_markdown.append(p_bytes.decode("utf-8"))
                except Exception:
                    pass

            for page_num in range(start_page, job.total_pages + 1):
                # Checkpoint 1 (before render): Check cancellation and pause
                if self._is_cancellation_requested(job.id):
                    return self._handle_cancellation(job)
                if self._is_pause_requested(job.id):
                    return self._handle_pause(job)

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
                    error_msg = sanitize_error_message(f"Page {page_num} render failed: {e}")
                    logger.error(f"[Job {job.id}] {error_msg}")
                    return self._fail_job(job, error_msg)

                # Checkpoint 2 (before AI request): Check cancellation and pause
                if self._is_cancellation_requested(job.id):
                    return self._handle_cancellation(job)
                if self._is_pause_requested(job.id):
                    return self._handle_pause(job)

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

                # Send to AI executor with fallback chain
                active_chain = job.api_chain[job.current_api_index:]
                raw_page_md, used_slot = self.ai_executor.execute_vision_with_fallback(
                    chain=active_chain,
                    image_bytes=page_jpeg_bytes,
                    prompt=job.prompt_text or "Convert page to markdown",
                    at_page=page_num,
                    mime_type="image/jpeg",
                    on_switch=on_switch_callback,
                )

                # Checkpoint 3 (after AI request): Check cancellation
                if self._is_cancellation_requested(job.id):
                    return self._handle_cancellation(job)

                if raw_page_md is None:
                    return self._pause_job(job, f"All APIs exhausted at page {page_num}.")

                # Update active slot index on job
                if used_slot:
                    for idx, slot in enumerate(job.api_chain):
                        if slot.id == used_slot.id and slot.slot_type == used_slot.slot_type:
                            job.current_api_index = idx
                            break

                # 1. Parse and persist initial AI visual region provenance
                parsed_box_matches = BoundingBoxParser.parse_matches(raw_page_md)
                created_regions: List[VisualRegion] = []
                for idx, match_item in enumerate(parsed_box_matches, start=1):
                    region = VisualRegion.create_ai_detected(
                        job_id=job.id,
                        page_number=page_num,
                        display_order=idx,
                        detected_bbox=match_item.box,
                    )
                    created_regions.append(region)

                if created_regions:
                    with self.uow_factory.create() as uow:
                        saved_regions = uow.visual_regions.save_all(created_regions)
                        uow.commit()
                        created_regions = saved_regions

                # 2. Extract and crop images with deterministic page-isolated naming
                final_page_md, cropped_images = self.doc_processor.extract_and_crop_images(
                    markdown_text=raw_page_md,
                    page_jpeg_bytes=page_jpeg_bytes,
                    job_id=job.id,
                    page_number=page_num,
                )

                # Deterministic association by display_order (protects against skipped/filtered crops)
                regions_by_order = {r.display_order: r for r in created_regions}

                for crop in cropped_images:
                    crop_name, crop_bytes = crop[0], crop[1]
                    order = getattr(crop, "display_order", None)
                    handle = self.storage.store(
                        job_id=job.id,
                        artifact_type=ArtifactType.CROPPED_IMAGE,
                        filename=crop_name,
                        data=crop_bytes,
                        mime_type="image/jpeg",
                    )
                    if order is not None and order in regions_by_order:
                        target_region = regions_by_order[order]
                        target_region.active_artifact_uri = handle.uri
                        target_region.active_artifact_version = 1
                        target_region.artifact_version_watermark = 1
                        target_region.sync_status = SyncStatus.SYNCED

                if created_regions:
                    with self.uow_factory.create() as uow:
                        uow.visual_regions.save_all(created_regions)
                        uow.commit()

                page_entry = f"<!-- Page {page_num} -->\n{final_page_md}\n"
                accumulated_markdown.append(page_entry)
                job.processed_pages = page_num

                # Persist page-level markdown snippet for deterministic checkpoint restoration
                try:
                    self.storage.store(
                        job_id=job.id,
                        artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                        filename=f"page_{page_num}.md",
                        data=page_entry.encode("utf-8"),
                        mime_type="text/markdown",
                    )
                except Exception:
                    pass

                # Save progress in database
                with self.uow_factory.create() as uow:
                    uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log)
                    if used_slot and hasattr(uow.apis, "report_pages_used"):
                        uow.apis.report_pages_used(used_slot.id, used_slot.slot_type, 1)
                    uow.commit()

                self._publish_progress(job)

                # Checkpoint 3b (after page progress commit): Check pause
                if self._is_pause_requested(job.id):
                    return self._handle_pause(job)

            # Checkpoint 4 (before final artifact commit): Check cancellation and pause
            if self._is_cancellation_requested(job.id):
                return self._handle_cancellation(job)
            if self._is_pause_requested(job.id):
                return self._handle_pause(job)

            # 3. Finalize job and store output Markdown artifact
            full_document = "\n\n".join(accumulated_markdown)
            output_handle = self.storage.store(
                job_id=job.id,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                filename=f"output_{job.id}.md",
                data=full_document.encode("utf-8"),
                mime_type="text/markdown",
            )

            with self.uow_factory.create() as uow:
                # Check for cancellation or pause race inside final commit transaction
                current_job = uow.jobs.get_by_id(job.id)
                if current_job and (getattr(current_job, "cancel_requested", False) is True or current_job.status == JobStatus.CANCELLED):
                    JobStateTransitionPolicy.validate_transition(job.status, JobStatus.CANCELLED)
                    uow.jobs.update_status(job.id, JobStatus.CANCELLED)
                    uow.commit()
                    job.status = JobStatus.CANCELLED
                    self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.CANCELLED)
                    self._publish_cancelled(job.id)
                    return job

                JobStateTransitionPolicy.validate_transition(job.status, JobStatus.DONE)
                job.status = JobStatus.DONE
                job.output_path = output_handle.uri
                job.output_artifact_version_watermark = 1
                uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log, output_path=output_handle.uri)
                uow.jobs.save(job)
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

            self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.DONE)
            self._publish_completed(job.id, output_handle.uri)
            return job
        finally:
            with self._lock:
                self._pause_requested_jobs.discard(job.id)

    def execute_next_pipeline2_job(self) -> Optional[Pipeline2Job]:
        """Executes the next pending Pipeline 2 text refinement job."""
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

    def pause_job(self, job_id: int) -> bool:
        """
        Canonical pause entry point for desktop controllers and application services.
        Transitions PENDING jobs immediately to PAUSED, or flags pause_requested for PROCESSING jobs.
        Preserves existing scheduled_at and checkpoint metadata.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if job.status == JobStatus.PENDING:
                JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
                uow.jobs.update_status(job_id, JobStatus.PAUSED)
                uow.commit()
                self._publish_state_changed(job_id, JobStatus.PENDING, JobStatus.PAUSED)
                return True

            elif job.status == JobStatus.PROCESSING:
                with self._lock:
                    self._pause_requested_jobs.add(job_id)
                return True

            elif job.status == JobStatus.PAUSED:
                return True

            else:
                raise DomainError(f"Job {job_id} is in status '{job.status.value}' and cannot be paused.")

    def _is_pause_requested(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._pause_requested_jobs

    def _handle_pause(self, job: Job, reason: Optional[str] = None) -> Job:
        with self._lock:
            self._pause_requested_jobs.discard(job.id)

        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
            job.status = JobStatus.PAUSED
            if reason:
                job.error_message = sanitize_error_message(reason)
            uow.jobs.update_status(job.id, JobStatus.PAUSED, error_message=job.error_message)
            uow.commit()

        self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.PAUSED)
        return job

    def cancel_job(self, job_id: int) -> bool:
        """
        Canonical cancellation entry point for desktop controllers and application services.
        Transitions PENDING jobs immediately to CANCELLED, or flags cancel_requested for PROCESSING jobs.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if job.status in (JobStatus.PENDING, JobStatus.PAUSED):
                old_status = job.status
                JobStateTransitionPolicy.validate_transition(old_status, JobStatus.CANCELLED)
                uow.jobs.update_status(job_id, JobStatus.CANCELLED)
                uow.commit()
                self._publish_state_changed(job_id, old_status, JobStatus.CANCELLED)
                self._publish_cancelled(job_id)
                return True

            elif job.status == JobStatus.PROCESSING:
                if hasattr(uow.jobs, "request_cancellation"):
                    uow.jobs.request_cancellation(job_id)
                else:
                    job.cancel_requested = True
                    uow.jobs.save(job)
                uow.commit()
                return True

            elif job.status == JobStatus.CANCELLED:
                return True

            else:
                raise DomainError(f"Job {job_id} is in status '{job.status.value}' and cannot be cancelled.")

    def _is_cancellation_requested(self, job_id: int) -> bool:
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            return bool(job and getattr(job, "cancel_requested", False) is True)

    def _handle_cancellation(self, job: Job) -> Job:
        with self._lock:
            self._pause_requested_jobs.discard(job.id)

        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.CANCELLED)
            job.status = JobStatus.CANCELLED
            uow.jobs.update_status(job.id, JobStatus.CANCELLED)
            uow.commit()

        self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.CANCELLED)
        self._publish_cancelled(job.id)
        return job

    def _pause_job(self, job: Job, error_msg: str) -> Job:
        sanitized = sanitize_error_message(error_msg)
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
            job.status = JobStatus.PAUSED
            job.error_message = sanitized
            uow.jobs.update_status(job.id, JobStatus.PAUSED, error_message=sanitized)
            uow.commit()

        self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.PAUSED)
        self._publish_failed(job.id, sanitized, is_retryable=True)
        return job

    def _fail_job(self, job: Job, error_msg: str) -> Job:
        sanitized = sanitize_error_message(error_msg)
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.FAILED)
            job.status = JobStatus.FAILED
            job.error_message = sanitized
            uow.jobs.update_status(job.id, JobStatus.FAILED, error_message=sanitized)
            uow.commit()

        self._publish_state_changed(job.id, JobStatus.PROCESSING, JobStatus.FAILED)
        self._publish_failed(job.id, sanitized, is_retryable=False)
        return job

    def _publish_state_changed(self, job_id: int, old_st: Optional[JobStatus], new_st: JobStatus) -> None:
        if not self.publisher:
            return
        if old_st is not None:
            self.publisher.publish(JobStateChangedEvent(job_id=job_id, old_status=old_st, new_status=new_st))

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

    def _publish_cancelled(self, job_id: int) -> None:
        if not self.publisher:
            return
        self.publisher.publish(JobCancelledEvent(job_id=job_id))
