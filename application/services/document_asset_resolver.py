# ============================================================
#  application/services/document_asset_resolver.py
#  Deterministic Application Document Asset Resolver
# ============================================================

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from application.dto.resolved_asset_dto import ResolvedAsset, ResolvedAssetSet
from application.hashing import compute_file_sha256
from core.entities.artifact import resolve_canonical_file_path
from core.exceptions.domain_exceptions import DomainError
from core.markdown import rewrite_asset_references, scan_asset_references

# Windows reserved device names that cannot be used as filenames or stems

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# Illegal filename characters across Windows, POSIX, and macOS: < > : " / \ | ? * and control chars
_ILLEGAL_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ============================================================
#  Domain / Application Error Hierarchy
# ============================================================

class DocumentAssetResolutionError(DomainError):
    """Base error for document asset resolution failures."""
    pass


class AssetContainmentError(DocumentAssetResolutionError):
    """Raised when an asset reference resolves outside the allowed job artifact boundary."""
    pass


class AssetNotFoundError(DocumentAssetResolutionError):
    """Raised when a referenced local asset file does not exist on disk."""
    pass


class AssetAccessError(DocumentAssetResolutionError):
    """Raised when a referenced asset cannot be accessed (e.g. directory or unreadable)."""
    pass


# ============================================================
#  Filename Sanitization Helper

# ============================================================

def sanitize_export_filename(name: str) -> str:
    """
    Sanitizes a source filename into a cross-platform safe, deterministic basename.
    - Strips path separators and directory elements
    - Replaces invalid characters (< > : " / \\ | ? * and control characters) with '_'
    - Handles Windows reserved device names (e.g. CON, NUL) by prepending '_'
    - Strips trailing dots and spaces forbidden by Windows filesystems
    - Falls back to 'asset' if the resulting basename is empty
    """
    base = os.path.basename(name).strip()
    clean = _ILLEGAL_CHARS_PATTERN.sub("_", base)
    clean = clean.rstrip(". ")

    if not clean:
        clean = "asset"

    stem = clean.split(".")[0]
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        clean = f"_{clean}"

    return clean


# ============================================================
#  Document Asset Resolver Service
# ============================================================

