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

    regionSelected = Signal(str, str)          # (region_id, occurrence_id)
    requestScrollToNode = Signal(int)          # (node_index)
    externalLinkActivated = Signal(str)        # (url)

    _internalDocLoaded = Signal(int, object)   # (req_id, MarkdownDocumentDTO)
    _internalDocError = Signal(int, str)       # (req_id, error_message)

    def __init__(
        self,
        viewer_service: MarkdownViewerService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.viewer_service = viewer_service
        self._model = MarkdownDocumentModel(parent=self)

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

        self._request_id: int = 0
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MarkdownViewerWorker")

        self._internalDocLoaded.connect(self._on_internal_doc_loaded)
        self._internalDocError.connect(self._on_internal_doc_error)

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

    def active_job_id(self) -> int:
        return self._active_job_id

    activeJobId = Property(int, active_job_id, notify=activeJobChanged)

    def active_version(self) -> int:
        return self._active_version

    activeVersion = Property(int, active_version, notify=activeVersionChanged)

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
        req_id = self._request_id

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
        req_id = self._request_id
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
        self._model.set_document(None)
        self._has_document = False
        self._active_job_id = 0
        self._active_version = 1
        self._error_message = ""
        self._is_loading = False
        self._selected_node_index = -1
        self._highlighted_region_id = ""
        self._highlighted_occurrence_id = ""
        self.documentChanged.emit()
        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.errorChanged.emit()
        self.loadingChanged.emit()
        self.selectedNodeIndexChanged.emit()
        self.highlightedRegionIdChanged.emit()
        self.highlightedOccurrenceIdChanged.emit()

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

    @Slot(str)
    @Slot(str, str)
    def selectRegion(self, region_id: str, occurrence_id: str = "") -> None:
        """
        Selects a visual region, updates highlighted properties, locates its
        node index in O(1), and requests the view to scroll to it.
        """
        if not region_id:
            return

        self.set_highlighted_region_id(region_id)
        if occurrence_id:
            self.set_highlighted_occurrence_id(occurrence_id)

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
        if req_id != self._request_id:
            return  # Stale generation, drop result

        self._model.set_document(document_dto)
        self._active_job_id = document_dto.job_id
        self._active_version = document_dto.version
        self._has_document = True
        self._is_loading = False
        self._error_message = ""

        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.documentChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()

    @Slot(int, str)
    def _on_internal_doc_error(self, req_id: int, error_message: str) -> None:
        if req_id != self._request_id:
            return

        self._model.set_document(None)
        self._has_document = False
        self._is_loading = False
        self._error_message = error_message

        self.documentChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()
