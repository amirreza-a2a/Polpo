# ============================================================
#  tests/unit/test_phase10e_parser.py
#  Unit Tests for Infrastructure MarkdownItParser & Wiki-Link Plugin
# ============================================================

import ast
from pathlib import Path
import unittest

from application.ports.markdown_parser import IMarkdownParser
from core.markdown.ast import (
    BlockType,
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    InlineSpan,
    InlineType,
    ListBlock,
    ListItem,
    MarkdownDocument,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.markdown.wiki_link_plugin import wiki_image_rule, wiki_link_plugin


class TestWikiLinkPlugin(unittest.TestCase):
    """Verifies the custom inline wiki-link image parser plugin under all syntax variations."""

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_valid_filename_only(self):
        doc = self.parser.parse("![[crop_1.jpg]]")
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.source, "crop_1.jpg")
        self.assertIsNone(block.region_id)
        self.assertEqual(block.raw_tag, "![[crop_1.jpg]]")

    def test_valid_filename_with_region_id(self):
        tag = "![[crop_1.jpg|region_id=018f2d5a-1234-7a8b-9cde-567812345678]]"
        doc = self.parser.parse(tag)
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.source, "crop_1.jpg")
        self.assertEqual(block.region_id, "018f2d5a-1234-7a8b-9cde-567812345678")
        self.assertEqual(block.raw_tag, tag)

    def test_malformed_region_id_is_extracted_for_resolver(self):
        tag = "![[crop_1.jpg|region_id=invalid-uuid-format]]"
        doc = self.parser.parse(tag)
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.region_id, "invalid-uuid-format")

    def test_unknown_metadata_key(self):
        tag = "![[crop_1.jpg|custom_key=custom_value]]"
        doc = self.parser.parse(tag)
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.source, "crop_1.jpg")
        self.assertIsNone(block.region_id)

    def test_unsupported_metadata_clause_leaves_region_id_none(self):
        tag = "![[crop_1.jpg|unsupported_param=123]]"
        doc = self.parser.parse(tag)
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.source, "crop_1.jpg")
        self.assertIsNone(block.region_id)
        self.assertEqual(block.alt_text, "")

    def test_trailing_whitespace_inside_parameters_stripped(self):
        tag = "![[crop_1.jpg|region_id=uuid-trimmed   ]]"
        doc = self.parser.parse(tag)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.region_id, "uuid-trimmed")

    def test_empty_region_id_value_leaves_region_id_none(self):
        tag = "![[crop_1.jpg|region_id=]]"
        doc = self.parser.parse(tag)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertIsNone(block.region_id)

    def test_strict_wiki_link_grammar_cases(self):
        # 1. Extra pipe-separated clause is rejected
        doc1 = self.parser.parse("![[file.jpg|region_id=id123|extra=value]]")
        self.assertEqual(len(doc1.blocks), 1)
        self.assertIsInstance(doc1.blocks[0], ImageBlock)
        self.assertEqual(doc1.blocks[0].source, "file.jpg")
        self.assertIsNone(doc1.blocks[0].region_id)

        # 2. Empty region_id remains None
        doc2 = self.parser.parse("![[file.jpg|region_id=]]")
        self.assertEqual(len(doc2.blocks), 1)
        self.assertIsInstance(doc2.blocks[0], ImageBlock)
        self.assertIsNone(doc2.blocks[0].region_id)

        # 3. Unsupported clause leaves region_id None
        doc3 = self.parser.parse("![[file.jpg|unsupported=value]]")
        self.assertEqual(len(doc3.blocks), 1)
        self.assertIsInstance(doc3.blocks[0], ImageBlock)
        self.assertIsNone(doc3.blocks[0].region_id)

        # 4. Valid single region_id assignment is extracted
        doc4 = self.parser.parse("![[file.jpg|region_id=id123]]")
        self.assertEqual(len(doc4.blocks), 1)
        self.assertIsInstance(doc4.blocks[0], ImageBlock)
        self.assertEqual(doc4.blocks[0].source, "file.jpg")
        self.assertEqual(doc4.blocks[0].region_id, "id123")

    def test_empty_filename_remains_literal_text(self):
        for empty_tag in ("![[]]", "![[   ]]", "![[ |region_id=123]]"):
            doc = self.parser.parse(empty_tag)
            self.assertEqual(len(doc.blocks), 1)
            self.assertIsInstance(doc.blocks[0], ParagraphBlock)

    def test_ellipsis_literal_filename(self):
        doc = self.parser.parse("![[...]]")
        self.assertEqual(len(doc.blocks), 1)
        block = doc.blocks[0]
        self.assertIsInstance(block, ImageBlock)
        self.assertEqual(block.source, "...")
        self.assertIsNone(block.region_id)

    def test_multiple_wiki_links_in_single_paragraph(self):
        text = "Here is ![[fig1.jpg|region_id=id1]] and ![[fig2.jpg|region_id=id2]] in one line."
        doc = self.parser.parse(text)
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ParagraphBlock)
        inlines = doc.blocks[0].inlines
        targets = [i.target for i in inlines if i.target]
        self.assertIn("fig1.jpg", targets)
        self.assertIn("fig2.jpg", targets)

    def test_adjacent_wiki_links(self):
        text = "![[fig1.jpg]]![[fig2.jpg]]"
        tokens = self.parser.parse_tokens(text)
        # Verify tokens inside inline
        inline_tokens = [t for t in tokens if t.type == "inline"][0]
        image_tokens = [c for c in inline_tokens.children if c.type == "image"]
        self.assertEqual(len(image_tokens), 2)
        self.assertEqual(image_tokens[0].attrs.get("src"), "fig1.jpg")
        self.assertEqual(image_tokens[1].attrs.get("src"), "fig2.jpg")

    def test_wiki_link_next_to_ordinary_markdown_image(self):
        text = "![[wiki.jpg|region_id=123]] and ![Standard](std.jpg)"
        tokens = self.parser.parse_tokens(text)
        inline_tokens = [t for t in tokens if t.type == "inline"][0]
        image_tokens = [c for c in inline_tokens.children if c.type == "image"]
        self.assertEqual(len(image_tokens), 2)
        self.assertTrue(image_tokens[0].meta.get("wiki_link"))
        self.assertEqual(image_tokens[0].meta.get("region_id"), "123")
        self.assertFalse(image_tokens[1].meta.get("wiki_link", False))


