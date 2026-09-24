"""DTO definitions for Visual Region publication and reconciliation (TICK-P02B, #28)."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RegionPublicationResultDTO:
    """Result of publishing or reconciling a visual region review."""
    job_id: int
    region_id: str
    success: bool
    document_version: Optional[int] = None
    artifact_version: Optional[int] = None
    artifact_uri: Optional[str] = None
    status_message: Optional[str] = None
