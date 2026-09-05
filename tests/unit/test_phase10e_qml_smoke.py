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
    QuoteChildBlockDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QQuickItem, QUrl


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

    def test_inline_flow_across_all_block_types(self):
        """
        R3 Verification: Verifies native inline images render across headings,
        paragraphs, list items, blockquotes, and table cells, creating a native
        MarkdownInlineImageItem with matching regionId and occurrenceId for each.
        """
        vref_h = VisualRegionRefDTO(occurrence_id="occ_h_1", source="crop_h.jpg", image_path="/tmp/crop_h.jpg", alt_text="Fig H", region_id="reg_h", is_associated=True, display_order=1, page_number=1)
        vref_p = VisualRegionRefDTO(occurrence_id="occ_p_1", source="crop_p.jpg", image_path="/tmp/crop_p.jpg", alt_text="Fig P", region_id="reg_p", is_associated=True, display_order=2, page_number=1)
        vref_l = VisualRegionRefDTO(occurrence_id="occ_l_1", source="crop_l.jpg", image_path="/tmp/crop_l.jpg", alt_text="Fig L", region_id="reg_l", is_associated=True, display_order=3, page_number=1)
        vref_b = VisualRegionRefDTO(occurrence_id="occ_b_1", source="crop_b.jpg", image_path="/tmp/crop_b.jpg", alt_text="Fig B", region_id="reg_b", is_associated=True, display_order=4, page_number=1)
        vref_t = VisualRegionRefDTO(occurrence_id="occ_t_1", source="crop_t.jpg", image_path="/tmp/crop_t.jpg", alt_text="Fig T", region_id="reg_t", is_associated=True, display_order=5, page_number=1)

        txt_seg = InlineSegmentDTO(segment_type="text", text_html="<b>Formatted</b> ")

        h_node = MarkdownNodeDTO(
            node_id="h_1",
            node_type="heading",
            level=2,
            content="Heading with Image",
            segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_h)),
            regions=(vref_h,),
        )
        p_node = MarkdownNodeDTO(
            node_id="p_1",
            node_type="paragraph",
            content="Paragraph with Image",
            segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_p)),
            regions=(vref_p,),
        )
        l_node = MarkdownNodeDTO(
            node_id="l_1",
            node_type="list",
            content="<ul><li>Item 1</li></ul>",
            list_items=("Item 1",),
            list_item_segments=((txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_l)),),
            segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_l)),
            regions=(vref_l,),
        )
        b_node = MarkdownNodeDTO(
            node_id="b_1",
            node_type="blockquote",
            content="<blockquote>Blockquote with image</blockquote>",
            quote_children=(
                QuoteChildBlockDTO(
                    child_type="paragraph",
                    content="Blockquote with image",
                    level=0,
                    segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_b)),
                ),
            ),
            segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_b)),
            regions=(vref_b,),
        )
        t_node = MarkdownNodeDTO(
            node_id="t_1",
            node_type="table_fallback",
            content="| Col 1 |",
            table_cell_segments=(
                ((txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_t)),),
            ),
            segments=(txt_seg, InlineSegmentDTO(segment_type="image", image_ref=vref_t)),
            regions=(vref_t,),
        )

        doc = MarkdownDocumentDTO(
            job_id=999,
            version=1,
            nodes=(h_node, p_node, l_node, b_node, t_node),
            region_to_occurrences={
                "reg_h": (RegionOccurrenceRef(node_index=0, occurrence_id="occ_h_1"),),
                "reg_p": (RegionOccurrenceRef(node_index=1, occurrence_id="occ_p_1"),),
                "reg_l": (RegionOccurrenceRef(node_index=2, occurrence_id="occ_l_1"),),
                "reg_b": (RegionOccurrenceRef(node_index=3, occurrence_id="occ_b_1"),),
                "reg_t": (RegionOccurrenceRef(node_index=4, occurrence_id="occ_t_1"),),
            },
        )

        self.controller._model.set_document(doc)
        self.controller._has_document = True
        self.controller.documentChanged.emit()
        self.app.processEvents()

        list_view = self.view_root.findChild(object, "markdownListView")
        self.assertEqual(list_view.property("count"), 5)

        test_engine = QQmlApplicationEngine()
        test_engine.rootContext().setContextProperty("markdownViewerController", self.controller)
        qml_src = """
import QtQuick 2.15
import "interfaces/desktop/qml/components"

Item {
    id: testRoot
    width: 800
    height: 3000

    ListView {
        id: lv
        anchors.fill: parent
        model: markdownViewerController ? markdownViewerController.model : null
        delegate: MarkdownNodeDelegate {
            width: 800
            controller: markdownViewerController
        }
    }
}
"""
        test_engine.loadData(qml_src.encode("utf-8"), QUrl.fromLocalFile(str(Path.cwd() / "test_visual_inline.qml")))
        self.app.processEvents()
        test_root = test_engine.rootObjects()[0]

        def find_visual_children(item, target_name):
            result = []
            if isinstance(item, QQuickItem):
                if item.objectName() == target_name:
                    result.append(item)
                for child in item.childItems():
                    result.extend(find_visual_children(child, target_name))
            return result

        inline_images = find_visual_children(test_root, "markdownInlineImageItem")
        self.assertEqual(len(inline_images), 5, "Expected exactly 5 native MarkdownInlineImageItem instances across all block types")

        found_map = {item.property("regionId"): item.property("occurrenceId") for item in inline_images}
        self.assertEqual(found_map.get("reg_h"), "occ_h_1", "Heading inline image regionId and occurrenceId must match DTO")
        self.assertEqual(found_map.get("reg_p"), "occ_p_1", "Paragraph inline image regionId and occurrenceId must match DTO")
        self.assertEqual(found_map.get("reg_l"), "occ_l_1", "List item inline image regionId and occurrenceId must match DTO")
        self.assertEqual(found_map.get("reg_b"), "occ_b_1", "Blockquote child inline image regionId and occurrenceId must match DTO")
        self.assertEqual(found_map.get("reg_t"), "occ_t_1", "Table cell inline image regionId and occurrenceId must match DTO")

        test_engine.deleteLater()
        self.app.processEvents()

        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w
        ]
        self.assertEqual(critical_errors, [], f"Errors during all-block-type inline rendering: {critical_errors}")

    def test_exact_occurrence_highlighting_isolation_in_qml(self):
        """
        R2.3 QML Verification: When an exact occurrence is targeted, only that specific
        item must be highlighted, while sibling items sharing the same region_id remain unhighlighted.
        """
        qml_src = """
import QtQuick 2.15
import "interfaces/desktop/qml/components"

Item {
    id: root
    MarkdownInlineImageItem {
        objectName: "item1"
        regionId: "reg_same"
        occurrenceId: "occ_1"
        controller: markdownViewerController
    }
    MarkdownInlineImageItem {
        objectName: "item2"
        regionId: "reg_same"
        occurrenceId: "occ_2"
        controller: markdownViewerController
    }
    MarkdownImageCard {
        objectName: "card1"
        regionId: "reg_same"
        occurrenceId: "occ_1"
        controller: markdownViewerController
    }
    MarkdownImageCard {
        objectName: "card2"
        regionId: "reg_same"
        occurrenceId: "occ_2"
        controller: markdownViewerController
    }
}
"""
        test_engine = QQmlApplicationEngine()
        test_engine.rootContext().setContextProperty("markdownViewerController", self.controller)
        test_engine.loadData(qml_src.encode("utf-8"), QUrl.fromLocalFile(str(Path.cwd() / "dummy.qml")))
        self.app.processEvents()

        root = test_engine.rootObjects()[0]
        item1 = root.findChild(object, "item1")
        item2 = root.findChild(object, "item2")
        card1 = root.findChild(object, "card1")
        card2 = root.findChild(object, "card2")

        # Select occurrence 2
        self.controller.selectRegion("reg_same", "occ_2")
        self.app.processEvents()

        self.assertFalse(item1.property("isHighlighted"))
        self.assertTrue(item2.property("isHighlighted"))
        self.assertFalse(card1.property("isHighlighted"))
        self.assertTrue(card2.property("isHighlighted"))

        # Select occurrence 1
        self.controller.selectRegion("reg_same", "occ_1")
        self.app.processEvents()

        self.assertTrue(item1.property("isHighlighted"))
        self.assertFalse(item2.property("isHighlighted"))
        self.assertTrue(card1.property("isHighlighted"))
        self.assertFalse(card2.property("isHighlighted"))

        test_engine.deleteLater()
        self.app.processEvents()

    def test_ordered_list_and_task_item_rendering(self):
        """
        R3 Verification: Ordered lists and task list items render cleanly without
        shadowed model property errors or missing task checkboxes.
        """
        txt_seg = InlineSegmentDTO(segment_type="text", text_html="Item text")
        task_seg0 = InlineSegmentDTO(segment_type="text", text_html="☐ Incomplete task")
        task_seg1 = InlineSegmentDTO(segment_type="text", text_html="☑ Completed task")

        ord_node = MarkdownNodeDTO(
            node_id="l_ord",
            node_type="list",
            is_ordered=True,
            start_index=1,
            content="<ol><li>Item text</li></ol>",
            list_items=("Item text",),
            list_item_segments=((txt_seg,),),
        )
        task_node = MarkdownNodeDTO(
            node_id="l_task",
            node_type="list",
            is_ordered=False,
            start_index=1,
            content="<ul><li>☐ Incomplete task</li><li>☑ Completed task</li></ul>",
            list_items=("☐ Incomplete task", "☑ Completed task"),
            list_item_segments=((task_seg0,), (task_seg1,)),
        )

        doc = MarkdownDocumentDTO(
            job_id=888,
            version=1,
            nodes=(ord_node, task_node),
            region_to_occurrences={},
        )

        self.controller._model.set_document(doc)
        self.controller._has_document = True
        self.controller.documentChanged.emit()
        self.app.processEvents()

        list_view = self.view_root.findChild(object, "markdownListView")
        self.assertEqual(list_view.property("count"), 2)

        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w
        ]
        self.assertEqual(critical_errors, [], f"Errors during ordered and task list rendering: {critical_errors}")
