# ============================================================
#  interfaces/desktop/controllers/document_viewer_controller.py
#  Desktop Presentation Controller for PDF Page Viewing & Region Overlays
# ============================================================

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

from interfaces.desktop.qt_compat import QObject, Signal, Slot, Property, QUrl
from application.services.document_viewer_service import DocumentViewerService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from application.dto.visual_region_dto import VisualRegionDTO
from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from core.entities.bounding_box import BoundingBox
from core.geometry.coordinates import (
    CoordinateTransformer,
    DisplayedImageMetrics,
    ViewportMetrics,
    FitMode,
    RectF,
    PointF,
)
from core.geometry.box_editor import (
    BoxGeometryEditor,
    HandleType,
    MIN_NORMALIZED_DIMENSION,
)


class DocumentViewerController(QObject):
    """
    Presentation controller managing PDF page raster viewing, zoom/pan navigation,
    and read-only semantic bounding box overlay projection for Document Review.

    Guarantees:
      - Asynchronous background page rendering: keeps Qt GUI responsive.
      - Generation-aware request token tracking: prevents slow render N from overwriting N+1.
      - Pure coordinate transformation bridging normalized S_norm to QML display items.
      - Read-only semantic projection: rejected regions are excluded from active visual overlays.
      - Asynchronous review apply and re-crop orchestration off GUI thread.
    """

    # Public Qt Signals for QML Property Binding
    pageChanged = Signal()
    imageChanged = Signal()
    dimensionsChanged = Signal()
    zoomChanged = Signal()
    panChanged = Signal()
    boundsChanged = Signal()
    viewportDimensionsChanged = Signal()
    itemDimensionsChanged = Signal()
    loadingChanged = Signal()
    errorChanged = Signal()
    regionsChanged = Signal()
    selectionChanged = Signal()
    editorStateChanged = Signal()
    interactionModeChanged = Signal()
    transientBoxChanged = Signal()
    selectedItemRectChanged = Signal()
    regionUpdated = Signal(str)
    regionCreated = Signal(str)
    regionDeleted = Signal(str)

    # Public signals for review apply and artifact regeneration (Visual Region Publication Pipeline)
    regionArtifactCommitted = Signal(int, str, int, str)  # (job_id, region_id, new_version, new_artifact_uri)
    regionApplied = Signal(int, str, int, str)            # Alias for regionArtifactCommitted
    applyFailed = Signal(int, str, str)                    # (job_id, region_id, error_message)

    # Internal Qt Signals for thread-safe worker-to-GUI dispatch
    _internalPageLoaded = Signal(int, object)
    _internalPageError = Signal(int, str)
    _internalApplyFinished = Signal(int, str, int, str)   # (job_id, region_id, new_version, new_artifact_uri)
    _internalApplyError = Signal(int, str, str)           # (job_id, region_id, error_message)

    def __init__(
        self,
        viewer_service: DocumentViewerService,
        region_publication_service: Optional[VisualRegionPublicationService] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.viewer_service = viewer_service
        self.region_publication_service = region_publication_service

        self._current_job_id: int = 0
        self._current_page: int = 1
        self._total_pages: int = 1
        self._page_image_uri: str = ""
        self._raster_width: int = 0
        self._raster_height: int = 0
        self._zoom: float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._viewport_width: float = 800.0
        self._viewport_height: float = 600.0
        self._item_width: float = 800.0
        self._item_height: float = 600.0
        self._is_loading: bool = False
        self._error_message: str = ""
        self._active_regions: List[Dict[str, Any]] = []

        # Interactive Bounding Box Editor State (Phase 10D / 10E Hardening)
        self._interaction_mode: str = "pan_select"  # "pan_select", "create_region"
        self._selected_region_id: str = ""
        self._editor_state: str = "idle"  # "idle", "selected", "dragging", "resizing", "creating"
        self._active_handle: str = ""
        self._interaction_start_norm_pt: Optional[PointF] = None
        self._interaction_initial_bbox: Optional[BoundingBox] = None
        self._transient_bbox: Optional[BoundingBox] = None

        self._request_id: int = 0
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="PdfViewerWorker")
        self._last_apply_future = None

        # Connect internal worker signals
        self._internalPageLoaded.connect(self._on_internal_page_loaded)
        self._internalPageError.connect(self._on_internal_page_error)
        self._internalApplyFinished.connect(self._on_internal_apply_finished)
        self._internalApplyError.connect(self._on_internal_apply_error)

    # =========================================================================
    # QML Properties
    # =========================================================================

    @Property(int, notify=pageChanged)
    def currentJobId(self) -> int:
        return self._current_job_id

    @Property(int, notify=pageChanged)
    def currentPage(self) -> int:
        return self._current_page

    @Property(int, notify=pageChanged)
    def totalPages(self) -> int:
        return self._total_pages

    @Property(str, notify=imageChanged)
    def pageImageUri(self) -> str:
        if not self._page_image_uri:
            return ""
        if not self._page_image_uri.startswith("file:"):
            return QUrl.fromLocalFile(self._page_image_uri).toString()
        return self._page_image_uri

    @Property(int, notify=dimensionsChanged)
    def rasterWidth(self) -> int:
        return self._raster_width

    @Property(int, notify=dimensionsChanged)
    def rasterHeight(self) -> int:
        return self._raster_height

    @Property(float, notify=zoomChanged)
    def zoom(self) -> float:
        return self._zoom

    @Property(float, notify=panChanged)
    def panX(self) -> float:
        return self._pan_x

    @Property(float, notify=panChanged)
    def panY(self) -> float:
        return self._pan_y

    @Property(float, notify=boundsChanged)
    def minPanX(self) -> float:
        (min_x, _), _ = self._get_viewport_metrics().get_pan_bounds(self._item_width, self._item_height)
        return min_x

    @Property(float, notify=boundsChanged)
    def maxPanX(self) -> float:
        (_, max_x), _ = self._get_viewport_metrics().get_pan_bounds(self._item_width, self._item_height)
        return max_x

    @Property(float, notify=boundsChanged)
    def minPanY(self) -> float:
        _, (min_y, _) = self._get_viewport_metrics().get_pan_bounds(self._item_width, self._item_height)
        return min_y

    @Property(float, notify=boundsChanged)
    def maxPanY(self) -> float:
        _, (_, max_y) = self._get_viewport_metrics().get_pan_bounds(self._item_width, self._item_height)
        return max_y

    @Property(float, notify=viewportDimensionsChanged)
    def viewportWidth(self) -> float:
        return self._viewport_width

    @Property(float, notify=viewportDimensionsChanged)
    def viewportHeight(self) -> float:
        return self._viewport_height

    @Property(float, notify=itemDimensionsChanged)
    def itemWidth(self) -> float:
        return self._item_width

    @Property(float, notify=itemDimensionsChanged)
    def itemHeight(self) -> float:
        return self._item_height

    @Property(bool, notify=loadingChanged)
    def isLoading(self) -> bool:
        return self._is_loading

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @Property(list, notify=regionsChanged)
    def activeRegions(self) -> List[Dict[str, Any]]:
        return self._active_regions

    @Property(str, notify=selectionChanged)
    def selectedRegionId(self) -> str:
        return self._selected_region_id

    @Property("QVariant", notify=selectionChanged)
    def selectedRegion(self) -> Dict[str, Any]:
        for r in self._active_regions:
            if r["region_id"] == self._selected_region_id:
                return r
        return {}

    @Property(bool, notify=selectionChanged)
    def hasSelection(self) -> bool:
        return bool(self._selected_region_id)

    @Property(bool, notify=selectionChanged)
    def canResetSelectedToAi(self) -> bool:
        sel = self.selectedRegion
        if not sel:
            return False
        return sel.get("origin") == "ai_detected" and sel.get("is_modified", False)

    @Property(str, notify=editorStateChanged)
    def editorState(self) -> str:
        return self._editor_state

    @Property(str, notify=editorStateChanged)
    def activeHandle(self) -> str:
        return self._active_handle

    @Property(str, notify=interactionModeChanged)
    def interactionMode(self) -> str:
        return self._interaction_mode

    @Property("QVariant", notify=transientBoxChanged)
    def transientBox(self) -> Dict[str, int]:
        if not self._transient_bbox:
            return {}
        return {
            "ymin": self._transient_bbox.ymin,
            "xmin": self._transient_bbox.xmin,
            "ymax": self._transient_bbox.ymax,
            "xmax": self._transient_bbox.xmax,
        }

    @Property("QVariant", notify=transientBoxChanged)
    def transientItemRect(self) -> Dict[str, float]:
        if not self._transient_bbox or self._raster_width <= 0 or self._raster_height <= 0:
            return {}
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width),
            raster_height=float(self._raster_height),
            item_width=float(self._item_width),
            item_height=float(self._item_height),
            fit_mode=FitMode.PRESERVE_ASPECT_FIT,
        )
        rect = CoordinateTransformer.normalized_to_item_rect(self._transient_bbox, metrics)
        return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}

    @Property("QVariant", notify=selectedItemRectChanged)
    def selectedItemRect(self) -> Dict[str, float]:
        """Item-space rectangle of the currently selected region (transient or committed)."""
        if (
            not self._selected_region_id
            or self._raster_width <= 0
            or self._raster_height <= 0
            or self._item_width <= 0
            or self._item_height <= 0
        ):
            return {}

        bbox = None
        if self._transient_bbox is not None and self._editor_state in ("dragging", "resizing"):
            bbox = self._transient_bbox
        else:
            for r in self._active_regions:
                if r["region_id"] == self._selected_region_id:
                    bbox = BoundingBox(
                        ymin=r["effective_ymin"],
                        xmin=r["effective_xmin"],
                        ymax=r["effective_ymax"],
                        xmax=r["effective_xmax"],
                    )
                    break

        if bbox is None:
            return {}

        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width),
            raster_height=float(self._raster_height),
            item_width=float(self._item_width),
            item_height=float(self._item_height),
            fit_mode=FitMode.PRESERVE_ASPECT_FIT,
        )
        rect = CoordinateTransformer.normalized_to_item_rect(bbox, metrics)
        return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}

    # =========================================================================
    # QML Slots: Navigation & Loading
    # =========================================================================

    @Slot(int, int)
    @Slot(int, int, int)
    def loadPage(self, job_id: int, page_number: int, dpi: int = 150) -> None:
        """
        Asynchronously loads and renders the specified page for job_id.
        Generation-tracked to drop stale results safely.
        """
        if job_id <= 0 or page_number <= 0:
            return

        self._request_id += 1
        req_id = self._request_id

        self._current_job_id = job_id
        self._current_page = page_number
        self._is_loading = True
        self._error_message = ""
        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.pageChanged.emit()

        def background_task():
            try:
                raster_dto = self.viewer_service.get_page_raster(
                    job_id=job_id,
                    page_number=page_number,
                    dpi=dpi,
                )
                regions_dto = self.viewer_service.get_active_page_regions(
                    job_id=job_id,
                    page_number=page_number,
                )
                self._internalPageLoaded.emit(req_id, (raster_dto, regions_dto))
            except Exception as e:
                self._internalPageError.emit(req_id, str(e))

        self._executor.submit(background_task)

    @Slot(int, int)
    @Slot(int, int, int)
    def loadPageSync(self, job_id: int, page_number: int, dpi: int = 150) -> None:
        """
        Synchronously loads and renders the specified page. Useful for tests and scripting.
        """
        if job_id <= 0 or page_number <= 0:
            return

        self._request_id += 1
        req_id = self._request_id

        self._current_job_id = job_id
        self._current_page = page_number
        self._is_loading = True
        self._error_message = ""
        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.pageChanged.emit()

        try:
            raster_dto = self.viewer_service.get_page_raster(
                job_id=job_id,
                page_number=page_number,
                dpi=dpi,
            )
            regions_dto = self.viewer_service.get_active_page_regions(
                job_id=job_id,
                page_number=page_number,
            )
            self._on_internal_page_loaded(req_id, (raster_dto, regions_dto))
        except Exception as e:
            self._on_internal_page_error(req_id, str(e))

    @Slot()
    def nextPage(self) -> None:
        if self._current_page < self._total_pages:
            self.loadPage(self._current_job_id, self._current_page + 1)

    @Slot()
    def previousPage(self) -> None:
        if self._current_page > 1:
            self.loadPage(self._current_job_id, self._current_page - 1)

    # =========================================================================
    # Visual Region Review Publication (Visual Region Publication Pipeline)
    # =========================================================================

    def _trigger_async_apply(self, job_id: int, region_id: str) -> None:
        """
        Dispatches background review publication via VisualRegionPublicationService.
        Guarantees non-blocking execution off the Qt GUI thread.
        """
        if not self.region_publication_service or not region_id:
            return

        resolved_job_id = job_id if job_id > 0 else self._current_job_id
        if resolved_job_id <= 0:
            for r in self._active_regions:
                if r.get("region_id") == region_id:
                    resolved_job_id = r.get("job_id", 0)
                    break
        if resolved_job_id <= 0:
            return

        def background_apply():
            try:
                res = self.region_publication_service.publish_region_review(job_id=resolved_job_id, region_id=region_id)
                if not res.success:
                    self._internalApplyError.emit(
                        resolved_job_id, region_id, res.status_message or "Visual region publication failed"
                    )
                    return
                new_ver = res.artifact_version if res.artifact_version is not None else 0
                new_uri = res.artifact_uri or ""
                self._internalApplyFinished.emit(resolved_job_id, region_id, new_ver, new_uri)
            except Exception as e:
                self._internalApplyError.emit(resolved_job_id, region_id, str(e))

        self._last_apply_future = self._executor.submit(background_apply)

    def wait_for_apply(self, timeout: float = 3.0) -> None:
        """Waits for any in-flight background review apply task to complete."""
        if hasattr(self, "_last_apply_future") and self._last_apply_future is not None:
            try:
                self._last_apply_future.result(timeout=timeout)
            except Exception:
                pass

    def apply_region_sync(
        self, job_id: int, region_id: str
    ) -> Optional[RegionPublicationResultDTO]:
        """
        Synchronously applies review and triggers update signals.
        Useful for unit/integration tests and deterministic headless verification.
        """
        if not self.region_publication_service or not region_id:
            return None

        resolved_job_id = job_id if job_id > 0 else self._current_job_id
        if resolved_job_id <= 0:
            for r in self._active_regions:
                if r.get("region_id") == region_id:
                    resolved_job_id = r.get("job_id", 0)
                    break
        if resolved_job_id <= 0:
            return None

        try:
            res = self.region_publication_service.publish_region_review(job_id=resolved_job_id, region_id=region_id)
            if not res.success:
                self._on_internal_apply_error(
                    resolved_job_id, region_id, res.status_message or "Visual region publication failed"
                )
                return res
            new_ver = res.artifact_version if res.artifact_version is not None else 0
            new_uri = res.artifact_uri or ""
            self._on_internal_apply_finished(resolved_job_id, region_id, new_ver, new_uri)
            return res
        except Exception as e:
            self._on_internal_apply_error(resolved_job_id, region_id, str(e))
            raise

    def _on_internal_apply_finished(
        self, job_id: int, region_id: str, new_version: int, new_artifact_uri: str
    ) -> None:
        """
        GUI-thread slot called when background apply completes successfully.
        Emits regionArtifactCommitted and regionApplied public signals and reloads page regions if matching current page.
        """
        if job_id == self._current_job_id:
            self._reload_page_regions()
            self.regionsChanged.emit()
            if self._selected_region_id:
                self.selectionChanged.emit()
        self.regionArtifactCommitted.emit(job_id, region_id, new_version, new_artifact_uri)
        self.regionApplied.emit(job_id, region_id, new_version, new_artifact_uri)

    def _on_internal_apply_error(
        self, job_id: int, region_id: str, error_msg: str
    ) -> None:
        """
        GUI-thread slot called when background apply encounters an error.
        Emits applyFailed public signal and updates error message.
        """
        self._error_message = f"Failed to apply review for region {region_id}: {error_msg}"
        self.errorChanged.emit()
        self.applyFailed.emit(job_id, region_id, error_msg)

    @Slot()
    def shutdown(self) -> None:
        """Explicit shutdown releasing executor resources."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _get_viewport_metrics(self) -> ViewportMetrics:
        return ViewportMetrics(
            zoom=self._zoom,
            pan_x=self._pan_x,
            pan_y=self._pan_y,
            viewport_width=self._viewport_width,
            viewport_height=self._viewport_height,
        )

    def _clamp_and_update_pan(self, target_pan_x: float, target_pan_y: float) -> bool:
        metrics = self._get_viewport_metrics()
        clamped_x, clamped_y = metrics.clamp_pan(
            target_pan_x,
            target_pan_y,
            self._item_width,
            self._item_height,
        )
        changed = False
        if abs(self._pan_x - clamped_x) > 1e-4 or abs(self._pan_y - clamped_y) > 1e-4:
            self._pan_x = clamped_x
            self._pan_y = clamped_y
            changed = True
        return changed

    # =========================================================================
    # Model B Architecture: Viewport, Base Scene, and Image Display Semantics
    # =========================================================================
    # 1. Viewport (ViewportMetrics.viewport_width / viewport_height):
    #    - Represents the physical visible clipping area in QML (viewportArea).
    #    - Constrains the visual panning boundaries (minPan, maxPan).
    #
    # 2. Base Scene (pageScene.width / pageScene.height & item_width / item_height):
    #    - Represents the unzoomed base coordinate space S_item.
    #    - In Model B, the base scene dimensions are synchronized with the viewport:
    #        item_width  == viewport_width
    #        item_height == viewport_height
    #    - Both pageImage and BoundingBoxOverlay occupy this base space with anchors.fill: parent.
    #    - At zoom = 1.0, the base scene matches the visible viewport exactly.
    #
    # 3. Image Display Rectangle (DisplayedImageMetrics.displayed_rect):
    #    - Inside the base scene (item_width, item_height), the PDF raster image is fitted
    #      using FitMode.PRESERVE_ASPECT_FIT:
    #        uniform_scale = min(item_width / raster_width, item_height / raster_height)
    #        displayed_width  = raster_width * uniform_scale
    #        displayed_height = raster_height * uniform_scale
    #        offset_x = (item_width - displayed_width) / 2.0
    #        offset_y = (item_height - displayed_height) / 2.0
    #    - Semantic bounding boxes from S_norm map into this exact displayed rectangle.
    #
    # 4. Viewport Affine Transform (Scene Graph Scaling & Translation):
    #    - pageScene applies the affine transform to both the image and the overlay:
    #        scale: controller.zoom (transformOrigin: Item.TopLeft)
    #        x: -controller.panX
    #        y: -controller.panY
    #    - Any point (x_item, y_item) lands at:
    #        x_viewport = (x_item * zoom) - pan_x
    #        y_viewport = (y_item * zoom) - pan_y
    # =========================================================================

    @Slot(float, float)
    def setViewportDimensions(self, width: float, height: float) -> None:
        """
        Sets the viewport container dimensions.
        In Model B, the base scene dimensions (item_width, item_height) synchronize
        with the viewport dimensions so that zoom=1.0 represents the full-viewport fit-to-page baseline.
        """
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        dim_changed = (abs(self._viewport_width - w) > 1e-4 or abs(self._viewport_height - h) > 1e-4)
        if dim_changed:
            self._viewport_width = w
            self._viewport_height = h
            self.viewportDimensionsChanged.emit()

        # In Model B, base scene dimensions track the viewport dimensions
        self.setItemDimensions(w, h)

    @Slot(float, float)
    def setItemDimensions(self, width: float, height: float) -> None:
        """Sets the page item container base dimensions and re-clamps pan."""
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        dim_changed = (abs(self._item_width - w) > 1e-4 or abs(self._item_height - h) > 1e-4)
        if dim_changed:
            self._item_width = w
            self._item_height = h
            self.itemDimensionsChanged.emit()
            if self._selected_region_id:
                self.selectedItemRectChanged.emit()

        pan_changed = self._clamp_and_update_pan(self._pan_x, self._pan_y)
        self.boundsChanged.emit()
        if pan_changed:
            self.panChanged.emit()

    @Slot(float)
    def setZoom(self, zoom: float) -> None:
        """Zooms around the center of the viewport."""
        anchor_x = self._viewport_width / 2.0
        anchor_y = self._viewport_height / 2.0
        self.zoomAt(zoom, anchor_x, anchor_y)

    @Slot(float, float, float)
    def zoomAt(self, zoom: float, anchor_x: float, anchor_y: float) -> None:
        """Zooms around a specific viewport anchor coordinate, preserving spatial stability."""
        new_zoom = max(0.1, min(10.0, float(zoom)))
        metrics = self._get_viewport_metrics()
        new_metrics = metrics.zoom_around_anchor(
            new_zoom=new_zoom,
            anchor_x=float(anchor_x),
            anchor_y=float(anchor_y),
            content_base_width=self._item_width,
            content_base_height=self._item_height,
        )

        zoom_changed = abs(self._zoom - new_metrics.zoom) > 1e-4
        pan_changed = (abs(self._pan_x - new_metrics.pan_x) > 1e-4 or abs(self._pan_y - new_metrics.pan_y) > 1e-4)

        self._zoom = new_metrics.zoom
        self._pan_x = new_metrics.pan_x
        self._pan_y = new_metrics.pan_y

        self.boundsChanged.emit()
        if zoom_changed:
            self.zoomChanged.emit()
        if pan_changed:
            self.panChanged.emit()

    @Slot(float, float)
    def setPan(self, pan_x: float, pan_y: float) -> None:
        """Sets pan coordinates clamped against valid content bounds."""
        if self._clamp_and_update_pan(float(pan_x), float(pan_y)):
            self.boundsChanged.emit()
            self.panChanged.emit()

    @Slot(float, float)
    def panBy(self, delta_x: float, delta_y: float) -> None:
        """Adjusts pan by the given delta and clamps to bounds."""
        self.setPan(self._pan_x + float(delta_x), self._pan_y + float(delta_y))

    @Slot()
    def fitToPage(self) -> None:
        """
        Restores the view to the Fit-to-Page baseline.
        In Model B, the base scene item dimensions (item_width, item_height) synchronize
        with the viewport dimensions, and DisplayedImageMetrics automatically fits the
        page raster into the scene with FitMode.PRESERVE_ASPECT_FIT.
        Therefore, zoom = 1.0 with centered pan (0.0, 0.0) is the canonical Fit-to-Page state.
        """
        self._zoom = 1.0
        self._clamp_and_update_pan(0.0, 0.0)
        self.boundsChanged.emit()
        self.zoomChanged.emit()
        self.panChanged.emit()

    @Slot()
    def resetView(self) -> None:
        """Resets the view to the Fit-to-Page baseline (zoom 1.0, centered pan)."""
        self.fitToPage()

    @Slot()
    def zoomToActualSize(self) -> None:
        """
        Scales zoom so that 1 image raster pixel equals 1 screen viewport pixel.
        Calculated as the inverse of DisplayedImageMetrics.uniform_scale.
        """
        if (
            self._raster_width > 0
            and self._raster_height > 0
            and self._item_width > 0
            and self._item_height > 0
        ):
            metrics = DisplayedImageMetrics(
                raster_width=float(self._raster_width),
                raster_height=float(self._raster_height),
                item_width=float(self._item_width),
                item_height=float(self._item_height),
                fit_mode=FitMode.PRESERVE_ASPECT_FIT,
            )
            if metrics.uniform_scale > 0:
                actual_zoom = 1.0 / metrics.uniform_scale
                self.setZoom(actual_zoom)

    # =========================================================================
    # QML Slots: Coordinate Transformation Bridging
    # =========================================================================

    @Slot(float, float, str, result=list)
    def getOverlayRects(
        self,
        item_width: float,
        item_height: float,
        fit_mode_str: str = "preserve_aspect_fit",
    ) -> List[Dict[str, Any]]:
        """
        Calculates item-space (S_item) coordinates for all active visual regions on the current page.
        """
        if (
            self._raster_width <= 0
            or self._raster_height <= 0
            or item_width <= 0
            or item_height <= 0
            or not self._active_regions
        ):
            return []

        fit_mode = FitMode.STRETCH if fit_mode_str == "stretch" else FitMode.PRESERVE_ASPECT_FIT
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width),
            raster_height=float(self._raster_height),
            item_width=float(item_width),
            item_height=float(item_height),
            fit_mode=fit_mode,
        )

        results = []
        for r in self._active_regions:
            bbox = BoundingBox(
                ymin=r["effective_ymin"],
                xmin=r["effective_xmin"],
                ymax=r["effective_ymax"],
                xmax=r["effective_xmax"],
            )
            item_rect = CoordinateTransformer.normalized_to_item_rect(bbox, metrics)
            results.append({
                "region_id": r["region_id"],
                "display_order": r["display_order"],
                "origin": r["origin"],
                "review_status": r["review_status"],
                "is_modified": r.get("is_modified", False),
                "is_selected": (r["region_id"] == self._selected_region_id),
                "x": item_rect.x,
                "y": item_rect.y,
                "width": item_rect.width,
                "height": item_rect.height,
                "effective_ymin": bbox.ymin,
                "effective_xmin": bbox.xmin,
                "effective_ymax": bbox.ymax,
                "effective_xmax": bbox.xmax,
            })

        return results

    @Slot(float, float, str, result=list)
    def getOverlayRectsInViewport(
        self,
        item_width: float,
        item_height: float,
        fit_mode_str: str = "preserve_aspect_fit",
    ) -> List[Dict[str, Any]]:
        """
        Calculates viewport-space (S_viewport) coordinates for all active visual regions.
        Corresponds exactly to: S_norm -> S_raster -> S_item -> S_viewport.
        """
        if (
            self._raster_width <= 0
            or self._raster_height <= 0
            or item_width <= 0
            or item_height <= 0
            or not self._active_regions
        ):
            return []

        fit_mode = FitMode.STRETCH if fit_mode_str == "stretch" else FitMode.PRESERVE_ASPECT_FIT
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width),
            raster_height=float(self._raster_height),
            item_width=float(item_width),
            item_height=float(item_height),
            fit_mode=fit_mode,
        )
        viewport = self._get_viewport_metrics()

        results = []
        for r in self._active_regions:
            is_sel = (r["region_id"] == self._selected_region_id)
            if is_sel and self._transient_bbox is not None and self._editor_state in ("dragging", "resizing"):
                bbox = self._transient_bbox
            else:
                bbox = BoundingBox(
                    ymin=r["effective_ymin"],
                    xmin=r["effective_xmin"],
                    ymax=r["effective_ymax"],
                    xmax=r["effective_xmax"],
                )
            vp_rect = CoordinateTransformer.normalized_to_viewport_rect(bbox, metrics, viewport)
            results.append({
                "region_id": r["region_id"],
                "display_order": r["display_order"],
                "origin": r["origin"],
                "review_status": r["review_status"],
                "is_modified": r.get("is_modified", False),
                "is_selected": is_sel,
                "x": vp_rect.x,
                "y": vp_rect.y,
                "width": vp_rect.width,
                "height": vp_rect.height,
                "effective_ymin": bbox.ymin,
                "effective_xmin": bbox.xmin,
                "effective_ymax": bbox.ymax,
                "effective_xmax": bbox.xmax,
            })

        return results

    @Slot(int, int, int, int, float, float, str, result=dict)
    def transformNormalizedToItem(
        self,
        ymin: int,
        xmin: int,
        ymax: int,
        xmax: int,
        item_width: float,
        item_height: float,
        fit_mode_str: str = "preserve_aspect_fit",
    ) -> Dict[str, float]:
        """S_norm -> S_item helper slot for QML."""
        fit_mode = FitMode.STRETCH if fit_mode_str == "stretch" else FitMode.PRESERVE_ASPECT_FIT
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width or 1000),
            raster_height=float(self._raster_height or 1000),
            item_width=float(item_width),
            item_height=float(item_height),
            fit_mode=fit_mode,
        )
        bbox = BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)
        rect = CoordinateTransformer.normalized_to_item_rect(bbox, metrics)
        return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}

    @Slot(float, float, str, result="QVariant")
    def getDisplayedRect(
        self,
        item_width: float,
        item_height: float,
        fit_mode_str: str = "preserve_aspect_fit",
    ) -> Dict[str, float]:
        """
        Calculates the rendered image rectangle within the item container (displayed_rect).
        Used by the QML overlay to constrain document-surface interactions (such as manual
        region creation) strictly to the rendered page surface, allowing clicks on surrounding
        letterbox/pillarbox margins to reach the canvas pan handler.
        """
        if self._raster_width <= 0 or self._raster_height <= 0 or item_width <= 0 or item_height <= 0:
            return {"x": 0.0, "y": 0.0, "width": item_width, "height": item_height}
        fit_mode = FitMode.STRETCH if fit_mode_str == "stretch" else FitMode.PRESERVE_ASPECT_FIT
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width),
            raster_height=float(self._raster_height),
            item_width=item_width,
            item_height=item_height,
            fit_mode=fit_mode,
        )
        rect = metrics.displayed_rect
        return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}

    @Slot(float, float, float, float, float, float, float, float, float, str, result=dict)
    def transformViewportToNormalized(
        self,
        vp_x: float,
        vp_y: float,
        vp_w: float,
        vp_h: float,
        item_width: float,
        item_height: float,
        zoom: float,
        pan_x: float,
        pan_y: float,
        fit_mode_str: str = "preserve_aspect_fit",
    ) -> Dict[str, int]:
        """S_viewport -> S_norm helper slot for QML (foundation for inverse transformations)."""
        fit_mode = FitMode.STRETCH if fit_mode_str == "stretch" else FitMode.PRESERVE_ASPECT_FIT
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width or 1000),
            raster_height=float(self._raster_height or 1000),
            item_width=float(item_width),
            item_height=float(item_height),
            fit_mode=fit_mode,
        )
        viewport = ViewportMetrics(zoom=float(zoom or 1.0), pan_x=float(pan_x), pan_y=float(pan_y))
        vp_rect = RectF(x=vp_x, y=vp_y, width=vp_w, height=vp_h)
        bbox = CoordinateTransformer.viewport_to_normalized_bbox(vp_rect, metrics, viewport)
        return {
            "ymin": bbox.ymin,
            "xmin": bbox.xmin,
            "ymax": bbox.ymax,
            "xmax": bbox.xmax,
        }

    # =========================================================================
    # QML Slots: Interactive Region Editing (Phase 10D)
    # =========================================================================

    def _norm_point_from_viewport(self, vp_x: float, vp_y: float) -> PointF:
        """Converts viewport coordinates S_viewport to normalized coordinates S_norm."""
        metrics = DisplayedImageMetrics(
            raster_width=float(self._raster_width or 1000),
            raster_height=float(self._raster_height or 1000),
            item_width=float(self._item_width or 800),
            item_height=float(self._item_height or 600),
            fit_mode=FitMode.PRESERVE_ASPECT_FIT,
        )
        viewport = self._get_viewport_metrics()
        return CoordinateTransformer.viewport_to_normalized_point(
            PointF(x=float(vp_x), y=float(vp_y)),
            metrics,
            viewport,
        )

    def _reload_page_regions(self) -> None:
        """Reloads active regions for current page from the application service."""
        if self._current_job_id <= 0 or self._current_page <= 0:
            self._active_regions = []
            return
        regions_dto = self.viewer_service.get_active_page_regions(
            job_id=self._current_job_id,
            page_number=self._current_page,
        )
        self._active_regions = [
            {
                "region_id": r.region_id,
                "job_id": r.job_id,
                "page_number": r.page_number,
                "display_order": r.display_order,
                "origin": r.origin,
                "review_status": r.review_status,
                "is_modified": r.is_modified,
                "effective_ymin": r.effective_bbox.ymin,
                "effective_xmin": r.effective_bbox.xmin,
                "effective_ymax": r.effective_bbox.ymax,
                "effective_xmax": r.effective_bbox.xmax,
            }
            for r in regions_dto
        ]
        if self._selected_region_id:
            self.selectedItemRectChanged.emit()

    @Slot(float, float, result=dict)
    def normPointFromViewport(self, vp_x: float, vp_y: float) -> Dict[str, float]:
        """Exposes S_viewport -> S_norm point mapping directly to QML."""
        pt = self._norm_point_from_viewport(vp_x, vp_y)
        return {"x": pt.x, "y": pt.y}

    @Slot(str)
    def selectRegion(self, region_id: str) -> None:
        """Selects an active region by its unique region_id."""
        if not region_id:
            self.clearSelection()
            return

        target = None
        for r in self._active_regions:
            if r["region_id"] == region_id:
                target = r
                break

        if not target:
            self.clearSelection()
            return

        changed = (self._selected_region_id != region_id or self._editor_state != "selected")
        self._selected_region_id = region_id
        self._editor_state = "selected"
        self._active_handle = ""
        self._transient_bbox = None
        self._interaction_start_norm_pt = None
        self._interaction_initial_bbox = None

        if changed:
            self.selectionChanged.emit()
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            self.selectedItemRectChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        """Deselects the currently selected region and returns editor to idle."""
        changed = bool(self._selected_region_id) or self._editor_state != "idle"
        self._selected_region_id = ""
        self._editor_state = "idle"
        self._active_handle = ""
        self._transient_bbox = None
        self._interaction_start_norm_pt = None
        self._interaction_initial_bbox = None

        if changed:
            self.selectionChanged.emit()
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            self.selectedItemRectChanged.emit()

    @Slot(str)
    def setInteractionMode(self, mode: str) -> None:
        """Sets the active pointer interaction mode ('pan_select' or 'create_region')."""
        if mode not in ("pan_select", "create_region"):
            return
        if self._interaction_mode != mode:
            self._interaction_mode = mode
            self.interactionModeChanged.emit()

    @Slot(str, float, float)
    def startDrag(self, region_id: str, vp_x: float, vp_y: float) -> None:
        """Initiates dragging of a visual region at viewport pointer coordinates."""
        if not region_id:
            return

        target = None
        for r in self._active_regions:
            if r["region_id"] == region_id:
                target = r
                break
        if not target:
            return

        sel_changed = (self._selected_region_id != region_id)
        self._selected_region_id = region_id
        self._editor_state = "dragging"
        self._active_handle = ""
        bbox = BoundingBox(
            ymin=target["effective_ymin"],
            xmin=target["effective_xmin"],
            ymax=target["effective_ymax"],
            xmax=target["effective_xmax"],
        )
        self._interaction_initial_bbox = bbox
        self._transient_bbox = bbox
        self._interaction_start_norm_pt = self._norm_point_from_viewport(vp_x, vp_y)

        if sel_changed:
            self.selectionChanged.emit()
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()
        if sel_changed:
            self.selectedItemRectChanged.emit()

    @Slot(float, float)
    def updateDrag(self, vp_x: float, vp_y: float) -> None:
        """Updates in-memory transient bbox during drag translation. Zero persistence."""
        if (
            self._editor_state != "dragging"
            or not self._interaction_start_norm_pt
            or not self._interaction_initial_bbox
        ):
            return

        curr_pt = self._norm_point_from_viewport(vp_x, vp_y)
        delta_x = curr_pt.x - self._interaction_start_norm_pt.x
        delta_y = curr_pt.y - self._interaction_start_norm_pt.y

        new_bbox = BoxGeometryEditor.translate_bbox(
            self._interaction_initial_bbox,
            delta_x,
            delta_y,
        )
        if self._transient_bbox != new_bbox:
            self._transient_bbox = new_bbox
            self.transientBoxChanged.emit()
            self.selectedItemRectChanged.emit()

    @Slot()
    def commitDrag(self) -> None:
        """Commits drag translation to persistent domain storage via Application Service."""
        if (
            self._editor_state != "dragging"
            or not self._transient_bbox
            or not self._selected_region_id
        ):
            self.cancelDrag()
            return

        target_id = self._selected_region_id
        new_bbox = self._transient_bbox

        # If geometry did not actually change (e.g. click to select without translation),
        # transition cleanly to selected without mutating domain state or marking dirty.
        if self._interaction_initial_bbox is not None and new_bbox == self._interaction_initial_bbox:
            self._editor_state = "selected"
            self._transient_bbox = None
            self._interaction_start_norm_pt = None
            self._interaction_initial_bbox = None
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            return

        try:
            self.viewer_service.update_region_geometry(target_id, new_bbox)
            self._reload_page_regions()
            self._editor_state = "selected"
            self._transient_bbox = None
            self._interaction_start_norm_pt = None
            self._interaction_initial_bbox = None
            self.regionUpdated.emit(target_id)
            self.selectionChanged.emit()
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            self.regionsChanged.emit()
            self._trigger_async_apply(self._current_job_id, target_id)
        except Exception as e:
            self._error_message = f"Failed to persist drag: {str(e)}"
            self.errorChanged.emit()
            self.cancelDrag()

    @Slot()
    def cancelDrag(self) -> None:
        """Aborts current drag interaction, restoring pre-drag state."""
        self._editor_state = "selected" if self._selected_region_id else "idle"
        self._transient_bbox = None
        self._interaction_start_norm_pt = None
        self._interaction_initial_bbox = None
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()
        self.selectedItemRectChanged.emit()

    @Slot(str, str, float, float)
    def startResize(self, region_id: str, handle: str, vp_x: float, vp_y: float) -> None:
        """Initiates resizing via one of the 8 handles at viewport pointer coordinates."""
        if not region_id:
            return

        target = None
        for r in self._active_regions:
            if r["region_id"] == region_id:
                target = r
                break
        if not target:
            return

        sel_changed = (self._selected_region_id != region_id)
        self._selected_region_id = region_id
        self._editor_state = "resizing"
        self._active_handle = handle.lower()
        bbox = BoundingBox(
            ymin=target["effective_ymin"],
            xmin=target["effective_xmin"],
            ymax=target["effective_ymax"],
            xmax=target["effective_xmax"],
        )
        self._interaction_initial_bbox = bbox
        self._transient_bbox = bbox

        if sel_changed:
            self.selectionChanged.emit()
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()
        if sel_changed:
            self.selectedItemRectChanged.emit()

    @Slot(float, float)
    def updateResize(self, vp_x: float, vp_y: float) -> None:
        """Updates in-memory transient bbox during handle resize. Zero persistence."""
        if (
            self._editor_state != "resizing"
            or not self._active_handle
            or not self._interaction_initial_bbox
        ):
            return

        curr_pt = self._norm_point_from_viewport(vp_x, vp_y)
        new_bbox = BoxGeometryEditor.resize_bbox(
            self._interaction_initial_bbox,
            self._active_handle,
            curr_pt,
        )
        if self._transient_bbox != new_bbox:
            self._transient_bbox = new_bbox
            self.transientBoxChanged.emit()
            self.selectedItemRectChanged.emit()

    @Slot()
    def commitResize(self) -> None:
        """Commits resize operation to persistent domain storage via Application Service."""
        if (
            self._editor_state != "resizing"
            or not self._transient_bbox
            or not self._selected_region_id
        ):
            self.cancelResize()
            return

        target_id = self._selected_region_id
        new_bbox = self._transient_bbox

        # If geometry did not actually change, transition cleanly to selected.
        if self._interaction_initial_bbox is not None and new_bbox == self._interaction_initial_bbox:
            self._editor_state = "selected"
            self._active_handle = ""
            self._transient_bbox = None
            self._interaction_initial_bbox = None
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            return

        try:
            self.viewer_service.update_region_geometry(target_id, new_bbox)
            self._reload_page_regions()
            self._editor_state = "selected"
            self._active_handle = ""
            self._transient_bbox = None
            self._interaction_initial_bbox = None
            self.regionUpdated.emit(target_id)
            self.selectionChanged.emit()
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            self.regionsChanged.emit()
            self._trigger_async_apply(self._current_job_id, target_id)
        except Exception as e:
            self._error_message = f"Failed to persist resize: {str(e)}"
            self.errorChanged.emit()
            self.cancelResize()

    @Slot()
    def cancelResize(self) -> None:
        """Aborts current resize interaction, restoring pre-resize state."""
        self._editor_state = "selected" if self._selected_region_id else "idle"
        self._active_handle = ""
        self._transient_bbox = None
        self._interaction_initial_bbox = None
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()
        self.selectedItemRectChanged.emit()

    @Slot(float, float)
    def startCreateManual(self, vp_x: float, vp_y: float) -> None:
        """Initiates manual bounding box creation on empty canvas at viewport pointer coordinates."""
        self.clearSelection()
        self._editor_state = "creating"
        self._interaction_start_norm_pt = self._norm_point_from_viewport(vp_x, vp_y)
        self._transient_bbox = None
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()

    @Slot(float, float)
    def updateCreateManual(self, vp_x: float, vp_y: float) -> None:
        """Updates rubber-band candidate box during manual region drawing."""
        if self._editor_state != "creating" or not self._interaction_start_norm_pt:
            return

        curr_pt = self._norm_point_from_viewport(vp_x, vp_y)
        candidate = BoxGeometryEditor.create_manual_bbox(
            self._interaction_start_norm_pt,
            curr_pt,
        )
        if self._transient_bbox != candidate:
            self._transient_bbox = candidate
            self.transientBoxChanged.emit()

    @Slot()
    def commitCreateManual(self) -> None:
        """Commits newly created manual visual region into persistent storage."""
        if self._editor_state != "creating" or not self._transient_bbox:
            self.cancelCreateManual()
            return

        bbox = self._transient_bbox
        try:
            dto = self.viewer_service.create_manual_region(
                self._current_job_id,
                self._current_page,
                bbox,
            )
            self._reload_page_regions()
            self._selected_region_id = dto.region_id
            self._editor_state = "selected"
            self._transient_bbox = None
            self._interaction_start_norm_pt = None
            self.regionCreated.emit(dto.region_id)
            self.selectionChanged.emit()
            self.editorStateChanged.emit()
            self.transientBoxChanged.emit()
            self.selectedItemRectChanged.emit()
            self.regionsChanged.emit()
            self._trigger_async_apply(self._current_job_id, dto.region_id)
        except Exception as e:
            self._error_message = f"Failed to create manual region: {str(e)}"
            self.errorChanged.emit()
            self.cancelCreateManual()

    @Slot()
    def cancelCreateManual(self) -> None:
        """Aborts manual region creation."""
        self._editor_state = "idle"
        self._transient_bbox = None
        self._interaction_start_norm_pt = None
        self.editorStateChanged.emit()
        self.transientBoxChanged.emit()

    @Slot()
    def deleteSelectedRegion(self) -> None:
        """Rejects (deletes) the currently selected region. Preserves historical audit log."""
        if not self._selected_region_id:
            return
        target_id = self._selected_region_id
        try:
            self.viewer_service.reject_region(target_id)
            self.clearSelection()
            self._reload_page_regions()
            self.regionDeleted.emit(target_id)
            self.regionsChanged.emit()
            self._trigger_async_apply(self._current_job_id, target_id)
        except Exception as e:
            self._error_message = f"Failed to delete region: {str(e)}"
            self.errorChanged.emit()

    @Slot()
    def resetSelectedRegionToAi(self) -> None:
        """Resets the selected region geometry back to its original AI detected_bbox."""
        if not self._selected_region_id:
            return
        target_id = self._selected_region_id
        try:
            self.viewer_service.reset_region_to_ai(target_id)
            self._reload_page_regions()
            self.regionUpdated.emit(target_id)
            self.selectionChanged.emit()
            self.regionsChanged.emit()
            self._trigger_async_apply(self._current_job_id, target_id)
        except Exception as e:
            self._error_message = f"Failed to reset region to AI: {str(e)}"
            self.errorChanged.emit()

    @Slot(float, float, float, float, result=list)
    @Slot(float, float, float, float, float, result=list)
    def getHandleRects(
        self,
        item_x: float,
        item_y: float,
        item_w: float,
        item_h: float,
        handle_size: float = 8.0,
    ) -> List[Dict[str, Any]]:
        """Computes 8 handle rectangles in item space for the given box item rectangle."""
        handles = BoxGeometryEditor.compute_8_handles(
            RectF(x=item_x, y=item_y, width=item_w, height=item_h),
            handle_size=handle_size,
        )
        return [
            {
                "handle": h_name,
                "x": rect.x,
                "y": rect.y,
                "width": rect.width,
                "height": rect.height,
            }
            for h_name, rect in handles.items()
        ]

    # =========================================================================
    # Internal Signal Handlers
    # =========================================================================

    def _on_internal_page_loaded(self, req_id: int, payload: object) -> None:
        """Executed on GUI thread when page rendering finishes."""
        if req_id != self._request_id:
            # Stale request result: silently drop
            return

        raster_dto, regions_dto = payload  # type: ignore
        self._current_job_id = raster_dto.job_id
        self._current_page = raster_dto.page_number
        self._total_pages = raster_dto.total_pages
        self._page_image_uri = raster_dto.image_uri
        self._raster_width = raster_dto.raster_width
        self._raster_height = raster_dto.raster_height
        self._error_message = ""
        self._is_loading = False

        self._active_regions = [
            {
                "region_id": r.region_id,
                "job_id": r.job_id,
                "page_number": r.page_number,
                "display_order": r.display_order,
                "origin": r.origin,
                "review_status": r.review_status,
                "is_modified": r.is_modified,
                "effective_ymin": r.effective_bbox.ymin,
                "effective_xmin": r.effective_bbox.xmin,
                "effective_ymax": r.effective_bbox.ymax,
                "effective_xmax": r.effective_bbox.xmax,
            }
            for r in regions_dto
        ]

        self.clearSelection()
        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.pageChanged.emit()
        self.imageChanged.emit()
        self.dimensionsChanged.emit()
        self.regionsChanged.emit()

    def _on_internal_page_error(self, req_id: int, error_msg: str) -> None:
        """Executed on GUI thread when page rendering fails."""
        if req_id != self._request_id:
            return

        self._is_loading = False
        self._error_message = error_msg
        self._active_regions = []
        self.clearSelection()

        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.regionsChanged.emit()
