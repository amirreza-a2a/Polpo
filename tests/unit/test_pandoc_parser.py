"""Unit tests for PandocParser implementing IMarkdownParser.

Verifies authoritative conversion of Pandoc JSON AST into canonical Core AST,
preservation of visual tokens, display and inline math, tables, blockquotes,
lists, code blocks, and reverse coordinate mapping.
"""

from __future__ import annotations

import pytest

from application.ports.markdown_parser import IMarkdownParser
from core.markdown.ast import (
    BlockType,
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    InlineType,
    ListBlock,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from infrastructure.markdown.pandoc_parser import PandocParser


def test_pandoc_parser_implements_interface():
    parser = PandocParser()
    assert isinstance(parser, IMarkdownParser)


def test_parse_empty_and_whitespace_documents():
    parser = PandocParser()
    doc1 = parser.parse("")
    assert isinstance(doc1, MarkdownDocument)
    assert len(doc1.blocks) == 0

    doc2 = parser.parse("   \n\n\t  \n")
    assert isinstance(doc2, MarkdownDocument)
    assert len(doc2.blocks) == 0


def test_parse_headings():
    parser = PandocParser()
    md = "# Heading 1\n\n## Heading 2\n\n### Heading 3"
    doc = parser.parse(md)

    assert len(doc.blocks) == 3
    for idx, expected_level in enumerate([1, 2, 3]):
        block = doc.blocks[idx]
        assert isinstance(block, HeadingBlock)
        assert block.level == expected_level
        assert block.block_type == BlockType.HEADING
        assert len(block.inlines) >= 1
        assert block.inlines[0].text == f"Heading {expected_level}"
        assert block.source_start_line is not None
        assert block.source_start_col is not None


def test_parse_paragraph_and_inline_formatting():
    parser = PandocParser()
    md = "This has *italic*, **bold**, `code_span`, and [a link](https://example.com)."
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)
    assert p.block_type == BlockType.PARAGRAPH
    assert p.source_start_line == 1

    inlines = p.inlines
    span_types = [span.span_type for span in inlines]
    assert InlineType.EMPHASIS in span_types
    assert InlineType.STRONG in span_types
    assert InlineType.CODE_SPAN in span_types
    assert InlineType.LINK in span_types

    # Find the link
    link_span = next(s for s in inlines if s.span_type == InlineType.LINK)
    assert link_span.target == "https://example.com"
    assert link_span.children[0].text == "a link"
    assert p.source_start_line == 1
    assert p.source_start_col == 1
    assert p.source_end_line is not None
    assert p.source_end_col is not None


def test_parse_multiline_paragraph_coordinates():
    parser = PandocParser()
    md = "Line 1 of paragraph\nLine 2 of paragraph\nLine 3 of paragraph"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)
    assert p.source_start_line == 1
    assert p.source_start_col == 1
    assert p.source_end_line == 4
    assert p.source_end_col == 1


def test_parse_inline_math():
    parser = PandocParser()
    md = "Euler's identity is $e^{i\\pi} + 1 = 0$ in complex analysis."
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)

    math_spans = [s for s in p.inlines if s.span_type == InlineType.MATH]
    assert len(math_spans) == 1
    assert math_spans[0].text == r"e^{i\pi} + 1 = 0"


def test_parse_display_math_block():
    parser = PandocParser()
    md = "$$\n\\int_{-\\infty}^\\infty e^{-x^2} dx = \\sqrt{\\pi}\n$$"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, MathBlock)
    assert block.block_type == BlockType.MATH_BLOCK
    assert r"\int_{-\infty}^\infty e^{-x^2} dx = \sqrt{\pi}" in block.content
    assert block.source_start_line == 1
    assert block.source_start_col == 1


def test_parse_code_block():
    parser = PandocParser()
    md = "```python\ndef hello():\n    return 'world'\n```"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, CodeBlock)
    assert block.language == "python"
    assert "return 'world'" in block.content
    assert block.source_start_line == 1
    assert block.source_start_col == 1


def test_parse_unordered_and_task_lists():
    parser = PandocParser()
    md = "- Ordinary item\n- [ ] Unchecked task\n- [x] Checked task"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, ListBlock)
    assert block.is_ordered is False
    assert len(block.items) == 3

    assert block.items[0].is_task is False
    assert block.items[0].inlines[0].text == "Ordinary item"

    assert block.items[1].is_task is True
    assert block.items[1].task_checked is False
    assert "Unchecked task" in block.items[1].inlines[0].text

    assert block.items[2].is_task is True
    assert block.items[2].task_checked is True
    assert "Checked task" in block.items[2].inlines[0].text


def test_parse_ordered_list():
    parser = PandocParser()
    md = "1. First\n2. Second\n3. Third"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, ListBlock)
    assert block.is_ordered is True
    assert block.start_index == 1
    assert len(block.items) == 3


def test_parse_blockquote():
    parser = PandocParser()
    md = "> # Quoted Title\n>\n> Quoted paragraph text."
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, BlockquoteBlock)
    assert len(block.blocks) == 2
    assert isinstance(block.blocks[0], HeadingBlock)
    assert isinstance(block.blocks[1], ParagraphBlock)