class DocumentAssetResolver:
    """
    Application service that resolves local artifact references within canonical Markdown
    into a deterministic, safe, export-ready asset set for packaging.

    Enforces strict path containment within the job artifact boundary, cooperative deduplication,
    deterministic collision renaming, and delegates Markdown rewriting to core.markdown.
    """

    def __init__(self, artifacts_dir: Optional[Union[Path, str]] = None):
        """
        Initializes the resolver with an optional base artifacts directory.
        If specified, job_artifacts_dir defaults to (artifacts_dir / f"job_{job_id}").
        """
        self.artifacts_dir = Path(artifacts_dir).resolve() if artifacts_dir is not None else None

    def resolve_document_assets(
        self,
        *,
        job_id: int,
        canonical_markdown: str,
        job_artifacts_dir: Optional[Union[Path, str]] = None,
    ) -> ResolvedAssetSet:
        """
        Resolves all local image assets referenced in the canonical Markdown.

        Args:
            job_id: Numeric identifier of the document job.
            canonical_markdown: Raw canonical Markdown content string.
            job_artifacts_dir: Boundary directory containing this job's artifacts.
                               If omitted, derived from self.artifacts_dir / f"job_{job_id}".

        Returns:
            ResolvedAssetSet containing the rewritten portable Markdown and resolved assets.

        Raises:
            AssetContainmentError: If a referenced local file or symlink escapes the job directory.
            AssetNotFoundError: If a referenced local asset is missing.
            AssetAccessError: If a referenced local path is a directory or unreadable.
        """
        if job_artifacts_dir is not None:
            canonical_job_dir = Path(job_artifacts_dir).resolve()
        elif self.artifacts_dir is not None:
            canonical_job_dir = (self.artifacts_dir / f"job_{job_id}").resolve()
        else:
            raise ValueError("Either job_artifacts_dir or artifacts_dir must be provided.")

        if not canonical_markdown or "![" not in canonical_markdown:
            return ResolvedAssetSet(resolved_markdown=canonical_markdown, assets=(), uri_mapping=MappingProxyType({}))

        refs = scan_asset_references(canonical_markdown)
        if not refs:
            return ResolvedAssetSet(resolved_markdown=canonical_markdown, assets=(), uri_mapping=MappingProxyType({}))

        resolved_assets_list: List[ResolvedAsset] = []
        uri_mapping: Dict[str, str] = {}
        resolved_by_identity: Dict[Tuple[Path, str], str] = {}
        allocated_destinations_lower: Dict[str, Tuple[str, str]] = {}
        unique_assets_by_dest: Dict[str, ResolvedAsset] = {}

        for ref in refs:
            raw_dest = ref.destination

            # Scheme classification:
            # - External HTTP(S): no network calls, skip mapping
            # - Unsupported custom schemes (e.g. ftp, data, custom): no network/file access, skip mapping
            # - Local references (file:///, Windows file://, relative/absolute paths): resolve
            parsed = urlparse(raw_dest)
            if parsed.scheme in ("http", "https"):
                continue

            # Non-file, non-empty URI scheme with length > 1 (excluding Windows drive letter like C:)
            if parsed.scheme and parsed.scheme != "file" and not (len(parsed.scheme) == 1 and parsed.scheme.isalpha()):
                continue

            # If this exact source destination string was already mapped in this document, reuse it
            if raw_dest in uri_mapping:
                continue

            # Resolve local candidate path via core canonical resolver
            candidate_path = resolve_canonical_file_path(raw_dest)
            if not candidate_path.is_absolute():
                candidate_path = canonical_job_dir / candidate_path

            # Strict containment check against canonical job artifact directory
            try:
                resolved_path = candidate_path.resolve()
            except Exception as e:
                raise AssetAccessError(f"Cannot resolve filesystem path for '{raw_dest}': {e}") from e

            try:
                is_contained = resolved_path.is_relative_to(canonical_job_dir) and resolved_path != canonical_job_dir
            except (ValueError, TypeError):
                is_contained = False

            if not is_contained:
                raise AssetContainmentError(
                    f"Asset reference escapes job artifact boundary: '{raw_dest}'"
                )

            # Verification of existence and regular file type
            if not resolved_path.exists():
                raise AssetNotFoundError(
                    f"Referenced local asset does not exist on disk: '{raw_dest}'"
                )

            if resolved_path.is_dir():
                raise AssetAccessError(
                    f"Referenced local asset is a directory, not a regular file: '{raw_dest}'"
                )

            if not resolved_path.is_file():
                raise AssetAccessError(
                    f"Referenced local asset is not a regular file: '{raw_dest}'"
                )

            # Metadata computation: size, chunked sha256, mime type
            try:
                size_bytes = resolved_path.stat().st_size
                sha256 = compute_file_sha256(resolved_path)
            except (PermissionError, OSError) as e:
                raise AssetAccessError(
                    f"Referenced local asset is inaccessible: '{raw_dest}'"
                ) from e

            mime_type, _ = mimetypes.guess_type(resolved_path.name)
            mime_type = mime_type or "application/octet-stream"

            # Deduplication & deterministic collision naming
            identity_key = (resolved_path, sha256)
            if identity_key in resolved_by_identity:
                relative_dest_path = resolved_by_identity[identity_key]
                uri_mapping[raw_dest] = relative_dest_path
                continue

            clean_name = sanitize_export_filename(resolved_path.name)
            candidate_dest = f"assets/{clean_name}"
            candidate_dest_lower = candidate_dest.lower()

            if candidate_dest_lower in allocated_destinations_lower:
                existing_dest, existing_sha = allocated_destinations_lower[candidate_dest_lower]
                if existing_sha == sha256:
                    # Same basename and same content: share the exported asset
                    relative_dest_path = existing_dest
                else:
                    # Collision: same basename but different content
                    stem = Path(clean_name).stem
                    suffix = Path(clean_name).suffix
                    relative_dest_path = f"assets/{stem}_{sha256[:8]}{suffix}"
                    if relative_dest_path.lower() in allocated_destinations_lower:
                        if allocated_destinations_lower[relative_dest_path.lower()][1] != sha256:
                            relative_dest_path = f"assets/{stem}_{sha256[:16]}{suffix}"
            else:
                relative_dest_path = candidate_dest

            allocated_destinations_lower[relative_dest_path.lower()] = (relative_dest_path, sha256)
            resolved_by_identity[identity_key] = relative_dest_path
            uri_mapping[raw_dest] = relative_dest_path

            if relative_dest_path not in unique_assets_by_dest:
                asset = ResolvedAsset(
                    original_reference=raw_dest,
                    source_path=resolved_path,
                    relative_dest_path=relative_dest_path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    mime_type=mime_type,
                )
                unique_assets_by_dest[relative_dest_path] = asset
                resolved_assets_list.append(asset)

        resolved_markdown = rewrite_asset_references(canonical_markdown, uri_mapping)
        return ResolvedAssetSet(
            resolved_markdown=resolved_markdown,
            assets=tuple(resolved_assets_list),
            uri_mapping=MappingProxyType(uri_mapping),
        )


# ============================================================
#  Functional API
# ============================================================

def resolve_document_assets(
    *,
    job_id: int,
    canonical_markdown: str,
    job_artifacts_dir: Union[Path, str],
) -> ResolvedAssetSet:
    """
    Convenience function that resolves local document assets for a job within job_artifacts_dir.
    """
    resolver = DocumentAssetResolver()
    return resolver.resolve_document_assets(
        job_id=job_id,
        canonical_markdown=canonical_markdown,
        job_artifacts_dir=job_artifacts_dir,
    )
