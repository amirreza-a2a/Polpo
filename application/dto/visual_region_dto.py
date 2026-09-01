# ============================================================
#  application/dto/visual_region_dto.py
#  DTO definitions for Visual Region workspace and review operations
# ============================================================

from dataclasses import dataclass, field
from typing import List, Optional
from core.entities.bounding_box import BoundingBox


@dataclass
class VisualRegionDTO:
    """Read-only presentation DTO for VisualRegion entities."""
    id: Optional[int]
    region_id: str
    job_id: int
    page_number: int
    display_order: int
    origin: str
    review_status: str
    sync_status: str
    effective_bbox: BoundingBox
    detected_bbox: Optional[BoundingBox]
    reviewed_bbox: Optional[BoundingBox]
    active_artifact_version: int
    active_artifact_uri: Optional[str]
    is_modified: bool
    is_deleted: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ApplyReviewResultDTO:
    """Result of applying user visual region reviews to disk artifacts and markdown."""
    job_id: int
    applied_count: int
    updated_region_ids: List[str] = field(default_factory=list)
    output_markdown_uri: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
