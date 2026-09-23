"""Unit tests for MathJax rendering integration in MarkdownViewerService preview pipeline (TICK-010A).

Verifies AST math node traversal, batch pre-rendering via IMathRenderer,
deterministic hash derivation, embedding of image://math/ in RichText HTML,
and pre-population of MathSvgCache.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

from application.ports.markdown_parser import IMarkdownParser
from application.ports.math_renderer import (
    IMathRenderer,
    MathRenderError,
    MathRenderRequest,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from core.markdown.ast import (
    BlockquoteBlock,
    HeadingBlock,
    InlineSpan,
    InlineType,
    ListBlock,
    ListItem,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
    TableFallbackBlock,
)
from infrastructure.math.lru_cache import MathSvgCache
from infrastructure.math.mathjax_client import MathJaxClient
from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor


class StubParser(IMarkdownParser):
    def __init__(self, doc: MarkdownDocument) -> None:
        self._doc = doc

    def parse(self, text: str) -> MarkdownDocument:
        return self._doc


def test_markdown_viewer_service_traverses_math_and_calls_render_batch():
    """Service collects all MathBlock and inline Math spans and calls render_batch."""
    tex_display = r"\int_0^\infty e^{-x} dx = 1"
    tex_inline1 = "E = mc^2"
    tex_inline2 = "a^2 + b^2 = c^2"

    block_math = MathBlock(content=tex_display)
    block_para = ParagraphBlock(
        inlines=(
            InlineSpan(span_type=InlineType.TEXT, text="Energy: "),
            InlineSpan(span_type=InlineType.MATH, text=tex_inline1),
        )
    )
    block_heading = HeadingBlock(
        level=1,
        inlines=(
            InlineSpan(span_type=InlineType.TEXT, text="Theorem "),
            InlineSpan(span_type=InlineType.MATH, text=tex_inline2),
        ),
    )

    doc = MarkdownDocument(blocks=(block_heading, block_para, block_math))
    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch.return_value = []

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    # Verify render_batch was called with all 3 math requests
    mock_renderer.render_batch.assert_called_once()
    called_requests: List[MathRenderRequest] = mock_renderer.render_batch.call_args[0][0]
    assert len(called_requests) == 3

    # Check request parameters
    req_map = {(r.tex, r.display): r for r in called_requests}
    assert (tex_display, True) in req_map
    assert (tex_inline1, False) in req_map
    assert (tex_inline2, False) in req_map

    # Verify DTOs have math_hash and inline image tags
    hash_display = MathRenderRequest(tex=tex_display, display=True).compute_hash()
    hash_inline1 = MathRenderRequest(tex=tex_inline1, display=False).compute_hash()
    hash_inline2 = MathRenderRequest(tex=tex_inline2, display=False).compute_hash()

    # Heading node
    heading_node = dto.nodes[0]
    assert f'<img src="image://math/{hash_inline2}" align="middle"/>' in heading_node.content
    assert heading_node.segments[1].math_hash == hash_inline2
    assert heading_node.segments[1].text_html == f'<img src="image://math/{hash_inline2}" align="middle"/>'

    # Paragraph node
    para_node = dto.nodes[1]
    assert f'<img src="image://math/{hash_inline1}" align="middle"/>' in para_node.content
    assert para_node.segments[1].math_hash == hash_inline1
    assert para_node.segments[1].text_html == f'<img src="image://math/{hash_inline1}" align="middle"/>'

    # Math block node
    math_node = dto.nodes[2]
    assert math_node.node_type == "math_block"
    assert math_node.math_hash == hash_display
    assert math_node.math_tex == tex_display


def test_markdown_viewer_service_deduplicates_batch_requests():
    """Identical formulas occurring multiple times produce only one MathRenderRequest in batch."""
    tex = "x = y"
    p1 = ParagraphBlock(inlines=(InlineSpan(span_type=InlineType.MATH, text=tex),))
    p2 = ParagraphBlock(inlines=(InlineSpan(span_type=InlineType.MATH, text=tex),))
    doc = MarkdownDocument(blocks=(p1, p2))

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch.return_value = []

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    service.render_text("", active_regions=[], job_id=1)

    mock_renderer.render_batch.assert_called_once()
    called_requests = mock_renderer.render_batch.call_args[0][0]
    assert len(called_requests) == 1
    assert called_requests[0].tex == tex


def test_markdown_viewer_service_traverses_lists_blockquotes_and_tables():
    """Collects math from complex composite blocks (lists, blockquotes, tables)."""
    tex_list = r"\lambda_1"
    tex_quote = r"\mu_2"
    tex_table = r"\sigma_3"

    list_blk = ListBlock(
        items=(
            ListItem(
                inlines=(InlineSpan(span_type=InlineType.MATH, text=tex_list),)
            ),
        )
    )
    quote_blk = BlockquoteBlock(
        blocks=(
            MathBlock(content=tex_quote),
        )
    )
    table_blk = TableFallbackBlock(
        raw_table="| col |",
        headers=((InlineSpan(span_type=InlineType.MATH, text=tex_table),),),
    )

    doc = MarkdownDocument(blocks=(list_blk, quote_blk, table_blk))
    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch.return_value = []

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    mock_renderer.render_batch.assert_called_once()
    called_tex = {r.tex for r in mock_renderer.render_batch.call_args[0][0]}
    assert called_tex == {tex_list, tex_quote, tex_table}

    # Verify blockquote math block has image tag in inner HTML
    quote_node = dto.nodes[1]
    quote_hash = MathRenderRequest(tex=tex_quote, display=True).compute_hash()
    assert f'<img src="image://math/{quote_hash}"/>' in quote_node.content


def test_markdown_viewer_service_prepopulates_cache_via_mathjax_client():
    """End-to-end integration: MathJaxClient pre-renders and warms MathSvgCache."""
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg viewBox='0 0 10 10'><path d='M0 0'/></svg>",
        "width": "2ex",
        "height": "1ex",
        "vertical_align": "0ex",
    }

    cache = MathSvgCache(capacity=50)
    client = MathJaxClient(supervisor=mock_supervisor, cache=cache)

    tex = r"\vec{F} = m \vec{a}"
    doc = MarkdownDocument(
        blocks=(
            ParagraphBlock(inlines=(InlineSpan(span_type=InlineType.MATH, text=tex),)),
        )
    )

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=client,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    # Verify formula is in cache before presentation delegates query it
    expected_hash = MathRenderRequest(tex=tex, display=False).compute_hash()
    cached_svg = cache.get(expected_hash)
    assert cached_svg is not None
    assert cached_svg.hash == expected_hash
    assert "<path d='M0 0'/>" in cached_svg.svg_xml

    # Verify DTO references the exact cached hash
    assert dto.nodes[0].segments[0].math_hash == expected_hash
    assert f'<img src="image://math/{expected_hash}" align="middle"/>' in dto.nodes[0].content


def test_markdown_viewer_service_tolerates_math_render_error():
    """Service gracefully finishes document DTO generation even if math rendering encounters an error."""
    doc = MarkdownDocument(
        blocks=(
            ParagraphBlock(
                inlines=(
                    InlineSpan(span_type=InlineType.TEXT, text="Intro "),
                    InlineSpan(span_type=InlineType.MATH, text=r"\bad{tex"),
                )
            ),
        )
    )

    failing_renderer = MagicMock(spec=IMathRenderer)
    failing_renderer.render_batch.side_effect = MathRenderError(-32602, "Syntax error")

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=failing_renderer,
    )

    # Does not raise; completes DTO construction
    dto = service.render_text("", active_regions=[], job_id=1)
    assert len(dto.nodes) == 1
    assert dto.nodes[0].node_type == "paragraph"
    assert len(dto.nodes[0].segments) == 2