def test_parse_thematic_break():
    parser = PandocParser()
    md = "Above\n\n---\n\nBelow"
    doc = parser.parse(md)

    assert len(doc.blocks) == 3
    assert isinstance(doc.blocks[0], ParagraphBlock)
    assert isinstance(doc.blocks[1], ThematicBreakBlock)
    assert isinstance(doc.blocks[2], ParagraphBlock)


def test_parse_table():
    parser = PandocParser()
    md = "| Header 1 | Header 2 |\n| --- | --- |\n| Val 1 | Val 2 |\n| Val 3 | Val 4 |"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, TableFallbackBlock)
    assert len(block.headers) == 2
    assert block.headers[0][0].text == "Header 1"
    assert block.headers[1][0].text == "Header 2"
    assert len(block.rows) == 2
    assert block.rows[0][0][0].text == "Val 1"
    assert block.rows[0][1][0].text == "Val 2"
    assert block.rows[1][0][0].text == "Val 3"
    assert block.rows[1][1][0].text == "Val 4"


def test_parse_canonical_visual_token_standalone_image():
    parser = PandocParser()
    reg_id = "00000000-0000-4000-8000-000000000001"
    occ_id = "00000000-0000-4000-8000-000000000002"
    md = f'![Figure 1](crop_1.jpg "polpo:region={reg_id};occ={occ_id}")'
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.source == "crop_1.jpg"
    assert block.alt_text == "Figure 1"
    assert block.region_id == reg_id


def test_parse_canonical_visual_token_inline_image():
    parser = PandocParser()
    reg_id = "00000000-0000-4000-8000-000000000001"
    occ_id = "00000000-0000-4000-8000-000000000002"
    md = f'Reference text ![Figure 1](crop_1.jpg "polpo:region={reg_id};occ={occ_id}") continues.'
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)

    img_spans = [s for s in p.inlines if s.span_type == InlineType.IMAGE]
    assert len(img_spans) == 1
    assert img_spans[0].target == "crop_1.jpg"
    assert img_spans[0].text == "Figure 1"
    assert img_spans[0].region_id == reg_id


def test_reverse_coordinate_mapping_with_legacy_normalizer():
    parser = PandocParser()
    # Line 1: legacy syntax ![[crop.jpg]] (17 chars) -> ![](crop.jpg) (15 chars)
    # Line 2: normal heading
    md = "![[crop.jpg]]\n\n# Heading After Shift"
    doc = parser.parse(md)

    assert len(doc.blocks) == 2
    img_block = doc.blocks[0]
    h_block = doc.blocks[1]

    assert isinstance(img_block, ImageBlock)
    assert img_block.source_start_line == 1
    assert img_block.source_start_col == 1

    assert isinstance(h_block, HeadingBlock)
    assert h_block.source_start_line == 3
    assert h_block.source_start_col == 1


def test_parse_single_line_display_math():
    parser = PandocParser()
    md = "$$E = mc^2$$"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, MathBlock)
    assert block.content == "E = mc^2"
    assert block.source_start_line == 1
    assert block.source_start_col == 1


def test_parse_inline_display_math_in_paragraph():
    parser = PandocParser()
    md = "Context before $$a^2 + b^2 = c^2$$ context after."
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)
    math_spans = [s for s in p.inlines if s.span_type == InlineType.MATH]
    assert len(math_spans) == 1
    assert math_spans[0].text == "a^2 + b^2 = c^2"


def test_parse_code_block_without_language():
    parser = PandocParser()
    md = "```\nraw untyped code\n```"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    block = doc.blocks[0]
    assert isinstance(block, CodeBlock)
    assert block.language == ""
    assert "raw untyped code" in block.content


def test_parse_nested_formatting_in_link():
    parser = PandocParser()
    md = "[**Bold Link** with *italics*](https://example.org)"
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)
    assert len(p.inlines) == 1
    link = p.inlines[0]
    assert link.span_type == InlineType.LINK
    assert link.target == "https://example.org"
    child_types = [c.span_type for c in link.children]
    assert InlineType.STRONG in child_types
    assert InlineType.EMPHASIS in child_types


def test_parse_multiple_legacy_replacements_on_same_line():
    parser = PandocParser()
    md = "Line with ![[crop1.jpg]] and ![[crop2.jpg]] on same line."
    doc = parser.parse(md)

    assert len(doc.blocks) == 1
    p = doc.blocks[0]
    assert isinstance(p, ParagraphBlock)

    img_spans = [s for s in p.inlines if s.span_type == InlineType.IMAGE]
    assert len(img_spans) == 2
    assert img_spans[0].target == "crop1.jpg"
    assert img_spans[1].target == "crop2.jpg"


def test_pandoc_parser_propagates_runner_errors():
    from unittest.mock import MagicMock
    from infrastructure.markdown.exceptions import MarkdownParserError, MarkdownParserTimeoutError

    mock_runner = MagicMock()
    mock_runner.run.side_effect = MarkdownParserTimeoutError("Timed out", timeout_seconds=5.0)

    parser = PandocParser(runner=mock_runner)
    with pytest.raises(MarkdownParserTimeoutError):
        parser.parse("Some markdown")

    mock_runner.run.side_effect = MarkdownParserError("Failed", returncode=1)
    with pytest.raises(MarkdownParserError):
        parser.parse("Some markdown")
