# ============================================================
#  interfaces/desktop/models/job_queue_model.py
#  QAbstractListModel for Active Document Conversion Queue
# ============================================================

from typing import List, Dict, Any, Optional
from interfaces.desktop.qt_compat import QAbstractListModel, QModelIndex, Qt, Slot
from interfaces.desktop.bridge import QtSignalEventBridge
from application.services.job_query import JobQueryService
from core.entities.job import JobStatus


class JobQueueModel(QAbstractListModel):
    """
    Reactive QAbstractListModel representing active/in-flight/scheduled document conversions.
    Receives GUI-thread Qt signals from QtSignalEventBridge and applies fine-grained row deltas.
    """

    IdRole = Qt.ItemDataRole.UserRole + 1
    FileNameRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    ProcessedPagesRole = Qt.ItemDataRole.UserRole + 4
    TotalPagesRole = Qt.ItemDataRole.UserRole + 5
    ProgressPercentRole = Qt.ItemDataRole.UserRole + 6
    ScheduledAtRole = Qt.ItemDataRole.UserRole + 7
    ActiveApiLabelRole = Qt.ItemDataRole.UserRole + 8
    ErrorMessageRole = Qt.ItemDataRole.UserRole + 9
    ActionStateRole = Qt.ItemDataRole.UserRole + 10

    def __init__(
        self,
        query_service: JobQueryService,
        bridge: Optional[QtSignalEventBridge] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.query_service = query_service
        self.bridge = bridge
        self._jobs: List[Dict[str, Any]] = []

        if self.bridge is not None:
            self._connect_signals()

        self.reload_queue()

    def _connect_signals(self) -> None:
        self.bridge.job_progress_received.connect(self._on_progress)
        self.bridge.api_switch_received.connect(self._on_api_switch)
        self.bridge.job_state_changed_received.connect(self._on_state_changed)
        self.bridge.schedule_updated_received.connect(self._on_schedule_updated)

    def roleNames(self) -> Dict[int, bytes]:
        return {
            self.IdRole: b"id",
            self.FileNameRole: b"fileName",
            self.StatusRole: b"status",
            self.ProcessedPagesRole: b"processedPages",
            self.TotalPagesRole: b"totalPages",
            self.ProgressPercentRole: b"progressPercent",
            self.ScheduledAtRole: b"scheduledAt",
            self.ActiveApiLabelRole: b"activeApiLabel",
            self.ErrorMessageRole: b"errorMessage",
            self.ActionStateRole: b"actionState",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._jobs)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._jobs):
            return None

        job = self._jobs[index.row()]
        if role == self.IdRole:
            return job["id"]
        elif role == self.FileNameRole:
            return job["file_name"]
        elif role == self.StatusRole:
            return job["status"]
        elif role == self.ProcessedPagesRole:
            return job["processed_pages"]
        elif role == self.TotalPagesRole:
            return job["total_pages"]
        elif role == self.ProgressPercentRole:
            return job["progress_percent"]
        elif role == self.ScheduledAtRole:
            return job["scheduled_at"]
        elif role == self.ActiveApiLabelRole:
            return job["active_api_label"]
        elif role == self.ErrorMessageRole:
            return job["error_message"]
        elif role == self.ActionStateRole:
            return job.get("action_state", "")

        return None

    @Slot()
    def reload_queue(self) -> None:
        """Initial baseline load of active/pending/processing/paused/failed jobs."""
        self.beginResetModel()
        self._jobs.clear()
        try:
            dtos = self.query_service.list_jobs(limit=100)
            active_statuses = {
                JobStatus.PENDING.value,
                JobStatus.PROCESSING.value,
                JobStatus.PAUSED.value,
                JobStatus.FAILED.value,
            }
            for d in dtos:
                if d.status in active_statuses:
                    pct = (d.processed_pages / d.total_pages * 100.0) if d.total_pages > 0 else 0.0
                    self._jobs.append({
                        "id": d.id,
                        "file_name": d.file_name,
                        "status": d.status,
                        "processed_pages": d.processed_pages,
                        "total_pages": d.total_pages,
                        "progress_percent": float(pct),
                        "scheduled_at": "",
                        "active_api_label": "",
                        "error_message": getattr(d, "error_message", "") or "",
                        "action_state": "",
                    })
        except Exception:
            pass
        self.endResetModel()

    def _find_job_index(self, job_id: int) -> int:
        for i, j in enumerate(self._jobs):
            if j["id"] == job_id:
                return i
        return -1

    @Slot(int, str)
    def set_action_state(self, job_id: int, action_state: str) -> None:
        """Sets a transient presentation-only action state (e.g. 'cancelling', 'retrying', 'resuming')."""
        idx = self._find_job_index(job_id)
        if idx >= 0:
            self._jobs[idx]["action_state"] = action_state
            model_idx = self.index(idx, 0)
            self.dataChanged.emit(model_idx, model_idx, [self.ActionStateRole])

    @Slot(int, int, int, float)
    def _on_progress(self, job_id: int, processed: int, total: int, pct: float) -> None:
        idx = self._find_job_index(job_id)
        if idx >= 0:
            self._jobs[idx]["processed_pages"] = processed
            self._jobs[idx]["total_pages"] = total
            self._jobs[idx]["progress_percent"] = pct
            self._jobs[idx]["action_state"] = ""
            model_idx = self.index(idx, 0)
            self.dataChanged.emit(
                model_idx,
                model_idx,
                [self.ProcessedPagesRole, self.TotalPagesRole, self.ProgressPercentRole, self.ActionStateRole],
            )

    @Slot(int, str, str, str, int)
    def _on_api_switch(self, job_id: int, old_label: str, new_label: str, reason: str, page: int) -> None:
        idx = self._find_job_index(job_id)
        if idx >= 0:
            self._jobs[idx]["active_api_label"] = new_label
            model_idx = self.index(idx, 0)
            self.dataChanged.emit(model_idx, model_idx, [self.ActiveApiLabelRole])

    @Slot(int, str, str)
    def _on_state_changed(self, job_id: int, old_status: str, new_status: str) -> None:
        idx = self._find_job_index(job_id)
        terminal_statuses = {"done", "cancelled"}

        if new_status in terminal_statuses:
            if idx >= 0:
                self.beginRemoveRows(QModelIndex(), idx, idx)
                self._jobs.pop(idx)
                self.endRemoveRows()
        else:
            if idx >= 0:
                self._jobs[idx]["status"] = new_status
                self._jobs[idx]["action_state"] = ""
                # If moving to failed, fetch error message
                if new_status == "failed":
                    try:
                        dto = self.query_service.get_job_detail(job_id)
                        self._jobs[idx]["error_message"] = dto.error_message or ""
                    except Exception:
                        pass
                model_idx = self.index(idx, 0)
                self.dataChanged.emit(
                    model_idx,
                    model_idx,
                    [self.StatusRole, self.ActionStateRole, self.ErrorMessageRole],
                )
            else:
                # Newly active or retried/resumed job -> fetch details and insert row
                try:
                    dto = self.query_service.get_job_detail(job_id)
                    pct = (dto.processed_pages / dto.total_pages * 100.0) if dto.total_pages > 0 else 0.0
                    self.beginInsertRows(QModelIndex(), 0, 0)
                    self._jobs.insert(0, {
                        "id": dto.id,
                        "file_name": dto.file_name,
                        "status": dto.status,
                        "processed_pages": dto.processed_pages,
                        "total_pages": dto.total_pages,
                        "progress_percent": float(pct),
                        "scheduled_at": "",
                        "active_api_label": dto.active_api_label or "",
                        "error_message": dto.error_message or "",
                        "action_state": "",
                    })
                    self.endInsertRows()
                except Exception:
                    pass

    @Slot(int, str)
    def _on_schedule_updated(self, job_id: int, sched_iso: str) -> None:
        idx = self._find_job_index(job_id)
        if idx >= 0:
            self._jobs[idx]["scheduled_at"] = sched_iso
            self._jobs[idx]["action_state"] = ""
            model_idx = self.index(idx, 0)
            self.dataChanged.emit(model_idx, model_idx, [self.ScheduledAtRole, self.ActionStateRole])
