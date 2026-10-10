# ============================================================
#  tests/integration/test_p05_richtext_warning_elimination.py
#  Verification suite for Issue #95 [TICK-P05]:
#  Eliminate Synchronous RichText Math Image Warnings
# ============================================================

import json
from pathlib import Path
from typing import List, Optional
import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl, qInstallMessageHandler
from PySide6.QtQuick import QQuickView

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
)
from application.ports.math_renderer import MathRenderResult
from infrastructure.math import MathSvgCache
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.providers.math_image_provider import MathImageProvider
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt application for offscreen QML Quick view rendering."""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


class QtWarningCapture:
    """Context manager capturing Qt debug, warning, critical, and fatal log messages."""

    def __init__(self):
        self.messages: List[str] = []
        self._old_handler = None

    def __enter__(self):
        self.messages.clear()

        def _handler(msg_type, context, msg):
            self.messages.append(str(msg))

        self._old_handler = qInstallMessageHandler(_handler)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            qInstallMessageHandler(self._old_handler)
        except Exception:
            qInstallMessageHandler(None)
        return False

    @property
    def connect_finished_warnings(self) -> List[str]:
        return [
            m for m in self.messages
            if "QQuickPixmap: connectFinished() called when not loading." in m
            or "connectFinished" in m
        ]

    @property
    def qml_warnings(self) -> List[str]:
        return [
            m for m in self.messages
            if "qrc:" in m or ".qml:" in m or "QML" in m or "TypeError" in m or "ReferenceError" in m
        ]


def _build_test_document_dto(num_formulas: int = 50) -> (MarkdownDocumentDTO, MathSvgCache):
    """
    Builds a synthetic document DTO containing num_formulas inline math nodes/segments,
    paired with a populated MathSvgCache.
    """
    cache = MathSvgCache(capacity=num_formulas + 50)
    nodes = []

    for i in range(num_formulas):
        formula_hash = f"test_hash_{i:04d}"
        tex_source = f"x_{{{i}}} + y_{{{i}}} = z_{{{i}}}"
        svg_xml = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="20" viewBox="0 0 60 20">'
            f'<text x="5" y="15">eq_{i}</text>'
            f'</svg>'
        )

        cache.put(
            formula_hash,
            MathRenderResult(
                hash=formula_hash,
                svg_xml=svg_xml,
                width="6ex",
                height="2ex",
                vertical_align="-0.5ex",
            ),
        )

        segment = InlineSegmentDTO(
            segment_type="math",
            text_html=f'<img src="image://math/{formula_hash}" align="middle"/>',
            math_hash=formula_hash,
            math_tex=tex_source,
            has_error=False,
            error_category="",
            error_message="",
        )

        node = MarkdownNodeDTO(
            node_id=f"node_{i}",
            node_type="paragraph",
            content=f'Formula {i}: <img src="image://math/{formula_hash}" align="middle"/>',
            segments=(
                InlineSegmentDTO(
                    segment_type="text",
                    text_html=f"Formula {i}: ",
                ),
                segment,
            ),
            raw_markdown=f"Formula {i}: ${tex_source}$",
        )
        nodes.append(node)

    doc_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=tuple(nodes),
        region_to_occurrences={},
    )
    return doc_dto, cache


def test_baseline_unprojected_image_provider_reproduces_warning(qapp):
    """
    Confirms that without data URI projection, loading 50+ inline math images via
    image://math/{hash} in Text.RichText reproduces the synchronous QQuickPixmap warning:
    'QQuickPixmap: connectFinished() called when not loading.'
    """
    doc_dto, cache = _build_test_document_dto(num_formulas=10)
    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)
        view.setSource(QUrl.fromLocalFile(str(qml_file)))
        root = view.rootObject()
        assert root is not None, "Failed to load MarkdownInlineFlow.qml"

        # Pass unprojected segments with raw image://math/... HTML
        raw_segments = [
            {
                "segmentType": "text",
                "textHtml": node.content,
            }
            for node in doc_dto.nodes
        ]

        with QtWarningCapture() as capture:
            root.setProperty("segments", raw_segments)
            view.show()
            QCoreApplication.processEvents()

            # Verify that the known Qt Quick warning was triggered by image://math
            assert len(capture.connect_finished_warnings) > 0, (
                "Expected QQuickPixmap connectFinished warning to be reproduced "
                "with raw image://math URLs in Text.RichText."
            )
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()


def test_projected_data_uri_eliminates_connect_finished_warning(qapp):
    """
    Validates Issue #95 core requirement:
    Projecting 50+ inline formulas via MarkdownDocumentModel into dimensioned, themed
    SVG Data URIs completely eliminates 'QQuickPixmap: connectFinished() called when not loading.'
    """
    doc_dto, cache = _build_test_document_dto(num_formulas=50)
    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    # Instantiate model with injected math resolver and dark theme foreground
    model = MarkdownDocumentModel(
        math_resolver=cache.get,
        math_foreground="#e6edf3",
    )
    model.set_document(doc_dto)

    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)
        view.setSource(QUrl.fromLocalFile(str(qml_file)))
        root = view.rootObject()
        assert root is not None

        # Collect projected segments from model data() API
        all_segments = []
        for row in range(model.rowCount()):
            idx = model.index(row, 0)
            row_segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
            assert row_segments is not None
            all_segments.extend(row_segments)

        assert len(all_segments) == 100  # 50 text + 50 math
        math_segments = [s for s in all_segments if s.get("segmentType") == "math"]
        assert len(math_segments) == 50

        # Verify that all 50 formulas were projected to data:image/svg+xml with pixel dimensions
        for math_seg in math_segments:
            html = math_seg["textHtml"]
            assert 'src="data:image/svg+xml;utf8,' in html
            assert 'width="48"' in html  # 6ex * 8 = 48
            assert 'height="16"' in html  # 2ex * 8 = 16
            assert 'align="middle"' in html
            assert "color%3D%22%23e6edf3%22" in html

        # Pass projected segments into MarkdownInlineFlow.qml and capture Qt messages
        with QtWarningCapture() as capture:
            root.setProperty("segments", all_segments)
            view.show()
            QCoreApplication.processEvents()

            single_text = root.findChild(QObject, "singleText")
            assert single_text is not None, "Failed to locate singleText in MarkdownInlineFlow.qml"
            rendered_rich_text = single_text.property("text")
            assert rendered_rich_text is not None

            # Assert all 50 formulas reach RichText as Data URIs
            assert rendered_rich_text.count("data:image/svg+xml") == 50, (
                f"Expected exactly 50 SVG Data URIs in RichText, found {rendered_rich_text.count('data:image/svg+xml')}"
            )

            # Confirm formula identifiers / expected SVG markers correspond to input formulas
            for i in range(50):
                assert f"eq_{i}" in rendered_rich_text, f"Formula marker eq_{i} missing from rendered RichText"
                assert f"Formula {i}:" in rendered_rich_text, f"Prose for formula {i} missing from rendered RichText"

            # Assert NO synchronous image://math formula URLs remain
            assert "image://math" not in rendered_rich_text.lower(), (
                "Synchronous image://math URL leaked into Text.RichText"
            )

            # Verify ZERO connectFinished warnings emitted
            assert capture.connect_finished_warnings == [], (
                f"Expected 0 QQuickPixmap warnings, but received: {capture.connect_finished_warnings}"
            )
            assert capture.qml_warnings == [], f"Unexpected QML warnings: {capture.qml_warnings}"
            assert view.status() != QQuickView.Status.Error, f"QML load error: {view.errors()}"
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()


def test_theme_switching_reprojects_math_without_warnings(qapp):
    """
    Validates dynamic theme switching:
    Switching themes (dark -> light -> dark) reprojects all SVG Data URIs with updated
    colors without reparsing AST and without emitting any QQuickPixmap warnings.
    """
    doc_dto, cache = _build_test_document_dto(num_formulas=50)
    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    model = MarkdownDocumentModel(
        math_resolver=cache.get,
        math_foreground="#e6edf3",
    )
    model.set_document(doc_dto)

    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)
        view.setSource(QUrl.fromLocalFile(str(qml_file)))
        root = view.rootObject()
        assert root is not None

        # Initial dark load
        with QtWarningCapture() as capture_init:
            row0_segs = model.data(model.index(0, 0), MarkdownDocumentModel.SegmentsRole)
            root.setProperty("segments", row0_segs)
            view.show()
            QCoreApplication.processEvents()
            assert capture_init.connect_finished_warnings == []

        # Switch to light theme (#1f2328)
        with QtWarningCapture() as capture_light:
            model.reproject_math("#1f2328")
            light_segs = model.data(model.index(0, 0), MarkdownDocumentModel.SegmentsRole)
            math_light = [s for s in light_segs if s.get("segmentType") == "math"][0]
            assert "color%3D%22%231f2328%22" in math_light["textHtml"]

            root.setProperty("segments", light_segs)
            QCoreApplication.processEvents()
            assert capture_light.connect_finished_warnings == []

        # Switch back to dark theme (#e6edf3)
        with QtWarningCapture() as capture_dark:
            model.reproject_math("#e6edf3")
            dark_segs = model.data(model.index(0, 0), MarkdownDocumentModel.SegmentsRole)
            math_dark = [s for s in dark_segs if s.get("segmentType") == "math"][0]
            assert "color%3D%22%23e6edf3%22" in math_dark["textHtml"]

            root.setProperty("segments", dark_segs)
            QCoreApplication.processEvents()
            assert capture_dark.connect_finished_warnings == []
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()


def test_cache_miss_fallback_projection_without_warnings(qapp):
    """
    Validates cache miss / error handling:
    Uncached formulas fall back to readable escaped TeX (<span class="math-fallback">${tex}$</span>)
    or fallback text without emitting image provider warnings.
    """
    doc_dto, _ = _build_test_document_dto(num_formulas=10)
    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    # Empty cache / resolver returning None
    empty_cache = MathSvgCache(capacity=10)
    model = MarkdownDocumentModel(
        math_resolver=empty_cache.get,
        math_foreground="#e6edf3",
    )
    model.set_document(doc_dto)

    view = QQuickView()
    try:
        provider = MathImageProvider(empty_cache)
        view.engine().addImageProvider("math", provider)
        view.setSource(QUrl.fromLocalFile(str(qml_file)))
        root = view.rootObject()
        assert root is not None

        idx0 = model.index(0, 0)
        segs0 = model.data(idx0, MarkdownDocumentModel.SegmentsRole)
        math0 = [s for s in segs0 if s.get("segmentType") == "math"][0]

        # Verify fallback HTML
        assert '<span class="math-fallback">$x_{0} + y_{0} = z_{0}$</span>' in math0["textHtml"]
        assert "data:image" not in math0["textHtml"]
        assert "image://math" not in math0["textHtml"]

        with QtWarningCapture() as capture:
            root.setProperty("segments", segs0)
            view.show()
            QCoreApplication.processEvents()
            assert capture.connect_finished_warnings == []
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()


def test_repeated_creation_and_destruction_cycles_leak_free(qapp):
    """
    Validates stability under repeated creation and destruction:
    Instantiates and destroys QQuickView with 50+ inline formulas over 10 cycles,
    verifying zero warning emissions across all cycles.
    """
    doc_dto, cache = _build_test_document_dto(num_formulas=50)
    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    model = MarkdownDocumentModel(
        math_resolver=cache.get,
        math_foreground="#e6edf3",
    )
    model.set_document(doc_dto)

    all_segments = []
    for row in range(model.rowCount()):
        idx = model.index(row, 0)
        all_segments.extend(model.data(idx, MarkdownDocumentModel.SegmentsRole))

    with QtWarningCapture() as capture:
        for cycle in range(10):
            view = QQuickView()
            provider = MathImageProvider(cache)
            view.engine().addImageProvider("math", provider)
            view.setSource(QUrl.fromLocalFile(str(qml_file)))
            root = view.rootObject()
            assert root is not None

            root.setProperty("segments", all_segments)
            view.show()
            QCoreApplication.processEvents()

            view.close()
            del view
            QCoreApplication.processEvents()

        assert capture.connect_finished_warnings == []


def test_native_image_block_math_retains_provider_support(qapp):
    """
    Validates that native QML Image elements (used for block math in MarkdownNodeDelegate.qml)
    continue to resolve images correctly through MathImageProvider without warnings or regressions.
    """
    cache = MathSvgCache(capacity=10)
    formula_hash = "block_hash_001"
    svg_xml = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" viewBox="0 0 100 40">'
        '<text x="10" y="25">E = mc^2</text>'
        '</svg>'
    )
    cache.put(
        formula_hash,
        MathRenderResult(
            hash=formula_hash,
            svg_xml=svg_xml,
            width="100px",
            height="40px",
            vertical_align="0ex",
        ),
    )

    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)

        # QML snippet mirroring block math Image in MarkdownNodeDelegate.qml
        qml_source = b'''
        import QtQuick 2.15
        Item {
            width: 200
            height: 100
            Image {
                id: mathImg
                source: "image://math/dark/block_hash_001"
                fillMode: Image.PreserveAspectFit
            }
        }
        '''
        from PySide6.QtQml import QQmlComponent
        component = QQmlComponent(view.engine())
        component.setData(qml_source, "")
        assert not component.isError(), f"QML errors: {component.errors()}"

        with QtWarningCapture() as capture:
            item = component.create()
            assert item is not None
            item.setParentItem(view.contentItem())
            view.show()
            QCoreApplication.processEvents()

            assert capture.connect_finished_warnings == []
            # Check Image loaded correctly via provider
            img = item.children()[0]
            assert img.property("progress") == 1.0
            assert img.property("implicitWidth") == 100.0
            assert img.property("implicitHeight") == 40.0
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()


def test_markdown_inline_flow_concatenates_all_segments_without_fallback(qapp):
    """
    Validates P1 review finding:
    MarkdownInlineFlow.qml must concatenate all segment textHtml values in order when
    textFallback is empty, without truncating formulas or subsequent text.
    Also verifies multi-segment table cell rendering representative of MarkdownNodeDelegate.qml.
    """
    cache = MathSvgCache(capacity=10)
    formula_hash = "h_multi_01"
    cache.put(
        formula_hash,
        MathRenderResult(
            hash=formula_hash,
            svg_xml='<svg><text>a^2+b^2=c^2</text></svg>',
            width="6ex",
            height="2ex",
            vertical_align="0ex",
        ),
    )
    model = MarkdownDocumentModel(math_resolver=cache.get, math_foreground="#e6edf3")

    qml_file = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownInlineFlow.qml"
    )

    # 1. Direct MarkdownInlineFlow test with 3 segments and no fallback
    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)
        view.setSource(QUrl.fromLocalFile(str(qml_file)))
        root = view.rootObject()
        assert root is not None

        seg0 = {"segmentType": "text", "textHtml": "Prefix: "}
        seg1 = {"segmentType": "math", "textHtml": model._project_html(f'<img src="image://math/{formula_hash}"/>')}
        seg2 = {"segmentType": "text", "textHtml": " suffix text."}

        root.setProperty("segments", [seg0, seg1, seg2])
        root.setProperty("textFallback", "")
        QCoreApplication.processEvents()

        single_text = root.findChild(QObject, "singleText")
        assert single_text is not None
        rendered_text = single_text.property("text")
        assert rendered_text is not None

        # Assert all segments are preserved in order
        assert rendered_text.startswith("Prefix: ")
        assert "data:image/svg+xml" in rendered_text
        assert rendered_text.endswith(" suffix text.")
        assert rendered_text.index("Prefix: ") < rendered_text.index("data:image/svg+xml") < rendered_text.index(" suffix text.")
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()

    # 2. Multi-segment table cell scenario representative of MarkdownNodeDelegate.qml
    table_node = MarkdownNodeDTO(
        node_id="table_1",
        node_type="table_fallback",
        content="| Col A | Col B |\n|---|---|\n| Start $x$ End | Val |",
        table_cell_segments=(
            (
                (InlineSegmentDTO(segment_type="text", text_html="Header A"),),
                (InlineSegmentDTO(segment_type="text", text_html="Header B"),),
            ),
            (
                (
                    InlineSegmentDTO(segment_type="text", text_html="Cell prefix: "),
                    InlineSegmentDTO(
                        segment_type="math",
                        text_html=f'<img src="image://math/{formula_hash}"/>',
                        math_hash=formula_hash,
                        math_tex="x",
                    ),
                    InlineSegmentDTO(segment_type="text", text_html=" cell suffix."),
                ),
                (InlineSegmentDTO(segment_type="text", text_html="Single cell"),),
            ),
        ),
    )
    doc_dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(table_node,), region_to_occurrences={})
    model.set_document(doc_dto)

    delegate_path = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownNodeDelegate.qml"
    )
    delegate_url = QUrl.fromLocalFile(str(delegate_path)).toString()

    view_tbl = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view_tbl.engine().addImageProvider("math", provider)
        view_tbl.rootContext().setContextProperty("testDocModel", model)

        qml_source = f"""
        import QtQuick 2.15
        Item {{
            width: 800
            height: 400
            ListView {{
                id: lv
                anchors.fill: parent
                model: testDocModel
                delegate: Loader {{
                    width: parent.width
                    source: {json.dumps(delegate_url)}
                }}
            }}
        }}
        """.encode("utf-8")

        from PySide6.QtQml import QQmlComponent
        comp = QQmlComponent(view_tbl.engine())
        comp.setData(qml_source, "")
        assert not comp.isError(), f"QML errors: {comp.errors()}"
        root_tbl = comp.create(view_tbl.rootContext())
        root_tbl.setParentItem(view_tbl.contentItem())
        view_tbl.show()
        for _ in range(10):
            QCoreApplication.processEvents()

        def find_texts_in_items(item):
            texts = []
            t = getattr(item, "property", lambda x: None)("text")
            if t:
                texts.append(t)
            for child in item.childItems():
                texts.extend(find_texts_in_items(child))
            return texts

        all_rendered = find_texts_in_items(root_tbl)
        # Find cell that started with "Cell prefix:"
        cell_match = [t for t in all_rendered if "Cell prefix:" in t]
        assert len(cell_match) == 1, f"Multi-segment table cell not found in rendered texts: {all_rendered}"
        cell_text = cell_match[0]
        assert "Cell prefix: " in cell_text
        assert "data:image/svg+xml" in cell_text
        assert " cell suffix." in cell_text
        assert cell_text.index("Cell prefix: ") < cell_text.index("data:image/svg+xml") < cell_text.index(" cell suffix.")
    finally:
        view_tbl.close()
        del view_tbl
        QCoreApplication.processEvents()


def test_full_delegate_flow_path_with_markdown_node_delegate_and_theme_switching(qapp):
    """
    Validates Requirement 8.D & Adversarial Review Finding:
    Exercises the complete MarkdownNodeDelegate.qml flow path with all 50 inline formulas
    materialized and verified in RichText, with repeated theme toggling, verifying zero warnings.
    Guarantees no formulas are skipped by virtualization.
    """
    doc_dto, cache = _build_test_document_dto(num_formulas=50)
    model = MarkdownDocumentModel(
        math_resolver=cache.get,
        math_foreground="#e6edf3",
    )
    model.set_document(doc_dto)

    delegate_path = (
        Path(__file__).resolve().parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownNodeDelegate.qml"
    )
    delegate_url = QUrl.fromLocalFile(str(delegate_path)).toString()

    view = QQuickView()
    try:
        provider = MathImageProvider(cache)
        view.engine().addImageProvider("math", provider)
        view.rootContext().setContextProperty("testDocModel", model)

        # Dedicated QML test harness guaranteeing materialization of all 50 delegates in ListView
        qml_source = f"""
        import QtQuick 2.15
        Item {{
            id: harnessRoot
            width: 800
            height: 10000

            property var loaders: []

            ListView {{
                id: lv
                anchors.fill: parent
                model: testDocModel
                cacheBuffer: 100000
                delegate: Loader {{
                    width: 800
                    source: {json.dumps(delegate_url)}
                    Component.onCompleted: harnessRoot.loaders.push(this)
                }}
            }}
        }}
        """.encode("utf-8")

        from PySide6.QtQml import QQmlComponent
        component = QQmlComponent(view.engine())
        component.setData(qml_source, "")
        assert not component.isError(), f"QML errors: {component.errors()}"

        with QtWarningCapture() as capture:
            root_item = component.create(view.rootContext())
            assert root_item is not None, "Failed to create harnessRoot item"
            root_item.setParentItem(view.contentItem())
            view.show()
            for _ in range(10):
                QCoreApplication.processEvents()

            assert view.status() != QQuickView.Status.Error, f"QML view status error: {view.errors()}"
            loaders = root_item.property("loaders").toVariant()
            # 1. Assert actual materialization of all 50 delegates in ListView
            assert len(loaders) == 50, f"Expected 50 materialized delegates in ListView, got {len(loaders)}"
            assert all(ld.property("item") is not None for ld in loaders), "Not all delegates were loaded by Loader"

            # 2. Verify all 50 delegates render their respective formula Data URI and marker
            for i, ld in enumerate(loaders):
                delegate = ld.property("item")
                flow = delegate.findChild(QObject, "markdownInlineFlow")
                assert flow is not None, f"Delegate {i} missing markdownInlineFlow"
                st = flow.findChild(QObject, "singleText")
                assert st is not None, f"Flow {i} missing singleText"
                txt = st.property("text")
                assert "data:image/svg+xml" in txt, f"Delegate {i} missing formula Data URI"
                assert f"eq_{i}" in txt, f"Delegate {i} formula marker missing"
                assert f"Formula {i}:" in txt, f"Delegate {i} formula label missing"
                assert "image://math" not in txt.lower(), f"Delegate {i} has unprojected image://math URL"

            # 3. Assert ZERO warnings on initial load
            assert capture.connect_finished_warnings == [], (
                f"Expected 0 QQuickPixmap warnings on initial load, got: {capture.connect_finished_warnings}"
            )
            assert capture.qml_warnings == [], f"Unexpected QML warnings on initial load: {capture.qml_warnings}"

            # 4. Switch to light theme (#1f2328)
            model.reproject_math("#1f2328")
            for _ in range(5):
                QCoreApplication.processEvents()
            assert capture.connect_finished_warnings == []
            for i, ld in enumerate(loaders):
                delegate = ld.property("item")
                st = delegate.findChild(QObject, "markdownInlineFlow").findChild(QObject, "singleText")
                assert "color%3D%22%231f2328%22" in st.property("text")

            # 5. Switch back to dark theme (#e6edf3)
            model.reproject_math("#e6edf3")
            for _ in range(5):
                QCoreApplication.processEvents()
            assert capture.connect_finished_warnings == []
            for i, ld in enumerate(loaders):
                delegate = ld.property("item")
                st = delegate.findChild(QObject, "markdownInlineFlow").findChild(QObject, "singleText")
                assert "color%3D%22%23e6edf3%22" in st.property("text")
    finally:
        view.close()
        del view
        QCoreApplication.processEvents()
