# ============================================================
#  interfaces/desktop/controllers/markdown_viewer_controller.py
#  Presentation Controller for Desktop Markdown AST Rendering
# ============================================================

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from application.services.markdown_viewer_service import MarkdownViewerService
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import (
    QDesktopServices,
    QObject,
    QTimer,
    Property,
    Signal,
    Slot,
    QUrl,
)


class MarkdownViewerController(QObject):
    """
    Presentation controller managing Markdown AST loading, zooming, region selection,
    and event bridging between UI and the MarkdownViewerService.

    Guarantees:
      - Asynchronous document loading via thread pool keeps the Qt GUI thread responsive.
      - Generation token tracking discards stale document load results.
      - Clean architecture boundary: zero direct database or vendor SDK access.
      - Direct interaction bridge to bidirectional synchronization.
    """

    loadingChanged = Signal()
    documentChanged = Signal()
    errorChanged = Signal()
    activeJobChanged = Signal()
    activeVersionChanged = Signal()
    scaleFactorChanged = Signal()
    selectedNodeIndexChanged = Signal()
    highlightedRegionIdChanged = Signal()
    highlightedOccurrenceIdChanged = Signal()
    pageFilterChanged = Signal()
    previewErrorChanged = Signal()
    hasPreviewErrorChanged = Signal()
    previewPausedChanged = Signal()

    regionSelected = Signal(str, str)          # (region_id, occurrence_id)
    requestScrollToNode = Signal(int)          # (node_index)
    externalLinkActivated = Signal(str)        # (url)
    modelReconciled = Signal(int)              # (model_generation)
    userScrolledNode = Signal(int, int)        # (node_index, model_generation)
    nodeClicked = Signal(int, int)             # (node_index, model_generation)

    _internalDocLoaded = Signal(int, object)   # (req_id, MarkdownDocumentDTO)
    _internalDocError = Signal(int, str)       # (req_id, error_message)
    _internalReconcileLoaded = Signal(int, object)  # (req_id, MarkdownDocumentDTO)
    _internalReconcileError = Signal(int, str)       # (req_id, error_message)
    _internalPreviewLoaded = Signal(int, int, object)  # (job_id, draft_revision, MarkdownDocumentDTO)
    _internalPreviewError = Signal(int, int, str)       # (job_id, draft_revision, error_message)

    def __init__(
        self,
        viewer_service: MarkdownViewerService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.viewer_service = viewer_service
        self._model = MarkdownDocumentModel(parent=self)
        self._model.generationChanged.connect(self._on_model_generation_changed)

        self._is_loading: bool = False
        self._has_document: bool = False
        self._error_message: str = ""
        self._active_job_id: int = 0
        self._active_version: int = 1
        self._scale_factor: float = 1.0
        self._selected_node_index: int = -1
        self._highlighted_region_id: str = ""
        self._highlighted_occurrence_id: str = ""
        self._page_filter: int = 0
        self._reconcile_in_flight: bool = False

        self._request_id: int = 0
        self._is_shutdown: bool = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MarkdownViewerWorker")
        self._last_reconcile_future = None

        # Live dual-pane synchronized preview state
        self._preview_paused: bool = False
        self._preview_paused_reason: str = ""
        self._has_preview_error: bool = False
        self._preview_error_message: str = ""
        self._draft_revision: int = 0
        self._last_applied_draft_revision: int = 0
        self._has_active_draft: bool = False
        self._pending_preview_job_id: int = 0
        self._pending_preview_text: str = ""
        self._pending_preview_base_version: int = 1

        self._live_preview_timer = QTimer(self)
        self._live_preview_timer.setSingleShot(True)
        self._live_preview_timer.setInterval(250)
        self._live_preview_timer.timeout.connect(self._dispatch_pending_preview)

        self._internalDocLoaded.connect(self._on_internal_doc_loaded)
        self._internalDocError.connect(self._on_internal_doc_error)
        self._internalReconcileLoaded.connect(self._on_internal_reconcile_loaded)
        self._internalReconcileError.connect(self._on_internal_reconcile_error)
        self._internalPreviewLoaded.connect(self._on_internal_preview_loaded)
        self._internalPreviewError.connect(self._on_internal_preview_error)

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    def get_model(self) -> MarkdownDocumentModel:
        return self._model

    model = Property(QObject, get_model, constant=True)

    def is_loading(self) -> bool:
        return self._is_loading

    isLoading = Property(bool, is_loading, notify=loadingChanged)

    def has_document(self) -> bool:
        return self._has_document

    hasDocument = Property(bool, has_document, notify=documentChanged)

    def error_message(self) -> str:
        return self._error_message

    errorMessage = Property(str, error_message, notify=errorChanged)

    def preview_error_message(self) -> str:
        return self._preview_error_message

    previewErrorMessage = Property(str, preview_error_message, notify=previewErrorChanged)

    def has_preview_error(self) -> bool:
        return self._has_preview_error

    hasPreviewError = Property(bool, has_preview_error, notify=hasPreviewErrorChanged)

    def preview_paused(self) -> bool:
        return self._preview_paused

    previewPaused = Property(bool, preview_paused, notify=previewPausedChanged)

    def preview_paused_reason(self) -> str:
        return self._preview_paused_reason

    previewPausedReason = Property(str, preview_paused_reason, notify=previewPausedChanged)

    @Slot(bool, str)
    @Slot(bool)
    def setPreviewPaused(self, paused: bool, reason: str = "") -> None:
        target_reason = reason if paused else ""
        if self._preview_paused == paused and self._preview_paused_reason == target_reason:
            return
        self._preview_paused = paused
        self._preview_paused_reason = target_reason
        if paused:
            if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
                self._live_preview_timer.stop()
        self.previewPausedChanged.emit()

    set_preview_paused = setPreviewPaused

    @property
    def has_active_draft(self) -> bool:
        return self._has_active_draft

    @property
    def draft_revision(self) -> int:
        return self._draft_revision

    @property
    def last_applied_draft_revision(self) -> int:
        return self._last_applied_draft_revision

    def active_job_id(self) -> int:
        return self._active_job_id

    activeJobId = Property(int, active_job_id, notify=activeJobChanged)

    def active_version(self) -> int:
        return self._active_version

    activeVersion = Property(int, active_version, notify=activeVersionChanged)

    @property
    def reconcile_in_flight(self) -> bool:
        return self._reconcile_in_flight

    def scale_factor(self) -> float:
        return self._scale_factor

    @Slot(float)
    def set_scale_factor(self, factor: float) -> None:
        clamped = max(0.5, min(3.0, round(factor, 2)))
        if abs(self._scale_factor - clamped) > 0.001:
            self._scale_factor = clamped
            self.scaleFactorChanged.emit()

    setScaleFactor = set_scale_factor
    scaleFactor = Property(float, scale_factor, set_scale_factor, notify=scaleFactorChanged)

    def selected_node_index(self) -> int:
        return self._selected_node_index

    @Slot(int)
    def set_selected_node_index(self, index: int) -> None:
        if self._selected_node_index != index:
            self._selected_node_index = index
            self.selectedNodeIndexChanged.emit()

    setSelectedNodeIndex = set_selected_node_index
    selectedNodeIndex = Property(int, selected_node_index, set_selected_node_index, notify=selectedNodeIndexChanged)

    def highlighted_region_id(self) -> str:
        return self._highlighted_region_id

    @Slot(str)
    def set_highlighted_region_id(self, region_id: str) -> None:
        if self._highlighted_region_id != region_id:
            self._highlighted_region_id = region_id
            self.highlightedRegionIdChanged.emit()

    setHighlightedRegionId = set_highlighted_region_id
    highlightedRegionId = Property(str, highlighted_region_id, set_highlighted_region_id, notify=highlightedRegionIdChanged)

    def highlighted_occurrence_id(self) -> str:
        return self._highlighted_occurrence_id

    @Slot(str)
    def set_highlighted_occurrence_id(self, occ_id: str) -> None:
        if self._highlighted_occurrence_id != occ_id:
            self._highlighted_occurrence_id = occ_id
            self.highlightedOccurrenceIdChanged.emit()

    setHighlightedOccurrenceId = set_highlighted_occurrence_id
    highlightedOccurrenceId = Property(str, highlighted_occurrence_id, set_highlighted_occurrence_id, notify=highlightedOccurrenceIdChanged)

    def page_filter(self) -> int:
        return self._page_filter

    @Slot(int)
    def set_page_filter(self, page: int) -> None:
        if self._page_filter != page:
            self._page_filter = max(0, page)
            self.pageFilterChanged.emit()

    setPageFilter = set_page_filter
    pageFilter = Property(int, page_filter, set_page_filter, notify=pageFilterChanged)

    # -----------------------------------------------------------------------
    # Document Loading & Lifecycle Slots
    # -----------------------------------------------------------------------

    @Slot(int)
    def loadDocument(self, job_id: int) -> None:
        """Asynchronously loads and parses the active Markdown document for job_id."""
        self._request_id += 1
        self._reconcile_in_flight = False
        req_id = self._request_id

        if job_id != self._active_job_id:
            if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
                self._live_preview_timer.stop()
            self._has_active_draft = False
            self._draft_revision += 1
            self._has_preview_error = False
            self._preview_error_message = ""
            self.hasPreviewErrorChanged.emit()
            self.previewErrorChanged.emit()

        self._is_loading = True
        self.loadingChanged.emit()
        self._error_message = ""
        self.errorChanged.emit()

        def _task():
            try:
                dto = self.viewer_service.load_document(job_id)
                self._internalDocLoaded.emit(req_id, dto)
            except Exception as e:
                self._internalDocError.emit(req_id, str(e))

        self._executor.submit(_task)

    def load_document_sync(self, job_id: int) -> None:
        """Synchronous loader for testing and deterministic inspection."""
        self._request_id += 1
        self._reconcile_in_flight = False
        req_id = self._request_id

        if job_id != self._active_job_id:
            if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
                self._live_preview_timer.stop()
            self._has_active_draft = False
            self._draft_revision += 1
            self._has_preview_error = False
            self._preview_error_message = ""
            self.hasPreviewErrorChanged.emit()
            self.previewErrorChanged.emit()

        try:
            dto = self.viewer_service.load_document(job_id)
            self._on_internal_doc_loaded(req_id, dto)
        except Exception as e:
            self._on_internal_doc_error(req_id, str(e))

    @Slot()
    def reload(self) -> None:
        """Reloads the active document."""
        if self._active_job_id > 0:
            self.loadDocument(self._active_job_id)

    @Slot()
    def clear(self) -> None:
        """Resets the controller and clears the document model."""
        self._request_id += 1
        self._draft_revision += 1
        if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
            self._live_preview_timer.stop()
        self._has_active_draft = False
        self._has_preview_error = False
        self._preview_error_message = ""
        self._pending_preview_job_id = 0
        self._pending_preview_text = ""
        self._reconcile_in_flight = False
        self._model.set_document(None)
        self._has_document = False
        self._active_job_id = 0
        self._active_version = 1
        self._error_message = ""
        self._is_loading = False
        self._selected_node_index = -1
        self._highlighted_region_id = ""
        self._highlighted_occurrence_id = ""
        self._preview_paused = False
        self._preview_paused_reason = ""
        self.documentChanged.emit()
        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.errorChanged.emit()
        self.previewErrorChanged.emit()
        self.hasPreviewErrorChanged.emit()
        self.loadingChanged.emit()
        self.selectedNodeIndexChanged.emit()
        self.highlightedRegionIdChanged.emit()
        self.highlightedOccurrenceIdChanged.emit()
        self.previewPausedChanged.emit()

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    @Slot()
    def shutdown(self) -> None:
        """
        Explicit idempotent shutdown lifecycle for MarkdownViewerController.
        Invalidates in-flight work via request_id increment, and cancels
        queued/not-yet-started tasks on the executor without unsafe termination.
        """
        if self._is_shutdown:
            return
        self._is_shutdown = True
        self._preview_paused = False
        self._preview_paused_reason = ""
        if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
            self._live_preview_timer.stop()
        self._has_active_draft = False
        self._reconcile_in_flight = False
        self._request_id += 1
        self._draft_revision += 1
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)

    # -----------------------------------------------------------------------
    # Zoom Slots
    # -----------------------------------------------------------------------

    @Slot()
    def zoomIn(self) -> None:
        self.set_scale_factor(self._scale_factor + 0.1)

    @Slot()
    def zoomOut(self) -> None:
        self.set_scale_factor(self._scale_factor - 0.1)

    @Slot()
    def resetZoom(self) -> None:
        self.set_scale_factor(1.0)

    # -----------------------------------------------------------------------
    # Region Selection & Hyperlink Navigation Slots
    # -----------------------------------------------------------------------

    @Slot(str, str, int)
    @Slot(str, str)
    def updateRegionArtifact(
        self, region_id: str, new_artifact_uri: str, new_version: int = 1
    ) -> None:
        """
        Updates the active artifact URI for all occurrences of region_id in-place.
        Notifies presentation model immediately for zero-latency image updates.
        Always triggers asynchronous structural reconciliation with the canonical document
        in SQLite to synchronize canonical document version, advance activeVersion, and notify
        coordinators/editors of the external canonical advance.
        """
        if not region_id:
            return

        existing_occs = self._model.occurrencesOfRegion(region_id)
        if existing_occs:
            self._model.update_region_artifact(region_id, new_artifact_uri, new_version)

        if self._has_document and self._active_job_id > 0:
            self._sync_document_structure(self._active_job_id)

    @Slot(str)
    @Slot(str, str)
    def selectRegion(self, region_id: str, occurrence_id: str = "") -> None:
        """
        Selects a visual region, updates highlighted properties, locates its
        node index in O(1), and requests the view to scroll to it.
        Supports exact occurrence targeting and primary occurrence fallback.
        """
        if not region_id:
            return

        self.set_highlighted_region_id(region_id)

        target_occ = occurrence_id
        if not target_occ:
            target_occ = self._model.primaryOccurrenceOfRegion(region_id)

        self.set_highlighted_occurrence_id(target_occ)

        node_idx = -1
        if target_occ:
            node_idx = self._model.indexOfOccurrence(target_occ)
        if node_idx < 0:
            node_idx = self._model.indexOfRegion(region_id)

        if node_idx >= 0:
            self.set_selected_node_index(node_idx)
            self.requestScrollToNode.emit(node_idx)

        self.regionSelected.emit(region_id, occurrence_id)

    @Slot(str)
    def handleLinkClicked(self, url_string: str) -> None:
        """
        Routes link activations:
        - region:// links trigger internal region selection and view scrolling.
        - http(s):// links trigger external desktop browser opening.
        """
        if not url_string:
            return

        if url_string.startswith("region://"):
            rid = url_string[9:].strip()
            self.selectRegion(rid)
        elif url_string.startswith("http://") or url_string.startswith("https://"):
            QDesktopServices.openUrl(QUrl(url_string))
            self.externalLinkActivated.emit(url_string)

    # -----------------------------------------------------------------------
    # Internal Signal Handlers (Qt GUI Thread)
    # -----------------------------------------------------------------------

    @Slot(int, object)
    def _on_internal_doc_loaded(self, req_id: int, document_dto) -> None:
        if self._is_shutdown:
            return
        if req_id != self._request_id:
            return  # Stale generation, drop result

        self._reconcile_in_flight = False

        if document_dto.job_id != self._active_job_id:
            self._has_active_draft = False
            if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
                self._live_preview_timer.stop()

        if not self._has_active_draft:
            self._model.set_document(document_dto)
            self.documentChanged.emit()

        self._active_job_id = document_dto.job_id
        self._active_version = document_dto.version

        self._has_document = True
        self._is_loading = False
        self._error_message = ""

        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()

    @Slot(int, str)
    def _on_internal_doc_error(self, req_id: int, error_message: str) -> None:
        if self._is_shutdown:
            return
        if req_id != self._request_id:
            return

        self._reconcile_in_flight = False
        self._model.set_document(None)
        self._has_document = False
        self._is_loading = False
        self._error_message = error_message

        self.documentChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()

    def _sync_document_structure(self, job_id: int) -> None:
        """
        Asynchronously loads the canonical document artifact and reconciles presentation
        model structure off the Qt GUI thread.
        """
        if job_id <= 0 or not self._has_document or self._active_job_id != job_id:
            return

        self._request_id += 1
        req_id = self._request_id
        self._reconcile_in_flight = True

        def _task():
            try:
                dto = self.viewer_service.load_document(job_id)
                self._internalReconcileLoaded.emit(req_id, dto)
            except Exception as e:
                self._internalReconcileError.emit(req_id, str(e))

        self._last_reconcile_future = self._executor.submit(_task)

    def sync_document_structure_sync(self, job_id: int) -> None:
        """
        Synchronous structural synchronizer for deterministic inspection and tests.
        """
        if job_id <= 0 or not self._has_document or self._active_job_id != job_id:
            return

        self._request_id += 1
        req_id = self._request_id
        self._reconcile_in_flight = True
        try:
            dto = self.viewer_service.load_document(job_id)
            self._on_internal_reconcile_loaded(req_id, dto)
        except Exception as e:
            self._on_internal_reconcile_error(req_id, str(e))

    def wait_for_reconciliation(self, timeout: float = 3.0) -> None:
        """Waits for any in-flight background reconciliation task to complete."""
        if hasattr(self, "_last_reconcile_future") and self._last_reconcile_future is not None:
            try:
                self._last_reconcile_future.result(timeout=timeout)
            except Exception:
                pass

    @Slot(int, object)
    def _on_internal_reconcile_loaded(self, req_id: int, document_dto) -> None:
        """
        GUI-thread handler applying canonical document reconciliation.
        Guarded against stale request IDs, closed documents, job mismatches,
        and regressive document versions.
        """
        if self._is_shutdown:
            return
        if req_id != self._request_id:
            return  # Stale generation, drop result

        self._reconcile_in_flight = False

        if not self._has_document or self._active_job_id <= 0:
            return
        if document_dto is None or document_dto.job_id != self._active_job_id:
            return

        if document_dto.version < self._active_version:
            return

        if document_dto.version > self._active_version:
            self._active_version = document_dto.version
            self.activeVersionChanged.emit()

        if not self._has_active_draft:
            self._model.reconcile_document(document_dto)


    @Slot(int, str)
    def _on_internal_reconcile_error(self, req_id: int, error_message: str) -> None:
        """
        GUI-thread handler recording reconciliation failure without resetting existing model.
        """
        if self._is_shutdown:
            return
        if req_id != self._request_id:
            return

        self._reconcile_in_flight = False
        self._error_message = f"Failed to reconcile markdown document: {error_message}"
        self.errorChanged.emit()

    # -----------------------------------------------------------------------
    # Live Dual-Pane Synchronized Preview Scheduling & Cancellation
    # -----------------------------------------------------------------------

    @Slot(int, str, int)
    @Slot(int, str)
    def scheduleLivePreview(
        self, job_id: int, raw_text: str, base_version: int = 1
    ) -> None:
        """
        Debounces live preview rendering requests. Sets dirty draft flag and starts/restarts
        the 250ms single-shot timer without touching canonical version metadata.
        """
        if self._is_shutdown or self._preview_paused or job_id <= 0:
            return
        self._has_active_draft = True
        self._draft_revision += 1
        self._pending_preview_job_id = job_id
        self._pending_preview_text = raw_text
        self._pending_preview_base_version = base_version
        self._live_preview_timer.start(250)

    schedule_live_preview = scheduleLivePreview

    @Slot()
    def flushLivePreview(self) -> None:
        """
        Immediately flushes any pending debounced live preview render without waiting
        for timer expiry.
        """
        if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
            self._live_preview_timer.stop()
            self._dispatch_pending_preview()

    flush_live_preview = flushLivePreview

    def _dispatch_pending_preview(self) -> None:
        """
        Dispatches background render task for the pending preview snapshot.
        """
        if self._is_shutdown or self._pending_preview_job_id <= 0:
            return
        job_id = self._pending_preview_job_id
        rev_id = self._draft_revision
        raw_text = self._pending_preview_text
        base_ver = self._pending_preview_base_version

        def _task():
            try:
                dto = self.viewer_service.render_preview(job_id, raw_text, base_ver)
                self._internalPreviewLoaded.emit(job_id, rev_id, dto)
            except Exception as e:
                self._internalPreviewError.emit(job_id, rev_id, str(e))

        self._executor.submit(_task)

    @Slot(int, str, int)
    @Slot(int, str)
    def cancelPendingLivePreviewAndReconcile(
        self, job_id: int, raw_text: str, base_version: int = 1
    ) -> None:
        """
        Cancels pending live preview timer, resets active draft flag, advances draft revision,
        and dispatches an immediate background render task for raw_text.
        """
        if self._is_shutdown:
            return
        if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
            self._live_preview_timer.stop()
        self._has_active_draft = False
        self._draft_revision += 1
        rev_id = self._draft_revision

        def _task():
            try:
                dto = self.viewer_service.render_preview(job_id, raw_text, base_version)
                self._internalPreviewLoaded.emit(job_id, rev_id, dto)
            except Exception as e:
                self._internalPreviewError.emit(job_id, rev_id, str(e))

        self._executor.submit(_task)

    cancel_pending_live_preview_and_reconcile = cancelPendingLivePreviewAndReconcile

    @Slot()
    def resetActiveDraft(self) -> None:
        """
        Explicitly resets active draft state without resetting the presentation model.
        Used by presentation coordinators when a canonical save or hard reload completes.
        """
        if hasattr(self, "_live_preview_timer") and self._live_preview_timer.isActive():
            self._live_preview_timer.stop()
        self._has_active_draft = False
        self._draft_revision += 1

    reset_active_draft = resetActiveDraft

    @Slot(int, int, object)

    def _on_internal_preview_loaded(
        self, job_id: int, draft_revision: int, document_dto
    ) -> None:
        """
        GUI-thread handler applying transient preview projection under Option B strict
        latest revision matching.
        Guards:
          - Shutdown check.
          - Preview paused check.
          - Job switch isolation (job_id == self._active_job_id).
          - Option B strict matching: draft_revision == self._draft_revision.
        Critical Invariant: Live preview updates MUST NEVER touch self._active_version
        and MUST NEVER emit activeVersionChanged.
        """
        if self._is_shutdown or self._preview_paused:
            return
        if job_id != self._active_job_id:
            return  # Job switch isolation: drop result from previous job
        if draft_revision != self._draft_revision:
            return  # Option B: newer revision typed, drop obsolete result

        self._last_applied_draft_revision = draft_revision
        self._model.apply_transient_preview(document_dto)
        self._has_document = True
        self._has_preview_error = False
        self._preview_error_message = ""

        self.hasPreviewErrorChanged.emit()
        self.previewErrorChanged.emit()
        self.documentChanged.emit()

    @Slot(int, int, str)
    def _on_internal_preview_error(
        self, job_id: int, draft_revision: int, error_message: str
    ) -> None:
        """
        GUI-thread handler exposing live preview syntax/render errors.
        Guards:
          - Shutdown check.
          - Preview paused check.
          - Job switch isolation (job_id == self._active_job_id).
          - Option B strict matching: draft_revision == self._draft_revision.
        Invariant: Retains existing _model (never blanked!).
        """
        if self._is_shutdown or self._preview_paused:
            return
        if job_id != self._active_job_id:
            return
        if draft_revision != self._draft_revision:
            return

        self._has_preview_error = True
        self._preview_error_message = error_message
        self.hasPreviewErrorChanged.emit()
        self.previewErrorChanged.emit()

    def _on_model_generation_changed(self, generation: int) -> None:
        self.modelReconciled.emit(generation)

    @Slot(int, int)
    def reportUserScrolled(self, node_index: int, model_generation: int) -> None:
        """Reports user-initiated scrolling in the rendered preview pane."""
        self.userScrolledNode.emit(node_index, model_generation)

    @Slot(int, int)
    def reportNodeClicked(self, node_index: int, model_generation: int) -> None:
        """Reports user clicking a block in the rendered preview pane."""
        if 0 <= node_index < self._model.rowCount():
            self.setSelectedNodeIndex(node_index)
        self.nodeClicked.emit(node_index, model_generation)

    @property
    def model_generation(self) -> int:
        return self._model.model_generation

    @Slot(result=int)
    def modelGeneration(self) -> int:
        return self._model.modelGeneration()
