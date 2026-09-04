# ============================================================
#  core/markdown/resolver.py
#  Pure Domain Four-Tier Image ↔ Region Identity Resolver
# ============================================================

import os
import re
from collections import defaultdict
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple, Union

from core.entities.visual_region import VisualRegion
from core.markdown.ast import (
    BlockquoteBlock,
    ImageBlock,
    MarkdownBlock,
    MarkdownDocument,
)

# Regex to detect explicit region_id attributes in tags, alt text, or titles:
# Matches patterns like |region_id=UUID, (region_id=UUID), region_id="UUID", etc.
EXPLICIT_REGION_ID_RE = re.compile(
    r"(?:^|[^\w\-])region_id=[\"']?([a-fA-F0-9\-]{32,36})[\"']?(?:[^\w\-]|$)",
    re.IGNORECASE,
)

# Tier 2: Reviewed artifact filename pattern: crop_{job_id}_{region_id}_v{version}.{ext}
TIER2_FILENAME_RE = re.compile(
    r"^crop_(?P<job_id>[a-zA-Z0-9_\-]+)_(?P<region_id>[a-fA-F0-9\-]{32,36})_v(?P<version>\d+)\.(?P<ext>jpe?g|png|webp)$",
    re.IGNORECASE,
)

# Tier 3: Legacy unreviewed filename pattern: crop_{job_id}_p{page}_{display_order}.{ext}
TIER3_LEGACY_RE = re.compile(
    r"^crop_(?P<job_id>[a-zA-Z0-9_\-]+)_p(?P<page>\d+)_(?P<order>\d+)\.(?P<ext>jpe?g|png|webp)$",
    re.IGNORECASE,
)


def _normalize_uuid(val: Optional[str]) -> Optional[str]:
    """
    Normalizes a UUID string (with or without hyphens) into a canonical
    32-character lowercase hexadecimal string.
    Returns None if the value cannot be parsed as a valid UUID.
    """
    if not val:
        return None
    clean = val.strip().replace("-", "").lower()
    if len(clean) == 32:
        try:
            int(clean, 16)
            return clean
        except ValueError:
            return None
    return None


def _extract_filename(source: str) -> str:
    """
    Extracts the clean file basename from a URI or relative file path,
    stripping query strings, fragments, and URL protocols.
    """
    if not source:
        return ""
    clean = source.split("?")[0].split("#")[0].strip()
    if clean.startswith("file://"):
        clean = clean[7:]
    return os.path.basename(clean)


def _find_explicit_region_id(image: ImageBlock) -> Optional[str]:
    """
    Inspects image properties for an explicit region_id declaration (Tier 1).
    Checks image.region_id, image.raw_tag, image.title, and image.alt_text.
    """
    if image.region_id:
        norm = _normalize_uuid(image.region_id)
        if norm:
            return norm

    for candidate in (image.raw_tag, image.title, image.alt_text):
        if candidate:
            m = EXPLICIT_REGION_ID_RE.search(candidate)
            if m:
                norm = _normalize_uuid(m.group(1))
                if norm:
                    return norm
    return None


