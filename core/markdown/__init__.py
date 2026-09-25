# ============================================================
#  core/markdown/__init__.py
#  Canonical PolpoT Core Markdown AST & Pure Resolver API
# ============================================================

from core.markdown.ast import (
    EMPTY_METADATA,
    BlockType,
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    InlineSpan,
    InlineType,
    ListBlock,
    ListItem,
    MarkdownBlock,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from core.markdown.asset_rewriter import (
    AssetReference,
    rewrite_asset_references,
    scan_asset_references,
)
from core.markdown.resolver import resolve_image_regions
from core.markdown.version import (
    CanonicalMarkdownSnapshot,
    capture_canonical_markdown_snapshot,
    parse_canonical_markdown_version,
)

__all__ = [
    "CanonicalMarkdownSnapshot",
    "capture_canonical_markdown_snapshot",
    "parse_canonical_markdown_version",
    "EMPTY_METADATA",
    "AssetReference",
    "scan_asset_references",
    "rewrite_asset_references",
    "BlockType",
    "BlockquoteBlock",
    "CodeBlock",
    "HeadingBlock",
    "ImageBlock",
    "InlineSpan",
    "InlineType",
    "ListBlock",
    "ListItem",
    "MarkdownBlock",
    "MarkdownDocument",
    "MathBlock",
    "ParagraphBlock",
    "TableFallbackBlock",
    "ThematicBreakBlock",
    "resolve_image_regions",
]