class TestCodeProtection(unittest.TestCase):
    """Verifies that code spans and code blocks take precedence over wiki-link syntax."""

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_inline_code_protects_wiki_link_syntax(self):
        text = "Check this command: `![[crop_1.jpg|region_id=018f2d5a]]` in terminal."
        doc = self.parser.parse(text)
        self.assertEqual(len(doc.blocks), 1)
        p = doc.blocks[0]
        self.assertIsInstance(p, ParagraphBlock)

        code_spans = [i for i in p.inlines if i.span_type == InlineType.CODE_SPAN]
        self.assertEqual(len(code_spans), 1)
        self.assertEqual(code_spans[0].text, "![[crop_1.jpg|region_id=018f2d5a]]")

        # Prove no ImageBlock was created
        image_blocks = [b for b in doc.blocks if isinstance(b, ImageBlock)]
        self.assertEqual(len(image_blocks), 0)

    def test_fenced_code_block_protects_wiki_link_syntax(self):
        text = """```markdown
![[crop_1.jpg|region_id=018f2d5a]]
```"""
        doc = self.parser.parse(text)
        self.assertEqual(len(doc.blocks), 1)
        code = doc.blocks[0]
        self.assertIsInstance(code, CodeBlock)
        self.assertEqual(code.language, "markdown")
        self.assertIn("![[crop_1.jpg|region_id=018f2d5a]]", code.content)

        # Prove no ImageBlock was created
        image_blocks = [b for b in doc.blocks if isinstance(b, ImageBlock)]
        self.assertEqual(len(image_blocks), 0)

    def test_indented_code_block_protects_wiki_link_syntax(self):
        text = "    ![[crop_1.jpg|region_id=018f2d5a]]\n"
        doc = self.parser.parse(text)
        self.assertEqual(len(doc.blocks), 1)
        code = doc.blocks[0]
        self.assertIsInstance(code, CodeBlock)
        self.assertIn("![[crop_1.jpg|region_id=018f2d5a]]", code.content)


