# ============================================================
#  interfaces/desktop/models/job_history_model.py
#  QAbstractListModel for Paginated Job History
# ============================================================

from typing import List, Dict, Any, Optional
from interfaces.desktop.qt_compat import QAbstractListModel, QModelIndex, Qt, Slot, Signal, Property
from interfaces.desktop.bridge import QtSignalEventBridge
from application.services.job_query import JobQueryService


class JobHistoryModel(QAbstractListModel):
    """
    QAbstractListModel representing paginated, historical conversion jobs.
    """

    IdRole = Qt.ItemDataRole.UserRole + 1
    FileNameRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    TotalPagesRole = Qt.ItemDataRole.UserRole + 4
    CreatedAtRole = Qt.ItemDataRole.UserRole + 5
    OutputPathRole = Qt.ItemDataRole.UserRole + 6
    ErrorMessageRole = Qt.ItemDataRole.UserRole + 7

    pagination_changed = Signal()

    def __init__(
        self,
        query_service: JobQueryService,
        bridge: Optional[QtSignalEventBridge] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.query_service = query_service
        self.bridge = bridge
        self._history: List[Dict[str, Any]] = []
        self._current_page: int = 1
        self._total_pages: int = 1
        self._total_jobs: int = 0
        self._per_page: int = 10

        if self.bridge is not None:
            self.bridge.job_state_changed_received.connect(self._on_state_changed)

        self.load_page(1)

    def roleNames(self) -> Dict[int, bytes]:
        return {
            self.IdRole: b"id",
            self.FileNameRole: b"fileName",
            self.StatusRole: b"status",
            self.TotalPagesRole: b"totalPages",
            self.CreatedAtRole: b"createdAt",
            self.OutputPathRole: b"outputPath",
            self.ErrorMessageRole: b"errorMessage",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._history)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._history):
            return None

        job = self._history[index.row()]
        if role == self.IdRole:
            return job["id"]
        elif role == self.FileNameRole:
            return job["file_name"]
        elif role == self.StatusRole:
            return job["status"]
        elif role == self.TotalPagesRole:
            return job["total_pages"]
        elif role == self.CreatedAtRole:
            return job["created_at"]
        elif role == self.OutputPathRole:
            return job["output_path"]
        elif role == self.ErrorMessageRole:
            return job["error_message"]

        return None

    # --- Properties ---

    def _get_current_page(self) -> int:
        return self._current_page

    def _get_total_pages(self) -> int:
        return self._total_pages

    def _get_total_jobs(self) -> int:
        return self._total_jobs

    currentPage = Property(int, _get_current_page, notify=pagination_changed)
    totalPages = Property(int, _get_total_pages, notify=pagination_changed)
    totalJobs = Property(int, _get_total_jobs, notify=pagination_changed)

    # --- Slots ---

    @Slot(int)
    @Slot(int, int)
    def load_page(self, page: int = 1, per_page: int = 10) -> None:
        """Loads a specific page of job history from the query service."""
        self._per_page = per_page
        dtos, total_jobs, total_pages = self.query_service.get_paginated_history(
            user_id=1, page=page, per_page=per_page
        )

        self.beginResetModel()
        self._history = [
            {
                "id": d.id,
                "file_name": d.file_name,
                "status": d.status,
                "total_pages": d.total_pages,
                "created_at": d.created_at or "",
                "output_path": d.output_path or "",
                "error_message": d.error_message or "",
            }
            for d in dtos
        ]
        self._current_page = page
        self._total_jobs = total_jobs
        self._total_pages = total_pages
        self.endResetModel()

        self.pagination_changed.emit()

    @Slot()
    def next_page(self) -> None:
        if self._current_page < self._total_pages:
            self.load_page(self._current_page + 1, self._per_page)

    @Slot()
    def prev_page(self) -> None:
        if self._current_page > 1:
            self.load_page(self._current_page - 1, self._per_page)

    @Slot(int, str, str)
    def _on_state_changed(self, job_id: int, old_status: str, new_status: str) -> None:
        """When a job transitions to a terminal state, refresh history page if viewing page 1."""
        if new_status in ("done", "failed", "cancelled"):
            if self._current_page == 1:
                self.load_page(1, self._per_page)
