# ============================================================
#  core/geometry/__init__.py
#  Pure Python Coordinate Transformations for Document Review
# ============================================================

from core.geometry.coordinates import (
    PointF,
    RectF,
    FitMode,
    DisplayedImageMetrics,
    ViewportMetrics,
    CoordinateTransformer,
)
from core.geometry.box_editor import (
    BoxGeometryEditor,
    HandleType,
    MIN_NORMALIZED_DIMENSION,
)

__all__ = [
    "PointF",
    "RectF",
    "FitMode",
    "DisplayedImageMetrics",
    "ViewportMetrics",
    "CoordinateTransformer",
    "BoxGeometryEditor",
    "HandleType",
    "MIN_NORMALIZED_DIMENSION",
]
