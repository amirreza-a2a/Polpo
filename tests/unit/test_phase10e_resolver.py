# ============================================================
#  tests/unit/test_phase10e_resolver.py
#  Unit & Invariant Tests for Core Markdown AST & Pure Resolver
# ============================================================

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
import unittest
import uuid

from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import VisualRegion
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
    MarkdownBlock,
    MarkdownDocument,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
    EMPTY_METADATA,
)
from core.markdown.resolver import resolve_image_regions


class TestCoreMarkdownASTImmutability(unittest.TestCase):
    """Verifies that all Core Markdown AST types are strictly immutable."""

    def test_document_and_blocks_reject_in_place_field_assignment(self):
        span = InlineSpan(span_type=InlineType.TEXT, text="Hello world")
        p = ParagraphBlock(inlines=(span,))
        doc = MarkdownDocument(blocks=(p,))

        with self.assertRaises(FrozenInstanceError):
            doc.blocks = ()  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            p.inlines = ()  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            span.text = "Mutated"  # type: ignore

    def test_document_metadata_mappingproxy_rejects_in_place_mutation(self):
        doc = MarkdownDocument(blocks=(), metadata={"title": "Original"})
        self.assertIsInstance(doc.metadata, MappingProxyType)
        self.assertEqual(doc.metadata.get("title"), "Original")

        with self.assertRaises(TypeError):
            doc.metadata["title"] = "Mutated"  # type: ignore

        with self.assertRaises(TypeError):
            doc.metadata["new_key"] = "Value"  # type: ignore

    def test_collections_are_coerced_to_immutable_tuples(self):
        span1 = InlineSpan(span_type=InlineType.TEXT, text="One")
        span2 = InlineSpan(span_type=InlineType.TEXT, text="Two")
        # Pass mutable lists during instantiation
        p = ParagraphBlock(inlines=[span1, span2])  # type: ignore
        h = HeadingBlock(level=1, inlines=[span1])  # type: ignore
        item = ListItem(inlines=[span1])  # type: ignore
        l = ListBlock(items=[item])  # type: ignore
        bq = BlockquoteBlock(blocks=[p])  # type: ignore
        tbl = TableFallbackBlock(raw_table="| a |", headers=[[span1]], rows=[[[span2]]])  # type: ignore
        doc = MarkdownDocument(blocks=[p, h, l, bq, tbl])  # type: ignore

        self.assertIsInstance(p.inlines, tuple)
        self.assertIsInstance(h.inlines, tuple)
        self.assertIsInstance(item.inlines, tuple)
        self.assertIsInstance(l.items, tuple)
        self.assertIsInstance(bq.blocks, tuple)
        self.assertIsInstance(tbl.headers, tuple)
        self.assertIsInstance(tbl.rows, tuple)
        self.assertIsInstance(doc.blocks, tuple)

    def test_table_fallback_block_deep_nested_immutability(self):
        """Proves that TableFallbackBlock converts every nested list to an immutable tuple."""
        span1 = InlineSpan(span_type=InlineType.TEXT, text="Col1")
        span2 = InlineSpan(span_type=InlineType.TEXT, text="Col2")
        span3 = InlineSpan(span_type=InlineType.TEXT, text="Val1")
        span4 = InlineSpan(span_type=InlineType.TEXT, text="Val2")

        headers_input = [[span1], [span2]]
        rows_input = [
            [[span3], [span4]],
            [[span1, span3], [span2, span4]],
        ]

        tbl = TableFallbackBlock(
            raw_table="| Col1 | Col2 |\n| Val1 | Val2 |",
            headers=headers_input,
            rows=rows_input,
        )

        # 1. tbl.headers is a tuple
        self.assertIsInstance(tbl.headers, tuple)
        # 2. Each header element is a tuple
        for header_cell in tbl.headers:
            self.assertIsInstance(header_cell, tuple)
            for item in header_cell:
                self.assertIsInstance(item, InlineSpan)

        # 3. tbl.rows is a tuple
        self.assertIsInstance(tbl.rows, tuple)
        # 4. Each row is a tuple
        for row in tbl.rows:
            self.assertIsInstance(row, tuple)
            # 5. Each cell in the row is an inline collection tuple
            for cell in row:
                self.assertIsInstance(cell, tuple)
                for item in cell:
                    self.assertIsInstance(item, InlineSpan)

        # 6. Attempted mutation through any nested collection fails
        with self.assertRaises(AttributeError):
            tbl.headers.append((span1,))  # type: ignore

        with self.assertRaises(AttributeError):
            tbl.headers[0].append(span1)  # type: ignore

        with self.assertRaises(AttributeError):
            tbl.rows.append(())  # type: ignore

        with self.assertRaises(AttributeError):
            tbl.rows[0].append(())  # type: ignore

        with self.assertRaises(AttributeError):
            tbl.rows[0][0].append(span1)  # type: ignore

        # 7. Mutating original caller lists has zero effect on the AST
        headers_input[0].append(span2)
        rows_input[0][0].append(span4)
        rows_input.append([[span1]])
        self.assertEqual(len(tbl.headers[0]), 1)
        self.assertEqual(len(tbl.rows[0][0]), 1)
        self.assertEqual(len(tbl.rows), 2)

    def test_metadata_deep_recursive_immutability(self):
        """Proves that MarkdownDocument recursively freezes nested dicts, lists, and sets."""
        meta_input = {
            "title": "Document Title",
            "tags": ["desktop", "local-first"],
            "frontmatter": {
                "version": 1,
                "authors": ["Alice", "Bob"],
            },
            "flags": {"draft", "reviewed"},
        }
        doc = MarkdownDocument(blocks=(), metadata=meta_input)

        # Verify types
        self.assertIsInstance(doc.metadata, MappingProxyType)
        self.assertIsInstance(doc.metadata["frontmatter"], MappingProxyType)
        self.assertIsInstance(doc.metadata["tags"], tuple)
        self.assertIsInstance(doc.metadata["frontmatter"]["authors"], tuple)
        self.assertIsInstance(doc.metadata["flags"], frozenset)

        # Verify mutations raise errors
        with self.assertRaises(TypeError):
            doc.metadata["title"] = "Hacked"  # type: ignore

        with self.assertRaises(TypeError):
            doc.metadata["frontmatter"]["version"] = 2  # type: ignore

        with self.assertRaises(TypeError):
            doc.metadata["tags"][0] = "mutated"  # type: ignore

        with self.assertRaises(AttributeError):
            doc.metadata["frontmatter"]["authors"].append("Eve")  # type: ignore

        with self.assertRaises(AttributeError):
            doc.metadata["flags"].add("extra")  # type: ignore

        # Mutating original caller dict has zero effect on doc.metadata
        meta_input["title"] = "Modified Outside"
        meta_input["tags"].append("extra")
        meta_input["frontmatter"]["version"] = 999
        self.assertEqual(doc.metadata["title"], "Document Title")
        self.assertEqual(doc.metadata["tags"], ("desktop", "local-first"))
        self.assertEqual(doc.metadata["frontmatter"]["version"], 1)

    def test_inline_span_plain_text_recursive_derivation(self):
        text_span = InlineSpan(span_type=InlineType.TEXT, text="click here")
        link_span = InlineSpan(
            span_type=InlineType.LINK,
            target="https://example.com",
            children=(text_span,),
        )
        strong_span = InlineSpan(
            span_type=InlineType.STRONG,
            children=(link_span,),
        )
        self.assertEqual(strong_span.plain_text, "click here")


