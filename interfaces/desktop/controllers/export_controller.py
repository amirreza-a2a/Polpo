"""Presentation controller for document exports (Standalone Markdown and Package ZIP)."""


from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from application.sanitizer import sanitize_error_message
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    ExportPackageService,
    derive_document_slug,
)
from interfaces.desktop.qt_compat import Property, QObject, QUrl, Signal, Slot


def _to_local_path(file_path: str) -> Path:
    """Safely normalizes local file path or file:// URI across Windows, macOS, and Linux."""
    if file_path.startswith("file:"):
        local_str = QUrl(file_path).toLocalFile()
        if local_str:
            return Path(local_str)
        cleaned = file_path[7:] if file_path.startswith("file://") else file_path[5:]
        return Path(cleaned)
    return Path(file_path)


class _JobFileHolder:
    """Lightweight duck-typed container for passing file_name to derive_document_slug."""
    def __init__(self, file_name: Optional[str]):
        self.file_name = file_name


class ExportController(QObject):
    """
    Presentation controller for document exports (Standalone Markdown and Package ZIP).
    Coordinates background execution, dirty-editor save guards, overwrite confirmation,
    error presentation, and clean executor shutdown.
    """

    exportingChanged = Signal()
    errorChanged = Signal()
    exportCompleted = Signal(int, str)             # (job_id, destination_path)
    exportFailed = Signal(int, str)                # (job_id, error_message)
    overwriteRequired = Signal(int, str, str, int) # (job_id, export_type, destination_path, version)
    saveBeforeExportRequired = Signal(int, str, int)  # (job_id, export_type, current_version)
    readyForDestination = Signal(int, str, int)    # (job_id, export_type, resolved_version)

    _internalExportCompleted = Signal(int, int, str)
    _internalExportError = Signal(int, int, str, bool, str, str, int)

    def __init__(
        self,
        export_service: ExportPackageService,
        editor_controller: Optional[QObject] = None,
        query_service: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.export_service = export_service
        self.editor_controller = editor_controller
        self.query_service = query_service

        self._is_exporting: bool = False
        self._error_message: str = ""
        self._is_shutdown: bool = False
        self._request_id: int = 0

        self._pending_export: Optional[Dict[str, Any]] = None
        self._pending_overwrite: Optional[Dict[str, Any]] = None
        self._save_connected: bool = False

        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ExportWorker")

        self._internalExportCompleted.connect(self._on_internal_export_completed)
        self._internalExportError.connect(self._on_internal_export_error)

    def is_exporting(self) -> bool:
        return self._is_exporting

    isExporting = Property(bool, is_exporting, notify=exportingChanged)

    def error_message(self) -> str:
        return self._error_message

    errorMessage = Property(str, error_message, notify=errorChanged)

    @property
    def hasPendingExport(self) -> bool:
        return self._pending_export is not None

    @property
    def hasPendingOverwrite(self) -> bool:
        return self._pending_overwrite is not None

    @property
    def isShutdown(self) -> bool:
        return self._is_shutdown

    def _is_editor_dirty_for_job(self, job_id: int) -> bool:
        """Checks if the editor currently holds unsaved changes for job_id."""
        if self.editor_controller is not None:
            try:
                active_id = getattr(self.editor_controller, "activeJobId", 0)
                is_dirty = getattr(self.editor_controller, "isDirty", False)
                has_conflict = getattr(self.editor_controller, "hasConflict", False)
                return bool(active_id == job_id and (is_dirty or has_conflict))
            except Exception:
                return False
        return False

    def _get_editor_version_for_job(self, job_id: int) -> int:
        """Gets active canonical version from the editor if matching job_id."""
        if self.editor_controller is not None:
            try:
                active_id = getattr(self.editor_controller, "activeJobId", 0)
                if active_id == job_id:
                    return int(getattr(self.editor_controller, "activeVersion", 0))
            except Exception:
                pass
        return 0

    @Slot(int, str, int, result=str)
    @Slot(int, str, result=str)
    def getSuggestedFileName(self, job_id: int, export_type: str, version: int = 0) -> str:
        """Generates a cross-platform clean default filename for the export."""
        ext = "zip" if export_type == "package" else "md"
        file_name = None
        if self.query_service is not None:
            try:
                detail = self.query_service.get_job_detail(job_id)
                if detail and detail.file_name:
                    file_name = detail.file_name
            except Exception:
                pass

        slug = derive_document_slug(_JobFileHolder(file_name) if file_name else None, job_id)
        ver = version if version > 0 else self._get_editor_version_for_job(job_id)
        if ver <= 0:
            ver = 1
        return f"{slug}_v{ver}.{ext}"

    @Slot(int, str, int)
    @Slot(int, str)
    def requestExport(self, job_id: int, export_type: str, version: int = 0) -> None:
        """
        Initiates export workflow:
        If editor is dirty for job_id, emits saveBeforeExportRequired.
        If clean, immediately emits readyForDestination.
        """
        if self._is_shutdown:
            return

        resolved_ver = version if version > 0 else self._get_editor_version_for_job(job_id)

        if self._is_editor_dirty_for_job(job_id):
            self._pending_export = {
                "job_id": job_id,
                "export_type": export_type,
                "destination_uri": "",
                "version": resolved_ver,
            }
            self.saveBeforeExportRequired.emit(job_id, export_type, resolved_ver)
        else:
            self.readyForDestination.emit(job_id, export_type, resolved_ver)

    @Slot()
    def confirmSaveAndExport(self) -> None:
        """User confirmed Save & Export for pending export."""
        if self._is_shutdown or not self._pending_export:
            return

        self._disconnect_save_signals()

        if self.editor_controller is None:
            job_id = self._pending_export.get("job_id", 0)
            self._pending_export = None
            sanitized = sanitize_error_message("Cannot save and export: editor controller is unavailable.")
            self._error_message = sanitized
            self.errorChanged.emit()
            self.exportFailed.emit(job_id, sanitized)
            return

        # Fail fast if unresolved conflicts exist (editor cannot save anyway)
        if getattr(self.editor_controller, "hasConflict", False):
            job_id = self._pending_export["job_id"]
            self._pending_export = None
            sanitized = sanitize_error_message("Cannot save and export: document has unresolved conflicts.")
            self._error_message = sanitized
            self.errorChanged.emit()
            self.exportFailed.emit(job_id, sanitized)
            return

        # If document is not dirty, calling save() is a no-op that emits no signals;
        # proceed directly to destination selection or export.
        if not getattr(self.editor_controller, "isDirty", False):
            cur_ver = self._get_editor_version_for_job(self._pending_export["job_id"])
            self._on_editor_saved(cur_ver if cur_ver > 0 else self._pending_export.get("version", 1))
            return

        try:
            if hasattr(self.editor_controller, "saved"):
                self.editor_controller.saved.connect(self._on_editor_saved)
            if hasattr(self.editor_controller, "errorChanged"):
                self.editor_controller.errorChanged.connect(self._on_editor_error)
            if hasattr(self.editor_controller, "conflictDetected"):
                self.editor_controller.conflictDetected.connect(self._on_editor_conflict)
            self._save_connected = True
            self.editor_controller.save()
        except Exception as e:
            self._disconnect_save_signals()
            job_id = self._pending_export.get("job_id", 0) if self._pending_export else 0
            self._pending_export = None
            sanitized = sanitize_error_message(str(e))
            self._error_message = sanitized
            self.errorChanged.emit()
            self.exportFailed.emit(job_id, sanitized)

    @Slot()
    def cancelPendingExport(self) -> None:
        """Cancels any pending export operation and cleans up signal connections."""
        self._disconnect_save_signals()
        self._pending_export = None

    @Slot()
    def confirmOverwrite(self) -> None:
        """User confirmed overwriting existing file. Retries export with overwrite=True."""
        if self._is_shutdown or not self._pending_overwrite:
            return

        pending = self._pending_overwrite
        self._pending_overwrite = None

        if pending["export_type"] == "markdown":
            self.exportMarkdown(
                job_id=pending["job_id"],
                destination_uri=pending["destination_path"],
                version=pending["version"],
                overwrite=True,
            )
        else:
            self.exportPackage(
                job_id=pending["job_id"],
                destination_uri=pending["destination_path"],
                version=pending["version"],
                overwrite=True,
            )

    @Slot()
    def cancelOverwrite(self) -> None:
        """User cancelled overwrite prompt."""
        self._pending_overwrite = None

    @Slot(int, str, int, bool)
    @Slot(int, str, int)
    @Slot(int, str)
    def exportMarkdown(
        self,
        job_id: int,
        destination_uri: str,
        version: int = 0,
        overwrite: bool = False,
    ) -> None:
        """Exports standalone portable Markdown asynchronously."""
        self._dispatch_export(
            job_id=job_id,
            export_type="markdown",
            destination_uri=destination_uri,
            version=version,
            overwrite=overwrite,
        )

    @Slot(int, str, int, bool)
    @Slot(int, str, int)
    @Slot(int, str)
    def exportPackage(
        self,
        job_id: int,
        destination_uri: str,
        version: int = 0,
        overwrite: bool = False,
    ) -> None:
        """Exports document ZIP package archive asynchronously."""
        self._dispatch_export(
            job_id=job_id,
            export_type="package",
            destination_uri=destination_uri,
            version=version,
            overwrite=overwrite,
        )

    def _dispatch_export(
        self,
        job_id: int,
        export_type: str,
        destination_uri: str,
        version: int,
        overwrite: bool,
        bypass_dirty_check: bool = False,
    ) -> None:
        """Internal dispatching logic for both Markdown and Package exports."""
        if self._is_shutdown or self._is_exporting:
            return

        # If document is dirty and overwrite is False and not currently retrying after save:
        if not bypass_dirty_check and self._is_editor_dirty_for_job(job_id) and not overwrite:
            resolved_ver = version if version > 0 else self._get_editor_version_for_job(job_id)
            self._pending_export = {
                "job_id": job_id,
                "export_type": export_type,
                "destination_uri": destination_uri,
                "version": resolved_ver,
            }
            self.saveBeforeExportRequired.emit(job_id, export_type, resolved_ver)
            return

        local_path = _to_local_path(destination_uri)
        req_ver = version if version > 0 else None

        self._request_id += 1
        req_id = self._request_id

        self._is_exporting = True
        self._error_message = ""
        self.exportingChanged.emit()
        self.errorChanged.emit()

        def _worker_task():
            try:
                if export_type == "markdown":
                    res = self.export_service.export_markdown(
                        job_id=job_id,
                        destination_path=local_path,
                        version=req_ver,
                        overwrite=overwrite,
                    )
                else:
                    res = self.export_service.export_package(
                        job_id=job_id,
                        destination_path=local_path,
                        version=req_ver,
                        overwrite=overwrite,
                    )
                self._internalExportCompleted.emit(req_id, job_id, str(res.destination_path))
            except DestinationAlreadyExistsError as dae:
                self._internalExportError.emit(
                    req_id, job_id, str(dae), True, export_type, str(local_path), version
                )
            except Exception as e:
                self._internalExportError.emit(
                    req_id, job_id, str(e), False, export_type, str(local_path), version
                )

        self._executor.submit(_worker_task)

    def _disconnect_save_signals(self) -> None:
        """Safely detaches save-lifecycle listeners from MarkdownEditorController."""
        if self._save_connected and self.editor_controller is not None:
            try:
                if hasattr(self.editor_controller, "saved"):
                    self.editor_controller.saved.disconnect(self._on_editor_saved)
            except (RuntimeError, TypeError):
                pass
            try:
                if hasattr(self.editor_controller, "errorChanged"):
                    self.editor_controller.errorChanged.disconnect(self._on_editor_error)
            except (RuntimeError, TypeError):
                pass
            try:
                if hasattr(self.editor_controller, "conflictDetected"):
                    self.editor_controller.conflictDetected.disconnect(self._on_editor_conflict)
            except (RuntimeError, TypeError):
                pass
            self._save_connected = False

    def _on_editor_saved(self, new_version: int) -> None:
        """Editor successfully committed new canonical version."""
        self._disconnect_save_signals()
        if self._is_shutdown or not self._pending_export:
            return

        job_id = self._pending_export["job_id"]
        export_type = self._pending_export["export_type"]
        dest_uri = self._pending_export.get("destination_uri", "")
        self._pending_export = None

        if dest_uri:
            # Destination was already chosen: proceed to export using newly saved version
            self._dispatch_export(
                job_id=job_id,
                export_type=export_type,
                destination_uri=dest_uri,
                version=new_version,
                overwrite=False,
                bypass_dirty_check=True,
            )
        else:
            # Destination not yet selected: prompt FileDialog with newly saved version
            self.readyForDestination.emit(job_id, export_type, new_version)

    def _on_editor_error(self) -> None:
        """Editor failed to save."""
        if not self._save_connected or self._is_shutdown or not self._pending_export:
            return
        err = getattr(self.editor_controller, "errorMessage", "")
        if not err:
            return

        self._disconnect_save_signals()
        job_id = self._pending_export["job_id"]
        self._pending_export = None

        sanitized = sanitize_error_message(err)
        self._error_message = sanitized
        self.errorChanged.emit()
        self.exportFailed.emit(job_id, sanitized)

    def _on_editor_conflict(self, conflict_msg: str) -> None:
        """Editor collided with newer version during save."""
        if not self._save_connected or self._is_shutdown or not self._pending_export:
            return

        self._disconnect_save_signals()
        job_id = self._pending_export["job_id"]
        self._pending_export = None

        sanitized = sanitize_error_message(conflict_msg)
        self._error_message = sanitized
        self.errorChanged.emit()
        self.exportFailed.emit(job_id, sanitized)

    @Slot(int, int, str)
    def _on_internal_export_completed(self, req_id: int, job_id: int, dest_path: str) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_exporting = False
        self._error_message = ""
        self._pending_export = None
        self._pending_overwrite = None

        self.exportingChanged.emit()
        self.errorChanged.emit()
        self.exportCompleted.emit(job_id, dest_path)

    @Slot(int, int, str, bool, str, str, int)
    def _on_internal_export_error(
        self,
        req_id: int,
        job_id: int,
        err_msg: str,
        is_overwrite: bool,
        export_type: str,
        dest_path: str,
        version: int,
    ) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_exporting = False
        self.exportingChanged.emit()

        if is_overwrite:
            self._pending_overwrite = {
                "job_id": job_id,
                "export_type": export_type,
                "destination_path": dest_path,
                "version": version,
            }
            self.overwriteRequired.emit(job_id, export_type, dest_path, version)
        else:
            sanitized = sanitize_error_message(err_msg)
            self._error_message = sanitized
            self._pending_export = None
            self._pending_overwrite = None
            self.errorChanged.emit()
            self.exportFailed.emit(job_id, sanitized)

    @Slot()
    def shutdown(self) -> None:
        """Gracefully terminates background executor and cleans up pending state."""
        self._is_shutdown = True
        self._disconnect_save_signals()
        self._pending_export = None
        self._pending_overwrite = None
        self._is_exporting = False
        self._request_id += 1
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=True)