class TestNormalMarkdownRegression(unittest.TestCase):
    """Verifies CommonMark constructs: headings, paragraphs, lists, quotes, tables, etc."""

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_headings_h1_through_h6(self):
        for lvl in range(1, 7):
            src = f"{'#' * lvl} Heading {lvl}"
            doc = self.parser.parse(src)
            self.assertEqual(len(doc.blocks), 1)
            h = doc.blocks[0]
            self.assertIsInstance(h, HeadingBlock)
            self.assertEqual(h.level, lvl)
            self.assertEqual(h.inlines[0].text, f"Heading {lvl}")

    def test_paragraph_with_emphasis_strong_and_links(self):
        src = "A **bold** word, an *italic* word, and a [Link](https://polpot.app)."
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        p = doc.blocks[0]
        self.assertIsInstance(p, ParagraphBlock)

        span_types = [i.span_type for i in p.inlines]
        self.assertIn(InlineType.STRONG, span_types)
        self.assertIn(InlineType.EMPHASIS, span_types)
        self.assertIn(InlineType.LINK, span_types)

        link_span = [i for i in p.inlines if i.span_type == InlineType.LINK][0]
        self.assertEqual(link_span.target, "https://polpot.app")
        self.assertEqual(link_span.plain_text, "Link")

    def test_standalone_standard_markdown_image(self):
        src = "![Architecture Diagram](https://example.com/arch.png)"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        img = doc.blocks[0]
        self.assertIsInstance(img, ImageBlock)
        self.assertEqual(img.source, "https://example.com/arch.png")
        self.assertEqual(img.alt_text, "Architecture Diagram")
        self.assertIsNone(img.region_id)

    def test_bullet_and_ordered_lists(self):
        src_bullet = "- Item A\n- Item B"
        doc_bullet = self.parser.parse(src_bullet)
        self.assertEqual(len(doc_bullet.blocks), 1)
        b_list = doc_bullet.blocks[0]
        self.assertIsInstance(b_list, ListBlock)
        self.assertFalse(b_list.is_ordered)
        self.assertEqual(len(b_list.items), 2)

        src_ordered = "1. First\n2. Second"
        doc_ordered = self.parser.parse(src_ordered)
        self.assertEqual(len(doc_ordered.blocks), 1)
        o_list = doc_ordered.blocks[0]
        self.assertIsInstance(o_list, ListBlock)
        self.assertTrue(o_list.is_ordered)
        self.assertEqual(o_list.start_index, 1)

    def test_task_lists_support(self):
        src = "- [ ] Unfinished task\n- [x] Finished task\n- [X] Also finished"
        doc = self.parser.parse(src)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertEqual(len(lst.items), 3)

        self.assertTrue(lst.items[0].is_task)
        self.assertFalse(lst.items[0].task_checked)
        self.assertEqual(lst.items[0].inlines[0].text, "Unfinished task")

        self.assertTrue(lst.items[1].is_task)
        self.assertTrue(lst.items[1].task_checked)
        self.assertEqual(lst.items[1].inlines[0].text, "Finished task")

        self.assertTrue(lst.items[2].is_task)
        self.assertTrue(lst.items[2].task_checked)

    def test_blockquote_with_nested_blocks(self):
        src = "> # Quoted Title\n>\n> Quoted paragraph."
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        bq = doc.blocks[0]
        self.assertIsInstance(bq, BlockquoteBlock)
        self.assertEqual(len(bq.blocks), 2)
        self.assertIsInstance(bq.blocks[0], HeadingBlock)
        self.assertIsInstance(bq.blocks[1], ParagraphBlock)

    def test_thematic_break(self):
        doc = self.parser.parse("---")
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ThematicBreakBlock)

    def test_table_parsing_into_table_fallback_block(self):
        src = "| Name | Role |\n|---|---|\n| Alice | Lead |\n| Bob | Reviewer |\n"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        tbl = doc.blocks[0]
        self.assertIsInstance(tbl, TableFallbackBlock)
        self.assertEqual(tbl.raw_table, src)
        self.assertEqual(len(tbl.headers), 2)
        self.assertEqual(tbl.headers[0][0].text, "Name")
        self.assertEqual(tbl.headers[1][0].text, "Role")
        self.assertEqual(len(tbl.rows), 2)
        self.assertEqual(tbl.rows[0][0][0].text, "Alice")
        self.assertEqual(tbl.rows[0][1][0].text, "Lead")

    def test_raw_table_preserves_exact_source_slice_with_no_normalization(self):
        src = "Intro\n\n   | Col 1 | Col 2 |\n   |---|---|\n   | Val 1 | Val 2 |\n\nOutro"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 3)
        tbl = doc.blocks[1]
        self.assertIsInstance(tbl, TableFallbackBlock)
        expected_raw = "   | Col 1 | Col 2 |\n   |---|---|\n   | Val 1 | Val 2 |\n"
        self.assertEqual(tbl.raw_table, expected_raw)

    def test_raw_html_disabled_to_reduce_injection_surface(self):
        src = "<script>alert('xss')</script>\n<div class='injected'>Text</div>"
        doc = self.parser.parse(src)
        # With html=False, markdown-it does not parse raw HTML blocks into html_block
        # Instead, it escapes or treats them as plain paragraph text
        for b in doc.blocks:
            self.assertNotEqual(b.block_type, "html_block")


