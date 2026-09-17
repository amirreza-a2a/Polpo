# ============================================================
#  interfaces/desktop/controllers/markdown_editor_controller.py
#  Phase 10F.1 — Native Markdown Editor Presentation Controller
# ============================================================

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from application.services.markdown_editor_service import MarkdownEditorService
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from interfaces.desktop.qt_compat import (
    QObject,
    Property,
    Signal,
    Slot,
)


class MarkdownEditorController(QObject):
    """
    Presentation controller for native Markdown editing.
    Coordinates source loading, explicit user edits, dirty-state tracking,
    optimistic concurrency-controlled saving, and conflict detection.

    Guarantees:
      - Asynchronous file I/O on background workers keeps the Qt GUI thread responsive.
      - Generation token tracking discards stale async load/save responses.
      - Conflict state is flagged only when dirty edits collide with newer canonical versions.
      - Discard cleanly reverts buffer to last saved state.
      - Clean architecture boundary: communicates with application services only.
    """

    sourceTextChanged = Signal()
    dirtyChanged = Signal()
    loadingChanged = Signal()
    savingChanged = Signal()
    errorChanged = Signal()
    activeJobChanged = Signal()
    activeVersionChanged = Signal()
    conflictChanged = Signal()

    saved = Signal(int)             # (new_version)
    discarded = Signal()
    conflictDetected = Signal(str)  # (conflict_message)

    _internalLoaded = Signal(int, str, int)          # (req_id, text, version)
    _internalLoadError = Signal(int, str)            # (req_id, error_message)
    _internalSaved = Signal(int, int, str)           # (req_id, new_version, saved_text)
    _internalSaveError = Signal(int, str, bool)      # (req_id, error_message, is_conflict)

    def __init__(
        self,
        editor_service: MarkdownEditorService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.editor_service = editor_service

        self._source_text: str = ""
        self._saved_source_text: str = ""
        self._is_dirty: bool = False
        self._is_loading: bool = False
        self._is_saving: bool = False
        self._error_message: str = ""
        self._active_job_id: int = 0
        self._active_version: int = 0
        self._has_conflict: bool = False
        self._conflict_message: str = ""

        self._request_id: int = 0
        self._is_shutdown: bool = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MarkdownEditorWorker")

        self._internalLoaded.connect(self._on_internal_loaded)
        self._internalLoadError.connect(self._on_internal_load_error)
        self._internalSaved.connect(self._on_internal_saved)
        self._internalSaveError.connect(self._on_internal_save_error)

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    def source_text(self) -> str:
        return self._source_text

    @Slot(str)
    def setSourceText(self, text: str) -> None:
        """QML-invokable slot to update editor buffer text."""
        self.set_source_text(text)

    @Slot(str)
    def set_source_text(self, text: str) -> None:
        """Updates buffer text and evaluates dirty state against last saved snapshot."""
        if self._source_text != text:
            self._source_text = text
            self.sourceTextChanged.emit()
            new_dirty = (self._source_text != self._saved_source_text)
            if self._is_dirty != new_dirty:
                self._is_dirty = new_dirty
                self.dirtyChanged.emit()

    sourceText = Property(str, source_text, set_source_text, notify=sourceTextChanged)

    def is_dirty(self) -> bool:
        return self._is_dirty

    isDirty = Property(bool, is_dirty, notify=dirtyChanged)

    def is_loading(self) -> bool:
        return self._is_loading

    isLoading = Property(bool, is_loading, notify=loadingChanged)

    def is_saving(self) -> bool:
        return self._is_saving

    isSaving = Property(bool, is_saving, notify=savingChanged)

    def error_message(self) -> str:
        return self._error_message

    errorMessage = Property(str, error_message, notify=errorChanged)

    def active_job_id(self) -> int:
        return self._active_job_id

    activeJobId = Property(int, active_job_id, notify=activeJobChanged)

    def active_version(self) -> int:
        return self._active_version

    activeVersion = Property(int, active_version, notify=activeVersionChanged)

    def has_conflict(self) -> bool:
        return self._has_conflict

    hasConflict = Property(bool, has_conflict, notify=conflictChanged)

    def conflict_message(self) -> str:
        return self._conflict_message

    conflictMessage = Property(str, conflict_message, notify=conflictChanged)

    # -----------------------------------------------------------------------
    # Operations
    # -----------------------------------------------------------------------

    @Slot(int)
    def loadSource(self, job_id: int) -> None:
        """Asynchronously loads raw Markdown source text for job_id."""
        self._request_id += 1
        req_id = self._request_id

        self._active_job_id = job_id
        self.activeJobChanged.emit()

        self._is_loading = True
        self.loadingChanged.emit()
        self._error_message = ""
        self.errorChanged.emit()
        self._has_conflict = False
        self._conflict_message = ""
        self.conflictChanged.emit()

        def _task():
            try:
                text, ver = self.editor_service.load_source_text(job_id)
                self._internalLoaded.emit(req_id, text, ver)
            except Exception as e:
                self._internalLoadError.emit(req_id, str(e))

        self._executor.submit(_task)

    def load_source_sync(self, job_id: int) -> None:
        """Synchronous source loader for testing and deterministic inspection."""
        self._request_id += 1
        req_id = self._request_id
        self._active_job_id = job_id
        self.activeJobChanged.emit()
        self._has_conflict = False
        self._conflict_message = ""
        self.conflictChanged.emit()

        try:
            text, ver = self.editor_service.load_source_text(job_id)
            self._on_internal_loaded(req_id, text, ver)
        except Exception as e:
            self._on_internal_load_error(req_id, str(e))

    @Slot()
    def save(self) -> None:
        """Asynchronously persists source text to a new immutable canonical document."""
        if not self._is_dirty or self._is_saving:
            return

        if self._has_conflict:
            self._error_message = "Cannot save: document has conflicting external changes. Please reload."
            self.errorChanged.emit()
            return

        self._request_id += 1
        req_id = self._request_id

        self._is_saving = True
        self.savingChanged.emit()
        self._error_message = ""
        self.errorChanged.emit()

        job_id = self._active_job_id
        raw_text = self._source_text
        base_ver = self._active_version

        def _task():
            try:
                new_ver = self.editor_service.commit_source_text(
                    job_id=job_id,
                    raw_text=raw_text,
                    base_version=base_ver,
                )
                self._internalSaved.emit(req_id, new_ver, raw_text)
            except StaleDocumentVersionError as sve:
                self._internalSaveError.emit(req_id, str(sve), True)
            except Exception as e:
                self._internalSaveError.emit(req_id, str(e), False)

        self._executor.submit(_task)

    def save_sync(self) -> bool:
        """Synchronous saver for testing and deterministic inspection."""
        if not self._is_dirty or self._is_saving:
            return False

        if self._has_conflict:
            self._error_message = "Cannot save: document has conflicting external changes. Please reload."
            self.errorChanged.emit()
            return False

        self._request_id += 1
        req_id = self._request_id
        job_id = self._active_job_id
        raw_text = self._source_text
        base_ver = self._active_version

        try:
            new_ver = self.editor_service.commit_source_text(
                job_id=job_id,
                raw_text=raw_text,
                base_version=base_ver,
            )
            self._on_internal_saved(req_id, new_ver, raw_text)
            return True
        except StaleDocumentVersionError as sve:
            self._on_internal_save_error(req_id, str(sve), True)
            return False
        except Exception as e:
            self._on_internal_save_error(req_id, str(e), False)
            return False

    @Slot()
    def discard(self) -> None:
        """Discards uncommitted buffer modifications and reverts to last saved text."""
        self._source_text = self._saved_source_text
        self._is_dirty = False
        self._has_conflict = False
        self._conflict_message = ""
        self._error_message = ""

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.conflictChanged.emit()
        self.errorChanged.emit()
        self.discarded.emit()

    @Slot(int)
    def notifyCanonicalDocumentAdvance(self, new_doc_version: int) -> None:
        """
        Receives notification that the active canonical Markdown document advanced to new_doc_version.
        If the editor buffer is dirty, sets conflict state.
        If the buffer is clean, automatically advances activeVersion and reloads the latest document.
        """
        if new_doc_version <= self._active_version:
            return

        if self._is_dirty:
            self._has_conflict = True
            self._conflict_message = (
                f"Canonical document updated to version {new_doc_version} while uncommitted changes exist."
            )
            self.conflictChanged.emit()
            self.conflictDetected.emit(self._conflict_message)
        else:
            self._active_version = new_doc_version
            self.activeVersionChanged.emit()
            if self._active_job_id > 0:
                self.loadSource(self._active_job_id)

    @Slot()
    def clear(self) -> None:
        """Clears all editor state."""
        self._request_id += 1
        self._source_text = ""
        self._saved_source_text = ""
        self._is_dirty = False
        self._is_loading = False
        self._is_saving = False
        self._error_message = ""
        self._active_job_id = 0
        self._active_version = 0
        self._has_conflict = False
        self._conflict_message = ""

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.loadingChanged.emit()
        self.savingChanged.emit()
        self.errorChanged.emit()
        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.conflictChanged.emit()

    def shutdown(self) -> None:
        """Shuts down background thread executor."""
        self._is_shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -----------------------------------------------------------------------
    # Internal Handlers (GUI Thread)
    # -----------------------------------------------------------------------

    def _on_internal_loaded(self, req_id: int, text: str, version: int) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._source_text = text
        self._saved_source_text = text
        self._active_version = version
        self._is_dirty = False
        self._is_loading = False
        self._error_message = ""
        self._has_conflict = False
        self._conflict_message = ""

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.activeVersionChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.conflictChanged.emit()

    def _on_internal_load_error(self, req_id: int, error_msg: str) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_loading = False
        self._error_message = error_msg
        self.loadingChanged.emit()
        self.errorChanged.emit()

    def _on_internal_saved(self, req_id: int, new_version: int, saved_text: str) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._saved_source_text = saved_text
        self._active_version = new_version
        self._is_dirty = (self._source_text != self._saved_source_text)
        self._is_saving = False
        self._error_message = ""
        self._has_conflict = False
        self._conflict_message = ""

        self.activeVersionChanged.emit()
        self.dirtyChanged.emit()
        self.savingChanged.emit()
        self.errorChanged.emit()
        self.conflictChanged.emit()
        self.saved.emit(new_version)

    def _on_internal_save_error(self, req_id: int, error_msg: str, is_conflict: bool) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_saving = False
        self._error_message = error_msg
        if is_conflict:
            self._has_conflict = True
            self._conflict_message = error_msg
            self.conflictChanged.emit()
            self.conflictDetected.emit(error_msg)

        self.savingChanged.emit()
        self.errorChanged.emit()
