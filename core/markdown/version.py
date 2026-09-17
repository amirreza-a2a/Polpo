# ============================================================
#  core/markdown/version.py
#  Authoritative Canonical Markdown Version & Snapshot Domain Logic
# ============================================================

from dataclasses import dataclass
import re
from typing import Optional

# Canonical Markdown output naming conventions:
# - Versioned: 'output_{job_id}_v{version}.md'
# - Legacy Unversioned: 'output_{job_id}.md' (version 1)
# Any other file pattern (e.g. page_1.md, crop_...jpg, etc.) is NOT a canonical document output.
_CANONICAL_VERSIONED_PATTERN = re.compile(r"(?:^|[/\\])output_[^/\\]+_v(\d+)\.md$")
_CANONICAL_UNVERSIONED_PATTERN = re.compile(r"(?:^|[/\\])output_[^/\\]+\.md$")


def parse_canonical_markdown_version(output_path: Optional[str]) -> int:
    """
    Extracts the canonical Markdown watermark integer from an artifact URI or filename.
    Returns:
        - Version number N from 'output_{job_id}_v{N}.md'
        - 1 for legacy unversioned canonical output ('output_{job_id}.md')
        - 0 if output_path is None, empty, or unversioned non-canonical file (e.g. page_1.md)
    """
    if not output_path:
        return 0
    cleaned = output_path.strip()
    if not cleaned:
        return 0

    match_ver = _CANONICAL_VERSIONED_PATTERN.search(cleaned)
    if match_ver:
        return int(match_ver.group(1))

    match_unver = _CANONICAL_UNVERSIONED_PATTERN.search(cleaned)
    if match_unver:
        return 1

    return 0


@dataclass(frozen=True)
class CanonicalMarkdownSnapshot:
    """Immutable point-in-time snapshot of the active canonical document metadata."""
    output_path: Optional[str]
    active_version: int

    @property
    def version(self) -> int:
        return self.active_version


def capture_canonical_markdown_snapshot(output_path: Optional[str]) -> CanonicalMarkdownSnapshot:
    """Creates an immutable snapshot of the canonical document path and version."""
    ver = parse_canonical_markdown_version(output_path)
    return CanonicalMarkdownSnapshot(output_path=output_path, active_version=ver)
