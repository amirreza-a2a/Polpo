# ============================================================
#  core/geometry/box_editor.py
#  Pure Mathematical Bounding Box Editing & Handle Calculations
# ============================================================

from enum import Enum
from typing import Dict, Optional

from core.entities.bounding_box import BoundingBox
from core.geometry.coordinates import PointF, RectF


class HandleType(str, Enum):
    """8 resize handles for 2D bounding box manipulation."""
    NW = "nw"  # Top-left corner
    N  = "n"   # Top-center edge
    NE = "ne"  # Top-right corner
    W  = "w"   # Middle-left edge
    E  = "e"   # Middle-right edge
    SW = "sw"  # Bottom-left corner
    S  = "s"   # Bottom-center edge
    SE = "se"  # Bottom-right corner


MIN_NORMALIZED_DIMENSION = 5
PAGE_BOUND_MIN = 0
PAGE_BOUND_MAX = 1000


class BoxGeometryEditor:
    """
    Pure geometric operations for translating, resizing, and creating
    normalized bounding boxes [0, 1000] x [0, 1000].
    Zero Qt / GUI dependencies.
    """

    @staticmethod
    def translate_bbox(
        bbox: BoundingBox,
        delta_x: float,
        delta_y: float,
        bound_min: int = PAGE_BOUND_MIN,
        bound_max: int = PAGE_BOUND_MAX,
    ) -> BoundingBox:
        """
        Translates a bounding box by (delta_x, delta_y) in normalized space.
        Preserves box width and height strictly, clamping translation so the entire
        rectangle remains inside [bound_min, bound_max].
        """
        w = bbox.xmax - bbox.xmin
        h = bbox.ymax - bbox.ymin

        target_xmin = bbox.xmin + delta_x
        target_ymin = bbox.ymin + delta_y

        # Clamp position to page bounds preserving dimensions
        clamped_xmin = max(float(bound_min), min(float(bound_max - w), target_xmin))
        clamped_ymin = max(float(bound_min), min(float(bound_max - h), target_ymin))

        xmin = int(round(clamped_xmin))
        ymin = int(round(clamped_ymin))
        xmax = xmin + w
        ymax = ymin + h

        return BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)

    @staticmethod
    def resize_bbox(
        bbox: BoundingBox,
        handle: str,
        target_norm_pt: PointF,
        min_size: int = MIN_NORMALIZED_DIMENSION,
        bound_min: int = PAGE_BOUND_MIN,
        bound_max: int = PAGE_BOUND_MAX,
    ) -> BoundingBox:
        """
        Resizes a bounding box using one of the 8 canonical resize handles.
        - Preserves the opposite edge or corner strictly.
        - Center handles (n, s, w, e) modify only the corresponding axis.
        - Corner handles (nw, ne, sw, se) modify both axes.
        - Prevents inverted rectangles and clamps all coordinates to [bound_min, bound_max].
        """
        x = max(float(bound_min), min(float(bound_max), target_norm_pt.x))
        y = max(float(bound_min), min(float(bound_max), target_norm_pt.y))

        xmin = bbox.xmin
        ymin = bbox.ymin
        xmax = bbox.xmax
        ymax = bbox.ymax

        h_str = handle.lower()

        if h_str == HandleType.NW:
            xmin = min(int(round(x)), bbox.xmax - min_size)
            ymin = min(int(round(y)), bbox.ymax - min_size)
        elif h_str == HandleType.N:
            ymin = min(int(round(y)), bbox.ymax - min_size)
        elif h_str == HandleType.NE:
            xmax = max(int(round(x)), bbox.xmin + min_size)
            ymin = min(int(round(y)), bbox.ymax - min_size)
        elif h_str == HandleType.W:
            xmin = min(int(round(x)), bbox.xmax - min_size)
        elif h_str == HandleType.E:
            xmax = max(int(round(x)), bbox.xmin + min_size)
        elif h_str == HandleType.SW:
            xmin = min(int(round(x)), bbox.xmax - min_size)
            ymax = max(int(round(y)), bbox.ymin + min_size)
        elif h_str == HandleType.S:
            ymax = max(int(round(y)), bbox.ymin + min_size)
        elif h_str == HandleType.SE:
            xmax = max(int(round(x)), bbox.xmin + min_size)
            ymax = max(int(round(y)), bbox.ymin + min_size)
        else:
            raise ValueError(f"Unknown resize handle '{handle}'. Valid handles: {[h.value for h in HandleType]}")

        # Final safety clamping
        xmin = max(bound_min, min(bound_max - min_size, xmin))
        ymin = max(bound_min, min(bound_max - min_size, ymin))
        xmax = max(xmin + min_size, min(bound_max, xmax))
        ymax = max(ymin + min_size, min(bound_max, ymax))

        return BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)

    @staticmethod
    def create_manual_bbox(
        start_norm_pt: PointF,
        end_norm_pt: PointF,
        min_size: int = MIN_NORMALIZED_DIMENSION,
        bound_min: int = PAGE_BOUND_MIN,
        bound_max: int = PAGE_BOUND_MAX,
    ) -> Optional[BoundingBox]:
        """
        Creates a new normalized bounding box from two corner points.
        Normalizes coordinates regardless of drag direction (top-left, bottom-right, etc.),
        clamps to valid page bounds, and rejects rectangles smaller than min_size.
        """
        x1 = max(float(bound_min), min(float(bound_max), start_norm_pt.x))
        y1 = max(float(bound_min), min(float(bound_max), start_norm_pt.y))
        x2 = max(float(bound_min), min(float(bound_max), end_norm_pt.x))
        y2 = max(float(bound_min), min(float(bound_max), end_norm_pt.y))

        xmin = int(round(min(x1, x2)))
        xmax = int(round(max(x1, x2)))
        ymin = int(round(min(y1, y2)))
        ymax = int(round(max(y1, y2)))

        if (xmax - xmin) < min_size or (ymax - ymin) < min_size:
            return None

        return BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)

    @staticmethod
    def compute_8_handles(item_rect: RectF, handle_size: float = 8.0) -> Dict[str, RectF]:
        """
        Calculates item-space (S_item) rectangles for each of the 8 resize handles.
        Handles are centered on the bounding box corners and edge midpoints.
        """
        hs = handle_size
        half = hs / 2.0
        x = item_rect.x
        y = item_rect.y
        w = item_rect.width
        h = item_rect.height

        return {
            HandleType.NW.value: RectF(x - half, y - half, hs, hs),
            HandleType.N.value:  RectF(x + (w / 2.0) - half, y - half, hs, hs),
            HandleType.NE.value: RectF(x + w - half, y - half, hs, hs),
            HandleType.W.value:  RectF(x - half, y + (h / 2.0) - half, hs, hs),
            HandleType.E.value:  RectF(x + w - half, y + (h / 2.0) - half, hs, hs),
            HandleType.SW.value: RectF(x - half, y + h - half, hs, hs),
            HandleType.S.value:  RectF(x + (w / 2.0) - half, y + h - half, hs, hs),
            HandleType.SE.value: RectF(x + w - half, y + h - half, hs, hs),
        }
