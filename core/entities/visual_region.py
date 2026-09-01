# ============================================================
#  core/entities/visual_region.py
#  Canonical Visual Region Domain Entity & Provenance Model
# ============================================================

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from core.entities.bounding_box import BoundingBox
from core.exceptions.domain_exceptions import DomainError


class RegionOrigin(str, Enum):
    """Origin source of a visual document region."""
    AI_DETECTED = "ai_detected"  # Extracted by AI Vision model
    USER_MANUAL = "user_manual"  # Manually created by user in workspace


class ReviewStatus(str, Enum):
    """User review status of a visual region."""
    UNREVIEWED = "unreviewed"    # Initial AI detection, uninspected
    MODIFIED   = "modified"      # User modified bounding box coordinates
    MANUAL     = "manual"        # User manually created region
    REJECTED   = "rejected"      # User rejected/deleted (omitted from document)
    ACCEPTED   = "accepted"      # Explicitly approved by user


class SyncStatus(str, Enum):
    """Synchronization status with disk artifacts and Markdown output."""
    PENDING_INITIAL_CROP = "pending_initial_crop"  # Created, awaiting first crop
    SYNCED               = "synced"                # Disk artifact & MD match effective_bbox
    DIRTY_RECROP_REQUIRED= "dirty_recrop_required" # Geometry modified, needs re-crop
    SYNC_FAILED          = "sync_failed"           # Crop or Markdown generation failed


