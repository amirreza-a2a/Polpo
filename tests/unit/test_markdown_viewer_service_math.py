"""Unit tests for MarkdownViewerService math projections and column positions (TICK-006).

Verifies projection of MathBlock to MarkdownNodeDTO, InlineType.MATH to InlineSegmentDTO,
deterministic node IDs for math blocks, and propagation of column positions.
"""

from __future__ import annotations

from core.markdown.ast import (
    BlockquoteBlock,
    HeadingBlock,
    InlineSpan,
    InlineType,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
)
from application.ports.markdown_parser import IMarkdownParser
from application.ports.math_renderer import MathRenderRequest
from application.services.markdown_viewer_service import MarkdownViewerService


class StubParser(IMarkdownParser):
    def __init__(self, doc: MarkdownDocument) -> None:
        self._doc = doc

    def parse(self, text: str) -> MarkdownDocument:
        return self._doc


def test_project_math_block_to_node_dto():
    tex = r"\int_{-\infty}^\infty e^{-x^2} dx = \sqrt{\pi}"
    math_block = MathBlock(
        content=tex,
        source_start_line=5,
        source_end_line=7,
        source_start_col=1,
        source_end_col=3,
    )
    doc = MarkdownDocument(blocks=(math_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "math_block"
    assert node.content == tex
    assert node.raw_markdown == f"$${tex}$$"
    assert node.math_tex == tex
    expected_hash = MathRenderRequest(tex=tex, display=True).compute_hash()
    assert node.math_hash == expected_hash
    assert node.node_id.startswith(f"math_{expected_hash[:12]}_")
    assert node.source_start_line == 5
    assert node.source_end_line == 7
    assert node.source_start_col == 1
    assert node.source_end_col == 3


def test_project_inline_math_segment_to_dto():
    tex = "E = mc^2"
    span_math = InlineSpan(span_type=InlineType.MATH, text=tex)
    span_text_before = InlineSpan(span_type=InlineType.TEXT, text="Formula: ")
    span_text_after = InlineSpan(span_type=InlineType.TEXT, text=" is famous.")
    p_block = ParagraphBlock(
        inlines=(span_text_before, span_math, span_text_after),
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=40,
    )
    doc = MarkdownDocument(blocks=(p_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "paragraph"
    assert len(node.segments) == 3

    math_seg = node.segments[1]
    assert math_seg.segment_type == "math"
    assert math_seg.math_tex == tex
    expected_hash = MathRenderRequest(tex=tex, display=False).compute_hash()
    assert math_seg.math_hash == expected_hash
    assert "$E = mc^2$" in math_seg.text_html
    # node.content should embed the image:// URL for rich text rendering
    assert f'image://math/{expected_hash}' in node.content


def test_blocks_propagate_column_positions_to_node_dto():
    h_block = HeadingBlock(
        level=2,
        inlines=(InlineSpan(span_type=InlineType.TEXT, text="Section Title"),),
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=15,
    )
    doc = MarkdownDocument(blocks=(h_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.source_start_col == 1
    assert node.source_end_col == 15


def test_duplicate_math_blocks_produce_unique_node_ids():
    tex = r"\alpha + \beta = \gamma"
    b1 = MathBlock(content=tex)
    b2 = MathBlock(content=tex)
    doc = MarkdownDocument(blocks=(b1, b2))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 2
    assert dto.nodes[0].node_id.endswith("_1")
    assert dto.nodes[1].node_id.endswith("_2")
    assert dto.nodes[0].node_id != dto.nodes[1].node_id


def test_blockquote_preserves_child_math_block():
    tex = r"\sum_{i=1}^n i = \frac{n(n+1)}{2}"
    math_b = MathBlock(content=tex)
    p_b = ParagraphBlock(inlines=(InlineSpan(span_type=InlineType.TEXT, text="Intro to sum"),))
    quote_b = BlockquoteBlock(blocks=(p_b, math_b))
    doc = MarkdownDocument(blocks=(quote_b,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "blockquote"
    assert len(node.quote_children) == 2
    assert node.quote_children[0].child_type == "paragraph"
    assert node.quote_children[1].child_type == "math_block"
    assert node.quote_children[1].content == tex
    assert len(node.quote_children[1].segments) == 1
    assert node.quote_children[1].segments[0].segment_type == "math"
    assert node.quote_children[1].segments[0].math_tex == tex
