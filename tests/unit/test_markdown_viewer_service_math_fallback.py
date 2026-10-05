"""Unit tests for MarkdownViewerService math fallback projection and degraded typing (TICK-P08B).

Verifies:
1. Isolated error projection into display MathBlock and InlineSegmentDTO.
2. Error category mapping across all structured MathRenderError subtypes.
3. Tracking of had_math_timeout on MarkdownDocumentDTO.
4. Degraded typing mode (degraded_math=True): cache hits render as SVG while cache misses
   project synthetic degraded error without invoking the worker.
5. Lifecycle clearing of NegativeFailureMemo via service helper.
"""

from __future__ import annotations

from typing import List, Mapping, Sequence, Union
from unittest.mock import MagicMock

import pytest

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO, InlineSegmentDTO
from application.ports.markdown_parser import IMarkdownParser
from application.ports.math_renderer import (
    IMathRenderer,
    MathBufferLimitExceededError,
    MathCircuitBreakerOpenError,
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
    MathRenderTimeoutError,
    MathSyntaxError,
    MathWorkerCrashedError,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from core.markdown.ast import (
    InlineSpan,
    InlineType,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
)


class StubParser(IMarkdownParser):
    def __init__(self, doc: MarkdownDocument) -> None:
        self._doc = doc

    def parse(self, text: str) -> MarkdownDocument:
        return self._doc


def test_display_and_inline_math_syntax_error_fallback():
    """Display MathBlock and inline Math span with syntax error project error DTOs."""
    tex_display = r"\frac{1}{"
    tex_inline = r"\sqrt{"

    req_display = MathRenderRequest(tex=tex_display, display=True)
    req_inline = MathRenderRequest(tex=tex_inline, display=False)

    doc = MarkdownDocument(
        blocks=(
            MathBlock(content=tex_display),
            ParagraphBlock(
                inlines=(
                    InlineSpan(span_type=InlineType.TEXT, text="Formula: "),
                    InlineSpan(span_type=InlineType.MATH, text=tex_inline),
                )
            ),
        )
    )

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch_isolated.return_value = {
        req_display.compute_hash(): MathSyntaxError(-32602, "Missing close brace"),
        req_inline.compute_hash(): MathSyntaxError(-32602, "Unexpected end of input"),
    }

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    assert dto.had_math_timeout is False

    # Check display MathBlock
    display_node = dto.nodes[0]
    assert display_node.node_type == "math_block"
    assert display_node.has_error is True
    assert display_node.error_category == "syntax"
    assert display_node.error_message == "Missing close brace"
    assert display_node.math_tex == tex_display
    assert display_node.raw_markdown == f"$${tex_display}$$"

    # Check inline math segment
    para_node = dto.nodes[1]
    assert para_node.node_type == "paragraph"
    inline_math_seg = para_node.segments[1]
    assert inline_math_seg.segment_type == "math"
    assert inline_math_seg.has_error is True
    assert inline_math_seg.error_category == "syntax"
    assert inline_math_seg.error_message == "Unexpected end of input"
    assert inline_math_seg.math_tex == tex_inline
    assert '<span style="' in inline_math_seg.text_html
    assert r"\sqrt{" in inline_math_seg.text_html


def test_mixed_math_batch_partial_success():
    """Valid formulas render as normal SVGs while failed formulas project error metadata."""
    tex_valid_display = "E = mc^2"
    tex_broken_display = r"\frac{1}{"
    tex_valid_inline = "a^2 + b^2 = c^2"

    req_valid_display = MathRenderRequest(tex=tex_valid_display, display=True)
    req_broken_display = MathRenderRequest(tex=tex_broken_display, display=True)
    req_valid_inline = MathRenderRequest(tex=tex_valid_inline, display=False)

    doc = MarkdownDocument(
        blocks=(
            MathBlock(content=tex_valid_display),
            MathBlock(content=tex_broken_display),
            ParagraphBlock(
                inlines=(
                    InlineSpan(span_type=InlineType.MATH, text=tex_valid_inline),
                )
            ),
        )
    )

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch_isolated.return_value = {
        req_valid_display.compute_hash(): MathRenderResult(
            hash=req_valid_display.compute_hash(),
            svg_xml="<svg>valid_display</svg>",
            width="1ex",
            height="1ex",
            vertical_align="0ex",
        ),
        req_broken_display.compute_hash(): MathSyntaxError(-32602, "Syntax error"),
        req_valid_inline.compute_hash(): MathRenderResult(
            hash=req_valid_inline.compute_hash(),
            svg_xml="<svg>valid_inline</svg>",
            width="1ex",
            height="1ex",
            vertical_align="0ex",
        ),
    }

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    assert dto.had_math_timeout is False

    # Node 0: valid display math
    node0 = dto.nodes[0]
    assert node0.has_error is False
    assert node0.error_category == ""
    assert node0.error_message == ""
    assert node0.math_hash == req_valid_display.compute_hash()

    # Node 1: broken display math
    node1 = dto.nodes[1]
    assert node1.has_error is True
    assert node1.error_category == "syntax"
    assert node1.error_message == "Syntax error"

    # Node 2: valid inline math
    node2 = dto.nodes[2]
    seg = node2.segments[0]
    assert seg.has_error is False
    assert seg.error_category == ""
    assert seg.text_html == f'<img src="image://math/{req_valid_inline.compute_hash()}" align="middle"/>'


def test_timeout_sets_had_math_timeout_flag():
    """MathRenderTimeoutError sets had_math_timeout=True on MarkdownDocumentDTO."""
    tex_timeout = r"\int_{-\infty}^\infty e^{-x^2} dx"
    req_timeout = MathRenderRequest(tex=tex_timeout, display=True)

    doc = MarkdownDocument(blocks=(MathBlock(content=tex_timeout),))

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch_isolated.return_value = {
        req_timeout.compute_hash(): MathRenderTimeoutError(-32603, "MathJax render deadline exceeded (5.0s)"),
    }

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    assert dto.had_math_timeout is True
    node = dto.nodes[0]
    assert node.has_error is True
    assert node.error_category == "timeout"
    assert "deadline exceeded" in node.error_message


@pytest.mark.parametrize(
    "error_cls,code,msg,expected_category",
    [
        (MathSyntaxError, -32602, "Syntax err", "syntax"),
        (MathRenderTimeoutError, -32603, "Timeout err", "timeout"),
        (MathWorkerCrashedError, -32603, "Worker crashed", "crash"),
        (MathBufferLimitExceededError, -32600, "Buffer limit", "buffer_limit"),
        (MathCircuitBreakerOpenError, -32001, "Circuit breaker open", "circuit_breaker"),
        (MathRenderError, -32000, "Generic render error", "unknown"),
    ],
)
def test_error_category_mapping_all_subtypes(error_cls, code, msg, expected_category):
    """Maps all MathRenderError subclasses to distinct category string tokens."""
    tex = "x"
    req = MathRenderRequest(tex=tex, display=True)
    doc = MarkdownDocument(blocks=(MathBlock(content=tex),))

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch_isolated.return_value = {
        req.compute_hash(): error_cls(code, msg),
    }

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)
    node = dto.nodes[0]
    assert node.has_error is True
    assert node.error_category == expected_category
    assert node.error_message == msg


