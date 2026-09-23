# ============================================================
#  core/markdown/ast.py
#  Canonical PolpoT Markdown AST & Immutable Structural Model
# ============================================================

from dataclasses import KW_ONLY, dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


class BlockType(str, Enum):
    """Enumeration of semantic Markdown block types."""
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    CODE_BLOCK = "code_block"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    THEMATIC_BREAK = "thematic_break"
    TABLE_FALLBACK = "table_fallback"
    MATH_BLOCK = "math_block"


class InlineType(str, Enum):
    """Enumeration of semantic Markdown inline span types."""
    TEXT = "text"
    EMPHASIS = "emphasis"      # Italic text
    STRONG = "strong"          # Bold text
    CODE_SPAN = "code_span"    # Inline monospaced backtick code
    LINK = "link"              # Hyperlink
    IMAGE = "image"            # Inline image (standard Markdown or wiki-link)
    MATH = "math"              # Inline mathematical formula


@dataclass(frozen=True)
class InlineSpan:
    """
    Immutable semantic inline span.
    Supports recursive composition of formatting (e.g. bold italic link)
    and semantic inline images without losing textual distinction.
    """
    span_type: InlineType
    text: str = ""
    target: Optional[str] = None  # URL for links; image source URI for inline images
    children: Tuple["InlineSpan", ...] = ()
    region_id: Optional[str] = None  # Optional explicit region_id or resolved region identity
    is_associated: bool = False  # True when region_id is confirmed bound to an active VisualRegion
    display_order: Optional[int] = None  # Visual region display order when associated

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple):
            object.__setattr__(self, "children", tuple(self.children))

    @property
    def plain_text(self) -> str:
        """Derives clean plain-text string recursively from the inline tree."""
        if not self.children:
            return self.text
        return "".join(c.plain_text for c in self.children)


@dataclass(frozen=True)
class MarkdownBlock:
    """Base class for all immutable block-level AST nodes."""
    block_type: BlockType
    _: KW_ONLY
    source_start_line: Optional[int] = None
    source_end_line: Optional[int] = None
    source_start_col: Optional[int] = None
    source_end_col: Optional[int] = None


@dataclass(frozen=True)
class HeadingBlock(MarkdownBlock):
    """Heading block (levels 1 through 6)."""
    level: int
    inlines: Tuple[InlineSpan, ...]
    block_type: BlockType = field(default=BlockType.HEADING, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.inlines, tuple):
            object.__setattr__(self, "inlines", tuple(self.inlines))


@dataclass(frozen=True)
class ParagraphBlock(MarkdownBlock):
    """Standard text paragraph block containing inline formatting."""
    inlines: Tuple[InlineSpan, ...]
    block_type: BlockType = field(default=BlockType.PARAGRAPH, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.inlines, tuple):
            object.__setattr__(self, "inlines", tuple(self.inlines))


@dataclass(frozen=True)
class ImageBlock(MarkdownBlock):
    """
    Image block node.
    Represents both standard Markdown images and PolpoT visual document crops.
    Can be in an unassociated state (region_id=None, is_associated=False) or
    bound to a canonical VisualRegion.
    """
    source: str
    alt_text: str = ""
    title: str = ""
    region_id: Optional[str] = None
    is_associated: bool = False
    display_order: Optional[int] = None
    raw_tag: str = ""
    block_type: BlockType = field(default=BlockType.IMAGE, init=False)


@dataclass(frozen=True)
class CodeBlock(MarkdownBlock):
    """Fenced or indented code block with optional syntax language."""
    content: str
    language: str = ""
    block_type: BlockType = field(default=BlockType.CODE_BLOCK, init=False)


@dataclass(frozen=True)
class ListItem:
    """Single item within an ordered or unordered list."""
    inlines: Tuple[InlineSpan, ...]
    is_task: bool = False
    task_checked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.inlines, tuple):
            object.__setattr__(self, "inlines", tuple(self.inlines))


@dataclass(frozen=True)
class ListBlock(MarkdownBlock):
    """Ordered or unordered list containing list items."""
    items: Tuple[ListItem, ...]
    is_ordered: bool = False
    start_index: int = 1
    block_type: BlockType = field(default=BlockType.LIST, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class BlockquoteBlock(MarkdownBlock):
    """Blockquote container containing nested block elements."""
    blocks: Tuple[MarkdownBlock, ...]
    block_type: BlockType = field(default=BlockType.BLOCKQUOTE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple):
            object.__setattr__(self, "blocks", tuple(self.blocks))


@dataclass(frozen=True)
class ThematicBreakBlock(MarkdownBlock):
    """Horizontal rule separator (---)."""
    block_type: BlockType = field(default=BlockType.THEMATIC_BREAK, init=False)


@dataclass(frozen=True)
class TableFallbackBlock(MarkdownBlock):
    """
    Preserves structured table layout for downstream fallback rendering
    without requiring third-party parser token objects.
    All nested containers (headers, rows, and cell inlines) are guaranteed
    to be immutable tuples.
    """
    raw_table: str = ""
    headers: Tuple[Tuple[InlineSpan, ...], ...] = ()
    rows: Tuple[Tuple[Tuple[InlineSpan, ...], ...], ...] = ()
    block_type: BlockType = field(default=BlockType.TABLE_FALLBACK, init=False)

    def __post_init__(self) -> None:
        # Deeply freeze headers: Tuple[Tuple[InlineSpan, ...], ...]
        frozen_headers = tuple(
            tuple(cell) if isinstance(cell, (tuple, list)) else (cell,)
            for cell in self.headers
        )
        object.__setattr__(self, "headers", frozen_headers)

        # Deeply freeze rows: Tuple[Tuple[Tuple[InlineSpan, ...], ...], ...]
        frozen_rows = tuple(
            tuple(
                tuple(cell) if isinstance(cell, (tuple, list)) else (cell,)
                for cell in row
            )
            for row in self.rows
        )
        object.__setattr__(self, "rows", frozen_rows)


@dataclass(frozen=True)
class MathBlock(MarkdownBlock):
    """Standalone display mathematical equation ($$...$$)."""
    content: str
    block_type: BlockType = field(default=BlockType.MATH_BLOCK, init=False)


def _freeze_metadata_value(val: Any) -> Any:
    """
    Recursively converts nested mutable collections (dict, list, set)
    into their standard immutable counterparts (MappingProxyType, tuple, frozenset).
    """
    if isinstance(val, dict):
        return MappingProxyType({k: _freeze_metadata_value(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple)):
        return tuple(_freeze_metadata_value(v) for v in val)
    elif isinstance(val, (set, frozenset)):
        return frozenset(_freeze_metadata_value(v) for v in val)
    return val


EMPTY_METADATA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class MarkdownDocument:
    """
    Top-level immutable Markdown document AST.
    Contains sequence of blocks and read-only metadata mapping.
    All nested metadata collections (nested dicts, lists, sets) are recursively
    frozen into read-only MappingProxyType and immutable tuples upon instantiation.
    """
    blocks: Tuple[MarkdownBlock, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple):
            object.__setattr__(self, "blocks", tuple(self.blocks))
        # Recursively freeze metadata mapping and any nested mutable structures
        frozen_metadata = _freeze_metadata_value(dict(self.metadata))
        if not isinstance(frozen_metadata, MappingProxyType):
            frozen_metadata = MappingProxyType(frozen_metadata if isinstance(frozen_metadata, dict) else {})
        object.__setattr__(self, "metadata", frozen_metadata)