def _resolve_image(
    image: ImageBlock,
    regions_by_uuid: Dict[str, VisualRegion],
    regions_by_page_order: Dict[Tuple[int, int], List[VisualRegion]],
    target_job_id_str: str,
) -> ImageBlock:
    """
    Resolves a single ImageBlock against active visual regions using
    the four-tier precedence model. Returns a new ImageBlock.
    """
    # -----------------------------------------------------------------
    # Tier 1: Explicit Metadata Token (Canonical Identity)
    # -----------------------------------------------------------------
    explicit_id = _find_explicit_region_id(image)
    if explicit_id is not None:
        if explicit_id in regions_by_uuid:
            matched = regions_by_uuid[explicit_id]
            return replace(
                image,
                region_id=matched.region_id,
                is_associated=True,
                display_order=matched.display_order,
            )
        # Explicit region ID was provided but does not match any active
        # region for this job (e.g. deleted or stale). Preserve explicit
        # reference without asserting active association. Do not fall back.
        return replace(
            image,
            region_id=explicit_id,
            is_associated=False,
            display_order=None,
        )

    filename = _extract_filename(image.source)
    if not filename:
        return replace(
            image,
            region_id=None,
            is_associated=False,
            display_order=None,
        )

    # -----------------------------------------------------------------
    # Tier 2: Reviewed Artifact Filename Pattern (Stable Artifact)
    # Pattern: crop_{job_id}_{region_id}_v{version}.{ext}
    # -----------------------------------------------------------------
    m_tier2 = TIER2_FILENAME_RE.match(filename)
    if m_tier2:
        extracted_job_id = m_tier2.group("job_id")
        if extracted_job_id == target_job_id_str:
            extracted_uuid = _normalize_uuid(m_tier2.group("region_id"))
            if extracted_uuid and extracted_uuid in regions_by_uuid:
                matched = regions_by_uuid[extracted_uuid]
                return replace(
                    image,
                    region_id=matched.region_id,
                    is_associated=True,
                    display_order=matched.display_order,
                )
        # Belongs to another job or unverified/stale region in Tier 2 pattern
        return replace(
            image,
            region_id=None,
            is_associated=False,
            display_order=None,
        )

    # -----------------------------------------------------------------
    # Tier 3: Legacy Display Order Heuristic (Compatibility Heuristic)
    # Pattern: crop_{job_id}_p{page}_{display_order}.{ext}
    # -----------------------------------------------------------------
    m_tier3 = TIER3_LEGACY_RE.match(filename)
    if m_tier3:
        extracted_job_id = m_tier3.group("job_id")
        if extracted_job_id == target_job_id_str:
            page_num = int(m_tier3.group("page"))
            order_num = int(m_tier3.group("order"))
            candidates = regions_by_page_order.get((page_num, order_num), [])
            if len(candidates) == 1:
                matched = candidates[0]
                return replace(
                    image,
                    region_id=matched.region_id,
                    is_associated=True,
                    display_order=matched.display_order,
                )
            # Ambiguous duplicate candidates (>1) or missing (0):
            # Do not guess; treat as unassociated.
            return replace(
                image,
                region_id=None,
                is_associated=False,
                display_order=None,
            )
        # Unrelated job ID in legacy pattern
        return replace(
            image,
            region_id=None,
            is_associated=False,
            display_order=None,
        )

    # -----------------------------------------------------------------
    # Tier 4: Unassociated Image
    # -----------------------------------------------------------------
    return replace(
        image,
        region_id=None,
        is_associated=False,
        display_order=None,
    )


def _resolve_block(
    block: MarkdownBlock,
    regions_by_uuid: Dict[str, VisualRegion],
    regions_by_page_order: Dict[Tuple[int, int], List[VisualRegion]],
    target_job_id_str: str,
) -> MarkdownBlock:
    """Recursively resolves image nodes within block structures."""
    if isinstance(block, ImageBlock):
        return _resolve_image(
            block,
            regions_by_uuid,
            regions_by_page_order,
            target_job_id_str,
        )
    elif isinstance(block, BlockquoteBlock):
        new_blocks = tuple(
            _resolve_block(b, regions_by_uuid, regions_by_page_order, target_job_id_str)
            for b in block.blocks
        )
        return replace(block, blocks=new_blocks)
    return block


def resolve_image_regions(
    document: MarkdownDocument,
    active_regions: Sequence[VisualRegion],
    job_id: Union[str, int],
) -> MarkdownDocument:
    """
    Pure domain function: resolves image region associations across the AST
    using the four-tier precedence model:
      - Tier 1: Explicit metadata token (highest precedence, canonical identity)
      - Tier 2: Reviewed artifact filename UUID (stable artifact identity)
      - Tier 3: Legacy display order fallback (compatibility heuristic, strictly non-stable)
      - Tier 4: Unassociated image (standard media fallback)

    Invariants:
      - Pure, deterministic, side-effect free.
      - Never mutates input document or visual region entities.
      - Zero database, parser, or UI framework dependencies.
      - Non-image AST nodes are preserved without modification.
    """
    target_job_id_str = str(job_id).strip()

    # Filter strictly to non-deleted active regions belonging to this job
    valid_regions = [
        r for r in active_regions
        if not r.is_deleted and str(r.job_id) == target_job_id_str
    ]

    regions_by_uuid: Dict[str, VisualRegion] = {}
    regions_by_page_order: Dict[Tuple[int, int], List[VisualRegion]] = defaultdict(list)

    for r in valid_regions:
        norm_uuid = _normalize_uuid(r.region_id)
        if norm_uuid:
            regions_by_uuid[norm_uuid] = r
        regions_by_page_order[(r.page_number, r.display_order)].append(r)

    resolved_blocks = tuple(
        _resolve_block(b, regions_by_uuid, regions_by_page_order, target_job_id_str)
        for b in document.blocks
    )

    return MarkdownDocument(blocks=resolved_blocks, metadata=document.metadata)