def test_degraded_math_mode_preserves_cached_and_bypasses_worker():
    """In degraded_math=True, warm cached formulas render while misses bypass the worker."""
    tex_cached = "x_1"
    tex_uncached = "x_2"

    req_cached = MathRenderRequest(tex=tex_cached, display=True)
    req_uncached = MathRenderRequest(tex=tex_uncached, display=True)

    doc = MarkdownDocument(
        blocks=(
            MathBlock(content=tex_cached),
            MathBlock(content=tex_uncached),
        )
    )

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_cache = MagicMock()
    # Cache hit for req_cached, miss for req_uncached
    mock_cache.get.side_effect = lambda h: (
        MathRenderResult(h, "<svg>cached</svg>", "1ex", "1ex", "0ex")
        if h == req_cached.compute_hash()
        else None
    )
    mock_renderer.cache = mock_cache

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1, degraded_math=True)

    # Worker must NOT be invoked when degraded
    mock_renderer.render_batch_isolated.assert_not_called()

    # Formula 1 was cached -> renders normally
    node0 = dto.nodes[0]
    assert node0.has_error is False
    assert node0.error_category == ""

    # Formula 2 was uncached -> projected with degraded fallback
    node1 = dto.nodes[1]
    assert node1.has_error is True
    assert node1.error_category == "degraded"
    assert "degraded during active typing" in node1.error_message


