# ============================================================
#  application/dto/staged_crop.py
#  Application DTO for staged, uncommitted visual crop files
# ============================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class StagedCropHandle:
    """
    Lightweight application DTO representing an uncommitted crop artifact in a staging directory.
    Does NOT modify or pollute the core artifact domain.
    """
    staging_id: str
    region_id: str
    artifact_version: int
    staging_path: str
    dest_filename: str
    sha256: str
    size_bytes: int
