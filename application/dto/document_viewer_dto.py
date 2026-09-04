# ============================================================
#  application/dto/document_viewer_dto.py
#  DTO definitions for PDF Viewer and Read-Only Region Overlays
# ============================================================

from dataclasses import dataclass
from typing import Optional, List
from core.entities.bounding_box import BoundingBox


@dataclass(frozen=True)
class PageRasterDTO:
    """Represents a rendered PDF page raster image with its dimensions and metadata."""
    job_id: int
    page_number: int
    total_pages: int
    raster_width: int
    raster_height: int
    image_uri: str
    dpi: int


@dataclass(frozen=True)
class VisualRegionOverlayItemDTO:
    """Represents a visual region transformed into presentation coordinates for visual overlay."""
    region_id: str
    job_id: int
    page_number: int
    display_order: int
    origin: str
    review_status: str
    effective_bbox: BoundingBox
    x: float
    y: float
    width: float
    height: float
