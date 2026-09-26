# ============================================================
#  application/services/export_package_service.py
#  Deterministic Export Package Service & Portable Projections
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid
import zipfile

from application.dto.resolved_asset_dto import ResolvedAsset, ResolvedAssetSet
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.document_asset_resolver import (
    AssetAccessError,
    AssetNotFoundError,
    DocumentAssetResolver,
)
from core.entities.artifact import resolve_canonical_file_path
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    DomainError,
    EntityNotFoundError,
    PublicationInProgressError,
)

# Cross-platform Windows reserved device names
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

_ILLEGAL_SLUG_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ============================================================
#  Export Error Hierarchy
# ============================================================

class ExportPackageError(DomainError):
    """Base exception for export package operations."""
    pass


class DestinationAlreadyExistsError(ExportPackageError):
    """Raised when the target export path already exists and overwrite is False."""
    pass


class DestinationDirectoryNotFoundError(ExportPackageError):
    """Raised when the destination file's parent directory does not exist."""
    pass


class ExportSourceNotFoundError(ExportPackageError):
    """Raised when the canonical document version or its file cannot be located."""
    pass


class AssetIntegrityError(ExportPackageError):
    """Raised when an asset's content or size changes between resolution and packaging (TOCTOU mismatch)."""
    pass


# ============================================================
#  Result DTOs
# ============================================================

@dataclass(frozen=True)
class PackageExportResult:
    """
    Result container for a successfully exported document package ZIP archive.
    """
    destination_path: Path
    root_directory: str
    canonical_version: int
    asset_count: int
    manifest: Dict[str, Any]
    canonical_sha256: str
    exported_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class MarkdownExportResult:
    """
    Result container for a successfully exported standalone portable Markdown document.
    """
    destination_path: Path
    canonical_version: int
    asset_count: int
    has_local_asset_references: bool
    canonical_sha256: str
    exported_sha256: str
    size_bytes: int


# ============================================================
#  Naming & Path Helpers
# ============================================================

def sanitize_export_slug(text: str, max_length: int = 64) -> str:
    """
    Sanitizes arbitrary text into a cross-platform safe, clean URL/filename slug.
    - Converts to lowercase
    - Replaces whitespace and punctuation with '-'
    - Strips invalid Windows/POSIX filename characters
    - Protects against Windows reserved device names
    - Truncates to max_length
    """
    clean = _ILLEGAL_SLUG_CHARS_PATTERN.sub("-", text.strip())
    clean = clean.lower()
    clean = re.sub(r"[\s_]+", "-", clean)
    clean = re.sub(r"[^\w\-]", "", clean)
    clean = re.sub(r"-+", "-", clean).strip(".- ")

    if not clean:
        return "document"

    stem = clean.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        clean = f"_{clean}"

    return clean[:max_length].rstrip(".- ") or "document"


def derive_document_slug(job: Optional[Job], job_id: int) -> str:
    """
    Derives a deterministic, human-readable document slug.
    Preferred: job file_name stem.
    Fallback: job_<job_id>.
    """
    if job and job.file_name:
        stem = Path(job.file_name).stem.strip()
        if stem:
            return sanitize_export_slug(stem)
    return f"job_{job_id}"


def validate_archive_entry_path(entry_path: str, expected_root: str) -> None:
    """
    Strictly validates that an archive path is relative, safe, and contained within expected_root.
    Rejects leading separators, backslashes, drive letters, and traversal components.
    """
    if entry_path.startswith("/") or entry_path.startswith("\\"):
        raise ValueError(f"Archive entry cannot start with separator: '{entry_path}'")
    if "\\" in entry_path:
        raise ValueError(f"Archive entry cannot contain backslashes: '{entry_path}'")
    if len(entry_path) >= 2 and entry_path[1] == ":":
        raise ValueError(f"Archive entry cannot contain drive letter: '{entry_path}'")

    parts = entry_path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Archive entry contains invalid path components: '{entry_path}'")

    if not entry_path.startswith(f"{expected_root}/"):
        raise ValueError(f"Archive entry escapes root directory: '{entry_path}' (expected root '{expected_root}')")