class TestMixedInlineImages(unittest.TestCase):
    """
    Verifies that mixed inline images inside paragraphs, formatted spans,
    and multiple occurrences preserve distinct inline image semantics without loss.
    """

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_standard_markdown_image_inside_paragraph(self):
        src = "Text ![diagram](diagram.png) more text"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ParagraphBlock)
        inlines = doc.blocks[0].inlines
        self.assertEqual(len(inlines), 3)

        self.assertEqual(inlines[0].span_type, InlineType.TEXT)
        self.assertEqual(inlines[0].text, "Text ")

        self.assertEqual(inlines[1].span_type, InlineType.IMAGE)
        self.assertEqual(inlines[1].text, "diagram")
        self.assertEqual(inlines[1].target, "diagram.png")
        self.assertIsNone(inlines[1].region_id)

        self.assertEqual(inlines[2].span_type, InlineType.TEXT)
        self.assertEqual(inlines[2].text, " more text")

    def test_wiki_link_image_inside_paragraph(self):
        src = "Text ![[figure.jpg|region_id=018f2d5a-1234-7a8b-9cde-567812345678]] more text"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ParagraphBlock)
        inlines = doc.blocks[0].inlines
        self.assertEqual(len(inlines), 3)

        self.assertEqual(inlines[0].span_type, InlineType.TEXT)
        self.assertEqual(inlines[0].text, "Text ")

        self.assertEqual(inlines[1].span_type, InlineType.IMAGE)
        self.assertEqual(inlines[1].text, "")
        self.assertEqual(inlines[1].target, "figure.jpg")
        self.assertEqual(inlines[1].region_id, "018f2d5a-1234-7a8b-9cde-567812345678")

        self.assertEqual(inlines[2].span_type, InlineType.TEXT)
        self.assertEqual(inlines[2].text, " more text")

    def test_image_between_formatted_inline_spans(self):
        src = "**Bold** ![diag](diag.png) *Italic*"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ParagraphBlock)
        inlines = doc.blocks[0].inlines

        self.assertEqual(len(inlines), 5)
        self.assertEqual(inlines[0].span_type, InlineType.STRONG)
        self.assertEqual(inlines[0].plain_text, "Bold")

        self.assertEqual(inlines[1].span_type, InlineType.TEXT)
        self.assertEqual(inlines[1].text, " ")

        self.assertEqual(inlines[2].span_type, InlineType.IMAGE)
        self.assertEqual(inlines[2].text, "diag")
        self.assertEqual(inlines[2].target, "diag.png")

        self.assertEqual(inlines[3].span_type, InlineType.TEXT)
        self.assertEqual(inlines[3].text, " ")

        self.assertEqual(inlines[4].span_type, InlineType.EMPHASIS)
        self.assertEqual(inlines[4].plain_text, "Italic")

    def test_multiple_inline_images_in_mixed_text(self):
        src = "![one](1.png) and ![two](2.png)"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        self.assertIsInstance(doc.blocks[0], ParagraphBlock)
        inlines = doc.blocks[0].inlines
        self.assertEqual(len(inlines), 3)

        self.assertEqual(inlines[0].span_type, InlineType.IMAGE)
        self.assertEqual(inlines[0].text, "one")
        self.assertEqual(inlines[0].target, "1.png")

        self.assertEqual(inlines[1].span_type, InlineType.TEXT)
        self.assertEqual(inlines[1].text, " and ")

        self.assertEqual(inlines[2].span_type, InlineType.IMAGE)
        self.assertEqual(inlines[2].text, "two")
        self.assertEqual(inlines[2].target, "2.png")


