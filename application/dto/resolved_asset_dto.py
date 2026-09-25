# ============================================================
#  application/dto/resolved_asset_dto.py
# ============================================================

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Tuple


@dataclass(frozen=True)
class ResolvedAsset:
    """
    Immutable value object representing a single resolved document asset ready for export packaging.

    Attributes:
        original_reference: Source reference spelling present in the canonical Markdown destination span.
        source_path: Canonical resolved filesystem path to the local asset file on disk.
        relative_dest_path: Deterministic export-relative path in the portable package (e.g. 'assets/figure.png').
        sha256: Hexadecimal SHA-256 checksum of the file contents.
        size_bytes: Size of the asset file in bytes.
        mime_type: Detected or fallback MIME type (e.g. 'image/png', 'application/octet-stream').
    """
    original_reference: str
    source_path: Path
    relative_dest_path: str
    sha256: str
    size_bytes: int
    mime_type: str


@dataclass(frozen=True)
class ResolvedAssetSet:
    """
    Immutable result container representing the deterministic outcome of document asset resolution.

    Attributes:
        resolved_markdown: Portable projection of canonical Markdown with rewritten relative asset URIs.
        assets: Ordered immutable sequence of unique resolved assets participating in the export package.
        uri_mapping: Complete immutable mapping of every local source reference spelling to its exported relative path.
    """
    resolved_markdown: str
    assets: Tuple[ResolvedAsset, ...] = field(default_factory=tuple)
    uri_mapping: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
