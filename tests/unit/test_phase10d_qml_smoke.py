# ============================================================
#  tests/unit/test_phase10d_qml_smoke.py
#  Phase 10D Interactive Bounding Box Editor QML Smoke Tests
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl
try:
    from PySide6.QtCore import QPointF
except ImportError:
    from PyQt6.QtCore import QPointF
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus
from application.dto.document_viewer_dto import PageRasterDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


def find_quick_item(root, object_name: str):
    """
    Finds a QQuickItem by objectName across the QObject hierarchy and QQuickItem visual scene graph.
    Traverses both childItems() (visual scene graph) and children() (QObject tree)
    to locate Repeater delegates whose QObject parent is detached from their visual parent.
    """
    try:
        from PySide6.QtQuick import QQuickItem
    except ImportError:
        from PyQt6.QtQuick import QQuickItem

    if isinstance(root, QQuickItem) and root.objectName() == object_name:
        return root

    direct = root.findChild(QQuickItem, object_name)
    if direct is not None:
        return direct

    def _search_visual(item):
        if hasattr(item, "objectName") and item.objectName() == object_name:
            return item
        if hasattr(item, "childItems"):
            for child in item.childItems():
                res = _search_visual(child)
                if res is not None:
                    return res
        return None

    res = _search_visual(root)
    if res is not None:
        return res

    overlay = root.findChild(QQuickItem, "regionOverlay")
    if overlay is not None:
        res = _search_visual(overlay)
        if res is not None:
            return res

    return None


