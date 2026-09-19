# ============================================================
#  application/services/crop_artifact_staging_service.py
#  Crop Artifact Staging Service with isolated staging_id directories
# ============================================================

import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import List, Optional
import uuid

from application.dto.staged_crop import StagedCropHandle

_CROP_FILENAME_PATTERN = re.compile(r"^crop_(?P<region>.+)_v(?P<version>\d+)\.jpg$")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")
_DEFAULT_STAGING_DIR = Path("output_files/.staging")


class CropArtifactStagingService:
    """
    Manages isolated filesystem staging directories for uncommitted visual crop files.
    Keyed by an opaque staging_id (UUID4).
    Enforces strict path containment, cross-platform safety, and traversal validation.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir:
            resolved = Path(base_dir).resolve()
            if resolved.name != ".staging":
                self.base_dir = (resolved / ".staging").resolve()
            else:
                self.base_dir = resolved
        else:
            self.base_dir = _DEFAULT_STAGING_DIR.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _validate_identifier(self, name: str, param_name: str) -> str:
        """Validates that an identifier strictly conforms to safe alphanumeric characters and hyphens/underscores."""
        cleaned = name.strip()
        if not cleaned:
            raise ValueError(f"{param_name} cannot be empty")
        if cleaned in (".", "..") or cleaned.startswith("."):
            raise ValueError(f"Path traversal or dot-file detected in {param_name}: '{name}'")
        if not _SAFE_IDENTIFIER_PATTERN.match(cleaned):
            raise ValueError(
                f"Path traversal or invalid characters detected in {param_name}: '{name}'. "
                f"Must match ^[a-zA-Z0-9_\\-]+$."
            )
        return cleaned

    def _get_staging_dir(self, staging_id: str) -> Path:
        clean_id = self._validate_identifier(staging_id, "staging_id")
        staging_dir = (self.base_dir / clean_id).resolve()
        if not staging_dir.is_relative_to(self.base_dir) or staging_dir == self.base_dir:
            raise ValueError(
                f"Path traversal detected: staging directory must be a direct child of root: '{staging_id}'"
            )
        return staging_dir

    def stage_crop(
        self,
        job_id: int,
        staging_id: str,
        region_id: str,
        version: int,
        image_bytes: bytes,
    ) -> StagedCropHandle:
        """
        Stages an uncommitted crop image in an isolated staging directory.
        Returns a frozen StagedCropHandle containing the target permanent filename and SHA-256 digest.
        """
        if job_id <= 0:
            raise ValueError(f"job_id must be a positive integer > 0 (got {job_id})")
        if version < 1:
            raise ValueError(f"version must be an integer >= 1 (got {version})")

        clean_region = self._validate_identifier(region_id, "region_id")
        staging_dir = self._get_staging_dir(staging_id)
        staging_dir.mkdir(parents=True, exist_ok=True)

        dest_filename = f"crop_{clean_region}_v{version}.jpg"
        file_path = (staging_dir / dest_filename).resolve()
        if not file_path.is_relative_to(staging_dir) or file_path == staging_dir:
            raise ValueError(f"Path traversal detected: target path escapes staging dir: '{region_id}'")

        # Thread-safe atomic file write using UUID in temporary file name
        unique_token = uuid.uuid4().hex
        tmp_file = staging_dir / f".{dest_filename}.{unique_token}.tmp"
        try:
            with open(tmp_file, "wb") as f:
                f.write(image_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, file_path)
        except Exception:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
            raise

        sha256_hash = hashlib.sha256(image_bytes).hexdigest()

        return StagedCropHandle(
            staging_id=staging_id,
            region_id=clean_region,
            artifact_version=version,
            staging_path=str(file_path),
            dest_filename=dest_filename,
            sha256=sha256_hash,
            size_bytes=len(image_bytes),
        )

    def list_staged(self, staging_id: str) -> List[StagedCropHandle]:
        """Lists all staged crop handles currently held in the specified staging directory."""
        staging_dir = self._get_staging_dir(staging_id)
        if not staging_dir.exists() or not staging_dir.is_dir():
            return []

        handles: List[StagedCropHandle] = []
        for entry in sorted(staging_dir.iterdir()):
            if not entry.is_file() or entry.name.startswith("."):
                continue

            match = _CROP_FILENAME_PATTERN.match(entry.name)
            if match:
                region = match.group("region")
                version = int(match.group("version"))
                data = entry.read_bytes()
                sha256_hash = hashlib.sha256(data).hexdigest()
                handles.append(
                    StagedCropHandle(
                        staging_id=staging_id,
                        region_id=region,
                        artifact_version=version,
                        staging_path=str(entry.resolve()),
                        dest_filename=entry.name,
                        sha256=sha256_hash,
                        size_bytes=len(data),
                    )
                )

        return handles

    def discard_staging(self, staging_id: str) -> None:
        """Completely and cleanly removes a staging directory. Idempotent."""
        try:
            staging_dir = self._get_staging_dir(staging_id)
        except ValueError:
            return

        if staging_dir.exists() and staging_dir != self.base_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
