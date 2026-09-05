# ============================================================
#  tests/unit/test_phase10e_qml_smoke.py
#  Phase 10E QML Runtime Smoke Test & Component Instantiation
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl


class TestMarkdownQmlSmoke(unittest.TestCase):
    """
    Verifies that MarkdownView.qml, MarkdownNodeDelegate.qml, MarkdownImageCard.qml,
    and MarkdownInlineImageItem.qml load cleanly in the Qt Quick runtime without
    syntax or reference errors.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_service = MagicMock(spec=MarkdownViewerService)
        self.controller = MarkdownViewerController(viewer_service=self.mock_service)

        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("markdownViewerController", self.controller)

        self.qml_warnings = []
        self.engine.warnings.connect(self._on_qml_warning)

        self.qml_views_dir = (
            Path(__file__).parent.parent.parent
            / "interfaces"
            / "desktop"
            / "qml"
            / "views"
        )
        self.view_path = self.qml_views_dir / "MarkdownView.qml"
        self.assertTrue(self.view_path.exists(), f"QML view file missing: {self.view_path}")

        self.engine.load(QUrl.fromLocalFile(str(self.view_path)))
        self.app.processEvents()

        root_objects = self.engine.rootObjects()
        self.assertEqual(len(root_objects), 1, "Expected exactly 1 root object loaded from MarkdownView.qml")
        self.view_root = root_objects[0]

    def tearDown(self):
        self.engine.deleteLater()
        self.app.processEvents()

    def _on_qml_warning(self, warnings):
        for w in warnings:
            self.qml_warnings.append(w.toString())

    def _create_sample_doc(self) -> MarkdownDocumentDTO:
        vref = VisualRegionRefDTO(
            occurrence_id="p_1_img_0",
            source="crop_100_rid1_v1.jpg",
            image_path="/tmp/crop.jpg",
            alt_text="Sample Diagram",
            region_id="rid_001",
            is_associated=True,
            display_order=1,
            page_number=1,
        )
        node0 = MarkdownNodeDTO(
            node_id="h1_doc_1",
            node_type="heading",
            level=1,
            content="Document Header",
        )
        node1 = MarkdownNodeDTO(
            node_id="p_sample_1",
            node_type="paragraph",
            content='Inline text with <a href="region://rid_001">[#1]</a>.',
            regions=(vref,),
            segments=(
                InlineSegmentDTO(segment_type="text", text_html="Inline text with "),
                InlineSegmentDTO(segment_type="image", image_ref=vref),
                InlineSegmentDTO(segment_type="text", text_html="."),
            ),
        )
        node2 = MarkdownNodeDTO(
            node_id="img_rid_001",
            node_type="image",
            content="Sample Diagram",
            regions=(vref,),
            segments=(InlineSegmentDTO(segment_type="image", image_ref=vref),),
        )
        node3 = MarkdownNodeDTO(
            node_id="code_python_1",
            node_type="code_block",
            language="python",
            content="print('Hello World')",
        )
        node4 = MarkdownNodeDTO(
            node_id="list_unord_1",
            node_type="list",
            content="<ul><li>Item 1</li><li>Item 2</li></ul>",
            list_items=("Item 1", "Item 2"),
        )
        node5 = MarkdownNodeDTO(
            node_id="quote_1",
            node_type="blockquote",
            content="<blockquote>Important quote</blockquote>",
        )
        node6 = MarkdownNodeDTO(
            node_id="hr_1",
            node_type="thematic_break",
            content="<hr/>",
        )
        node7 = MarkdownNodeDTO(
            node_id="table_1",
            node_type="table_fallback",
            content="| Col A | Col B |\n|---|---|\n| 1 | 2 |",
        )

        return MarkdownDocumentDTO(
            job_id=42,
            version=1,
            nodes=(node0, node1, node2, node3, node4, node5, node6, node7),
            region_to_occurrences={
                "rid_001": (RegionOccurrenceRef(node_index=1, occurrence_id="p_1_img_0"),),
            },
        )

    def test_markdown_view_loads_cleanly_without_critical_errors(self):
        """Verifies MarkdownView.qml loads with zero critical errors."""
        self.assertEqual(self.view_root.objectName(), "markdownView")

        list_view = self.view_root.findChild(object, "markdownListView")
        self.assertIsNotNone(list_view, "markdownListView must resolve in QML hierarchy")

        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w
        ]
        self.assertEqual(critical_errors, [], f"Critical QML errors detected: {critical_errors}")

    def test_markdown_view_renders_nodes_dynamically(self):
        """Verifies that loading a document populates the virtualized ListView."""
        doc = self._create_sample_doc()
        self.controller._model.set_document(doc)
        self.controller._has_document = True
        self.controller.documentChanged.emit()
        self.app.processEvents()

        list_view = self.view_root.findChild(object, "markdownListView")
        self.assertEqual(list_view.property("count"), 8)

        # Zoom scale factor updates dynamically
        self.controller.zoomIn()
        self.app.processEvents()
        self.assertEqual(self.controller.scaleFactor, 1.1)

        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w
        ]
        self.assertEqual(critical_errors, [], f"Critical QML errors during rendering: {critical_errors}")