@dataclass
class VisualRegion:
    """
    Core Domain Entity representing a visual document region (figure, chart, diagram).
    Preserves immutable AI detection provenance alongside user review state.
    """
    id: Optional[int]
    region_id: str
    job_id: int
    page_number: int
    display_order: int = 1
    origin: RegionOrigin = RegionOrigin.AI_DETECTED
    detected_bbox: Optional[BoundingBox] = None
    reviewed_bbox: Optional[BoundingBox] = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    sync_status: SyncStatus = SyncStatus.PENDING_INITIAL_CROP
    active_artifact_version: int = 0
    artifact_version_watermark: int = 0
    active_artifact_uri: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.region_id or not isinstance(self.region_id, str):
            raise DomainError("VisualRegion requires a valid non-empty string 'region_id'.")

        try:
            val_uuid = uuid.UUID(hex=self.region_id, version=4)
            if val_uuid.hex != self.region_id.lower():
                raise DomainError(f"VisualRegion region_id '{self.region_id}' must be a canonical 32-character UUID4 hex string.")
        except (ValueError, TypeError, AttributeError) as e:
            raise DomainError(f"VisualRegion region_id '{self.region_id}' must be a valid 32-character UUID4 hex string.") from e

        if not isinstance(self.job_id, int) or self.job_id <= 0:
            raise DomainError(f"VisualRegion requires a positive integer job_id, got {self.job_id}.")

        if not isinstance(self.page_number, int) or self.page_number <= 0:
            raise DomainError(f"VisualRegion requires a positive integer page_number, got {self.page_number}.")

        if not isinstance(self.display_order, int) or self.display_order <= 0:
            raise DomainError(f"VisualRegion requires a positive integer display_order, got {self.display_order}.")

        # Ensure watermark invariant: watermark is always >= active_artifact_version
        if self.artifact_version_watermark < self.active_artifact_version:
            self.artifact_version_watermark = self.active_artifact_version

        # Provenance invariants
        if self.origin == RegionOrigin.AI_DETECTED:
            if self.detected_bbox is None or not isinstance(self.detected_bbox, BoundingBox):
                raise DomainError(f"AI-detected region '{self.region_id}' requires a valid detected_bbox.")
        elif self.origin == RegionOrigin.USER_MANUAL:
            if self.detected_bbox is not None:
                raise DomainError(f"Manual region '{self.region_id}' cannot have detected_bbox.")
            if self.reviewed_bbox is None or not isinstance(self.reviewed_bbox, BoundingBox):
                raise DomainError(f"Manual region '{self.region_id}' requires a valid reviewed_bbox.")

        now = datetime.now(timezone.utc)
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now

    @classmethod
    def create_ai_detected(
        cls,
        job_id: int,
        page_number: int,
        display_order: int,
        detected_bbox: BoundingBox,
        region_id: Optional[str] = None,
    ) -> "VisualRegion":
        """Factory method for creating an initial AI-detected region."""
        rid = region_id or uuid.uuid4().hex
        return cls(
            id=None,
            region_id=rid,
            job_id=job_id,
            page_number=page_number,
            display_order=display_order,
            origin=RegionOrigin.AI_DETECTED,
            detected_bbox=detected_bbox,
            reviewed_bbox=None,
            review_status=ReviewStatus.UNREVIEWED,
            sync_status=SyncStatus.PENDING_INITIAL_CROP,
            active_artifact_version=0,
            artifact_version_watermark=0,
            active_artifact_uri=None,
        )

    @classmethod
    def create_user_manual(
        cls,
        job_id: int,
        page_number: int,
        display_order: int,
        reviewed_bbox: BoundingBox,
        region_id: Optional[str] = None,
    ) -> "VisualRegion":
        """Factory method for creating a user-drawn manual region."""
        rid = region_id or uuid.uuid4().hex
        return cls(
            id=None,
            region_id=rid,
            job_id=job_id,
            page_number=page_number,
            display_order=display_order,
            origin=RegionOrigin.USER_MANUAL,
            detected_bbox=None,
            reviewed_bbox=reviewed_bbox,
            review_status=ReviewStatus.MANUAL,
            sync_status=SyncStatus.PENDING_INITIAL_CROP,
            active_artifact_version=0,
            artifact_version_watermark=0,
            active_artifact_uri=None,
        )

    @property
    def effective_bbox(self) -> BoundingBox:
        """Returns the active bounding box geometry for rendering and cropping."""
        if self.origin == RegionOrigin.USER_MANUAL:
            assert self.reviewed_bbox is not None
            return self.reviewed_bbox
        return self.reviewed_bbox if self.reviewed_bbox is not None else self.detected_bbox  # type: ignore

    @property
    def is_modified(self) -> bool:
        """Returns True if the region differs from original AI detection."""
        if self.origin == RegionOrigin.USER_MANUAL:
            return True
        return self.reviewed_bbox is not None and self.reviewed_bbox != self.detected_bbox

    @property
    def is_deleted(self) -> bool:
        """Returns True if user rejected/deleted this region."""
        return self.review_status == ReviewStatus.REJECTED

    def update_geometry(self, new_bbox: BoundingBox) -> None:
        """Updates user review bounding box and marks dirty for re-crop."""
        if not isinstance(new_bbox, BoundingBox):
            raise DomainError("update_geometry requires a valid BoundingBox instance.")
        self.reviewed_bbox = new_bbox
        if self.origin == RegionOrigin.AI_DETECTED:
            self.review_status = ReviewStatus.MODIFIED
        else:
            self.review_status = ReviewStatus.MANUAL
        self.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        self.updated_at = datetime.now(timezone.utc)

    def reset_to_ai(self) -> None:
        """Resets geometry to original AI detection."""
        if self.origin == RegionOrigin.USER_MANUAL:
            raise DomainError("Cannot reset manual region to AI: no AI detection exists.")
        self.reviewed_bbox = None
        self.review_status = ReviewStatus.UNREVIEWED
        self.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        self.updated_at = datetime.now(timezone.utc)

    def reject(self) -> None:
        """Marks region as deleted/rejected."""
        self.review_status = ReviewStatus.REJECTED
        self.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        self.updated_at = datetime.now(timezone.utc)

    def restore(self) -> None:
        """Restores a rejected region back to unreviewed, manual, or modified state, marking it dirty for re-inclusion."""
        if not self.is_deleted:
            return
        if self.origin == RegionOrigin.AI_DETECTED:
            if self.reviewed_bbox is not None and self.reviewed_bbox != self.detected_bbox:
                self.review_status = ReviewStatus.MODIFIED
            else:
                self.review_status = ReviewStatus.UNREVIEWED
        else:
            self.review_status = ReviewStatus.MANUAL
        self.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        self.updated_at = datetime.now(timezone.utc)
