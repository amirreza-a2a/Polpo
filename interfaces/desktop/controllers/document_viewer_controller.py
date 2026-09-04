# ============================================================
#  interfaces/desktop/controllers/document_viewer_controller.py
#  Desktop Presentation Controller for PDF Page Viewing & Region Overlays
# ============================================================

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

from interfaces.desktop.qt_compat import QObject, Signal, Slot, Property, QUrl
from application.services.document_viewer_service import DocumentViewerService
from core.entities.bounding_box import BoundingBox
from core.geometry.coordinates import (
    CoordinateTransformer,
    DisplayedImageMetrics,
    ViewportMetrics,
    FitMode,
    RectF,
    PointF,
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

    # Internal Qt Signals for thread-safe worker-to-GUI dispatch
    _internalPageLoaded = Signal(int, object)
    _internalPageError = Signal(int, str)

    def __init__(
        self,
        viewer_service: DocumentViewerService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.viewer_service = viewer_service

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

        self._request_id: int = 0
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="PdfViewerWorker")

        # Connect internal worker signals
        self._internalPageLoaded.connect(self._on_internal_page_loaded)
        self._internalPageError.connect(self._on_internal_page_error)

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

        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.regionsChanged.emit()