class TestPureRegionResolver(unittest.TestCase):
    """Verifies the four-tier image region resolver under all edge cases."""

    def setUp(self):
        self.job_id = 42
        self.bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        self.rid_1 = uuid.uuid4().hex
        self.rid_2 = uuid.uuid4().hex
        self.rid_3 = uuid.uuid4().hex

        self.region_1 = VisualRegion.create_ai_detected(
            job_id=self.job_id,
            page_number=1,
            display_order=1,
            detected_bbox=self.bbox,
            region_id=self.rid_1,
        )
        self.region_2 = VisualRegion.create_ai_detected(
            job_id=self.job_id,
            page_number=1,
            display_order=2,
            detected_bbox=self.bbox,
            region_id=self.rid_2,
        )
        self.region_3 = VisualRegion.create_user_manual(
            job_id=self.job_id,
            page_number=2,
            display_order=1,
            reviewed_bbox=self.bbox,
            region_id=self.rid_3,
        )
        self.active_regions = [self.region_1, self.region_2, self.region_3]

    def test_tier_1_explicit_region_id_in_attribute(self):
        """Tier 1: Explicit region_id in ImageBlock.region_id resolves canonical identity."""
        img = ImageBlock(
            source="custom_crop.jpg",
            region_id=self.rid_1,
            raw_tag=f"![[custom_crop.jpg|region_id={self.rid_1}]]",
        )
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertIsInstance(res_img, ImageBlock)
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)
        self.assertEqual(res_img.display_order, 1)

    def test_tier_1_explicit_region_id_in_raw_tag(self):
        """Tier 1: Explicit region_id declared in raw_tag resolves correctly."""
        img = ImageBlock(
            source="crop.jpg",
            raw_tag=f"![[crop.jpg|region_id={self.rid_2}|alt=Diagram]]",
        )
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_2)
        self.assertEqual(res_img.display_order, 2)

    def test_tier_1_explicit_region_id_in_title_and_alt(self):
        """Tier 1: Explicit region_id declared in Markdown image title or alt."""
        img_title = ImageBlock(
            source="crop.jpg",
            title=f"Figure 1 (region_id={self.rid_3})",
        )
        doc = MarkdownDocument(blocks=(img_title,))
        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_3)
        self.assertEqual(res_img.display_order, 1)

    def test_tier_1_explicit_region_id_precedence_over_lower_tiers(self):
        """Tier 1 takes precedence over Tier 2 and Tier 3 filename patterns."""
        # Filename implies Tier 3 (p1_1 -> region_1), but explicit tag declares region_2
        img = ImageBlock(
            source=f"crop_{self.job_id}_p1_1.jpg",
            region_id=self.rid_2,
            raw_tag=f"![[crop_{self.job_id}_p1_1.jpg|region_id={self.rid_2}]]",
        )
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_2)
        self.assertEqual(res_img.display_order, 2)

    def test_tier_1_unmatched_explicit_id_preserves_id_without_association(self):
        """Explicit region_id not in active regions preserves region_id with is_associated=False."""
        unknown_rid = uuid.uuid4().hex
        img = ImageBlock(
            source="crop.jpg",
            region_id=unknown_rid,
            raw_tag=f"![[crop.jpg|region_id={unknown_rid}]]",
        )
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertEqual(res_img.region_id, unknown_rid)
        self.assertIsNone(res_img.display_order)

    def test_tier_2_reviewed_filename_pattern_resolves(self):
        """Tier 2: Filename crop_{job_id}_{region_id}_v{version}.jpg resolves canonical UUID."""
        source_name = f"crop_{self.job_id}_{self.rid_1}_v2.jpg"
        img = ImageBlock(source=source_name)
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)
        self.assertEqual(res_img.display_order, 1)

    def test_tier_2_different_job_id_in_filename_rejected(self):
        """Tier 2 rejects filename when job_id does not match the target document job_id."""
        other_job_id = 999
        source_name = f"crop_{other_job_id}_{self.rid_1}_v1.jpg"
        img = ImageBlock(source=source_name)
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertIsNone(res_img.region_id)

    def test_tier_2_stale_uuid_in_filename_remains_unassociated(self):
        """Tier 2 with unknown/deleted UUID does not associate."""
        stale_rid = uuid.uuid4().hex
        source_name = f"crop_{self.job_id}_{stale_rid}_v1.jpg"
        img = ImageBlock(source=source_name)
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertIsNone(res_img.region_id)

    def test_tier_3_legacy_display_order_heuristic_resolves(self):
        """Tier 3: crop_{job_id}_p{page}_{order}.jpg resolves against active region."""
        source_name = f"crop_{self.job_id}_p1_2.jpg"
        img = ImageBlock(source=source_name)
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_2)
        self.assertEqual(res_img.display_order, 2)

    def test_tier_3_ambiguous_legacy_mapping_remains_unassociated(self):
        """Tier 3 with duplicate display order candidates refuses to guess."""
        dup_rid = uuid.uuid4().hex
        dup_region = VisualRegion.create_ai_detected(
            job_id=self.job_id,
            page_number=1,
            display_order=1,  # Same as self.region_1!
            detected_bbox=self.bbox,
            region_id=dup_rid,
        )
        ambiguous_regions = [self.region_1, dup_region]

        img = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, ambiguous_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertIsNone(res_img.region_id)

    def test_tier_3_different_job_id_does_not_match(self):
        """Tier 3 rejects legacy filename with a foreign job ID."""
        img = ImageBlock(source="crop_888_p1_1.jpg")
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertIsNone(res_img.region_id)

    def test_tier_4_unassociated_external_image(self):
        """Tier 4: Standard user markdown images remain unassociated."""
        img = ImageBlock(source="architecture_diagram.png", alt_text="Overview")
        doc = MarkdownDocument(blocks=(img,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertFalse(res_img.is_associated)
        self.assertIsNone(res_img.region_id)
        self.assertIsNone(res_img.display_order)
        self.assertEqual(res_img.source, "architecture_diagram.png")
        self.assertEqual(res_img.alt_text, "Overview")

    def test_rejected_regions_are_never_associated(self):
        """Regions with review_status=REJECTED (is_deleted=True) are ignored."""
        self.region_1.reject()
        self.assertTrue(self.region_1.is_deleted)

        img_tier2 = ImageBlock(source=f"crop_{self.job_id}_{self.rid_1}_v1.jpg")
        img_tier3 = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")
        doc = MarkdownDocument(blocks=(img_tier2, img_tier3))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        self.assertFalse(resolved.blocks[0].is_associated)
        self.assertFalse(resolved.blocks[1].is_associated)

    def test_empty_active_regions_and_empty_document(self):
        """Resolver handles empty regions or empty document without error."""
        doc = MarkdownDocument(blocks=())
        resolved = resolve_image_regions(doc, [], self.job_id)
        self.assertEqual(len(resolved.blocks), 0)

        img = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")
        doc_img = MarkdownDocument(blocks=(img,))
        resolved_img = resolve_image_regions(doc_img, [], self.job_id)
        self.assertFalse(resolved_img.blocks[0].is_associated)

    def test_non_image_ast_nodes_are_preserved_completely(self):
        """Headings, paragraphs, code blocks, lists, thematic breaks, tables remain untouched."""
        span = InlineSpan(span_type=InlineType.TEXT, text="Sample text")
        h = HeadingBlock(level=2, inlines=(span,))
        p = ParagraphBlock(inlines=(span,))
        code = CodeBlock(content="print(1)", language="python")
        item = ListItem(inlines=(span,))
        lst = ListBlock(items=(item,), is_ordered=True)
        hr = ThematicBreakBlock()
        tbl = TableFallbackBlock(raw_table="| a |", headers=((span,),), rows=(((span,),),))
        img = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")

        doc = MarkdownDocument(blocks=(h, p, code, lst, hr, tbl, img))
        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)

        self.assertEqual(resolved.blocks[0], h)
        self.assertEqual(resolved.blocks[1], p)
        self.assertEqual(resolved.blocks[2], code)
        self.assertEqual(resolved.blocks[3], lst)
        self.assertEqual(resolved.blocks[4], hr)
        self.assertEqual(resolved.blocks[5], tbl)
        self.assertTrue(resolved.blocks[6].is_associated)

    def test_nested_images_inside_blockquotes_resolve_recursively(self):
        """Images nested inside BlockquoteBlock are resolved."""
        img = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")
        bq = BlockquoteBlock(blocks=(img,))
        doc = MarkdownDocument(blocks=(bq,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_bq = resolved.blocks[0]
        self.assertIsInstance(res_bq, BlockquoteBlock)
        res_img = res_bq.blocks[0]
        self.assertIsInstance(res_img, ImageBlock)
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)

    def test_resolver_determinism_and_input_non_mutation(self):
        """Resolving identical input multiple times produces equivalent output without mutating input."""
        img = ImageBlock(source=f"crop_{self.job_id}_p1_1.jpg")
        doc = MarkdownDocument(blocks=(img,))

        res1 = resolve_image_regions(doc, self.active_regions, self.job_id)
        res2 = resolve_image_regions(doc, self.active_regions, self.job_id)

        # Output is equivalent
        self.assertEqual(res1.blocks[0].region_id, res2.blocks[0].region_id)
        self.assertEqual(res1.blocks[0].is_associated, res2.blocks[0].is_associated)

        # Input doc was NOT mutated
        self.assertFalse(doc.blocks[0].is_associated)
        self.assertIsNone(doc.blocks[0].region_id)


class TestCoreMarkdownDependencyBoundaries(unittest.TestCase):
    """
    Architectural invariant test verifying that core/markdown contains
    zero imports of Qt, PySide6, SQLite, markdown_it, or outer layers.
    """

    def test_core_markdown_has_zero_forbidden_imports(self):
        markdown_dir = Path(__file__).parent.parent.parent / "core" / "markdown"
        forbidden_roots = {
            "PySide6",
            "PyQt6",
            "PyQt5",
            "PySide2",
            "Qt",
            "sqlite3",
            "markdown_it",
            "application",
            "infrastructure",
            "interfaces",
        }

        python_files = list(markdown_dir.glob("*.py"))
        self.assertGreater(len(python_files), 0, "Expected python files in core/markdown/")

        for fpath in python_files:
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        root_mod = name.name.split(".")[0]
                        self.assertNotIn(
                            root_mod,
                            forbidden_roots,
                            f"Forbidden import '{name.name}' in {fpath}",
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root_mod = node.module.split(".")[0]
                        self.assertNotIn(
                            root_mod,
                            forbidden_roots,
                            f"Forbidden from-import '{node.module}' in {fpath}",
                        )


class TestInlineVisualRegionResolver(unittest.TestCase):
    """
    Focused regression tests for inline visual region identity resolution across
    paragraphs, headings, lists, blockquotes, tables, and nested inline spans.
    Verifies that InlineSpan(IMAGE) adheres to the exact same deterministic 4-tier
    resolution policy as standalone ImageBlock.
    """

    def setUp(self):
        self.job_id = 42
        self.bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        self.rid_1 = uuid.uuid4().hex
        self.rid_2 = uuid.uuid4().hex
        self.rid_3 = uuid.uuid4().hex

        self.region_1 = VisualRegion.create_ai_detected(
            job_id=self.job_id,
            page_number=1,
            display_order=1,
            detected_bbox=self.bbox,
            region_id=self.rid_1,
        )
        self.region_2 = VisualRegion.create_ai_detected(
            job_id=self.job_id,
            page_number=1,
            display_order=2,
            detected_bbox=self.bbox,
            region_id=self.rid_2,
        )
        self.region_3 = VisualRegion.create_user_manual(
            job_id=self.job_id,
            page_number=2,
            display_order=1,
            reviewed_bbox=self.bbox,
            region_id=self.rid_3,
        )
        self.active_regions = [self.region_1, self.region_2, self.region_3]

    def test_case_01_standalone_image_block_resolves_exactly_as_before(self):
        """Case 1: Standalone ImageBlock still resolves exactly as before."""
        img = ImageBlock(source=f"crop_{self.job_id}_{self.rid_1}_v1.jpg")
        doc = MarkdownDocument(blocks=(img,))
        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0]
        self.assertIsInstance(res_img, ImageBlock)
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)
        self.assertEqual(res_img.display_order, 1)

    def test_case_02_paragraph_containing_one_inline_image_resolves(self):
        """Case 2: Paragraph containing one inline image resolves."""
        text_span = InlineSpan(span_type=InlineType.TEXT, text="Caption: ")
        img_span = InlineSpan(
            span_type=InlineType.IMAGE,
            target=f"crop_{self.job_id}_{self.rid_1}_v2.png",
            text="Diagram",
        )
        p = ParagraphBlock(inlines=(text_span, img_span))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_p = resolved.blocks[0]
        self.assertIsInstance(res_p, ParagraphBlock)
        self.assertEqual(len(res_p.inlines), 2)
        self.assertFalse(res_p.inlines[0].is_associated)
        self.assertTrue(res_p.inlines[1].is_associated)
        self.assertEqual(res_p.inlines[1].region_id, self.rid_1)
        self.assertEqual(res_p.inlines[1].display_order, 1)
        self.assertEqual(res_p.inlines[1].text, "Diagram")
        self.assertEqual(res_p.inlines[1].target, f"crop_{self.job_id}_{self.rid_1}_v2.png")

    def test_case_03_paragraph_containing_multiple_inline_images_resolves_independently(self):
        """Case 3: Paragraph containing multiple inline images resolves independently."""
        img1 = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_1, target="custom1.png")
        sep = InlineSpan(span_type=InlineType.TEXT, text=" and ")
        img2 = InlineSpan(span_type=InlineType.IMAGE, target=f"crop_{self.job_id}_{self.rid_2}_v1.png")
        p = ParagraphBlock(inlines=(img1, sep, img2))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_p = resolved.blocks[0]
        self.assertTrue(res_p.inlines[0].is_associated)
        self.assertEqual(res_p.inlines[0].region_id, self.rid_1)
        self.assertEqual(res_p.inlines[0].display_order, 1)
        self.assertFalse(res_p.inlines[1].is_associated)
        self.assertTrue(res_p.inlines[2].is_associated)
        self.assertEqual(res_p.inlines[2].region_id, self.rid_2)
        self.assertEqual(res_p.inlines[2].display_order, 2)

    def test_case_04_heading_containing_an_inline_image_resolves(self):
        """Case 4: Heading containing an inline image resolves."""
        img = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_3, target="icon.png")
        title = InlineSpan(span_type=InlineType.TEXT, text=" Section Title")
        h = HeadingBlock(level=2, inlines=(img, title))
        doc = MarkdownDocument(blocks=(h,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_h = resolved.blocks[0]
        self.assertIsInstance(res_h, HeadingBlock)
        self.assertTrue(res_h.inlines[0].is_associated)
        self.assertEqual(res_h.inlines[0].region_id, self.rid_3)
        self.assertEqual(res_h.inlines[0].display_order, 1)
        self.assertEqual(res_h.inlines[1].text, " Section Title")

    def test_case_05_list_item_containing_an_inline_image_resolves(self):
        """Case 5: List item containing an inline image resolves."""
        label = InlineSpan(span_type=InlineType.TEXT, text="Figure item: ")
        img = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_1, target="crop.png")
        item = ListItem(inlines=(label, img))
        lst = ListBlock(items=(item,), is_ordered=False)
        doc = MarkdownDocument(blocks=(lst,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_lst = resolved.blocks[0]
        self.assertIsInstance(res_lst, ListBlock)
        res_item = res_lst.items[0]
        self.assertTrue(res_item.inlines[1].is_associated)
        self.assertEqual(res_item.inlines[1].region_id, self.rid_1)
        self.assertEqual(res_item.inlines[1].display_order, 1)

    def test_case_06_blockquote_containing_inline_image_resolves_recursively(self):
        """Case 6: Blockquote containing inline image resolves recursively."""
        img = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_2, target="quote.png")
        p = ParagraphBlock(inlines=(img,))
        bq = BlockquoteBlock(blocks=(p,))
        doc = MarkdownDocument(blocks=(bq,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_bq = resolved.blocks[0]
        self.assertIsInstance(res_bq, BlockquoteBlock)
        res_p = res_bq.blocks[0]
        self.assertIsInstance(res_p, ParagraphBlock)
        self.assertTrue(res_p.inlines[0].is_associated)
        self.assertEqual(res_p.inlines[0].region_id, self.rid_2)
        self.assertEqual(res_p.inlines[0].display_order, 2)

    def test_case_07_explicit_region_id_remains_authoritative(self):
        """Case 7: Explicit region_id remains authoritative over filename patterns."""
        # Filename implies Tier 3 (p1_1 -> region_1), but explicit region_id specifies region_2
        img = InlineSpan(
            span_type=InlineType.IMAGE,
            target=f"crop_{self.job_id}_p1_1.jpg",
            region_id=self.rid_2,
        )
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0].inlines[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_2)
        self.assertEqual(res_img.display_order, 2)

    def test_case_08_unknown_explicit_region_id_remains_unassociated_according_to_existing_policy(self):
        """Case 8: Unknown explicit region_id remains unassociated without falling back to filename."""
        unknown_rid = uuid.uuid4().hex
        img = InlineSpan(
            span_type=InlineType.IMAGE,
            target=f"crop_{self.job_id}_p1_1.jpg",
            region_id=unknown_rid,
        )
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0].inlines[0]
        self.assertFalse(res_img.is_associated)
        self.assertEqual(res_img.region_id, unknown_rid)
        self.assertIsNone(res_img.display_order)

    def test_case_09_legacy_filename_association_works_where_currently_supported(self):
        """Case 9: Legacy filename association works where currently supported."""
        img = InlineSpan(
            span_type=InlineType.IMAGE,
            target=f"crop_{self.job_id}_p1_2.jpg",
        )
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0].inlines[0]
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_2)
        self.assertEqual(res_img.display_order, 2)

    def test_case_10_repeated_resolution_is_deterministic(self):
        """Case 10: Repeated resolution is deterministic."""
        img1 = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_1, target="1.jpg")
        img2 = InlineSpan(span_type=InlineType.IMAGE, target=f"crop_{self.job_id}_{self.rid_2}_v1.jpg")
        p = ParagraphBlock(inlines=(img1, img2))
        doc = MarkdownDocument(blocks=(p,))

        res1 = resolve_image_regions(doc, self.active_regions, self.job_id)
        res2 = resolve_image_regions(doc, self.active_regions, self.job_id)

        self.assertEqual(res1.blocks[0].inlines[0].region_id, res2.blocks[0].inlines[0].region_id)
        self.assertEqual(res1.blocks[0].inlines[0].is_associated, res2.blocks[0].inlines[0].is_associated)
        self.assertEqual(res1.blocks[0].inlines[1].region_id, res2.blocks[0].inlines[1].region_id)
        self.assertEqual(res1.blocks[0].inlines[1].is_associated, res2.blocks[0].inlines[1].is_associated)

    def test_case_11_original_immutable_ast_is_not_mutated(self):
        """Case 11: Original immutable AST is not mutated."""
        img = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_1, target="crop.jpg")
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        # Input AST remains untouched
        self.assertFalse(img.is_associated)
        self.assertFalse(p.inlines[0].is_associated)
        self.assertFalse(doc.blocks[0].inlines[0].is_associated)
        # Resolved output reflects association
        self.assertTrue(resolved.blocks[0].inlines[0].is_associated)

    def test_case_12_existing_10e1_resolver_behavior_remains_unchanged_for_block_level_images(self):
        """Case 12: Existing 10E.1 resolver behavior remains unchanged for block-level images."""
        img_t1 = ImageBlock(source="t1.jpg", region_id=self.rid_1)
        img_t2 = ImageBlock(source=f"crop_{self.job_id}_{self.rid_2}_v1.png")
        img_t3 = ImageBlock(source=f"crop_{self.job_id}_p2_1.png")
        img_t4 = ImageBlock(source="standard.jpg")

        doc = MarkdownDocument(blocks=(img_t1, img_t2, img_t3, img_t4))
        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)

        # Tier 1
        self.assertTrue(resolved.blocks[0].is_associated)
        self.assertEqual(resolved.blocks[0].region_id, self.rid_1)
        self.assertEqual(resolved.blocks[0].display_order, 1)
        # Tier 2
        self.assertTrue(resolved.blocks[1].is_associated)
        self.assertEqual(resolved.blocks[1].region_id, self.rid_2)
        self.assertEqual(resolved.blocks[1].display_order, 2)
        # Tier 3
        self.assertTrue(resolved.blocks[2].is_associated)
        self.assertEqual(resolved.blocks[2].region_id, self.rid_3)
        self.assertEqual(resolved.blocks[2].display_order, 1)
        # Tier 4
        self.assertFalse(resolved.blocks[3].is_associated)
        self.assertIsNone(resolved.blocks[3].region_id)

    def test_table_fallback_cell_inline_image_resolves(self):
        """Verifies that inline images inside TableFallbackBlock header and row cells resolve."""
        img_header = InlineSpan(span_type=InlineType.IMAGE, region_id=self.rid_1, target="head.png")
        img_cell = InlineSpan(span_type=InlineType.IMAGE, target=f"crop_{self.job_id}_{self.rid_2}_v1.jpg")
        text_span = InlineSpan(span_type=InlineType.TEXT, text="Header")

        tbl = TableFallbackBlock(
            raw_table="| Header | Icon |\n|---|---|\n| Cell | Crop |",
            headers=((text_span,), (img_header,)),
            rows=(((text_span,), (img_cell,)),),
        )
        doc = MarkdownDocument(blocks=(tbl,))
        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)

        res_tbl = resolved.blocks[0]
        self.assertIsInstance(res_tbl, TableFallbackBlock)
        self.assertTrue(res_tbl.headers[1][0].is_associated)
        self.assertEqual(res_tbl.headers[1][0].region_id, self.rid_1)
        self.assertTrue(res_tbl.rows[0][1][0].is_associated)
        self.assertEqual(res_tbl.rows[0][1][0].region_id, self.rid_2)

    def test_stale_explicit_id_preserves_declared_identity_without_lower_tier_override(self):
        """
        Verifies that a stale explicit region_id (e.g. STALE-ID) preserves the declared
        identity without asserting association and strictly prevents lower-tier filename
        matching (e.g. Tier 3 legacy pattern) from overriding it.
        """
        # Filename would match region_1 via Tier 3 (crop_42_p1_1.jpg), but explicit region_id="STALE-ID"
        img = InlineSpan(
            span_type=InlineType.IMAGE,
            target=f"crop_{self.job_id}_p1_1.jpg",
            region_id="STALE-ID",
        )
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0].inlines[0]
        self.assertFalse(res_img.is_associated)
        self.assertEqual(res_img.region_id, "STALE-ID")
        self.assertIsNone(res_img.display_order)

    def test_inline_image_alt_text_is_not_an_identity_source(self):
        """
        Verifies that span.text (alt text) is never parsed for region_id=... syntax.
        Tier 1 identity for inline images must strictly originate from span.region_id.
        """
        # span.text has region_id=self.rid_2, but span.region_id is None and filename matches region_1
        img = InlineSpan(
            span_type=InlineType.IMAGE,
            text=f"Figure alt text region_id={self.rid_2}",
            target=f"crop_{self.job_id}_p1_1.jpg",
            region_id=None,
        )
        p = ParagraphBlock(inlines=(img,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_img = resolved.blocks[0].inlines[0]
        # Since span.text is NOT an identity source, it does not match rid_2 via Tier 1.
        # Instead, filename resolves via Tier 3 to region_1.
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)
        self.assertEqual(res_img.display_order, 1)
        self.assertEqual(res_img.text, f"Figure alt text region_id={self.rid_2}")

    def test_nested_inline_image_inside_strong_emphasis_link_resolves_recursively(self):
        """
        Verifies that an inline image deeply nested inside link -> strong -> emphasis
        is resolved without destroying the surrounding formatting wrappers.
        """
        inner_img = InlineSpan(
            span_type=InlineType.IMAGE,
            region_id=self.rid_1,
            target="nested_crop.png",
            text="Nested Image",
        )
        em_span = InlineSpan(span_type=InlineType.EMPHASIS, children=(inner_img,))
        strong_span = InlineSpan(span_type=InlineType.STRONG, children=(em_span,))
        link_span = InlineSpan(
            span_type=InlineType.LINK,
            target="https://polpot.app",
            children=(strong_span,),
        )
        p = ParagraphBlock(inlines=(link_span,))
        doc = MarkdownDocument(blocks=(p,))

        resolved = resolve_image_regions(doc, self.active_regions, self.job_id)
        res_link = resolved.blocks[0].inlines[0]

        self.assertEqual(res_link.span_type, InlineType.LINK)
        self.assertEqual(res_link.target, "https://polpot.app")

        res_strong = res_link.children[0]
        self.assertEqual(res_strong.span_type, InlineType.STRONG)

        res_em = res_strong.children[0]
        self.assertEqual(res_em.span_type, InlineType.EMPHASIS)

        res_img = res_em.children[0]
        self.assertEqual(res_img.span_type, InlineType.IMAGE)
        self.assertTrue(res_img.is_associated)
        self.assertEqual(res_img.region_id, self.rid_1)
        self.assertEqual(res_img.display_order, 1)
        self.assertEqual(res_img.text, "Nested Image")


if __name__ == "__main__":
    unittest.main()