class TestDocumentViewerPhase10DQmlSmoke(unittest.TestCase):
    """
    Verifies that DocumentViewerView.qml and BoundingBoxOverlay.qml with Phase 10D
    interactive editing features instantiate cleanly without runtime errors.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.r1 = VisualRegionDTO(
            id=1,
            region_id="test-r1",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.MODIFIED.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(100, 100, 300, 300),
            detected_bbox=BoundingBox(100, 100, 250, 250),
            reviewed_bbox=BoundingBox(100, 100, 300, 300),
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=True,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.mock_service.get_active_page_regions.return_value = [self.r1]
        self.mock_service.get_page_raster.return_value = PageRasterDTO(
            job_id=1,
            page_number=1,
            total_pages=1,
            raster_width=1000,
            raster_height=1000,
            image_uri="",
            dpi=150,
        )

        self.controller = DocumentViewerController(viewer_service=self.mock_service)
        self.controller.loadPageSync(1, 1)

        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("documentViewerController", self.controller)

        self.qml_warnings = []
        self.engine.warnings.connect(self._on_qml_warning)

        qml_path = (
            Path(__file__).parent.parent.parent
            / "interfaces"
            / "desktop"
            / "qml"
            / "views"
            / "DocumentViewerView.qml"
        )
        self.assertTrue(qml_path.exists(), f"QML file not found: {qml_path}")

        self.engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root_objects = self.engine.rootObjects()
        self.assertEqual(len(root_objects), 1)
        self.view_root = root_objects[0]
        self.view_root.setProperty("width", 1000)
        self.view_root.setProperty("height", 800)
        self.controller.setViewportDimensions(1000, 800)
        self.controller.setItemDimensions(1000, 800)
        self.app.processEvents()

    def tearDown(self):
        self.engine.deleteLater()
        self.app.processEvents()

    def _on_qml_warning(self, warnings):
        for w in warnings:
            self.qml_warnings.append(w.toString())

    def test_editor_qml_loads_cleanly_with_zero_critical_errors(self):
        """1. Verifies that DocumentViewerView and interactive BoundingBoxOverlay load without syntax/binding errors."""
        self.assertEqual(self.view_root.objectName(), "documentViewerView")

        # Verify child components
        overlay = self.view_root.findChild(object, "regionOverlay")
        self.assertIsNotNone(overlay)
        creation_preview = self.view_root.findChild(object, "creationPreview")
        self.assertIsNotNone(creation_preview)

        # Verify no critical QML runtime errors
        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w or "Cannot assign to" in w
        ]
        self.assertEqual(critical_errors, [], f"QML runtime produced critical errors: {critical_errors}")

    def test_toolbar_buttons_bind_to_controller_selection_state(self):
        """2. Verifies deleteRegionButton and resetToAiButton exist and dynamically update their enabled state."""
        del_btn = self.view_root.findChild(object, "deleteRegionButton")
        self.assertIsNotNone(del_btn)
        reset_btn = self.view_root.findChild(object, "resetToAiButton")
        self.assertIsNotNone(reset_btn)

        # Initially no selection -> both disabled
        self.app.processEvents()
        self.assertFalse(del_btn.property("enabled"))
        self.assertFalse(reset_btn.property("enabled"))

        # Select modified AI region -> both become enabled
        self.controller.selectRegion("test-r1")
        self.app.processEvents()
        self.assertTrue(del_btn.property("enabled"))
        self.assertTrue(reset_btn.property("enabled"))

        # Deselect -> disabled again
        self.controller.clearSelection()
        self.app.processEvents()
        self.assertFalse(del_btn.property("enabled"))
        self.assertFalse(reset_btn.property("enabled"))

    def test_transient_creation_preview_tracks_controller(self):
        """3. Verifies rubber-band preview visibility dynamically tracks controller.editorState."""
        creation_preview = self.view_root.findChild(object, "creationPreview")
        self.assertIsNotNone(creation_preview)

        # Initially idle -> preview hidden
        self.assertFalse(creation_preview.property("visible"))

        # Start manual creation
        self.controller.startCreateManual(100.0, 100.0)
        self.controller.updateCreateManual(250.0, 250.0)
        self.app.processEvents()

        self.assertTrue(creation_preview.property("visible"))
        self.assertGreater(creation_preview.property("width"), 0)

        # Commit creation
        self.controller.cancelCreateManual()
        self.app.processEvents()
        self.assertFalse(creation_preview.property("visible"))

    def test_8_resize_handles_instantiated_at_runtime_when_region_selected(self):
        """
        4. Verifies that all 8 resize handles (nw, n, ne, w, e, sw, s, se) actually instantiate
        in the real Qt Quick runtime when a region is selected, and are removed when deselected.
        """
        canonical_handles = ["nw", "n", "ne", "w", "e", "sw", "s", "se"]

        # Initially no selection -> selectionManipulator is hidden
        self.app.processEvents()
        manipulator = find_quick_item(self.view_root, "selectionManipulator")
        self.assertIsNotNone(manipulator)
        self.assertFalse(manipulator.property("visible"), "selectionManipulator must be hidden when no region is selected")

        # Select the region in the controller
        self.controller.selectRegion("test-r1")
        self.app.processEvents()

        # Manipulator and all 8 handles must now be visible
        self.assertTrue(manipulator.property("visible"), "selectionManipulator must be visible when region is selected")
        for h in canonical_handles:
            handle_item = find_quick_item(self.view_root, f"handle_{h}")
            self.assertIsNotNone(handle_item, f"Handle delegate 'handle_{h}' must instantiate at runtime")
            self.assertEqual(handle_item.property("handleType"), h)
            self.assertGreater(handle_item.property("width"), 0)
            self.assertGreater(handle_item.property("height"), 0)

            # Check that each handle's MouseArea is present and configured
            handle_area = find_quick_item(self.view_root, f"handleArea_{h}")
            self.assertIsNotNone(handle_area, f"MouseArea 'handleArea_{h}' must exist for handle '{h}'")
            self.assertTrue(handle_area.property("enabled"))

        # Deselect region -> selectionManipulator must be hidden
        self.controller.clearSelection()
        self.app.processEvents()

        self.assertFalse(manipulator.property("visible"), "selectionManipulator must be hidden when region is deselected")

        # Verify no QML critical errors or warnings were logged during handle lifecycle
        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w or "Cannot assign to" in w
        ]
        self.assertEqual(critical_errors, [], f"Handle lifecycle produced critical QML errors: {critical_errors}")

    def test_interaction_layering_hierarchy(self):
        """
        5. Verifies interaction layering so that panZoomArea does not block region body or handles:
           - Handle z (10) > Box bodyDragArea z (1) > emptyArea z (0)
           - Selected box z is 5, unselected box z is 1
           - pageScene is stacked above panZoomArea in viewportArea.
           - Pointer events route according to priority:
             Handle clicks -> handle MouseArea (z=10)
             Region body clicks -> bodyDragArea (z=1 inside boxRect)
             Empty document space clicks -> emptyArea (z=0)
             Letterboxed margin clicks -> panZoomArea (behind pageScene)
        """
        viewport_area = find_quick_item(self.view_root, "viewportArea")
        self.assertIsNotNone(viewport_area, "viewportArea must exist")

        pan_zoom_area = find_quick_item(self.view_root, "panZoomArea")
        self.assertIsNotNone(pan_zoom_area, "panZoomArea must exist on viewportArea")

        empty_area = find_quick_item(self.view_root, "emptyArea")
        self.assertIsNotNone(empty_area, "emptyArea must exist on BoundingBoxOverlay")
        self.assertEqual(empty_area.property("z"), 0)

        # In viewportArea, verify pageScene is declared after panZoomArea in child order
        if hasattr(viewport_area, "childItems"):
            children = viewport_area.childItems()
            page_scene = find_quick_item(self.view_root, "pageScene")
            self.assertIsNotNone(page_scene)
            pz_idx = children.index(pan_zoom_area)
            ps_idx = children.index(page_scene)
            self.assertGreater(ps_idx, pz_idx, "pageScene must be stacked after panZoomArea in visual child order")

        # Select region to instantiate box and handles
        self.controller.selectRegion("test-r1")
        self.app.processEvents()

        box_rect = find_quick_item(self.view_root, "boxRect_test-r1")
        self.assertIsNotNone(box_rect, "boxRect must exist for region")
        self.assertEqual(box_rect.property("z"), 5, "Selected box must have z=5 (above emptyArea z=0)")

        body_drag_area = find_quick_item(self.view_root, "bodyDragArea_test-r1")
        self.assertIsNotNone(body_drag_area, "bodyDragArea must exist inside boxRect")
        self.assertEqual(body_drag_area.property("z"), 1)

        # Handle items inside selected box must have z=10 (above bodyDragArea z=1)
        canonical_handles = ["nw", "n", "ne", "w", "e", "sw", "s", "se"]
        for h in canonical_handles:
            h_item = find_quick_item(self.view_root, f"handle_{h}")
            self.assertIsNotNone(h_item, f"Handle {h} must exist")
            self.assertEqual(h_item.property("z"), 10, f"Handle {h} must have z=10 (above bodyDragArea z=1)")
            h_area = find_quick_item(self.view_root, f"handleArea_{h}")
            self.assertIsNotNone(h_area, f"Handle area {h} must exist")
            self.assertTrue(h_area.property("enabled"))

    def test_letterbox_margin_and_document_surface_hit_routing(self):
        """
        6. Verifies hit-testing semantics between document surface and letterbox/pillarbox margins:
           - displayedRect accurately computes rendered page bounds (100, 0, 800, 800) for 1000x1000 page in 1000x800 container.
           - emptyArea geometry matches displayedRect and contains document points, but NOT margin points.
           - Clicking in letterbox margin (x=50, y=400) does NOT hit emptyArea (overlay.childAt is None).
           - Clicking in empty document space (x=500, y=400) hits emptyArea (overlay.childAt is emptyArea).
           - Region body at (260, 160) takes precedence over emptyArea (overlay.childAt is boxRect_test-r1).
           - Resize handle at (0, 0) inside box takes precedence over region body (boxRect.childAt is handle_nw).
           - panZoomArea covers viewport margin space and accepts pointer events for canvas panning.
        """
        overlay = find_quick_item(self.view_root, "regionOverlay")
        self.assertIsNotNone(overlay)

        # 1. Verify displayedRect and emptyArea geometry (1000x1000 raster in 1000x800 viewport -> 800x800 with offset_x=100)
        disp_rect = overlay.property("displayedRect")
        self.assertIsNotNone(disp_rect)
        self.assertEqual(disp_rect.get("x"), 100.0)
        self.assertEqual(disp_rect.get("y"), 0.0)
        self.assertEqual(disp_rect.get("width"), 800.0)
        self.assertEqual(disp_rect.get("height"), 800.0)

        empty_area = find_quick_item(self.view_root, "emptyArea")
        self.assertIsNotNone(empty_area)
        self.assertEqual(empty_area.property("x"), 100.0)
        self.assertEqual(empty_area.property("y"), 0.0)
        self.assertEqual(empty_area.property("width"), 800.0)
        self.assertEqual(empty_area.property("height"), 800.0)

        # 2. Inside letterbox margin (x=50, y=400):
        # emptyArea does NOT contain point; overlay.childAt is None (falls through to panZoomArea)
        local_margin = empty_area.mapFromItem(overlay, QPointF(50.0, 400.0))
        self.assertFalse(empty_area.contains(local_margin), "emptyArea must not contain letterbox margin point")
        if hasattr(overlay, "childAt"):
            self.assertIsNone(overlay.childAt(50.0, 400.0), "Letterbox margin click must pass through overlay")

        # 3. Inside empty document space (x=500, y=400):
        # emptyArea contains point; overlay.childAt is emptyArea
        local_doc = empty_area.mapFromItem(overlay, QPointF(500.0, 400.0))
        self.assertTrue(empty_area.contains(local_doc), "emptyArea must contain document point")
        if hasattr(overlay, "childAt"):
            child = overlay.childAt(500.0, 400.0)
            self.assertIsNotNone(child)
            self.assertEqual(child.objectName(), "emptyArea")

        # 4. Region body takes precedence over emptyArea
        # r1 is at [180, 80, 340, 240]; center is (260, 160)
        if hasattr(overlay, "childAt"):
            region_child = overlay.childAt(260.0, 160.0)
            self.assertIsNotNone(region_child)
            self.assertEqual(region_child.objectName(), "boxRect_test-r1")

        # 5. Resize handles take precedence over region body when selected
        self.controller.selectRegion("test-r1")
        self.app.processEvents()

        box_rect = find_quick_item(self.view_root, "boxRect_test-r1")
        self.assertIsNotNone(box_rect)
        manipulator = find_quick_item(self.view_root, "selectionManipulator")
        target_container = manipulator if manipulator is not None else box_rect
        if hasattr(target_container, "childAt"):
            # nw handle is at (0, 0)
            handle_child = target_container.childAt(0.0, 0.0)
            self.assertIsNotNone(handle_child)
            self.assertEqual(handle_child.objectName(), "handle_nw")
            # center of box (80, 80) is bodyDragArea / manipulatorDragArea
            body_child = target_container.childAt(80.0, 80.0)
            self.assertIsNotNone(body_child)
            self.assertIn(body_child.objectName(), ["bodyDragArea_test-r1", "manipulatorDragArea"])

        # 6. Verify panZoomArea captures margin gestures
        pan_zoom_area = find_quick_item(self.view_root, "panZoomArea")
        self.assertIsNotNone(pan_zoom_area)
        self.assertTrue(pan_zoom_area.property("enabled"))
        self.assertEqual(self.controller.editorState, "selected")
