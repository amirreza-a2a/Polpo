# ============================================================
#  application/dto/markdown_dto.py
#  Presentation DTOs for Native Markdown AST Rendering
# ============================================================

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class VisualRegionRefDTO:
    """
    Presentation reference for an image occurrence within the Markdown document.
    Carries visual region linkage, filesystem path, and display ordering.
    """
    occurrence_id: str
    source: str
    image_path: Optional[str] = None
    alt_text: str = ""
    region_id: Optional[str] = None
    is_associated: bool = False
    display_order: Optional[int] = None
    page_number: Optional[int] = None


@dataclass(frozen=True)
class InlineSegmentDTO:
    """
    Discrete inline segment for mixed inline rendering in QML Flow layouts.
    A segment is either formatted text (RichText HTML) or an embedded native image item.
    """
    segment_type: str  # "text" or "image"
    text_html: str = ""
    image_ref: Optional[VisualRegionRefDTO] = None


@dataclass(frozen=True)
class RegionOccurrenceRef:
    """
    Points to a specific visual region occurrence within a document block node.
    Enables O(1) reverse lookup from region_id to node index and occurrence.
    """
    node_index: int
    occurrence_id: str


@dataclass(frozen=True)
class QuoteChildBlockDTO:
    """
    Structured child block within a blockquote (e.g. paragraph or heading),
    preserving block structure, level, content, and native inline segments.
    """
    child_type: str  # "heading", "paragraph", etc.
    content: str = ""
    level: int = 0
    segments: Tuple[InlineSegmentDTO, ...] = ()


@dataclass(frozen=True)
class MarkdownNodeDTO:
    """
    Flattened block-level presentation node consumed by QML ListView delegates.
    Contains deterministic identity, sanitized RichText content, and inline segments.
    """
    node_id: str
    node_type: str
    content: str = ""
    raw_markdown: str = ""
    level: int = 0
    language: str = ""
    is_ordered: bool = False
    start_index: int = 1
    regions: Tuple[VisualRegionRefDTO, ...] = ()
    segments: Tuple[InlineSegmentDTO, ...] = ()
    list_items: Tuple[str, ...] = ()
    list_item_segments: Tuple[Tuple[InlineSegmentDTO, ...], ...] = ()
    table_cell_segments: Tuple[Tuple[Tuple[InlineSegmentDTO, ...], ...], ...] = ()
    quote_children: Tuple[QuoteChildBlockDTO, ...] = ()



@dataclass(frozen=True)
class MarkdownDocumentDTO:
    """
    Top-level presentation document DTO containing the complete sequence of block nodes
    and an inverted index of region_id to occurrences for bidirectional navigation.
    """
    job_id: int
    version: int
    nodes: Tuple[MarkdownNodeDTO, ...]
    region_to_occurrences: Mapping[str, Tuple[RegionOccurrenceRef, ...]]
