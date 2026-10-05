"""Unit tests for presentation Markdown DTO schema extensions (TICK-P08A).

Verifies backwards-compatible defaults, typed error fields, and frozen immutability
for InlineSegmentDTO, MarkdownNodeDTO, and MarkdownDocumentDTO.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
)


def test_inline_segment_dto_math_error_fields_defaults():
    """InlineSegmentDTO defaults has_error to False and error strings to empty."""
    seg = InlineSegmentDTO(segment_type="math", math_tex="x^2", math_hash="hash123")
    assert seg.segment_type == "math"
    assert seg.math_tex == "x^2"
    assert seg.math_hash == "hash123"
    assert seg.has_error is False
    assert seg.error_category == ""
    assert seg.error_message == ""


def test_inline_segment_dto_math_error_fields_custom():
    """InlineSegmentDTO accepts explicit error categorization and diagnostics."""
    seg = InlineSegmentDTO(
        segment_type="math",
        math_tex=r"\frac{1}{",
        math_hash="bad_hash",
        has_error=True,
        error_category="syntax",
        error_message="Missing close brace",
    )
    assert seg.has_error is True
    assert seg.error_category == "syntax"
    assert seg.error_message == "Missing close brace"


def test_inline_segment_dto_is_frozen():
    """InlineSegmentDTO remains immutable and rejects attribute mutation."""
    seg = InlineSegmentDTO(segment_type="text", text_html="hello")
    with pytest.raises(FrozenInstanceError):
        seg.has_error = True  # type: ignore[misc]


def test_markdown_node_dto_math_error_fields_defaults():
    """MarkdownNodeDTO defaults has_error to False and error strings to empty."""
    node = MarkdownNodeDTO(node_id="math_1", node_type="math_block", math_tex="E=mc^2")
    assert node.has_error is False
    assert node.error_category == ""
    assert node.error_message == ""


def test_markdown_node_dto_math_error_fields_custom():
    """MarkdownNodeDTO stores explicit math error metadata."""
    node = MarkdownNodeDTO(
        node_id="math_err_1",
        node_type="math_block",
        math_tex=r"\slow",
        math_hash="hash_timeout",
        has_error=True,
        error_category="timeout",
        error_message="MathJax rendering timed out after 5.0s",
    )
    assert node.has_error is True
    assert node.error_category == "timeout"
    assert node.error_message == "MathJax rendering timed out after 5.0s"


def test_markdown_node_dto_is_frozen():
    """MarkdownNodeDTO remains immutable and rejects attribute mutation."""
    node = MarkdownNodeDTO(node_id="n1", node_type="paragraph")
    with pytest.raises(FrozenInstanceError):
        node.has_error = True  # type: ignore[misc]


def test_markdown_document_dto_had_math_timeout_default():
    """MarkdownDocumentDTO defaults had_math_timeout to False."""
    doc = MarkdownDocumentDTO(
        job_id=10,
        version=1,
        nodes=(),
        region_to_occurrences={},
    )
    assert doc.had_math_timeout is False


def test_markdown_document_dto_had_math_timeout_custom():
    """MarkdownDocumentDTO stores explicit had_math_timeout flag."""
    doc = MarkdownDocumentDTO(
        job_id=10,
        version=1,
        nodes=(),
        region_to_occurrences={},
        had_math_timeout=True,
    )
    assert doc.had_math_timeout is True


def test_markdown_document_dto_is_frozen():
    """MarkdownDocumentDTO remains immutable and rejects attribute mutation."""
    doc = MarkdownDocumentDTO(
        job_id=10,
        version=1,
        nodes=(),
        region_to_occurrences={},
    )
    with pytest.raises(FrozenInstanceError):
        doc.had_math_timeout = True  # type: ignore[misc]


def test_markdown_dto_fields_defaults_and_backward_compatibility():
    """Asserts DTOs instantiate with default values and support immutability and equality."""
    seg1 = InlineSegmentDTO(segment_type="text", text_html="abc")
    seg2 = InlineSegmentDTO(segment_type="text", text_html="abc", has_error=False, error_category="", error_message="")
    assert seg1 == seg2
    assert hash(seg1) == hash(seg2)

    seg_err = InlineSegmentDTO(segment_type="text", text_html="abc", has_error=True)
    assert seg1 != seg_err

    node1 = MarkdownNodeDTO(node_id="n1", node_type="paragraph", content="p")
    node2 = MarkdownNodeDTO(node_id="n1", node_type="paragraph", content="p", has_error=False, error_category="", error_message="")
    assert node1 == node2
    assert hash(node1) == hash(node2)

    node_err = MarkdownNodeDTO(node_id="n1", node_type="paragraph", content="p", has_error=True)
    assert node1 != node_err

    doc1 = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node1,), region_to_occurrences={})
    doc2 = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node1,), region_to_occurrences={}, had_math_timeout=False)
    assert doc1 == doc2

    doc_timeout = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node1,), region_to_occurrences={}, had_math_timeout=True)
    assert doc1 != doc_timeout
