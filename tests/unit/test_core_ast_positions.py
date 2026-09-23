"""Unit tests for Core AST MarkdownBlock position fields extension (TICK-005)."""

from dataclasses import FrozenInstanceError

import pytest

from core.markdown.ast import (
    BlockType,
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    MarkdownBlock,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)


def test_markdown_block_position_fields_defaults():
    """Default values for source column fields must be None."""
    block = MarkdownBlock(block_type=BlockType.PARAGRAPH)
    assert block.source_start_line is None
    assert block.source_end_line is None
    assert block.source_start_col is None
    assert block.source_end_col is None


def test_markdown_block_position_fields_explicit():
    """Explicitly passing source line and column fields sets them correctly."""
    block = MarkdownBlock(
        block_type=BlockType.HEADING,
        source_start_line=10,
        source_end_line=10,
        source_start_col=1,
        source_end_col=25,
    )
    assert block.source_start_line == 10
    assert block.source_end_line == 10
    assert block.source_start_col == 1
    assert block.source_end_col == 25


def test_all_eight_subclasses_inherit_column_fields():
    """All 8 block subclasses must inherit and expose source column fields."""
    heading = HeadingBlock(
        level=1,
        inlines=(),
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=15,
    )
    assert heading.source_start_col == 1
    assert heading.source_end_col == 15

    para = ParagraphBlock(
        inlines=(),
        source_start_line=2,
        source_end_line=2,
        source_start_col=1,
        source_end_col=40,
    )
    assert para.source_start_col == 1
    assert para.source_end_col == 40

    img = ImageBlock(
        source="figure.png",
        source_start_line=3,
        source_end_line=3,
        source_start_col=5,
        source_end_col=22,
    )
    assert img.source_start_col == 5
    assert img.source_end_col == 22

    code = CodeBlock(
        content="x = 42",
        source_start_line=4,
        source_end_line=4,
        source_start_col=1,
        source_end_col=7,
    )
    assert code.source_start_col == 1
    assert code.source_end_col == 7

    lst = ListBlock(
        items=(),
        source_start_line=5,
        source_end_line=6,
        source_start_col=1,
        source_end_col=10,
    )
    assert lst.source_start_col == 1
    assert lst.source_end_col == 10

    quote = BlockquoteBlock(
        blocks=(),
        source_start_line=7,
        source_end_line=8,
        source_start_col=1,
        source_end_col=20,
    )
    assert quote.source_start_col == 1
    assert quote.source_end_col == 20

    hr = ThematicBreakBlock(
        source_start_line=9,
        source_end_line=9,
        source_start_col=1,
        source_end_col=4,
    )
    assert hr.source_start_col == 1
    assert hr.source_end_col == 4

    table = TableFallbackBlock(
        raw_table="| a | b |",
        source_start_line=10,
        source_end_line=11,
        source_start_col=1,
        source_end_col=10,
    )
    assert table.source_start_col == 1
    assert table.source_end_col == 10


def test_frozen_immutability():
    """AST blocks must remain strictly immutable (FrozenInstanceError on mutation)."""
    block = ParagraphBlock(
        inlines=(),
        source_start_line=1,
        source_start_col=1,
        source_end_col=10,
    )
    with pytest.raises(FrozenInstanceError):
        block.source_start_col = 5  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        block.source_end_col = 20  # type: ignore[misc]


def test_keyword_only_enforcement():
    """Column fields after KW_ONLY cannot be passed positionally."""
    # HeadingBlock accepts (level, inlines) positionally, then keyword-only fields
    with pytest.raises(TypeError):
        HeadingBlock(1, (), 1, 1, 1, 15)  # type: ignore[call-arg]


def test_backward_compatibility_with_line_only_instantiation():
    """Existing code instantiating blocks with only line numbers continues to work unchanged."""
    heading = HeadingBlock(level=2, inlines=(), source_start_line=5, source_end_line=5)
    assert heading.source_start_line == 5
    assert heading.source_end_line == 5
    assert heading.source_start_col is None
    assert heading.source_end_col is None