def test_clear_math_negative_memo():
    """clear_math_negative_memo invokes clear() on the underlying renderer memo."""
    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_memo = MagicMock()
    mock_renderer.negative_memo = mock_memo

    service = MarkdownViewerService(
        parser=MagicMock(),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    service.clear_math_negative_memo()
    mock_memo.clear.assert_called_once()

    # Safe to call when math_renderer is None or has no negative_memo
    service_no_renderer = MarkdownViewerService(
        parser=MagicMock(),
        uow_factory=None,
        storage=None,
        math_renderer=None,
    )
    service_no_renderer.clear_math_negative_memo()  # Does not raise


def test_render_text_isolated_partial_failures():
    """Document with 1 valid math block, 1 broken syntax math block, 1 valid inline math, and 1 timeout inline math."""
    tex_valid_display = "E = mc^2"
    tex_broken_display = r"\frac{1}{"
    tex_valid_inline = "a^2 + b^2 = c^2"
    tex_timeout_inline = r"\int_{-\infty}^\infty"

    req_valid_display = MathRenderRequest(tex=tex_valid_display, display=True)
    req_broken_display = MathRenderRequest(tex=tex_broken_display, display=True)
    req_valid_inline = MathRenderRequest(tex=tex_valid_inline, display=False)
    req_timeout_inline = MathRenderRequest(tex=tex_timeout_inline, display=False)

    doc = MarkdownDocument(
        blocks=(
            MathBlock(content=tex_valid_display),
            MathBlock(content=tex_broken_display),
            ParagraphBlock(
                inlines=(
                    InlineSpan(span_type=InlineType.MATH, text=tex_valid_inline),
                    InlineSpan(span_type=InlineType.TEXT, text=" and "),
                    InlineSpan(span_type=InlineType.MATH, text=tex_timeout_inline),
                )
            ),
        )
    )

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_renderer.render_batch_isolated.return_value = {
        req_valid_display.compute_hash(): MathRenderResult(
            hash=req_valid_display.compute_hash(),
            svg_xml="<svg>valid_display</svg>",
            width="1ex",
            height="1ex",
            vertical_align="0ex",
        ),
        req_broken_display.compute_hash(): MathSyntaxError(-32602, "Syntax error"),
        req_valid_inline.compute_hash(): MathRenderResult(
            hash=req_valid_inline.compute_hash(),
            svg_xml="<svg>valid_inline</svg>",
            width="1ex",
            height="1ex",
            vertical_align="0ex",
        ),
        req_timeout_inline.compute_hash(): MathRenderTimeoutError(-32603, "Timeout error"),
    }

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=None,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_text("", active_regions=[], job_id=1)

    assert dto.had_math_timeout is True

    # Display math 0: valid
    assert dto.nodes[0].has_error is False
    assert dto.nodes[0].error_category == ""

    # Display math 1: broken syntax
    assert dto.nodes[1].has_error is True
    assert dto.nodes[1].error_category == "syntax"
    assert dto.nodes[1].error_message == "Syntax error"

    # Inline math 0: valid
    para = dto.nodes[2]
    seg_valid = para.segments[0]
    assert seg_valid.has_error is False
    assert seg_valid.error_category == ""
    assert f'<img src="image://math/{req_valid_inline.compute_hash()}"' in seg_valid.text_html

    # Inline math 1: timeout
    seg_timeout = para.segments[2]
    assert seg_timeout.has_error is True
    assert seg_timeout.error_category == "timeout"
    assert seg_timeout.error_message == "Timeout error"
    assert 'class="math-error"' in seg_timeout.text_html


def test_render_preview_supports_degraded_math():
    """render_preview passes degraded_math to render_text and bypasses worker on cache miss."""
    mock_uow_factory = MagicMock()
    mock_uow = MagicMock()
    mock_uow_factory.create.return_value.__enter__.return_value = mock_uow
    mock_job = MagicMock()
    mock_job.output_path = "/tmp/fake.md"
    mock_uow.jobs.get_by_id.return_value = mock_job
    mock_uow.visual_regions.get_by_job_id.return_value = []

    tex = "x + y"
    doc = MarkdownDocument(blocks=(MathBlock(content=tex),))

    mock_renderer = MagicMock(spec=IMathRenderer)
    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    mock_renderer.cache = mock_cache

    service = MarkdownViewerService(
        parser=StubParser(doc),
        uow_factory=mock_uow_factory,
        storage=None,
        math_renderer=mock_renderer,
    )

    dto = service.render_preview(job_id=42, raw_text="$$x + y$$", base_version=2, degraded_math=True)

    mock_renderer.render_batch_isolated.assert_not_called()
    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.has_error is True
    assert node.error_category == "degraded"
