# ============================================================
#  interfaces/desktop/controllers/job_controller.py
#  Desktop Presentation Controller for Document Job Operations
# ============================================================

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from interfaces.desktop.qt_compat import QObject, Slot, Signal, QDesktopServices, QUrl
from application.dto.job_dto import SubmitJobCommand
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.schedule_service import ScheduleService
from application.services.job_recovery import JobRecoveryService
from application.services.artifact_service import ArtifactService
from application.services.job_query import JobQueryService
from application.sanitizer import sanitize_error_message


def _to_local_path(file_path: str) -> Path:
    """Safely normalizes local file path or file:// URI across Windows, macOS, and Linux."""
    if file_path.startswith("file:"):
        local_str = QUrl(file_path).toLocalFile()
        if local_str:
            return Path(local_str)
        cleaned = file_path[7:] if file_path.startswith("file://") else file_path[5:]
        return Path(cleaned)
    return Path(file_path)


class JobController(QObject):
    """
    Presentation controller for document conversions, cancellation, scheduling, and artifact access.
    Delegates domain actions directly to Application Services and exposes Qt Slots/Signals to QML.
    """

    job_submitted = Signal(int)
    error_occurred = Signal(str)

    def __init__(
        self,
        submission_service: JobSubmissionService,
        execution_service: JobExecutionService,
        schedule_service: ScheduleService,
        recovery_service: JobRecoveryService,
        artifact_service: ArtifactService,
        query_service: JobQueryService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.submission_service = submission_service
        self.execution_service = execution_service
        self.schedule_service = schedule_service
        self.recovery_service = recovery_service
        self.artifact_service = artifact_service
        self.query_service = query_service

    @Slot(str, int, str, bool, result=int)
    @Slot(str, int, str, result=int)
    @Slot(str, int, result=int)
    @Slot(str, result=int)
    def submit_job(
        self,
        file_path: str,
        prompt_id: int = 0,
        scheduled_at_iso: str = "",
        auto_pipeline2: bool = False,
    ) -> int:
        """
        Submits a local document file for conversion, optionally with a custom prompt ID and scheduled UTC time.
        """
        try:
            path = _to_local_path(file_path)
            if not path.exists() or not path.is_file():
                self.error_occurred.emit(f"File not found: '{file_path}'")
                return 0

            file_bytes = path.read_bytes()
            sched_dt: Optional[datetime] = None
            if scheduled_at_iso and scheduled_at_iso.strip():
                try:
                    sched_dt = datetime.fromisoformat(scheduled_at_iso.strip())
                    if sched_dt.tzinfo is None:
                        sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                except ValueError as e:
                    self.error_occurred.emit(f"Invalid scheduled date format: {e}")
                    return 0

            p_id = prompt_id if prompt_id > 0 else None
            cmd = SubmitJobCommand(
                user_id=1,
                filename=path.name,
                file_bytes=file_bytes,
                prompt_id=p_id,
                auto_pipeline2=auto_pipeline2,
                scheduled_at=sched_dt,
            )
            dto = self.submission_service.submit_job(cmd)
            self.job_submitted.emit(dto.id)
            return dto.id
        except Exception as err:
            sanitized = sanitize_error_message(str(err))
            self.error_occurred.emit(sanitized)
            return 0

    @Slot(int, result=bool)
    def cancel_job(self, job_id: int) -> bool:
        """Requests cooperative cancellation of a pending, scheduled, or processing job."""
        try:
            return self.execution_service.cancel_job(job_id)
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def retry_job(self, job_id: int) -> bool:
        """Resets a failed job to PENDING state and triggers execution."""
        try:
            return self.execution_service.retry_job(job_id)
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def resume_job(self, job_id: int) -> bool:
        """Resumes a paused job from its last processed checkpoint."""
        try:
            return self.execution_service.resume_job(job_id)
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, str, result=bool)
    def reschedule_job(self, job_id: int, new_scheduled_at_iso: str) -> bool:
        """Updates the scheduled execution time for a pending or paused job."""
        try:
            sched_dt: Optional[datetime] = None
            if new_scheduled_at_iso and new_scheduled_at_iso.strip():
                sched_dt = datetime.fromisoformat(new_scheduled_at_iso.strip())
                if sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)

            dto = self.schedule_service.reschedule_job(job_id, sched_dt)
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def run_now(self, job_id: int) -> bool:
        """Triggers immediate execution for a scheduled or paused job."""
        try:
            dto = self.schedule_service.reschedule_job(job_id, new_scheduled_at=None)
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, str, str, result=bool)
    @Slot(int, str, result=bool)
    def acknowledge_missed_schedule(
        self,
        job_id: int,
        action: str,
        new_scheduled_at_iso: str = "",
    ) -> bool:
        """Resolves a missed schedule via 'run_now', 'mark_paused', 'cancel', or 'reschedule'."""
        try:
            new_dt: Optional[datetime] = None
            if action == "reschedule" and new_scheduled_at_iso.strip():
                new_dt = datetime.fromisoformat(new_scheduled_at_iso.strip())
                if new_dt.tzinfo is None:
                    new_dt = new_dt.replace(tzinfo=timezone.utc)

            dto = self.schedule_service.acknowledge_missed_schedule(
                job_id=job_id,
                action=action,
                new_scheduled_at=new_dt,
            )
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result="QVariantMap")
    def get_job_detail(self, job_id: int) -> Dict[str, Any]:
        """Returns full job detail dictionary for UI inspection."""
        try:
            dto = self.query_service.get_job_detail(job_id)
            return {
                "id": dto.id,
                "file_name": dto.file_name,
                "status": dto.status,
                "total_pages": dto.total_pages,
                "processed_pages": dto.processed_pages,
                "prompt_id": dto.prompt_id,
                "prompt_text": dto.prompt_text,
                "active_api_label": dto.active_api_label,
                "output_path": dto.output_path,
                "error_message": dto.error_message,
                "auto_pipeline2": dto.auto_pipeline2,
                "created_at": dto.created_at,
                "updated_at": dto.updated_at,
            }
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return {}

    @Slot(int, result=bool)
    def reveal_artifact_in_explorer(self, job_id: int) -> bool:
        """Opens the OS file manager highlighting the job artifact directory."""
        try:
            dto = self.query_service.get_job_detail(job_id)
            if not dto or not dto.output_path:
                self.error_occurred.emit(f"No output artifact available for job {job_id}")
                return False
            path = _to_local_path(dto.output_path)
            target_dir = path.parent if path.is_file() else path
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir)))
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def open_artifact_default(self, job_id: int) -> bool:
        """Opens the output markdown file in the system default application."""
        try:
            dto = self.query_service.get_job_detail(job_id)
            if not dto or not dto.output_path:
                self.error_occurred.emit(f"No output artifact available for job {job_id}")
                return False
            path = _to_local_path(dto.output_path)
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False