class TestNestedListParsing(unittest.TestCase):
    """
    Verifies that nested list structures preserve token nesting depth,
    never terminate parent list items early, and represent visible nested-list
    content as a deterministic textual fallback inside the parent ListItem.
    Note: This preserves visible content, but does not preserve structural/hierarchical
    nested list semantics because the Core ListItem model has no child/nested list fields.
    """

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_nested_list_contract_uses_deterministic_textual_fallback_not_structural_nodes(self):
        """
        Explicitly documents the Phase 10E.2 AST contract limitation:
        Core ListItem lacks nested-block/child fields. Therefore, nested lists
        do not produce separate nested ListItem nodes, but rather append visible
        content as deterministic textual fallback spans within the parent ListItem.
        """
        src = "- Top item\n  - Sub item"
        doc = self.parser.parse(src)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        # Structural contract: exactly 1 top-level ListItem node exists; no sub-nodes are created
        self.assertEqual(len(lst.items), 1)
        parent_item = lst.items[0]
        # Semantic limitation: parent_item has no nested_list or children attribute
        self.assertFalse(hasattr(parent_item, "nested_list"))
        self.assertFalse(hasattr(parent_item, "children"))
        self.assertFalse(hasattr(parent_item, "items"))
        # Deterministic textual fallback: sub-item content is preserved as inlines within parent
        inlines_text = "".join(span.plain_text for span in parent_item.inlines)
        self.assertEqual(inlines_text, "Top item\n  - Sub item")

    def test_one_level_nested_bullet_list(self):
        src = "- A\n  - B\n  - C"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertFalse(lst.is_ordered)
        self.assertEqual(len(lst.items), 1)
        item_text = "".join(span.plain_text for span in lst.items[0].inlines)
        self.assertIn("A", item_text)
        self.assertIn("- B", item_text)
        self.assertIn("- C", item_text)

    def test_nested_ordered_list(self):
        src = "1. First\n   1. Sub 1\n   2. Sub 2"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertTrue(lst.is_ordered)
        self.assertEqual(len(lst.items), 1)
        item_text = "".join(span.plain_text for span in lst.items[0].inlines)
        self.assertIn("First", item_text)
        self.assertIn("1. Sub 1", item_text)
        self.assertIn("2. Sub 2", item_text)

    def test_mixed_bullet_ordered_nesting(self):
        src = "- Bullet item\n  1. Ordered sub 1\n  2. Ordered sub 2"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertFalse(lst.is_ordered)
        self.assertEqual(len(lst.items), 1)
        item_text = "".join(span.plain_text for span in lst.items[0].inlines)
        self.assertIn("Bullet item", item_text)
        self.assertIn("1. Ordered sub 1", item_text)
        self.assertIn("2. Ordered sub 2", item_text)

    def test_nested_list_followed_by_another_top_level_item(self):
        src = "- A\n  - B\n- C"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertEqual(len(lst.items), 2)
        item0_text = "".join(span.plain_text for span in lst.items[0].inlines)
        item1_text = "".join(span.plain_text for span in lst.items[1].inlines)
        self.assertIn("A", item0_text)
        self.assertIn("- B", item0_text)
        self.assertEqual(item1_text.strip(), "C")

    def test_nested_list_followed_by_top_level_user_example(self):
        src = "- A\n  - B\n  - C\n- D"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertEqual(len(lst.items), 2)
        item0_text = "".join(span.plain_text for span in lst.items[0].inlines)
        item1_text = "".join(span.plain_text for span in lst.items[1].inlines)
        self.assertIn("A", item0_text)
        self.assertIn("- B", item0_text)
        self.assertIn("- C", item0_text)
        self.assertEqual(item1_text.strip(), "D")

    def test_deeply_nested_list(self):
        src = "- Level 1\n  - Level 2\n    - Level 3"
        doc = self.parser.parse(src)
        self.assertEqual(len(doc.blocks), 1)
        lst = doc.blocks[0]
        self.assertIsInstance(lst, ListBlock)
        self.assertEqual(len(lst.items), 1)
        item_text = "".join(span.plain_text for span in lst.items[0].inlines)
        self.assertIn("Level 1", item_text)
        self.assertIn("- Level 2", item_text)
        self.assertIn("- Level 3", item_text)


