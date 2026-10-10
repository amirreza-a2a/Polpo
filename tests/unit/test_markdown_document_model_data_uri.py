# ============================================================
#  tests/unit/test_markdown_document_model_data_uri.py
#  Unit tests for MarkdownDocumentModel math SVG Data URI projection
# ============================================================

import html
import urllib.parse
import pytest

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    QuoteChildBlockDTO,
    VisualRegionRefDTO,
)
from application.ports.math_renderer import MathRenderResult
from interfaces.desktop.models.markdown_document_model import (
    MarkdownDocumentModel,
    normalize_math_tex,
)
from interfaces.desktop.providers.math_image_provider import inject_svg_color
from interfaces.desktop.qt_compat import QGuiApplication, Qt


_SAMPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="6ex" height="2ex" viewBox="0 0 60 20">'
    '<path d="M0,0 L60,20" stroke="currentColor"/>'
    '</svg>'
)


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


class TestMarkdownDocumentModelDataUriProjection:
    def test_formula_cache_hit_projection(self, qapp):
        """Verify formula image is projected into theme-aware Data URI with explicit pixel dimensions."""
        hash_1 = "hash_formula_1"
        result_1 = MathRenderResult(
            hash=hash_1,
            svg_xml=_SAMPLE_SVG,
            width="6ex",
            height="2ex",
            vertical_align="0ex",
        )

        def resolver(h: str):
            return result_1 if h == hash_1 else None

        model = MarkdownDocumentModel(math_resolver=resolver, math_foreground="#e6edf3")

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{hash_1}" align="middle"/>',
            math_tex="x + 1",
            math_hash=hash_1,
        )
        node = MarkdownNodeDTO(
            node_id="p1",
            node_type="paragraph",
            content=f'<p>Formula: <img src="image://math/{hash_1}" align="middle"/> here.</p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert len(segments) == 1
        proj_seg = segments[0]

        # Must NOT contain image://math/ URL
        assert "image://math/" not in proj_seg["textHtml"]
        # Must contain valid SVG Data URI
        assert "src=\"data:image/svg+xml;utf8," in proj_seg["textHtml"]
        # Must contain semantic theme color
        assert urllib.parse.quote('color="#e6edf3"') in proj_seg["textHtml"]
        # Must have explicit pixel dimensions: 6ex * 8 = 48px, 2ex * 8 = 16px
        assert 'width="48"' in proj_seg["textHtml"]
        assert 'height="16"' in proj_seg["textHtml"]
        assert 'align="middle"' in proj_seg["textHtml"]

        # Content role must also be projected
        content = model.data(idx, MarkdownDocumentModel.ContentRole)
        assert "image://math/" not in content
        assert "data:image/svg+xml;utf8," in content
        assert 'width="48"' in content
        assert 'height="16"' in content
        assert "Formula: " in content and " here." in content

    def test_multiple_formulas_in_single_content(self, qapp):
        """Verify multiple formulas in one fragment are all projected and surrounding prose is preserved."""
        h1, h2 = "hash_alpha", "hash_beta"
        cache = {
            h1: MathRenderResult(hash=h1, svg_xml=_SAMPLE_SVG, width="4ex", height="2ex", vertical_align="0ex"),
            h2: MathRenderResult(hash=h2, svg_xml=_SAMPLE_SVG, width="8ex", height="3ex", vertical_align="0ex"),
        }

        model = MarkdownDocumentModel(math_resolver=cache.get, math_foreground="#e6edf3")

        node = MarkdownNodeDTO(
            node_id="p_multi",
            node_type="paragraph",
            content=(
                f'<p>Start <img src="image://math/{h1}" align="middle"/> middle '
                f'<img src="image://math/{h2}" align="middle"/> end.</p>'
            ),
            segments=(
                InlineSegmentDTO(segment_type="math", text_html=f'<img src="image://math/{h1}" align="middle"/>', math_tex=r"\alpha", math_hash=h1),
                InlineSegmentDTO(segment_type="math", text_html=f'<img src="image://math/{h2}" align="middle"/>', math_tex=r"\beta", math_hash=h2),
            ),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        content = model.data(idx, MarkdownDocumentModel.ContentRole)
        assert "image://math/" not in content
        assert content.count("data:image/svg+xml;utf8,") == 2
        assert 'width="32"' in content   # 4ex * 8
        assert 'width="64"' in content   # 8ex * 8
        assert "Start " in content and " middle " in content and " end." in content

    def test_cache_miss_with_available_tex_source(self, qapp):
        """On cache miss, render readable fallback using available TeX source safely escaped."""
        model = MarkdownDocumentModel(math_resolver=lambda _: None)

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/missing_hash_1" align="middle"/>',
            math_tex="a < b & c > d",
            math_hash="missing_hash_1",
        )
        node = MarkdownNodeDTO(
            node_id="p_miss_tex",
            node_type="paragraph",
            content='<p>Let <img src="image://math/missing_hash_1" align="middle"/> hold.</p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        proj_seg = segments[0]

        assert "image://math/" not in proj_seg["textHtml"]
        # Must render readable fallback with escaped TeX
        assert '<span class="math-fallback">$a &lt; b &amp; c &gt; d$</span>' in proj_seg["textHtml"]

        content = model.data(idx, MarkdownDocumentModel.ContentRole)
        assert "image://math/" not in content
        assert '<span class="math-fallback">$a &lt; b &amp; c &gt; d$</span>' in content
        assert "Let " in content and " hold." in content

    def test_cache_miss_without_available_tex_source(self, qapp):
        """On cache miss without formula TeX available, render clear fallback label."""
        model = MarkdownDocumentModel(math_resolver=lambda _: None)

        # Node where segment has no math_tex metadata
        seg = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/unknown_hash" align="middle"/>',
            math_tex="",
            math_hash="unknown_hash",
        )
        node = MarkdownNodeDTO(
            node_id="p_miss_notex",
            node_type="paragraph",
            content='<p>Formula: <img src="image://math/unknown_hash" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert '<span class="math-fallback">[Math]</span>' in segments[0]["textHtml"]
        assert "image://math/" not in segments[0]["textHtml"]

    def test_resolver_raises_exception_falls_back_gracefully(self, qapp):
        """When resolver raises an unexpected exception, model catches it and renders safe fallback."""
        def faulty_resolver(h: str):
            raise RuntimeError(f"Simulated resolver crash for {h}")

        model = MarkdownDocumentModel(math_resolver=faulty_resolver)

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/err_hash" align="middle"/>',
            math_tex="E = mc^2",
            math_hash="err_hash",
        )
        node = MarkdownNodeDTO(
            node_id="p_err",
            node_type="paragraph",
            content='<p><img src="image://math/err_hash" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})

        # Must not raise exception
        model.set_document(doc)
        idx = model.index(0, 0)
        segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert '<span class="math-fallback">$E = mc^2$</span>' in segments[0]["textHtml"]
        assert "image://math/" not in segments[0]["textHtml"]

    def test_preserves_existing_html_attributes_and_surrounding_prose(self, qapp):
        """Verify extra attributes like alt or class are preserved alongside injected dimensions."""
        h = "h_attrs"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="5ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        node = MarkdownNodeDTO(
            node_id="p_attrs",
            node_type="paragraph",
            content=f'<p class="intro">See <img src="image://math/{h}" class="math-glyph" alt="formula" align="middle"/> above.</p>',
            segments=(),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        content = model.data(idx, MarkdownDocumentModel.ContentRole)
        assert 'class="intro"' in content
        assert 'class="math-glyph"' in content
        assert 'alt="formula"' in content
        assert 'align="middle"' in content
        assert 'width="40"' in content
        assert 'height="16"' in content
        assert "image://math/" not in content

    def test_malformed_dimensions_fallback_handling(self, qapp):
        """Verify malformed dimension values fall back to defaults (30x15 logical px)."""
        h = "h_bad_dim"
        result = MathRenderResult(
            hash=h,
            svg_xml=_SAMPLE_SVG,
            width="malformed_width_val",
            height="",
            vertical_align="0ex",
        )
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="x",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="p_bad_dim",
            node_type="paragraph",
            content=f'<p><img src="image://math/{h}" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        # Default fallbacks: width=30px, height=15px
        assert 'width="30"' in segments[0]["textHtml"]
        assert 'height="15"' in segments[0]["textHtml"]

    def test_preservation_of_unrelated_images_and_links(self, qapp):
        """Ordinary images, hyperlinks, and non-math HTML must remain completely unmodified."""
        model = MarkdownDocumentModel()
        unrelated_html = (
            '<p>Check <a href="https://example.com">link</a> and '
            '<img src="file:///tmp/crops/crop_1.jpg" width="100" height="50"/> or '
            '<img src="https://example.com/photo.png"/> here.</p>'
        )
        node = MarkdownNodeDTO(
            node_id="p_unrelated",
            node_type="paragraph",
            content=unrelated_html,
            segments=(InlineSegmentDTO(segment_type="text", text_html=unrelated_html),),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        assert model.data(idx, MarkdownDocumentModel.ContentRole) == unrelated_html
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert segs[0]["textHtml"] == unrelated_html


class TestFullSegmentCoverage:
    def test_list_item_segments_projection(self, qapp):
        """ListItemSegmentsRole and ListItemsRole receive projected Data URIs."""
        h = "hash_list_eq"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="5ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        item_seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="y = mx + b",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="list_1",
            node_type="list",
            content=f'<ul><li>Item with <img src="image://math/{h}" align="middle"/></li></ul>',
            list_items=(f'Item with <img src="image://math/{h}" align="middle"/>',),
            list_item_segments=((item_seg,),),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        list_segs = model.data(idx, MarkdownDocumentModel.ListItemSegmentsRole)
        assert len(list_segs) == 1
        assert "image://math/" not in list_segs[0][0]["textHtml"]
        assert "data:image/svg+xml;utf8," in list_segs[0][0]["textHtml"]

        list_items = model.data(idx, MarkdownDocumentModel.ListItemsRole)
        assert "image://math/" not in list_items[0]
        assert "data:image/svg+xml;utf8," in list_items[0]

    def test_table_cell_segments_projection(self, qapp):
        """TableCellSegmentsRole receives projected Data URIs."""
        h = "hash_table_eq"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="4ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        cell_seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="a^2",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="table_1",
            node_type="table_fallback",
            content="Table",
            table_cell_segments=(((cell_seg,),),),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        table_segs = model.data(idx, MarkdownDocumentModel.TableCellSegmentsRole)
        assert len(table_segs) == 1
        assert len(table_segs[0]) == 1
        assert "image://math/" not in table_segs[0][0][0]["textHtml"]
        assert "data:image/svg+xml;utf8," in table_segs[0][0][0]["textHtml"]

    def test_quote_children_segments_projection(self, qapp):
        """QuoteChildrenRole receives projected Data URIs in both content and segments."""
        h = "hash_quote_eq"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="4ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        q_seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="\\pi",
            math_hash=h,
        )
        q_child = QuoteChildBlockDTO(
            child_type="paragraph",
            content=f'<p>Quote formula: <img src="image://math/{h}" align="middle"/></p>',
            level=1,
            segments=(q_seg,),
        )
        node = MarkdownNodeDTO(
            node_id="quote_1",
            node_type="blockquote",
            content="Quote block",
            quote_children=(q_child,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        quote_children = model.data(idx, MarkdownDocumentModel.QuoteChildrenRole)
        assert len(quote_children) == 1
        qc = quote_children[0]
        assert "image://math/" not in qc["content"]
        assert "data:image/svg+xml;utf8," in qc["content"]
        assert "image://math/" not in qc["segments"][0]["textHtml"]
        assert "data:image/svg+xml;utf8," in qc["segments"][0]["textHtml"]

    def test_document_without_formulas_remains_semantically_unchanged(self, qapp):
        """Documents without math references are preserved verbatim with fast path."""
        model = MarkdownDocumentModel()
        raw_text = "Clean plain text document without any formulas."
        node = MarkdownNodeDTO(
            node_id="clean_p",
            node_type="paragraph",
            content=f"<p>{raw_text}</p>",
            segments=(InlineSegmentDTO(segment_type="text", text_html=f"<p>{raw_text}</p>"),),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        assert model.data(idx, MarkdownDocumentModel.ContentRole) == f"<p>{raw_text}</p>"
        assert model.data(idx, MarkdownDocumentModel.SegmentsRole)[0]["textHtml"] == f"<p>{raw_text}</p>"


class TestThemeReprojectionLifecycle:
    def test_theme_switch_dark_to_light_to_dark(self, qapp):
        """
        Verify theme switching Dark -> Light -> Dark:
          - Injects active semantic color into projected Data URIs.
          - Emits targeted dataChanged signals for affected rows.
          - Does not alter rowCount, generation, or node identity.
          - Reprojects from pristine canonical DTO.
        """
        h = "switch_hash"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="6ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="f(x)",
            math_hash=h,
        )
        node_math = MarkdownNodeDTO(
            node_id="p_math",
            node_type="paragraph",
            content=f'<p><img src="image://math/{h}" align="middle"/></p>',
            segments=(seg,),
        )
        node_clean = MarkdownNodeDTO(
            node_id="p_clean",
            node_type="paragraph",
            content="<p>Plain text</p>",
            segments=(InlineSegmentDTO(segment_type="text", text_html="<p>Plain text</p>"),),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node_math, node_clean), region_to_occurrences={})
        model.set_document(doc)

        initial_gen = model.model_generation

        # Check initial dark theme
        idx_0 = model.index(0, 0)
        dark_html = model.data(idx_0, MarkdownDocumentModel.SegmentsRole)[0]["textHtml"]
        assert urllib.parse.quote('color="#e6edf3"') in dark_html

        # Track dataChanged emissions
        changed_events = []
        model.dataChanged.connect(lambda tl, br, roles: changed_events.append((tl.row(), br.row(), roles)))

        # Switch to light theme (#1f2328)
        model.reproject_math("#1f2328")

        # Must NOT increment model generation or reset model
        assert model.model_generation == initial_gen
        assert model.rowCount() == 2

        # Exactly 1 dataChanged event for row 0 (row 1 without math must NOT fire)
        assert len(changed_events) == 1
        tl_row, br_row, roles = changed_events[0]
        assert tl_row == 0 and br_row == 0
        assert MarkdownDocumentModel.SegmentsRole in roles
        assert MarkdownDocumentModel.ContentRole in roles

        light_html = model.data(idx_0, MarkdownDocumentModel.SegmentsRole)[0]["textHtml"]
        assert urllib.parse.quote('color="#1f2328"') in light_html
        assert urllib.parse.quote('color="#e6edf3"') not in light_html
        assert 'width="48"' in light_html and 'height="16"' in light_html

        # Switch back to dark theme (#e6edf3)
        changed_events.clear()
        model.reproject_math("#e6edf3")

        assert len(changed_events) == 1
        assert changed_events[0][0] == 0

        restored_dark_html = model.data(idx_0, MarkdownDocumentModel.SegmentsRole)[0]["textHtml"]
        assert urllib.parse.quote('color="#e6edf3"') in restored_dark_html
        assert urllib.parse.quote('color="#1f2328"') not in restored_dark_html

        # Verify pristine canonical DTO was not mutated
        assert doc.nodes[0].segments[0].text_html == f'<img src="image://math/{h}" align="middle"/>'

    def test_reproject_math_preserves_runtime_region_artifacts(self, qapp):
        """Updating math theme must not clobber in-place region image artifact overrides."""
        h = "hash_with_region"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="4ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        reg_ref = VisualRegionRefDTO(occurrence_id="occ_1", source="crops/orig.jpg", region_id="reg_1")
        seg_img = InlineSegmentDTO(segment_type="image", text_html="[img]", image_ref=reg_ref)
        seg_math = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="z",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="p_mixed",
            node_type="paragraph",
            content=f'<p>[img] <img src="image://math/{h}" align="middle"/></p>',
            segments=(seg_img, seg_math),
            regions=(reg_ref,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        # Commit new region artifact in-place
        new_artifact_path = "/path/to/crops/reg_1_v2.jpg"
        model.update_region_artifact("reg_1", new_artifact_path, 2)

        idx = model.index(0, 0)
        assert model.data(idx, MarkdownDocumentModel.ImageUriRole) == f"file://{new_artifact_path}"

        # Now reproject math for light theme
        model.reproject_math("#1f2328")

        # The region image artifact URI must remain preserved!
        assert model.data(idx, MarkdownDocumentModel.ImageUriRole) == f"file://{new_artifact_path}"
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert segs[0]["imageRef"]["imageUri"] == f"file://{new_artifact_path}"
        # And math formula was updated
        assert urllib.parse.quote('color="#1f2328"') in segs[1]["textHtml"]

    def test_parse_dimension_edge_cases_and_fallbacks(self):
        """parse_dimension must safely reject non-finite, non-positive, or malformed values."""
        from interfaces.desktop.providers.math_image_provider import parse_dimension

        # Standard valid units
        assert parse_dimension("6ex", fallback=15.0, base_unit=8.0) == 48.0
        assert parse_dimension("2em", fallback=15.0, base_unit=8.0) == 32.0
        assert parse_dimension("20px", fallback=15.0) == 20.0
        assert parse_dimension(25, fallback=15.0) == 25.0
        assert parse_dimension("25.5", fallback=15.0) == 25.5

        # Non-finite floats must fall back
        assert parse_dimension("nan", fallback=15.0) == 15.0
        assert parse_dimension("inf", fallback=15.0) == 15.0
        assert parse_dimension("-inf", fallback=15.0) == 15.0

        # Non-positive values must fall back
        assert parse_dimension("-5px", fallback=15.0) == 15.0
        assert parse_dimension("0px", fallback=15.0) == 15.0
        assert parse_dimension("0ex", fallback=15.0) == 15.0
        assert parse_dimension("-1", fallback=15.0) == 15.0
        assert parse_dimension(0, fallback=15.0) == 15.0

        # Empty and invalid
        assert parse_dimension("", fallback=15.0) == 15.0
        assert parse_dimension(None, fallback=15.0) == 15.0
        assert parse_dimension("abc", fallback=15.0) == 15.0

    def test_formula_with_arbitrary_theme_prefix_in_url(self, qapp):
        """URL with system or custom theme prefix must resolve formula hash properly."""
        h = "hash_custom_theme_01"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="6ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        node = MarkdownNodeDTO(
            node_id="p_theme",
            node_type="paragraph",
            content=f'<p>Formula: <img src="image://math/system/{h}" align="middle"/></p>',
            segments=(
                InlineSegmentDTO(
                    segment_type="math",
                    text_html=f'<img src="image://math/system/{h}" align="middle"/>',
                    math_tex="x",
                    math_hash=h,
                ),
            ),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert "data:image/svg+xml;utf8," in segs[0]["textHtml"]
        assert "image://math/" not in segs[0]["textHtml"]

    def test_empty_or_whitespace_svg_falls_back_to_tex(self, qapp):
        """Resolver returning empty or whitespace SVG must fall back to TeX rather than 0-byte Data URI."""
        h = "hash_empty_svg"
        bad_result = MathRenderResult(hash=h, svg_xml="   ", width="6ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: bad_result, math_foreground="#e6edf3")

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="E = mc^2",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="p_bad_svg",
            node_type="paragraph",
            content=f'<p><img src="image://math/{h}" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert '<span class="math-fallback">$E = mc^2$</span>' in segs[0]["textHtml"]
        assert "data:image" not in segs[0]["textHtml"]

    def test_set_math_presentation_resolver_reprojects_loaded_document(self, qapp):
        """Setting presentation resolver on already-loaded document triggers immediate re-projection."""
        h = "hash_warm_up"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="6ex", height="2ex", vertical_align="0ex")

        # Load initially with no resolver
        model = MarkdownDocumentModel(math_resolver=None, math_foreground="#e6edf3")
        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align="middle"/>',
            math_tex="y = mx + b",
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="p_warm",
            node_type="paragraph",
            content=f'<p><img src="image://math/{h}" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        # Initially in fallback
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert '<span class="math-fallback">$y = mx + b$</span>' in segs[0]["textHtml"]

        # Now configure resolver
        model.set_math_presentation_resolver(lambda _: result)

        # Reprojection should have updated item to Data URI
        segs_updated = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert "data:image/svg+xml;utf8," in segs_updated[0]["textHtml"]
        assert 'width="48"' in segs_updated[0]["textHtml"]

    def test_reproject_math_on_document_without_formulas_emits_no_datachanged(self, qapp):
        """Calling reproject_math on document without formulas must emit zero dataChanged events."""
        model = MarkdownDocumentModel(math_foreground="#e6edf3")
        reg_ref = VisualRegionRefDTO(occurrence_id="occ_1", source="img.png", region_id="r1")
        seg_img = InlineSegmentDTO(segment_type="image", text_html="[img]", image_ref=reg_ref)
        node = MarkdownNodeDTO(
            node_id="p_plain",
            node_type="paragraph",
            content="<p>Plain text paragraph without math.</p>",
            segments=(seg_img,),
            regions=(reg_ref,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={"r1": []})
        model.set_document(doc)

        # Update region artifact in-place
        model.update_region_artifact("r1", "file:///new.png", "image://artifact/r1")

        events = []
        model.dataChanged.connect(lambda tl, br, roles: events.append((tl.row(), br.row(), roles)))

        # Reproject math
        model.reproject_math("#1f2328")

        # Must NOT emit any dataChanged event because no formula textHtml changed
        assert events == []

    def test_tex_fallback_with_preexisting_delimiters(self, qapp):
        """Fallback TeX containing pre-existing $ delimiters must not result in double delimiters."""
        model = MarkdownDocumentModel(math_resolver=lambda _: None)
        seg = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/miss_delim" align="middle"/>',
            math_tex="$x + 1$",
            math_hash="miss_delim",
        )
        node = MarkdownNodeDTO(
            node_id="p_delim",
            node_type="paragraph",
            content='<p><img src="image://math/miss_delim" align="middle"/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        assert segs[0]["textHtml"] == '<span class="math-fallback">$x + 1$</span>'

    def test_inject_svg_color_case_insensitivity(self):
        """inject_svg_color must handle uppercase SVG tags."""
        svg_upper = '<SVG xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path/></SVG>'
        injected = inject_svg_color(svg_upper, "#1f2328")
        assert 'color="#1f2328"' in injected

    def test_case_insensitive_url_scheme_and_host_matching(self, qapp):
        """Case variants in formula URLs, schemes, hosts, themes, and HTML tag/attributes must be projected."""
        h1 = "hash_UPPERCASE_1"
        h2 = "hash_MixedCase_2"
        h3 = "hash_Theme_3"
        result = MathRenderResult(
            hash=h1,
            svg_xml=_SAMPLE_SVG,
            width="6ex",
            height="2ex",
            vertical_align="0ex",
        )
        cache = {h1: result, h2: result, h3: result}
        model = MarkdownDocumentModel(math_resolver=cache.get, math_foreground="#e6edf3")

        node = MarkdownNodeDTO(
            node_id="p_case",
            node_type="paragraph",
            content=(
                f'<p><IMG SRC="IMAGE://MATH/{h1}" align="middle"/> '
                f'<img Src="Image://Math/{h2}" align="middle"/> '
                f'<img src="image://math/DARK/{h3}" align="middle"/> '
                f'<img src="http://example.com/IMAGE.PNG" alt="Unrelated"/> '
                f'<img src="file:///local/PATH/IMAGE.SVG" alt="Local"/></p>'
            ),
            segments=(),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        content = model.data(model.index(0, 0), MarkdownDocumentModel.ContentRole)
        # All 3 formulas projected to Data URI
        assert content.count("data:image/svg+xml;utf8,") == 3
        # No synchronous math URLs remain
        assert "image://math" not in content.lower()
        # Formula hash casing preserved
        assert h1 in cache
        assert h2 in cache
        assert h3 in cache
        # Unrelated images untouched
        assert '<img src="http://example.com/IMAGE.PNG" alt="Unrelated"/>' in content
        assert '<img src="file:///local/PATH/IMAGE.SVG" alt="Local"/>' in content

    def test_whitespace_around_equals_in_img_attributes(self, qapp):
        """Formula <img> tags with whitespace around '=' or unquoted values must be projected."""
        h = "h_ws"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="6ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        node = MarkdownNodeDTO(
            node_id="p_ws",
            node_type="paragraph",
            content=(
                f'<p><img src = "image://math/{h}"> '
                f'<img src= "image://math/{h}"> '
                f'<img src ="image://math/{h}"> '
                f'<img src=image://math/{h}> '
                f'<img src = \x27image://math/{h}\x27/></p>'
            ),
            segments=(),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        content = model.data(model.index(0, 0), MarkdownDocumentModel.ContentRole)
        assert content.count("data:image/svg+xml;utf8,") == 5
        assert "image://math" not in content.lower()

    def test_inject_svg_color_hyphenated_and_whitespace_attributes(self):
        """inject_svg_color must not alter stop-color/flood-color and must accept whitespace around '='."""
        # 1. Root color attribute
        assert inject_svg_color('<svg color="#ffffff"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3"><path/></svg>'
        # 2. Whitespace around '='
        assert inject_svg_color('<svg color = "#ffffff"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3"><path/></svg>'
        assert inject_svg_color('<svg color= "#ffffff"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3"><path/></svg>'
        assert inject_svg_color('<svg color ="#ffffff"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3"><path/></svg>'
        # 3. stop-color must not be treated as root color
        res3 = inject_svg_color('<svg stop-color="#ffffff"><path/></svg>', "#e6edf3")
        assert 'stop-color="#ffffff"' in res3
        assert 'color="#e6edf3"' in res3
        # 4. flood-color must not be treated as root color
        res4 = inject_svg_color('<svg flood-color="#ffffff"><path/></svg>', "#e6edf3")
        assert 'flood-color="#ffffff"' in res4
        assert 'color="#e6edf3"' in res4
        # 5. SVG containing both stop-color and root color
        res5 = inject_svg_color('<svg stop-color="#ffffff" color="#000000"><path/></svg>', "#e6edf3")
        assert 'stop-color="#ffffff"' in res5
        assert 'color="#e6edf3"' in res5
        assert 'color="#000000"' not in res5
        # 6. SVG containing no root color attribute
        assert inject_svg_color('<svg width="60"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3" width="60"><path/></svg>'
        # 7. Nested element stop-color and lighting-color unchanged
        nested_svg = '<svg width="60"><defs><linearGradient><stop stop-color="#112233"/></linearGradient></defs></svg>'
        res7 = inject_svg_color(nested_svg, "#e6edf3")
        assert 'color="#e6edf3"' in res7
        assert 'stop-color="#112233"' in res7
        # 8. Self-closing root tag and attributes containing 'color=' in value
        assert inject_svg_color('<svg/>', "#e6edf3") == '<svg color="#e6edf3"/>'
        assert inject_svg_color('<svg />', "#e6edf3") == '<svg color="#e6edf3" />'
        assert inject_svg_color('<svg desc="color=red" width="10"></svg>', "#e6edf3") == '<svg color="#e6edf3" desc="color=red" width="10"></svg>'
        assert inject_svg_color('<svg COLOR="#ffffff"><path/></svg>', "#e6edf3") == '<svg color="#e6edf3"><path/></svg>'
        assert inject_svg_color('<svg-icon color="#ffffff">', "#e6edf3") == '<svg-icon color="#ffffff">'
        # 9. Malformed or empty
        assert inject_svg_color("", "#e6edf3") == ""
        assert inject_svg_color("not an svg", "#e6edf3") == "not an svg"

    def test_normalize_math_tex_direct_edge_cases(self):
        """Direct verification of normalize_math_tex edge cases."""
        # Standard delimiters
        assert normalize_math_tex("$x + 1$") == "x + 1"
        assert normalize_math_tex("$$x + 1$$") == "x + 1"
        assert normalize_math_tex("   $$  x + 1  $$   ") == "x + 1"
        assert normalize_math_tex("   $  x + 1  $   ") == "x + 1"
        # LaTeX bracket delimiters
        assert normalize_math_tex(r"\[x + 1\]") == "x + 1"
        assert normalize_math_tex(r"\(x + 1\)") == "x + 1"
        # Escaped TeX containing dollar signs
        assert normalize_math_tex(r"\$x + 1") == r"\$x + 1"
        assert normalize_math_tex(r"\$5.00\$") == r"\$5.00\$"
        assert normalize_math_tex(r"$x + \$5$") == r"x + \$5"
        assert normalize_math_tex(r"$$x + \$5$$") == r"x + \$5"
        # Unwrapped or malformed content
        assert normalize_math_tex("x + 1") == "x + 1"
        assert normalize_math_tex("$x + 1") == "$x + 1"
        assert normalize_math_tex("x + 1$") == "x + 1$"
        # Empty or dollar-only
        assert normalize_math_tex("$$") == ""
        assert normalize_math_tex("$") == ""
        assert normalize_math_tex("$$  $$") == ""
        assert normalize_math_tex("$ $") == ""
        assert normalize_math_tex(None) == ""

    def test_display_math_fallback_delimiter_normalization(self, qapp):
        """Display math fallback must correctly strip outer '$$' without creating duplicate '$$'."""
        model = MarkdownDocumentModel(math_resolver=lambda _: None)

        cases = [
            ("$$x + 1$$", '<span class="math-fallback">$x + 1$</span>'),
            ("$x + 1$", '<span class="math-fallback">$x + 1$</span>'),
            ("  $$  x + 1  $$  ", '<span class="math-fallback">$x + 1$</span>'),
            (r"$$x + \$5$$", '<span class="math-fallback">$x + \\$5$</span>'),
            (r"\$5.00\$", '<span class="math-fallback">$\\$5.00\\$$</span>'),
            ("x + 1", '<span class="math-fallback">$x + 1$</span>'),
            ("$$", '<span class="math-fallback">[Math]</span>'),
            ("", '<span class="math-fallback">[Math]</span>'),
        ]

        for i, (tex_in, expected_html) in enumerate(cases):
            h = f"miss_{i}"
            seg = InlineSegmentDTO(
                segment_type="math",
                text_html=f'<img src="image://math/{h}" align="middle"/>',
                math_tex=tex_in,
                math_hash=h,
            )
            node = MarkdownNodeDTO(
                node_id=f"node_{i}",
                node_type="paragraph",
                content=f'<p><img src="image://math/{h}" align="middle"/></p>',
                segments=(seg,),
            )
            doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
            model.set_document(doc)

            idx = model.index(0, 0)
            segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
            assert segs[0]["textHtml"] == expected_html, f"Failed for tex_in={tex_in!r}: got {segs[0]['textHtml']!r}"

    def test_remove_duplicate_unquoted_attributes_and_idempotence(self, qapp):
        """Existing align=middle, width, height must be normalized without duplicates, preserving classes and idempotence."""
        h = "h_unquoted"
        result = MathRenderResult(hash=h, svg_xml=_SAMPLE_SVG, width="6ex", height="2ex", vertical_align="0ex")
        model = MarkdownDocumentModel(math_resolver=lambda _: result, math_foreground="#e6edf3")

        seg = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{h}" align=middle width=100 height = 50 class="custom formula with space" alt="Formula with align=middle and width=100 inside" title=\x27some alt\x27/>',
            math_hash=h,
        )
        node = MarkdownNodeDTO(
            node_id="p_attr_norm",
            node_type="paragraph",
            content=f'<p><img src="image://math/{h}" align=middle width=100 height = 50 class="custom formula with space" alt="Formula with align=middle and width=100 inside" title=\x27some alt\x27/></p>',
            segments=(seg,),
        )
        doc = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
        model.set_document(doc)

        idx = model.index(0, 0)
        segs = model.data(idx, MarkdownDocumentModel.SegmentsRole)
        html_out = segs[0]["textHtml"]

        # Exactly one align attribute on the img tag
        assert html_out.count('align="middle"') == 1
        tag_prefix = html_out[:html_out.index('alt=')]
        assert "align=middle" not in tag_prefix
        assert 'align="middle"' in tag_prefix
        # Exactly one width and height attribute
        assert html_out.count('width="48"') == 1
        assert html_out.count('height="16"') == 1
        assert "100" not in tag_prefix
        assert "50" not in tag_prefix
        # Preserves class, title, and alt with quoted spaces and inner attribute-like tokens intact
        assert 'class="custom formula with space"' in html_out
        assert "title='some alt'" in html_out
        assert 'alt="Formula with align=middle and width=100 inside"' in html_out

        # Idempotence: projecting the output HTML again must return identical string
        projected_again = model._project_html(html_out)
        assert projected_again == html_out

    def test_resolver_exception_observability_separates_miss_from_error(self, qapp, caplog):
        """Ordinary cache misses must not log warnings; unexpected resolver exceptions must log WARNING with hash and details."""
        import logging

        # 1. Ordinary cache miss: returns None
        model_miss = MarkdownDocumentModel(math_resolver=lambda _: None)
        seg_miss = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/miss_hash" align="middle"/>',
            math_tex="a + b",
            math_hash="miss_hash",
        )
        doc_miss = MarkdownDocumentDTO(
            job_id=1,
            version=1,
            nodes=(MarkdownNodeDTO(node_id="p_m", node_type="paragraph", content='<p><img src="image://math/miss_hash"/></p>', segments=(seg_miss,)),),
            region_to_occurrences={},
        )
        with caplog.at_level(logging.WARNING):
            caplog.clear()
            model_miss.set_document(doc_miss)
            # ZERO warnings for ordinary cache miss
            assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
            segs = model_miss.data(model_miss.index(0, 0), MarkdownDocumentModel.SegmentsRole)
            assert '<span class="math-fallback">$a + b$</span>' in segs[0]["textHtml"]

        # 2. Unexpected resolver exception
        def crashing_resolver(h: str):
            raise RuntimeError(f"Database connection severed for {h}")

        model_err = MarkdownDocumentModel(math_resolver=crashing_resolver)
        seg_err = InlineSegmentDTO(
            segment_type="math",
            text_html='<img src="image://math/err_crash_hash" align="middle"/>',
            math_tex="c + d",
            math_hash="err_crash_hash",
        )
        doc_err = MarkdownDocumentDTO(
            job_id=2,
            version=1,
            nodes=(MarkdownNodeDTO(node_id="p_e", node_type="paragraph", content='<p><img src="image://math/err_crash_hash"/></p>', segments=(seg_err,)),),
            region_to_occurrences={},
        )
        with caplog.at_level(logging.WARNING):
            caplog.clear()
            model_err.set_document(doc_err)
            # Must log a WARNING containing the formula hash and error message
            warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert len(warn_records) >= 1
            assert any("err_crash_hash" in r.message and "Database connection severed" in r.message for r in warn_records)
            segs_err = model_err.data(model_err.index(0, 0), MarkdownDocumentModel.SegmentsRole)
            assert '<span class="math-fallback">$c + d$</span>' in segs_err[0]["textHtml"]
