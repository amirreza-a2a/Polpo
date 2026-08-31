# ============================================================
#  application/services/job_execution.py
# ============================================================

import logging
from datetime import datetime
from typing import List, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IProgressNotifier
from application.ports.ai_executor import IAIExecutionService
from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.policies.job_state_policy import JobStateTransitionPolicy
from core.exceptions.domain_exceptions import ArtifactNotFoundError

logger = logging.getLogger("polpot.execution")


class JobExecutionService:
    """
    ارکستراتور اجرای کارها در لایه کاربرد.
    حلقه پردازش صفحات سند، فراخوانی درگاه هوش مصنوعی، برش تصاویر و تولید خروجی نهایی را مدیریت می‌کند.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
        ai_executor: IAIExecutionService,
        notifier: IProgressNotifier,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor
        self.ai_executor = ai_executor
        self.notifier = notifier

    def execute_next_job(self) -> Optional[Job]:
        # 1. واکشی کار بعدی در صف
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_next_pending()
            if not job:
                return None

            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PROCESSING)
            job.status = JobStatus.PROCESSING
            uow.jobs.update_status(job.id, JobStatus.PROCESSING)
            uow.commit()

        logger.info(f"[Job {job.id}] Started execution. Total pages: {job.total_pages}")

        # 2. واکشی فایل مبدأ از مخزن آرتیفکت
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
            self.notifier.notify_job_failed(job.user_id, job.id, f"Source artifact missing: {e}")
            return job

        # 3. حلقه پردازش صفحات
        accumulated_markdown: List[str] = []
        start_page = job.processed_pages + 1

        for page_num in range(start_page, job.total_pages + 1):
            # رندر صفحه به JPEG
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

            # کال‌بک ثبت رویداد سوئیچ
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
                self.notifier.notify_api_switch(job.user_id, job.id, old_label, new_label, reason, page)

            # ارسال به مجری هوش مصنوعی با زنجیره فعال از اسلات فعلی
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

            # به‌روزرسانی شاخص اسلات فعال در کار
            if used_slot:
                for idx, slot in enumerate(job.api_chain):
                    if slot.id == used_slot.id and slot.slot_type == used_slot.slot_type:
                        job.current_api_index = idx
                        break

            # استخراج و برش تصاویر
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

            # ذخیره پیشرفت در دیتابیس
            with self.uow_factory.create() as uow:
                uow.jobs.update_progress(job.id, job.processed_pages, job.api_switch_log)
                uow.users.increment_daily_pages(job.user_id, 1)
                if used_slot:
                    uow.apis.report_pages_used(used_slot.id, used_slot.slot_type, 1)
                uow.commit()

            self.notifier.notify_progress(job.user_id, job.id, job.processed_pages, job.total_pages)

        # 4. تکمیل کار و ذخیره‌سازی فایل مارک‌داون یکپارچه
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
                    user_id=job.user_id,
                    prompt_id=job.pipeline2_prompt_id,
                    status=JobStatus.PENDING,
                    input_path=output_handle.uri,
                    api_chain=job.api_chain,
                    current_api_index=0,
                )
                uow.pipeline2_jobs.save(p2_job)

            uow.commit()

        self.notifier.notify_job_completed(job.user_id, job.id, output_handle.uri)
        return job

    def _pause_job(self, job: Job, error_msg: str) -> None:
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.PAUSED)
            job.status = JobStatus.PAUSED
            job.error_message = error_msg
            uow.jobs.update_status(job.id, JobStatus.PAUSED, error_message=error_msg)
            uow.commit()
        self.notifier.notify_job_failed(job.user_id, job.id, error_msg)

    def _fail_job(self, job: Job, error_msg: str) -> None:
        with self.uow_factory.create() as uow:
            JobStateTransitionPolicy.validate_transition(job.status, JobStatus.FAILED)
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            uow.jobs.update_status(job.id, JobStatus.FAILED, error_message=error_msg)
            uow.commit()
        self.notifier.notify_job_failed(job.user_id, job.id, error_msg)