class TestParserDeterminismAndTokenIsolation(unittest.TestCase):
    """Verifies that parser execution is 100% deterministic and tokens are strictly isolated."""

    def setUp(self):
        self.parser = MarkdownItParser()

    def test_parsing_is_deterministic(self):
        sample = """# Header\n\nParagraph text with **bold**.\n\n![[crop.jpg|region_id=123]]\n\n- Item 1\n- Item 2"""
        doc1 = self.parser.parse(sample)
        doc2 = self.parser.parse(sample)

        self.assertEqual(len(doc1.blocks), len(doc2.blocks))
        for b1, b2 in zip(doc1.blocks, doc2.blocks):
            self.assertEqual(b1, b2)

    def test_markdown_it_tokens_never_leak_into_ast(self):
        sample = """# Header\n\nParagraph with ![[crop.jpg|region_id=abc]] and [link](url)."""
        doc = self.parser.parse(sample)

        for block in doc.blocks:
            # Check block itself
            self.assertFalse(hasattr(block, "type_"))
            self.assertFalse(hasattr(block, "tag"))
            self.assertFalse(hasattr(block, "children"))
            self.assertFalse(hasattr(block, "attrs"))
            self.assertFalse(hasattr(block, "map"))
            # Check inlines if present
            if hasattr(block, "inlines"):
                for span in block.inlines:
                    self.assertIsInstance(span, InlineSpan)
                    self.assertFalse(hasattr(span, "attrs"))

    def test_port_interface_has_zero_forbidden_imports(self):
        port_file = Path(__file__).parent.parent.parent / "application" / "ports" / "markdown_parser.py"
        tree = ast.parse(port_file.read_text(encoding="utf-8"), filename=str(port_file))

        forbidden = {"markdown_it", "PySide6", "PyQt6", "Qt", "sqlite3", "infrastructure", "interfaces"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    root = n.name.split(".")[0]
                    self.assertNotIn(root, forbidden, f"Forbidden import '{n.name}' in {port_file}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, forbidden, f"Forbidden from-import '{node.module}' in {port_file}")


if __name__ == "__main__":
    unittest.main()