def get_deterministic_zip_time(created_at: Optional[str]) -> Tuple[int, int, int, int, int, int]:
    """
    Derives a deterministic ZipInfo date_time tuple (year, month, day, hour, min, sec)
    from the canonical version's created_at ISO 8601 string, clamped to the ZIP range [1980..2099].
    Falls back to a stable epoch (2026, 1, 1, 0, 0, 0) if missing or invalid.
    """
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            year = max(1980, min(2099, dt.year))
            return (year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        except Exception:
            pass
    return (2026, 1, 1, 0, 0, 0)


def build_export_manifest(
    *,
    canonical_version: int,
    canonical_sha256: str,
    exported_sha256: str,
    exported_size_bytes: int,
    created_at: Optional[str],
    assets: List[ResolvedAsset],
) -> Dict[str, Any]:
    """
    Constructs the canonical manifest dictionary conforming strictly to the public export schema.
    Ensures assets are sorted deterministically and no host paths leak into metadata.
    """
    sorted_assets = sorted(assets, key=lambda a: a.relative_dest_path)
    return {
        "schema_version": "1.0",
        "generator": {
            "name": "PolpoT Desktop",
            "version": "1.0.0",
        },
        "document": {
            "canonical_version": canonical_version,
            "entry_point": "document.md",
            "canonical_sha256": canonical_sha256,
            "exported_sha256": exported_sha256,
            "exported_size_bytes": exported_size_bytes,
            "created_at": created_at or "",
        },
        "assets": [
            {
                "exported_path": asset.relative_dest_path,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "mime_type": asset.mime_type,
            }
            for asset in sorted_assets
        ],
    }


# ============================================================
#  ExportPackageService Implementation
# ============================================================

class ExportPackageService:
    """
    Application service orchestrating portable document export (ZIP package and standalone Markdown).

    Coordinates:
    1. Retrieval and validation of immutable canonical document versions;
    2. Exact canonical Markdown extraction;
    3. Asset resolution and format-preserving rewriting via DocumentAssetResolver;
    4. Deterministic manifest creation;
    5. Byte-reproducible ZIP assembly;
    6. Atomic destination file write.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        artifacts_dir: Union[Path, str],
        resolver: Optional[DocumentAssetResolver] = None,
    ):
        self.uow_factory = uow_factory
        self.artifacts_dir = Path(artifacts_dir).resolve()
        self.resolver = resolver or DocumentAssetResolver(self.artifacts_dir)

    def _load_canonical_source(
        self,
        job_id: int,
        version: Optional[int],
    ) -> Tuple[Job, DocumentVersionRecord, str, str, Path]:
        """
        Loads and validates the canonical document version and underlying file.
        Returns: (job, version_record, canonical_markdown, canonical_sha256, job_artifacts_dir)
        """
        with self.uow_factory.create() as uow:
            # Precondition: No publication intent in progress for this job
            active_intent = uow.publish_intents.get_by_job_id(job_id)
            if active_intent:
                raise PublicationInProgressError(job_id)

            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if version is not None:
                version_record = uow.document_versions.get_by_version(job_id, version)
            else:
                version_record = uow.document_versions.get_latest(job_id)

            if not version_record:
                ver_msg = f"version {version}" if version is not None else "latest version"
                raise ExportSourceNotFoundError(f"No canonical document version found for job {job_id} ({ver_msg}).")

            if version_record.integrity_status == "QUARANTINED":
                raise CanonicalDocumentIntegrityError(
                    f"Cannot export job {job_id} version {version_record.version}: canonical document is QUARANTINED."
                )

        doc_file = resolve_canonical_file_path(version_record.output_path)
        if not doc_file.is_file():
            raise ExportSourceNotFoundError(
                f"Canonical document file does not exist on disk: {version_record.output_path}"
            )

        try:
            raw_bytes = doc_file.read_bytes()
        except (OSError, PermissionError) as e:
            raise ExportSourceNotFoundError(
                f"Cannot read canonical document file at '{doc_file}': {e}"
            ) from e

        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if version_record.sha256 and version_record.sha256 != actual_sha256:
            raise CanonicalDocumentIntegrityError(
                f"Canonical document SHA-256 mismatch for job {job_id} version {version_record.version}: "
                f"expected '{version_record.sha256}', calculated '{actual_sha256}'."
            )

        canonical_sha256 = version_record.sha256 or actual_sha256
        try:
            canonical_markdown = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise CanonicalDocumentIntegrityError(
                f"Canonical document at '{doc_file}' is not valid UTF-8: {e}"
            ) from e
        job_artifacts_dir = (self.artifacts_dir / f"job_{job_id}").resolve()

        return job, version_record, canonical_markdown, canonical_sha256, job_artifacts_dir

    def _prevalidate_destination(
        self,
        destination_path: Union[Path, str],
        overwrite: bool,
    ) -> None:
        """
        Performs early validation on destination path existence and overwrite policy before expensive loading.
        """
        dest = Path(destination_path)
        if not dest.is_dir():
            if not dest.parent.exists() or not dest.parent.is_dir():
                raise DestinationDirectoryNotFoundError(
                    f"Destination parent directory does not exist: '{dest.parent}'"
                )
            if dest.exists() and not overwrite:
                raise DestinationAlreadyExistsError(
                    f"Destination file already exists and overwrite=False: '{dest}'"
                )
        else:
            if not dest.exists() or not dest.is_dir():
                raise DestinationDirectoryNotFoundError(
                    f"Destination directory does not exist: '{dest}'"
                )

    def _resolve_target_path(
        self,
        destination_path: Union[Path, str],
        filename_default: str,
        overwrite: bool,
    ) -> Path:
        """
        Validates parent directory existence, checks overwrite policy, and returns target Path.
        """
        dest = Path(destination_path)
        if dest.is_dir():
            target_path = (dest / filename_default).resolve()
        else:
            target_path = dest.resolve()

        if not target_path.parent.exists() or not target_path.parent.is_dir():
            raise DestinationDirectoryNotFoundError(
                f"Destination parent directory does not exist: '{target_path.parent}'"
            )

        if target_path.exists() and not overwrite:
            raise DestinationAlreadyExistsError(
                f"Destination file already exists and overwrite=False: '{target_path}'"
            )

        return target_path

    def export_package(
        self,
        *,
        job_id: int,
        destination_path: Union[Path, str],
        version: Optional[int] = None,
        overwrite: bool = False,
    ) -> PackageExportResult:
        """
        Exports the canonical document and its strict reference asset closure into a self-contained ZIP package.

        The archive layout:
        <doc_slug>_v<version>/
        ├── document.md
        ├── manifest.json
        └── assets/
            └── ...

        All archive contents, manifest, entry order, and timestamps are strictly deterministic.
        Writes atomically to destination_path via same-directory temporary file.
        """
        self._prevalidate_destination(destination_path, overwrite)

        job, version_record, canonical_markdown, canonical_sha256, job_artifacts_dir = (
            self._load_canonical_source(job_id, version)
        )


        slug = derive_document_slug(job, job_id)
        root_dir = f"{slug}_v{version_record.version}"
        default_zip_name = f"{root_dir}.zip"

        target_file = self._resolve_target_path(destination_path, default_zip_name, overwrite)

        # Resolve asset closure and portable projection via existing DocumentAssetResolver
        resolved_set: ResolvedAssetSet = self.resolver.resolve_document_assets(
            job_id=job_id,
            canonical_markdown=canonical_markdown,
            job_artifacts_dir=job_artifacts_dir,
        )

        exported_md_bytes = resolved_set.resolved_markdown.encode("utf-8")
        exported_sha256 = hashlib.sha256(exported_md_bytes).hexdigest()

        # Build manifest
        manifest_dict = build_export_manifest(
            canonical_version=version_record.version,
            canonical_sha256=canonical_sha256,
            exported_sha256=exported_sha256,
            exported_size_bytes=len(exported_md_bytes),
            created_at=version_record.created_at,
            assets=list(resolved_set.assets),
        )
        manifest_bytes = (
            json.dumps(manifest_dict, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
        )

        zip_time = get_deterministic_zip_time(version_record.created_at)

        # Atomic write via temporary file in the same parent directory
        token = uuid.uuid4().hex[:12]
        temp_file = target_file.parent / f".{target_file.name}.{os.getpid()}.{token}.tmp"

        try:
            with zipfile.ZipFile(temp_file, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.comment = b""

                # 1. document.md
                doc_path = f"{root_dir}/document.md"
                validate_archive_entry_path(doc_path, root_dir)
                doc_zinfo = zipfile.ZipInfo(filename=doc_path, date_time=zip_time)
                doc_zinfo.compress_type = zipfile.ZIP_DEFLATED
                doc_zinfo.external_attr = 0o644 << 16
                doc_zinfo.extra = b""
                zf.writestr(doc_zinfo, exported_md_bytes)

                # 2. manifest.json
                man_path = f"{root_dir}/manifest.json"
                validate_archive_entry_path(man_path, root_dir)
                man_zinfo = zipfile.ZipInfo(filename=man_path, date_time=zip_time)
                man_zinfo.compress_type = zipfile.ZIP_DEFLATED
                man_zinfo.external_attr = 0o644 << 16
                man_zinfo.extra = b""
                zf.writestr(man_zinfo, manifest_bytes)

                # 3. assets in deterministic sorted order
                for asset in sorted(resolved_set.assets, key=lambda a: a.relative_dest_path):
                    asset_entry_path = f"{root_dir}/{asset.relative_dest_path}"
                    validate_archive_entry_path(asset_entry_path, root_dir)

                    if not asset.source_path.is_file():
                        raise AssetNotFoundError(
                            f"Referenced local asset does not exist on disk: '{asset.source_path}'"
                        )
                    try:
                        asset_bytes = asset.source_path.read_bytes()
                    except (PermissionError, OSError) as e:
                        raise AssetAccessError(
                            f"Cannot read referenced asset at '{asset.source_path}': {e}"
                        ) from e

                    actual_asset_sha256 = hashlib.sha256(asset_bytes).hexdigest()
                    actual_asset_size = len(asset_bytes)

                    if actual_asset_sha256 != asset.sha256:
                        raise AssetIntegrityError(
                            f"Asset content mismatch for '{asset.source_path}' during packaging: "
                            f"expected SHA-256 '{asset.sha256}', found '{actual_asset_sha256}'."
                        )
                    if actual_asset_size != asset.size_bytes:
                        raise AssetIntegrityError(
                            f"Asset size mismatch for '{asset.source_path}' during packaging: "
                            f"expected {asset.size_bytes} bytes, found {actual_asset_size} bytes."
                        )

                    asset_zinfo = zipfile.ZipInfo(filename=asset_entry_path, date_time=zip_time)
                    asset_zinfo.compress_type = zipfile.ZIP_DEFLATED
                    asset_zinfo.external_attr = 0o644 << 16
                    asset_zinfo.extra = b""
                    zf.writestr(asset_zinfo, asset_bytes)

            # Explicitly fsync the completed temporary archive before atomic rename
            fd = os.open(temp_file, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

            os.replace(temp_file, target_file)
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

        return PackageExportResult(
            destination_path=target_file,
            root_directory=root_dir,
            canonical_version=version_record.version,
            asset_count=len(resolved_set.assets),
            manifest=manifest_dict,
            canonical_sha256=canonical_sha256,
            exported_sha256=exported_sha256,
            size_bytes=target_file.stat().st_size,
        )

    def export_markdown(
        self,
        *,
        job_id: int,
        destination_path: Union[Path, str],
        version: Optional[int] = None,
        overwrite: bool = False,
    ) -> MarkdownExportResult:
        """
        Exports the standalone portable Markdown projection (with local assets rewritten to assets/...).
        Does not bundle the binary assets.
        Uses the exact same projection as document.md inside the ZIP package.
        """
        self._prevalidate_destination(destination_path, overwrite)

        job, version_record, canonical_markdown, canonical_sha256, job_artifacts_dir = (
            self._load_canonical_source(job_id, version)
        )


        slug = derive_document_slug(job, job_id)
        default_md_name = f"{slug}_v{version_record.version}.md"

        target_file = self._resolve_target_path(destination_path, default_md_name, overwrite)

        # Resolve asset closure and portable projection via existing DocumentAssetResolver
        resolved_set: ResolvedAssetSet = self.resolver.resolve_document_assets(
            job_id=job_id,
            canonical_markdown=canonical_markdown,
            job_artifacts_dir=job_artifacts_dir,
        )

        exported_md_bytes = resolved_set.resolved_markdown.encode("utf-8")
        exported_sha256 = hashlib.sha256(exported_md_bytes).hexdigest()

        # Atomic write
        token = uuid.uuid4().hex[:12]
        temp_file = target_file.parent / f".{target_file.name}.{os.getpid()}.{token}.tmp"

        try:
            with open(temp_file, "wb") as f:
                f.write(exported_md_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, target_file)
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

        return MarkdownExportResult(
            destination_path=target_file,
            canonical_version=version_record.version,
            asset_count=len(resolved_set.assets),
            has_local_asset_references=len(resolved_set.assets) > 0,
            canonical_sha256=canonical_sha256,
            exported_sha256=exported_sha256,
            size_bytes=target_file.stat().st_size,
        )
