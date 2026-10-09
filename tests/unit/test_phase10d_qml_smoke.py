# ============================================================
#  tests/unit/test_phase10d_qml_smoke.py
#  Phase 10D Interactive Bounding Box Editor QML Smoke Tests
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QQmlApplicationEngine,
    QUrl,
    QEvent,
    Qt,
    QPointF,
    QMouseEvent,
)
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus
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

        self.mock_pub_service = MagicMock()
        self.controller = DocumentViewerController(
            viewer_service=self.mock_service,
            region_publication_service=self.mock_pub_service,
        )
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
        region_menu = self.view_root.findChild(object, "regionContextMenu")
        self.assertIsNotNone(region_menu)
        self.assertEqual(region_menu.property("controller"), self.controller)

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
        disp_dict = disp_rect.toVariant() if hasattr(disp_rect, "toVariant") else disp_rect
        self.assertEqual(disp_dict.get("x"), 100.0)
        self.assertEqual(disp_dict.get("y"), 0.0)
        self.assertEqual(disp_dict.get("width"), 800.0)
        self.assertEqual(disp_dict.get("height"), 800.0)

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

    def test_color_decoupling_preserves_semantic_colors_under_selection(self):
        """
        7. Verifies that selecting any region does NOT overwrite its semantic status color with cyan.
           - Accepted region: stays #2a9d8f (green)
           - AI unreviewed: stays #00b4d8 (cyan)
           - User modified: stays #f77f00 (amber)
           - User manual: stays #9d4edd (purple)
           - Outer selectionManipulator (#00f0ff) and 8 resize handles provide the selection affordance.
           - Badge text color stays #ffffff for high contrast.
        """
        def _to_hex(val) -> str:
            if hasattr(val, "name"):
                return val.name().lower()
            return str(val).lower()

        r_accepted = VisualRegionDTO(
            id=10,
            region_id="reg-accepted",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.ACCEPTED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(50, 50, 150, 150),
            detected_bbox=BoundingBox(50, 50, 150, 150),
            reviewed_bbox=BoundingBox(50, 50, 150, 150),
            active_artifact_version=1,
            active_artifact_uri="art/p1_r1.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_unreviewed = VisualRegionDTO(
            id=11,
            region_id="reg-unreviewed",
            job_id=1,
            page_number=1,
            display_order=2,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(200, 200, 300, 300),
            detected_bbox=BoundingBox(200, 200, 300, 300),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="art/p1_r2.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_modified = VisualRegionDTO(
            id=12,
            region_id="reg-modified",
            job_id=1,
            page_number=1,
            display_order=3,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.MODIFIED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(350, 350, 450, 450),
            detected_bbox=BoundingBox(350, 350, 400, 400),
            reviewed_bbox=BoundingBox(350, 350, 450, 450),
            active_artifact_version=1,
            active_artifact_uri="art/p1_r3.png",
            is_modified=True,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_manual = VisualRegionDTO(
            id=13,
            region_id="reg-manual",
            job_id=1,
            page_number=1,
            display_order=4,
            origin=RegionOrigin.USER_MANUAL.value,
            review_status=ReviewStatus.MANUAL.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(500, 500, 600, 600),
            detected_bbox=None,
            reviewed_bbox=BoundingBox(500, 500, 600, 600),
            active_artifact_version=1,
            active_artifact_uri="art/p1_r4.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )

        self.mock_service.get_active_page_regions.return_value = [
            r_accepted, r_unreviewed, r_modified, r_manual
        ]
        self.controller.loadPageSync(1, 1)
        self.app.processEvents()

        # Check all unselected colors
        box_acc = find_quick_item(self.view_root, "boxRect_reg-accepted")
        box_unrev = find_quick_item(self.view_root, "boxRect_reg-unreviewed")
        box_mod = find_quick_item(self.view_root, "boxRect_reg-modified")
        box_man = find_quick_item(self.view_root, "boxRect_reg-manual")

        self.assertIsNotNone(box_acc)
        self.assertIsNotNone(box_unrev)
        self.assertIsNotNone(box_mod)
        self.assertIsNotNone(box_man)

        self.assertEqual(_to_hex(box_acc.property("boxColor")), "#2a9d8f")
        self.assertEqual(_to_hex(box_unrev.property("boxColor")), "#00b4d8")
        self.assertEqual(_to_hex(box_mod.property("boxColor")), "#f77f00")
        self.assertEqual(_to_hex(box_man.property("boxColor")), "#9d4edd")

        # Unselected fill alpha must be 0.15
        color_unsel = box_acc.property("color")
        self.assertAlmostEqual(color_unsel.alphaF(), 0.15, delta=0.03, msg="Unselected fill alpha must be 0.15")

        # Now select the accepted region: must retain #2a9d8f, NOT turn #00f0ff
        self.controller.selectRegion("reg-accepted")
        self.app.processEvents()

        manipulator = find_quick_item(self.view_root, "selectionManipulator")
        self.assertIsNotNone(manipulator)
        self.assertTrue(manipulator.property("visible"))
        self.assertEqual(_to_hex(box_acc.property("boxColor")), "#2a9d8f", "Accepted region must stay green when selected")

        # Selected fill alpha must be 0.25
        color_sel = box_acc.property("color")
        self.assertAlmostEqual(color_sel.alphaF(), 0.25, delta=0.03, msg="Selected fill alpha must be 0.25")

        # Select unreviewed AI region: must retain #00b4d8
        self.controller.selectRegion("reg-unreviewed")
        self.app.processEvents()
        self.assertEqual(_to_hex(box_unrev.property("boxColor")), "#00b4d8", "Unreviewed region must stay cyan default when selected")

        # Select modified region: must retain #f77f00
        self.controller.selectRegion("reg-modified")
        self.app.processEvents()
        self.assertEqual(_to_hex(box_mod.property("boxColor")), "#f77f00", "Modified region must stay amber when selected")

        # Select manual region: must retain #9d4edd
        self.controller.selectRegion("reg-manual")
        self.app.processEvents()
        self.assertEqual(_to_hex(box_man.property("boxColor")), "#9d4edd", "Manual region must stay purple when selected")

    def test_accept_region_button_binding_and_click(self):
        """
        8. Verifies acceptRegionButton toolbar binding and click behavior:
           - Enabled only when controller.canAcceptSelected is True (unreviewed AI region selected).
           - Disabled when no selection, or when accepted/modified/manual region is selected.
           - Clicking triggers controller.acceptSelectedRegion().
        """
        r_unrev = VisualRegionDTO(
            id=20,
            region_id="reg-to-accept",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(100, 100, 200, 200),
            detected_bbox=BoundingBox(100, 100, 200, 200),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="art/p1_r1.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_accepted_res = VisualRegionDTO(
            id=20,
            region_id="reg-to-accept",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.ACCEPTED.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(100, 100, 200, 200),
            detected_bbox=BoundingBox(100, 100, 200, 200),
            reviewed_bbox=BoundingBox(100, 100, 200, 200),
            active_artifact_version=1,
            active_artifact_uri="art/p1_r1.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )

        self.mock_service.get_active_page_regions.return_value = [r_unrev]
        self.controller.loadPageSync(1, 1)
        self.app.processEvents()

        accept_btn = self.view_root.findChild(object, "acceptRegionButton")
        self.assertIsNotNone(accept_btn, "acceptRegionButton must exist in DocumentViewerView")
        self.assertFalse(accept_btn.property("enabled"), "Initially disabled without selection")

        # Select unreviewed AI region -> button becomes enabled
        self.controller.selectRegion("reg-to-accept")
        self.app.processEvents()
        self.assertTrue(self.controller.canAcceptSelected)
        self.assertTrue(accept_btn.property("enabled"), "Enabled when unreviewed AI region is selected")

        # Clicking acceptRegionButton triggers accept
        self.mock_service.accept_region.return_value = r_accepted_res
        self.mock_service.get_active_page_regions.return_value = [r_accepted_res]

        accept_btn.clicked.emit()
        self.app.processEvents()

        self.mock_service.accept_region.assert_called_with("reg-to-accept")
        self.assertFalse(self.controller.canAcceptSelected, "Once accepted, canAcceptSelected must be False")
        self.assertFalse(accept_btn.property("enabled"), "acceptRegionButton must be disabled after acceptance")

    def test_undo_delete_button_binding_and_click(self):
        """
        9. Verifies undoDeleteButton toolbar binding and click behavior:
           - Enabled only when controller.canUndoDelete is True.
           - Clicking invokes controller.undoDelete(), which restores the deleted region.
        """
        r_del = VisualRegionDTO(
            id=30,
            region_id="reg-to-delete",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(100, 100, 200, 200),
            detected_bbox=BoundingBox(100, 100, 200, 200),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="art/p1_r1.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.mock_service.get_active_page_regions.return_value = [r_del]
        self.controller.loadPageSync(1, 1)
        self.app.processEvents()

        undo_btn = self.view_root.findChild(object, "undoDeleteButton")
        del_btn = self.view_root.findChild(object, "deleteRegionButton")
        self.assertIsNotNone(undo_btn, "undoDeleteButton must exist in DocumentViewerView")
        self.assertIsNotNone(del_btn, "deleteRegionButton must exist in DocumentViewerView")

        # Initially no deleted regions -> undoDeleteButton disabled
        self.assertFalse(undo_btn.property("enabled"))
        self.assertFalse(self.controller.canUndoDelete)
        self.assertFalse(del_btn.property("enabled"))
        self.assertFalse(self.controller.canDeleteSelected)

        # Select region and click delete
        self.controller.selectRegion("reg-to-delete")
        self.app.processEvents()
        self.assertTrue(self.controller.canDeleteSelected)
        self.assertTrue(del_btn.property("enabled"), "deleteRegionButton must be enabled when region is selected")

        self.mock_service.get_active_page_regions.return_value = []
        del_btn.clicked.emit()
        self.app.processEvents()

        self.mock_service.reject_region.assert_called_with("reg-to-delete")
        self.assertTrue(self.controller.canUndoDelete)
        self.assertTrue(undo_btn.property("enabled"), "undoDeleteButton must be enabled after deleting a region")
        self.assertFalse(self.controller.canDeleteSelected)
        self.assertFalse(del_btn.property("enabled"))

        # Click undo delete
        self.mock_service.get_active_page_regions.return_value = [r_del]
        undo_btn.clicked.emit()
        self.app.processEvents()

        self.mock_service.restore_region.assert_called_with("reg-to-delete")
        self.assertFalse(self.controller.canUndoDelete)
        self.assertFalse(undo_btn.property("enabled"), "undoDeleteButton must be disabled once undone")

    def test_sync_error_badge_visibility_and_retry_click(self):
        """
        10. Verifies sync error feedback, retry affordance, and responsive text:
           - Visible on bounding box canvas delegate when sync_status === "sync_failed".
           - Hidden when sync_status === "synced".
           - High contrast #ffffff text color for both badges.
           - Responsive text: "Sync Error ↻" when box width >= 80, "↻" when box width < 80.
           - Clicking syncErrorMouseArea invokes controller.retryRegionSync(region_id).
           - Selection manipulator displays selectionRetryAffordance (z: 20) when selectedRegion.sync_status === "sync_failed".
           - Clicking selectionRetryMouseArea invokes controller.retryRegionSync.
        """
        def _to_hex(val) -> str:
            if hasattr(val, "name"):
                return val.name().lower()
            return str(val).lower()

        r_failed_wide = VisualRegionDTO(
            id=40,
            region_id="reg-sync-fail",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNC_FAILED.value,
            effective_bbox=BoundingBox(100, 100, 200, 300),
            detected_bbox=BoundingBox(100, 100, 200, 300),
            reviewed_bbox=None,
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_failed_narrow = VisualRegionDTO(
            id=42,
            region_id="reg-sync-fail-narrow",
            job_id=1,
            page_number=1,
            display_order=2,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNC_FAILED.value,
            effective_bbox=BoundingBox(100, 100, 150, 130),
            detected_bbox=BoundingBox(100, 100, 150, 130),
            reviewed_bbox=None,
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_synced = VisualRegionDTO(
            id=41,
            region_id="reg-sync-ok",
            job_id=1,
            page_number=1,
            display_order=3,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(300, 300, 400, 400),
            detected_bbox=BoundingBox(300, 300, 400, 400),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="art/p1_r2.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )

        self.mock_service.get_active_page_regions.return_value = [r_failed_wide, r_failed_narrow, r_synced]
        self.controller.loadPageSync(1, 1)
        self.app.processEvents()

        # Canvas delegates
        badge_failed = find_quick_item(self.view_root, "syncErrorBadge_reg-sync-fail")
        badge_narrow = find_quick_item(self.view_root, "syncErrorBadge_reg-sync-fail-narrow")
        badge_synced = find_quick_item(self.view_root, "syncErrorBadge_reg-sync-ok")

        self.assertIsNotNone(badge_failed, "syncErrorBadge must exist for wide failed region")
        self.assertIsNotNone(badge_narrow, "syncErrorBadge must exist for narrow failed region")
        self.assertIsNotNone(badge_synced, "syncErrorBadge must exist for synced region")

        self.assertTrue(badge_failed.property("visible"), "syncErrorBadge must be visible when sync_status == 'sync_failed'")
        self.assertTrue(badge_narrow.property("visible"), "syncErrorBadge must be visible on narrow failed region")
        self.assertFalse(badge_synced.property("visible"), "syncErrorBadge must be hidden when sync_status == 'synced'")

        # Verify high contrast text color #ffffff and responsive label
        text_failed = find_quick_item(badge_failed, "syncErrorText_reg-sync-fail")
        text_narrow = find_quick_item(badge_narrow, "syncErrorText_reg-sync-fail-narrow")
        self.assertIsNotNone(text_failed)
        self.assertIsNotNone(text_narrow)
        self.assertEqual(_to_hex(text_failed.property("color")), "#ffffff", "Sync error text color must be #ffffff for contrast")
        self.assertEqual(text_failed.property("text"), "Sync Error ↻", "Wide box must show full Sync Error ↻")
        self.assertEqual(text_narrow.property("text"), "↻", "Narrow box must show compact ↻")

        # Click retry on unselected failed badge via retryRequested signal
        mouse_area_failed = find_quick_item(self.view_root, "syncErrorMouseArea_reg-sync-fail")
        self.assertIsNotNone(mouse_area_failed)

        mock_apply_res = MagicMock()
        mock_apply_res.success = True
        mock_apply_res.artifact_version = 1
        mock_apply_res.artifact_uri = "art/p1_r1.png"
        self.mock_pub_service.publish_region_review.return_value = mock_apply_res
        self.mock_pub_service.publish_region_review.reset_mock()

        badge_failed.retryRequested.emit()
        self.controller.wait_for_apply()
        self.app.processEvents()
        self.mock_pub_service.publish_region_review.assert_called_with(job_id=1, region_id="reg-sync-fail")

        # Now select failed region: selectionManipulator must show selectionRetryAffordance with z=20
        self.controller.selectRegion("reg-sync-fail")
        self.app.processEvents()

        sel_retry = find_quick_item(self.view_root, "selectionRetryAffordance")
        self.assertIsNotNone(sel_retry, "selectionRetryAffordance must exist on selectionManipulator")
        self.assertTrue(sel_retry.property("visible"))
        self.assertEqual(sel_retry.property("z"), 20, "selectionRetryAffordance must have z=20 (above drag area and handles)")

        sel_retry_text = find_quick_item(sel_retry, "selectionRetryText")
        self.assertIsNotNone(sel_retry_text)
        self.assertEqual(_to_hex(sel_retry_text.property("color")), "#ffffff", "Selection retry text color must be #ffffff")

        sel_retry_mouse = find_quick_item(self.view_root, "selectionRetryMouseArea")
        self.assertIsNotNone(sel_retry_mouse)

        self.mock_pub_service.publish_region_review.reset_mock()
        sel_retry.retryRequested.emit()
        self.controller.wait_for_apply()
        self.app.processEvents()
        self.mock_pub_service.publish_region_review.assert_called_with(job_id=1, region_id="reg-sync-fail")

        # Select synced region: selectionRetryAffordance must be hidden
        self.controller.selectRegion("reg-sync-ok")
        self.app.processEvents()
        self.assertFalse(sel_retry.property("visible"), "selectionRetryAffordance must be hidden for synced region")

    def test_inflight_sync_indicators_and_pending_affordances(self):
        """
        11. Verifies in-flight sync indicators for pending_initial_crop and dirty_recrop_required:
           - syncPendingBadge visible on canvas delegates when pending_initial_crop or dirty_recrop_required.
           - Text color is #ffffff and label is responsive.
           - selectionPendingAffordance (z: 20) visible on selectionManipulator when selected region is pending.
           - selectionPendingAffordance hidden when selected region is synced.
        """
        def _to_hex(val) -> str:
            if hasattr(val, "name"):
                return val.name().lower()
            return str(val).lower()

        r_pending_initial = VisualRegionDTO(
            id=50,
            region_id="reg-pending-init",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.USER_MANUAL.value,
            review_status=ReviewStatus.MANUAL.value,
            sync_status=SyncStatus.PENDING_INITIAL_CROP.value,
            effective_bbox=BoundingBox(50, 50, 200, 200),
            detected_bbox=None,
            reviewed_bbox=BoundingBox(50, 50, 200, 200),
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_dirty_recrop = VisualRegionDTO(
            id=51,
            region_id="reg-dirty-recrop",
            job_id=1,
            page_number=1,
            display_order=2,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.MODIFIED.value,
            sync_status=SyncStatus.DIRTY_RECROP_REQUIRED.value,
            effective_bbox=BoundingBox(250, 250, 400, 400),
            detected_bbox=BoundingBox(250, 250, 350, 350),
            reviewed_bbox=BoundingBox(250, 250, 400, 400),
            active_artifact_version=1,
            active_artifact_uri="art/p1_r2.png",
            is_modified=True,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        r_synced = VisualRegionDTO(
            id=52,
            region_id="reg-synced-clean",
            job_id=1,
            page_number=1,
            display_order=3,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.ACCEPTED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(450, 450, 550, 550),
            detected_bbox=BoundingBox(450, 450, 550, 550),
            reviewed_bbox=BoundingBox(450, 450, 550, 550),
            active_artifact_version=2,
            active_artifact_uri="art/p1_r3.png",
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )

        self.mock_service.get_active_page_regions.return_value = [
            r_pending_initial, r_dirty_recrop, r_synced
        ]
        self.controller.loadPageSync(1, 1)
        self.app.processEvents()

        # Canvas delegates for pending indicators
        badge_init = find_quick_item(self.view_root, "syncPendingBadge_reg-pending-init")
        badge_dirty = find_quick_item(self.view_root, "syncPendingBadge_reg-dirty-recrop")
        badge_clean = find_quick_item(self.view_root, "syncPendingBadge_reg-synced-clean")

        self.assertIsNotNone(badge_init, "syncPendingBadge must exist for pending_initial_crop")
        self.assertIsNotNone(badge_dirty, "syncPendingBadge must exist for dirty_recrop_required")
        self.assertIsNotNone(badge_clean, "syncPendingBadge must exist for synced region")

        self.assertTrue(badge_init.property("visible"), "Visible when pending_initial_crop")
        self.assertTrue(badge_dirty.property("visible"), "Visible when dirty_recrop_required")
        self.assertFalse(badge_clean.property("visible"), "Hidden when synced")

        # Text color #ffffff
        text_init = find_quick_item(badge_init, "syncPendingText_reg-pending-init")
        self.assertIsNotNone(text_init)
        self.assertEqual(_to_hex(text_init.property("color")), "#ffffff")
        self.assertEqual(text_init.property("text"), "Syncing...")

        # Select dirty region -> selectionPendingAffordance on manipulator must be visible with z=20
        self.controller.selectRegion("reg-dirty-recrop")
        self.app.processEvents()

        sel_pending = find_quick_item(self.view_root, "selectionPendingAffordance")
        self.assertIsNotNone(sel_pending)
        self.assertTrue(sel_pending.property("visible"))
        self.assertEqual(sel_pending.property("z"), 20)

        sel_pending_text = find_quick_item(sel_pending, "selectionPendingText")
        self.assertIsNotNone(sel_pending_text)
        self.assertEqual(_to_hex(sel_pending_text.property("color")), "#ffffff")

        # Select clean region -> selectionPendingAffordance must be hidden
        self.controller.selectRegion("reg-synced-clean")
        self.app.processEvents()
        self.assertFalse(sel_pending.property("visible"))

    def test_region_context_menu_coexistence_and_right_click_interactions(self):
        """
        12. Verifies TICK-P04B RegionContextMenu presence and interaction coexistence:
            - regionContextMenu exists and is bound to the document viewer controller.
            - bodyDragArea accepts both LeftButton and RightButton.
            - manipulatorDragArea accepts both LeftButton and RightButton.
            - Left-click drag on region body remains functional without interference.
            - Right-click on emptyArea initiates canvas panning (isPanDragging=True) without opening context menu.
        """
        overlay = find_quick_item(self.view_root, "regionOverlay")
        self.assertIsNotNone(overlay)

        menu = self.view_root.findChild(object, "regionContextMenu")
        self.assertIsNotNone(menu)
        self.assertEqual(menu.property("controller"), self.controller)

        # Region delegate bodyDragArea acceptedButtons
        body_area = find_quick_item(self.view_root, "bodyDragArea_test-r1")
        self.assertIsNotNone(body_area)
        accepted_body = body_area.property("acceptedButtons")
        self.assertTrue(bool(accepted_body & Qt.LeftButton), "bodyDragArea must accept LeftButton")
        self.assertTrue(bool(accepted_body & Qt.RightButton), "bodyDragArea must accept RightButton")

        # Select region to activate manipulator
        self.controller.selectRegion("test-r1")
        self.app.processEvents()

        manip_area = find_quick_item(self.view_root, "manipulatorDragArea")
        self.assertIsNotNone(manip_area)
        accepted_manip = manip_area.property("acceptedButtons")
        self.assertTrue(bool(accepted_manip & Qt.LeftButton), "manipulatorDragArea must accept LeftButton")
        self.assertTrue(bool(accepted_manip & Qt.RightButton), "manipulatorDragArea must accept RightButton")

        # Left-click drag on bodyDragArea still initiates dragging state
        self.controller.clearSelection()
        self.app.processEvents()
        self.assertEqual(self.controller.editorState, "idle")

        left_press = QMouseEvent(QEvent.MouseButtonPress, QPointF(10.0, 10.0), QPointF(10.0, 10.0), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(body_area, left_press)
        self.app.processEvents()
        self.assertEqual(self.controller.editorState, "dragging")
        self.controller.cancelDrag()
        self.app.processEvents()

        # Right-click on emptyArea initiates canvas pan drag and does not open context menu
        empty_area = find_quick_item(self.view_root, "emptyArea")
        self.assertIsNotNone(empty_area)
        self.assertFalse(empty_area.property("isPanDragging"))
        self.assertFalse(menu.property("opened"))

        right_press_empty = QMouseEvent(QEvent.MouseButtonPress, QPointF(500.0, 400.0), QPointF(500.0, 400.0), Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        self.app.sendEvent(empty_area, right_press_empty)
        self.app.processEvents()

        self.assertTrue(empty_area.property("isPanDragging"), "Right click on emptyArea must engage isPanDragging")
        self.assertFalse(menu.property("opened"), "Right click on emptyArea must not open context menu")

        # Right-click on region selects region and routes to showForRegion establishing context target
        right_press_body = QMouseEvent(QEvent.MouseButtonPress, QPointF(15.0, 15.0), QPointF(15.0, 15.0), Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        self.app.sendEvent(body_area, right_press_body)
        self.app.processEvents()
        self.assertEqual(self.controller.selectedRegionId, "test-r1")
        self.assertEqual(menu.property("contextRegionId"), "test-r1")
