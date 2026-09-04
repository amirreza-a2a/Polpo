# ============================================================
#  core/geometry/coordinates.py
#  Pure Python Coordinate Transformations across S_norm, S_raster, S_item, S_viewport
# ============================================================

import math
from dataclasses import dataclass
from enum import Enum
from typing import Tuple, Optional

from core.entities.bounding_box import BoundingBox


class FitMode(str, Enum):
    """Specifies the visual scaling policy for the raster image within an item."""
    PRESERVE_ASPECT_FIT = "preserve_aspect_fit"
    STRETCH = "stretch"


@dataclass(frozen=True)
class PointF:
    """Floating-point 2D point representation."""
    x: float
    y: float

    @property
    def as_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class RectF:
    """
    Floating-point 2D rectangle representation.
    Origin at Top-Left (x, y) with positive width and height extending right and down.
    """
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        if self.width < 0 or self.height < 0:
            raise ValueError(f"Rectangle dimensions cannot be negative: width={self.width}, height={self.height}")

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + (self.width / 2.0)

    @property
    def center_y(self) -> float:
        return self.y + (self.height / 2.0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class DisplayedImageMetrics:
    """
    Encapsulates raster image dimensions and the target QML item container size.
    Calculates exact scale factors and centering letterbox/pillarbox offsets.
    """
    raster_width: float
    raster_height: float
    item_width: float
    item_height: float
    fit_mode: FitMode = FitMode.PRESERVE_ASPECT_FIT

    def __post_init__(self):
        if self.raster_width <= 0 or self.raster_height <= 0:
            raise ValueError(f"Raster dimensions must be positive, got {self.raster_width}x{self.raster_height}")
        if self.item_width <= 0 or self.item_height <= 0:
            raise ValueError(f"Item dimensions must be positive, got {self.item_width}x{self.item_height}")

    @property
    def scale_x(self) -> float:
        if self.fit_mode == FitMode.STRETCH:
            return self.item_width / self.raster_width
        return self.uniform_scale

    @property
    def scale_y(self) -> float:
        if self.fit_mode == FitMode.STRETCH:
            return self.item_height / self.raster_height
        return self.uniform_scale

    @property
    def uniform_scale(self) -> float:
        """Uniform aspect-preserving scale factor."""
        sx = self.item_width / self.raster_width
        sy = self.item_height / self.raster_height
        return min(sx, sy)

    @property
    def displayed_width(self) -> float:
        """Width of the rendered image inside the item container."""
        return self.raster_width * self.scale_x

    @property
    def displayed_height(self) -> float:
        """Height of the rendered image inside the item container."""
        return self.raster_height * self.scale_y

    @property
    def offset_x(self) -> float:
        """Horizontal pillarbox offset to center the image."""
        if self.fit_mode == FitMode.STRETCH:
            return 0.0
        return (self.item_width - self.displayed_width) / 2.0

    @property
    def offset_y(self) -> float:
        """Vertical letterbox offset to center the image."""
        if self.fit_mode == FitMode.STRETCH:
            return 0.0
        return (self.item_height - self.displayed_height) / 2.0

    @property
    def displayed_rect(self) -> RectF:
        """Rectangle of the image content within the item."""
        return RectF(
            x=self.offset_x,
            y=self.offset_y,
            width=self.displayed_width,
            height=self.displayed_height,
        )


@dataclass(frozen=True)
class ViewportMetrics:
    """
    Encapsulates viewport zoom and pan state.
    zoom: scale factor (> 0, default 1.0)
    pan_x: horizontal offset (default 0.0)
    pan_y: vertical offset (default 0.0)
    viewport_width: float = 0.0
    viewport_height: float = 0.0
    """
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    viewport_width: float = 0.0
    viewport_height: float = 0.0

    def __post_init__(self):
        if self.zoom <= 0:
            raise ValueError(f"Zoom factor must be strictly positive, got {self.zoom}")

    @staticmethod
    def compute_axis_pan_bounds(content_size: float, viewport_size: float) -> Tuple[float, float]:
        """
        Computes (min_pan, max_pan) along a single axis.
        - When content_size >= viewport_size:
            min_pan = 0.0 (top/left aligned)
            max_pan = content_size - viewport_size (bottom/right aligned)
        - When content_size < viewport_size:
            Content is centered in the viewport:
            min_pan = max_pan = (content_size - viewport_size) / 2.0
            (giving visual position -pan = (viewport_size - content_size) / 2.0)
        """
        if viewport_size <= 0:
            return (0.0, max(0.0, content_size))
        if content_size >= viewport_size:
            return (0.0, content_size - viewport_size)
        else:
            centered = (content_size - viewport_size) / 2.0
            return (centered, centered)

    def get_pan_bounds(
        self,
        content_base_width: float,
        content_base_height: float,
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Returns ((min_pan_x, max_pan_x), (min_pan_y, max_pan_y))."""
        content_w = content_base_width * self.zoom
        content_h = content_base_height * self.zoom
        bounds_x = self.compute_axis_pan_bounds(content_w, self.viewport_width)
        bounds_y = self.compute_axis_pan_bounds(content_h, self.viewport_height)
        return (bounds_x, bounds_y)

    def clamp_pan(
        self,
        pan_x: float,
        pan_y: float,
        content_base_width: float,
        content_base_height: float,
    ) -> Tuple[float, float]:
        """Clamps the specified pan coordinates against valid viewport content bounds."""
        (min_x, max_x), (min_y, max_y) = self.get_pan_bounds(content_base_width, content_base_height)
        clamped_x = max(min_x, min(max_x, pan_x))
        clamped_y = max(min_y, min(max_y, pan_y))
        return (clamped_x, clamped_y)

    def zoom_around_anchor(
        self,
        new_zoom: float,
        anchor_x: float,
        anchor_y: float,
        content_base_width: float,
        content_base_height: float,
    ) -> "ViewportMetrics":
        """
        Calculates new pan preserving the spatial viewport position of anchor (anchor_x, anchor_y).
        Ensures the anchor point maps to the same item coordinate before and after zooming.
        Clamps the resulting pan against the new zoom's content bounds.
        """
        if new_zoom <= 0:
            raise ValueError(f"New zoom must be strictly positive, got {new_zoom}")

        # Inverse transform to find the item coordinate under the anchor before zoom:
        item_x = (anchor_x + self.pan_x) / self.zoom
        item_y = (anchor_y + self.pan_y) / self.zoom

        # Forward transform with new zoom to keep item point at anchor:
        unclamped_pan_x = (item_x * new_zoom) - anchor_x
        unclamped_pan_y = (item_y * new_zoom) - anchor_y

        temp_metrics = ViewportMetrics(
            zoom=new_zoom,
            pan_x=unclamped_pan_x,
            pan_y=unclamped_pan_y,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
        )
        clamped_x, clamped_y = temp_metrics.clamp_pan(
            unclamped_pan_x,
            unclamped_pan_y,
            content_base_width,
            content_base_height,
        )

        return ViewportMetrics(
            zoom=new_zoom,
            pan_x=clamped_x,
            pan_y=clamped_y,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
        )


class CoordinateTransformer:
    """
    Pure mathematical transformation pipeline connecting four coordinate spaces:
      S_norm     : Normalized canonical bounding box [0, 1000] x [0, 1000]
      S_raster   : Page raster image pixel coordinates [0, W_raster] x [0, H_raster]
      S_item     : QML page-image item coordinate space [0, W_item] x [0, H_item]
      S_viewport : Final visible viewport coordinate space after zoom + pan

    Closed-form, stateless transformations with zero Qt dependencies.
    """

    # =========================================================================
    # 1. Forward Pipeline (S_norm -> S_raster -> S_item -> S_viewport)
    # =========================================================================

    @staticmethod
    def normalized_to_raster_point(norm_pt: PointF, raster_w: float, raster_h: float) -> PointF:
        """S_norm -> S_raster point mapping."""
        return PointF(
            x=norm_pt.x * raster_w / 1000.0,
            y=norm_pt.y * raster_h / 1000.0,
        )

    @staticmethod
    def normalized_to_raster_rect(bbox: BoundingBox, raster_w: float, raster_h: float) -> RectF:
        """
        S_norm -> S_raster rectangle mapping.
        Note: BoundingBox uses [ymin, xmin, ymax, xmax] convention.
        """
        x = bbox.xmin * raster_w / 1000.0
        y = bbox.ymin * raster_h / 1000.0
        w = (bbox.xmax - bbox.xmin) * raster_w / 1000.0
        h = (bbox.ymax - bbox.ymin) * raster_h / 1000.0
        return RectF(x=x, y=y, width=w, height=h)

    @staticmethod
    def raster_to_item_point(raster_pt: PointF, metrics: DisplayedImageMetrics) -> PointF:
        """S_raster -> S_item point mapping."""
        return PointF(
            x=metrics.offset_x + (raster_pt.x * metrics.scale_x),
            y=metrics.offset_y + (raster_pt.y * metrics.scale_y),
        )

    @staticmethod
    def raster_to_item_rect(raster_rect: RectF, metrics: DisplayedImageMetrics) -> RectF:
        """S_raster -> S_item rectangle mapping."""
        return RectF(
            x=metrics.offset_x + (raster_rect.x * metrics.scale_x),
            y=metrics.offset_y + (raster_rect.y * metrics.scale_y),
            width=raster_rect.width * metrics.scale_x,
            height=raster_rect.height * metrics.scale_y,
        )

    @staticmethod
    def item_to_viewport_point(item_pt: PointF, viewport: ViewportMetrics) -> PointF:
        """S_item -> S_viewport point mapping."""
        return PointF(
            x=(item_pt.x * viewport.zoom) - viewport.pan_x,
            y=(item_pt.y * viewport.zoom) - viewport.pan_y,
        )

    @staticmethod
    def item_to_viewport_rect(item_rect: RectF, viewport: ViewportMetrics) -> RectF:
        """S_item -> S_viewport rectangle mapping."""
        return RectF(
            x=(item_rect.x * viewport.zoom) - viewport.pan_x,
            y=(item_rect.y * viewport.zoom) - viewport.pan_y,
            width=item_rect.width * viewport.zoom,
            height=item_rect.height * viewport.zoom,
        )

    @classmethod
    def normalized_to_item_rect(cls, bbox: BoundingBox, metrics: DisplayedImageMetrics) -> RectF:
        """Direct closed-form composition S_norm -> S_item."""
        raster_rect = cls.normalized_to_raster_rect(bbox, metrics.raster_width, metrics.raster_height)
        return cls.raster_to_item_rect(raster_rect, metrics)

    @classmethod
    def normalized_to_viewport_rect(
        cls,
        bbox: BoundingBox,
        metrics: DisplayedImageMetrics,
        viewport: ViewportMetrics,
    ) -> RectF:
        """Direct closed-form composition S_norm -> S_viewport."""
        item_rect = cls.normalized_to_item_rect(bbox, metrics)
        return cls.item_to_viewport_rect(item_rect, viewport)

    # =========================================================================
    # 2. Inverse Pipeline (S_viewport -> S_item -> S_raster -> S_norm)
    # =========================================================================

    @staticmethod
    def viewport_to_item_point(vp_pt: PointF, viewport: ViewportMetrics) -> PointF:
        """S_viewport -> S_item point mapping."""
        return PointF(
            x=(vp_pt.x + viewport.pan_x) / viewport.zoom,
            y=(vp_pt.y + viewport.pan_y) / viewport.zoom,
        )

    @staticmethod
    def viewport_to_item_rect(vp_rect: RectF, viewport: ViewportMetrics) -> RectF:
        """S_viewport -> S_item rectangle mapping."""
        return RectF(
            x=(vp_rect.x + viewport.pan_x) / viewport.zoom,
            y=(vp_rect.y + viewport.pan_y) / viewport.zoom,
            width=vp_rect.width / viewport.zoom,
            height=vp_rect.height / viewport.zoom,
        )

    @staticmethod
    def item_to_raster_point(item_pt: PointF, metrics: DisplayedImageMetrics) -> PointF:
        """S_item -> S_raster point mapping."""
        return PointF(
            x=(item_pt.x - metrics.offset_x) / metrics.scale_x,
            y=(item_pt.y - metrics.offset_y) / metrics.scale_y,
        )

    @staticmethod
    def item_to_raster_rect(item_rect: RectF, metrics: DisplayedImageMetrics) -> RectF:
        """S_item -> S_raster rectangle mapping."""
        return RectF(
            x=(item_rect.x - metrics.offset_x) / metrics.scale_x,
            y=(item_rect.y - metrics.offset_y) / metrics.scale_y,
            width=item_rect.width / metrics.scale_x,
            height=item_rect.height / metrics.scale_y,
        )

    @staticmethod
    def raster_to_normalized_point(raster_pt: PointF, raster_w: float, raster_h: float) -> PointF:
        """S_raster -> S_norm point mapping."""
        return PointF(
            x=raster_pt.x * 1000.0 / raster_w,
            y=raster_pt.y * 1000.0 / raster_h,
        )

    @staticmethod
    def raster_to_normalized_bbox(
        raster_rect: RectF,
        raster_w: float,
        raster_h: float,
        clamp: bool = True,
    ) -> BoundingBox:
        """
        S_raster -> S_norm BoundingBox mapping.
        Rounds to nearest integer coordinates and enforces normalized domain invariants [0, 1000].
        """
        raw_xmin = raster_rect.x * 1000.0 / raster_w
        raw_ymin = raster_rect.y * 1000.0 / raster_h
        raw_xmax = (raster_rect.x + raster_rect.width) * 1000.0 / raster_w
        raw_ymax = (raster_rect.y + raster_rect.height) * 1000.0 / raster_h

        xmin = int(round(raw_xmin))
        ymin = int(round(raw_ymin))
        xmax = int(round(raw_xmax))
        ymax = int(round(raw_ymax))

        if clamp:
            xmin = max(0, min(1000, xmin))
            ymin = max(0, min(1000, ymin))
            xmax = max(0, min(1000, xmax))
            ymax = max(0, min(1000, ymax))

            if xmin > xmax:
                xmax = xmin
            if ymin > ymax:
                ymax = ymin

        return BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)

    @classmethod
    def viewport_to_normalized_point(
        cls,
        vp_pt: PointF,
        metrics: DisplayedImageMetrics,
        viewport: ViewportMetrics,
    ) -> PointF:
        """Direct closed-form inverse composition S_viewport -> S_norm point."""
        item_pt = cls.viewport_to_item_point(vp_pt, viewport)
        raster_pt = cls.item_to_raster_point(item_pt, metrics)
        return cls.raster_to_normalized_point(raster_pt, metrics.raster_width, metrics.raster_height)

    @classmethod
    def viewport_to_normalized_bbox(
        cls,
        vp_rect: RectF,
        metrics: DisplayedImageMetrics,
        viewport: ViewportMetrics,
        clamp: bool = True,
    ) -> BoundingBox:
        """Direct closed-form inverse composition S_viewport -> S_norm BoundingBox."""
        item_rect = cls.viewport_to_item_rect(vp_rect, viewport)
        raster_rect = cls.item_to_raster_rect(item_rect, metrics)
        return cls.raster_to_normalized_bbox(
            raster_rect,
            metrics.raster_width,
            metrics.raster_height,
            clamp=clamp,
        )
