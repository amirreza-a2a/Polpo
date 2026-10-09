# ============================================================
#  tests/unit/test_region_context_menu_qml.py
#  Visual Region Context Menu & Right-Click Interaction Tests
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QQmlApplicationEngine,
    QUrl,
    QObject,
    QMetaObject,
    Signal,
    Slot,
    Property,
    QPointF,
    QMouseEvent,
    QEvent,
    Qt,
)


def find_quick_item(root, object_name: str):
    """Finds a QQuickItem by objectName traversing visual childItems and QObject children."""
    from interfaces.desktop.qt_compat import QQuickItem

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
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus
from application.dto.document_viewer_dto import PageRasterDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.app import wire_review_workspace_sync
from core.domain.visual_token import VisualOccurrenceToken, serialize_canonical_token


class MockMenuController(QObject):
    """QObject mock for RegionContextMenu unit testing."""
    pageChanged = Signal()
    regionsChanged = Signal()
    regionDeleted = Signal(str)
    regionUpdated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executed_action = ""
        self.executed_target = ""
        self._active_regions = [{"region_id": "reg-A"}, {"region_id": "reg-B"}]
        self._selected_region_id = ""

    @Property("QVariant", notify=regionsChanged)
    def activeRegions(self):
        return self._active_regions

    @Property(str)
    def selectedRegionId(self):
        return self._selected_region_id

    @selectedRegionId.setter
    def selectedRegionId(self, val):
        self._selected_region_id = val

    @Slot(str, result="QVariant")
    def getRegionContextActions(self, target_id: str):
        if target_id == "reg-custom":
            return [
                {
                    "action_id": "convert_to_tikz",
                    "label": "Convert to TikZ",
                    "group": "latex",
                    "order": 15,
                    "is_enabled": True,
                    "is_visible": True,
                    "disabled_reason": "",
                }
            ]
        return [
            {
                "action_id": "insert_markdown",
                "label": "Insert into Markdown",
                "group": "markdown",
                "order": 10,
                "is_enabled": True,
                "is_visible": True,
                "disabled_reason": "",
            },
            {
                "action_id": "copy_token",
                "label": "Copy Markdown Token",
                "group": "markdown",
                "order": 20,
                "is_enabled": False,
                "is_visible": True,
                "disabled_reason": "Not synced",
            },
            {
                "action_id": "copy_path",
                "label": "Copy Image Path",
                "group": "clipboard",
                "order": 30,
                "is_enabled": True,
                "is_visible": True,
                "disabled_reason": "",
            },
            {
                "action_id": "copy_bbox",
                "label": "Copy Bounding Box",
                "group": "clipboard",
                "order": 40,
                "is_enabled": True,
                "is_visible": True,
                "disabled_reason": "",
            },
            {
                "action_id": "copy_id",
                "label": "Copy Region ID",
                "group": "clipboard",
                "order": 50,
                "is_enabled": True,
                "is_visible": True,
                "disabled_reason": "",
            },
            {
                "action_id": "accept_region",
                "label": "Accept Region",
                "group": "review",
                "order": 60,
                "is_enabled": True,
                "is_visible": True,
                "disabled_reason": "",
            },
            {
                "action_id": "retry_sync",
                "label": "Retry Sync",
                "group": "diagnostics",
                "order": 90,
                "is_enabled": True,
                "is_visible": False,
                "disabled_reason": "",
            },
        ]

    @Slot(str, str, result=bool)
    def executeRegionAction(self, action_id: str, target_id: str):
        self.executed_action = action_id
        self.executed_target = target_id
        return True


class TestRegionContextMenuQml(unittest.TestCase):
    """
    Unit tests for RegionContextMenu.qml and its integration with BoundingBoxOverlay.qml,
    DocumentViewerController, and ReviewWorkspaceView.qml.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.engine = QQmlApplicationEngine()
        self.qml_warnings = []
        self.engine.warnings.connect(self._on_qml_warning)

    def tearDown(self):
        self.engine.deleteLater()
        self.app.processEvents()

    def _on_qml_warning(self, warnings):
        for w in warnings:
            self.qml_warnings.append(w.toString())

    def _create_test_window_with_menu(self, controller=None):
        """Creates a test ApplicationWindow containing RegionContextMenu."""
        qml_code = """
        import QtQuick 2.15
        import QtQuick.Controls 2.15
        import "interfaces/desktop/qml/components"

        ApplicationWindow {
            id: testWin
            objectName: "testWin"
            width: 800
            height: 600
            visible: true

            Component.onCompleted: {
                Overlay.overlay.x = 0;
                Overlay.overlay.y = 0;
                Overlay.overlay.width = width;
                Overlay.overlay.height = height;
            }

            Rectangle {
                id: testBox
                objectName: "testBox"
                x: 100
                y: 120
                width: 150
                height: 100
            }

            RegionContextMenu {
                id: testMenu
                objectName: "testMenu"
            }
        }
        """
        repo_root = Path(__file__).parent.parent.parent
        self.engine.rootContext().setContextProperty("testController", controller)
        self.engine.loadData(qml_code.encode("utf-8"), QUrl.fromLocalFile(str(repo_root / "test.qml")))
        self.app.processEvents()

        root = self.engine.rootObjects()[0]
        root.show()
        self.app.processEvents()
        menu = root.findChild(QObject, "testMenu")
        if controller:
            menu.setProperty("controller", controller)
        return root, menu

    # =========================================================================
    # 1. Menu Rendering & Item Structure Tests
    # =========================================================================

    def test_menu_renders_action_descriptors_with_correct_groups_and_separators(self):
        """Verifies that descriptors become MenuItems, ordered by 'order', separated by group."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        # Open menu for target
        menu.showForRegion(box, 10, 10, "reg-101")
        self.app.processEvents()

        # 6 visible items across 3 distinct groups (markdown: 2, clipboard: 3, review: 1)
        # Separator between markdown and clipboard, and separator between clipboard and review = 2 separators
        # Total items in menu: 6 + 2 = 8
        count = menu.property("count")
        self.assertEqual(count, 8)

        # Verify insert_markdown
        item_insert = menu.findChild(QObject, "menuItem_insert_markdown")
        self.assertIsNotNone(item_insert)
        self.assertEqual(item_insert.property("text"), "Insert into Markdown")
        self.assertTrue(item_insert.property("enabled"))
        self.assertEqual(item_insert.property("disabledReason"), "")

        # Verify copy_token disabled with reason and tooltip binding
        item_token = menu.findChild(QObject, "menuItem_copy_token")
        self.assertIsNotNone(item_token)
        self.assertEqual(item_token.property("text"), "Copy Markdown Token")
        self.assertFalse(item_token.property("enabled"))
        self.assertEqual(item_token.property("disabledReason"), "Not synced")
        self.assertEqual(item_token.property("tipText"), "Not synced")

        # Verify disabled reason tooltip becomes visible on hover
        import time
        self.assertFalse(item_token.property("tipVisible"))
        hover_pt = QPointF(10.0, 10.0)
        hover_ev = QMouseEvent(QEvent.MouseMove, hover_pt, hover_pt, Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        self.app.sendEvent(item_token, hover_ev)
        self.app.processEvents()
        time.sleep(0.35)
        self.app.processEvents()
        self.assertTrue(item_token.property("tipVisible"))

        # Verify hidden action is not rendered
        item_diag = menu.findChild(QObject, "menuItem_retry_sync")
        self.assertIsNone(item_diag)

        # Trigger insert_markdown
        QMetaObject.invokeMethod(item_insert, "triggered")
        self.app.processEvents()
        self.assertEqual(mock_ctrl.executed_action, "insert_markdown")
        self.assertEqual(mock_ctrl.executed_target, "reg-101")

        # Close menu
        menu.close()
        self.app.processEvents()
        self.assertEqual(menu.property("count"), 0)
        self.assertEqual(menu.property("contextRegionId"), "")

    def test_disabled_action_cannot_be_triggered_or_executed(self):
        """Verifies that triggering a disabled menuItem does not execute action on controller."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        menu.showForRegion(box, 10, 10, "reg-disabled-test")
        self.app.processEvents()

        item_token = menu.findChild(QObject, "menuItem_copy_token")
        self.assertIsNotNone(item_token)
        self.assertFalse(item_token.property("enabled"))

        QMetaObject.invokeMethod(item_token, "triggered")
        self.app.processEvents()
        self.assertEqual(mock_ctrl.executed_action, "")

    def test_future_unknown_action_descriptor_renders_without_qml_changes(self):
        """Verifies that adding a new action descriptor in Python automatically renders in QML."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        menu.showForRegion(box, 5, 5, "reg-custom")
        self.app.processEvents()

        item_tikz = menu.findChild(QObject, "menuItem_convert_to_tikz")
        self.assertIsNotNone(item_tikz)
        self.assertEqual(item_tikz.property("text"), "Convert to TikZ")
        self.assertTrue(item_tikz.property("enabled"))

        QMetaObject.invokeMethod(item_tikz, "triggered")
        self.app.processEvents()
        self.assertEqual(mock_ctrl.executed_action, "convert_to_tikz")
        self.assertEqual(mock_ctrl.executed_target, "reg-custom")

    def test_repeated_open_close_cycles_do_not_leak_or_accumulate_warnings(self):
        """Verifies clean dynamic item lifecycle with zero engine warnings across repeated open/close cycles."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        for _ in range(5):
            menu.showForRegion(box, 10, 10, "reg-cycle")
            self.app.processEvents()
            self.assertEqual(menu.property("count"), 8)
            menu.close()
            self.app.processEvents()
            self.assertEqual(menu.property("count"), 0)

        # Check warnings
        critical_warnings = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w or "graphics scene" in w
        ]
        self.assertEqual(critical_warnings, [])

    # =========================================================================
    # 2. Target Freeze & Safe Invalidation Tests
    # =========================================================================

    def test_target_freeze_preserves_context_target_across_selection_changes(self):
        """Verifies that contextRegionId is frozen upon opening and not mutated if selection changes."""
        mock_ctrl = MockMenuController()
        mock_ctrl.selectedRegionId = "reg-INITIAL"

        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        # Open menu targeting reg-FROZEN
        menu.showForRegion(box, 10, 10, "reg-FROZEN")
        self.app.processEvents()
        self.assertEqual(menu.property("contextRegionId"), "reg-FROZEN")

        # Background selection changes to another region
        mock_ctrl.selectedRegionId = "reg-DIFFERENT"
        self.app.processEvents()

        # Trigger action: must still execute against reg-FROZEN
        item = menu.findChild(QObject, "menuItem_copy_id")
        self.assertIsNotNone(item)
        QMetaObject.invokeMethod(item, "triggered")
        self.app.processEvents()

        self.assertEqual(mock_ctrl.executed_target, "reg-FROZEN")

    def test_menu_dismisses_on_page_changed_signal(self):
        """Verifies that page navigation automatically dismisses the popup and clears target."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        menu.showForRegion(box, 10, 10, "reg-page-test")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))

        # Emit pageChanged
        mock_ctrl.pageChanged.emit()
        self.app.processEvents()

        self.assertFalse(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "")

    def test_menu_dismisses_when_target_region_deleted_or_removed(self):
        """Verifies that target region deletion or removal dismisses the menu."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        # Open targeting reg-A
        menu.showForRegion(box, 10, 10, "reg-A")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))

        # Emit regionDeleted for an unrelated region: menu stays open
        mock_ctrl.regionDeleted.emit("reg-B")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))

        # Emit regionDeleted for targeted region reg-A: menu dismisses
        mock_ctrl.regionDeleted.emit("reg-A")
        self.app.processEvents()
        self.assertFalse(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "")

        # Reopen for reg-B
        menu.showForRegion(box, 10, 10, "reg-B")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))

        # Remove reg-B from activeRegions and emit regionsChanged
        mock_ctrl._active_regions = [{"region_id": "reg-C"}]
        mock_ctrl.regionsChanged.emit()
        self.app.processEvents()
        self.assertFalse(menu.property("opened"))

    def test_menu_dismisses_when_target_region_updated(self):
        """Verifies that regionUpdated for the target region dismisses the menu, while unrelated updates do not."""
        mock_ctrl = MockMenuController()
        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        menu.showForRegion(box, 10, 10, "reg-A")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))

        # Emit regionUpdated for unrelated region: menu stays open
        mock_ctrl.regionUpdated.emit("reg-B")
        self.app.processEvents()
        self.assertTrue(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "reg-A")

        # Emit regionUpdated for targeted region reg-A: menu dismisses
        mock_ctrl.regionUpdated.emit("reg-A")
        self.app.processEvents()
        self.assertFalse(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "")

    def test_show_for_region_with_empty_or_unknown_region_resets_context_target(self):
        """Verifies that showForRegion clears contextRegionId and does not open when region is invalid or has no actions."""
        mock_ctrl = MockMenuController()
        mock_ctrl.getRegionContextActions = MagicMock(return_value=[])

        win, menu = self._create_test_window_with_menu(mock_ctrl)
        box = win.findChild(QObject, "testBox")

        # Empty region
        menu.showForRegion(box, 10, 10, "")
        self.app.processEvents()
        self.assertFalse(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "")

        # Nonexistent/empty actions region
        menu.showForRegion(box, 10, 10, "reg-unknown")
        self.app.processEvents()
        self.assertFalse(menu.property("opened"))
        self.assertEqual(menu.property("contextRegionId"), "")

    # =========================================================================
    # 3. Coordinate Mapping under Zoom and Pan
    # =========================================================================

    def test_coordinate_mapping_under_zoom_and_pan(self):
        """Verifies that menu positioning correctly tracks the pointer position under zoom != 1 and pan != 0."""
        qml_code = """
        import QtQuick 2.15
        import QtQuick.Controls 2.15
        import "interfaces/desktop/qml/components"

        ApplicationWindow {
            id: win
            width: 1000
            height: 800
            visible: true

            Component.onCompleted: {
                Overlay.overlay.x = 0;
                Overlay.overlay.y = 0;
                Overlay.overlay.width = width;
                Overlay.overlay.height = height;
            }

            Item {
                id: scene
                x: -120
                y: -60
                scale: 1.5
                transformOrigin: Item.TopLeft

                Rectangle {
                    id: targetBox
                    objectName: "targetBox"
                    x: 180
                    y: 140
                    width: 100
                    height: 80
                }
            }

            RegionContextMenu {
                id: coordMenu
                objectName: "coordMenu"
            }

            function openAt(target, mx, my) {
                coordMenu.showForRegion(target, mx, my, "test-reg");
            }
        }
        """
        repo_root = Path(__file__).parent.parent.parent
        mock_ctrl = MockMenuController()

        self.engine.loadData(qml_code.encode("utf-8"), QUrl.fromLocalFile(str(repo_root / "test_coord.qml")))
        self.app.processEvents()
        win = self.engine.rootObjects()[0]
        win.show()
        self.app.processEvents()

        menu = win.findChild(QObject, "coordMenu")
        menu.setProperty("controller", mock_ctrl)
        box = win.findChild(QObject, "targetBox")

        # Open at (20, 25) in targetBox coordinates
        win.openAt(box, 20, 25)
        self.app.processEvents()

        self.assertTrue(menu.property("opened"))
        # Expected in window:
        # Pt in scene: (180 + 20, 140 + 25) = (200, 165)
        # Scaled by 1.5: (300, 247.5)
        # Translated by (-120, -60): (180.0, 187.5)
        expected_x = 180.0
        expected_y = 187.5

        menu_parent = menu.property("parent")
        actual_in_win = menu_parent.mapToItem(None, menu.property("x"), menu.property("y"))
        self.assertAlmostEqual(actual_in_win.x(), expected_x, delta=1.0)
        self.assertAlmostEqual(actual_in_win.y(), expected_y, delta=1.0)

    # =========================================================================
    # 4. ReviewWorkspaceView Tab Switching on Markdown Navigation
    # =========================================================================

    def test_markdown_navigation_switches_preview_tab_to_source_editor(self):
        """Verifies that requestNavigateToPosition automatically switches rightPane from Preview (0) to Editor (1)."""
        view_path = (
            Path(__file__).parent.parent.parent
            / "interfaces"
            / "desktop"
            / "qml"
            / "views"
            / "ReviewWorkspaceView.qml"
        )
        self.assertTrue(view_path.exists())

        doc_service = MagicMock(spec=DocumentViewerService)
        doc_service.get_active_page_regions.return_value = []
        doc_service.get_page_raster.return_value = PageRasterDTO(
            job_id=5, page_number=1, total_pages=1, raster_width=100, raster_height=100, image_uri="", dpi=150
        )
        doc_ctrl = DocumentViewerController(viewer_service=doc_service)
        doc_ctrl.loadPageSync(5, 1)

        editor_service = MagicMock()
        editor_ctrl = MarkdownEditorController(editor_service=editor_service)
        editor_ctrl._active_job_id = 5

        viewer_service = MagicMock()
        viewer_service.get_canonical_document.return_value = MagicMock(version=1, raw_text="# Hello", occurrences={})
        viewer_ctrl = MarkdownViewerController(viewer_service=viewer_service)
        viewer_ctrl._active_job_id = 5

        sync_coord = wire_review_workspace_sync(doc_ctrl, viewer_ctrl, editor_ctrl)

        try:
            self.engine.rootContext().setContextProperty("documentViewerController", doc_ctrl)
            self.engine.rootContext().setContextProperty("markdownViewerController", viewer_ctrl)
            self.engine.rootContext().setContextProperty("markdownEditorController", editor_ctrl)
            self.engine.rootContext().setContextProperty("reviewWorkspaceSyncCoordinator", sync_coord)
            self.engine.rootContext().setContextProperty("exportController", None)

            self.engine.load(QUrl.fromLocalFile(str(view_path)))
            self.app.processEvents()

            root = self.engine.rootObjects()[0]
            right_pane = root.findChild(QObject, "markdownPane")
            self.assertIsNotNone(right_pane)

            # Initially tab 0 (Rendered Preview)
            self.assertEqual(right_pane.property("currentTab"), 0)

            # Emit requestNavigateToPosition from editor for matching job
            editor_ctrl.requestNavigateToPosition.emit(10)
            self.app.processEvents()

            # rightPane should now be on tab 1 (Source Editor)
            self.assertEqual(right_pane.property("currentTab"), 1)

            # Emit again while on tab 1: stays on tab 1
            editor_ctrl.requestNavigateToPosition.emit(20)
            self.app.processEvents()
            self.assertEqual(right_pane.property("currentTab"), 1)

            # Switch back to tab 0 (Preview)
            right_pane.setTab(0)
            self.app.processEvents()
            self.assertEqual(right_pane.property("currentTab"), 0)

            # Navigation for a different job does NOT switch tab
            editor_ctrl._active_job_id = 99
            editor_ctrl.requestNavigateToPosition.emit(30)
            self.app.processEvents()
            self.assertEqual(right_pane.property("currentTab"), 0)

            # Restore active job and switch to tab 2 (Dual Pane)
            editor_ctrl._active_job_id = 5
            right_pane.setTab(2)
            self.app.processEvents()
            self.assertEqual(right_pane.property("currentTab"), 2)

            # Navigation while on tab 2 preserves tab 2 (Dual Pane)
            editor_ctrl.requestNavigateToPosition.emit(40)
            self.app.processEvents()
            self.assertEqual(right_pane.property("currentTab"), 2)
        finally:
            doc_ctrl.shutdown()
            editor_ctrl.shutdown()
            viewer_ctrl.shutdown()

    # =========================================================================
    # 5. Right-Click Interaction in BoundingBoxOverlay
    # =========================================================================

    def test_right_click_on_region_selects_and_opens_menu_for_target(self):
        """Verifies that right-clicking boxRect selects the region and opens context menu for that region."""
        qml_code = """
        import QtQuick 2.15
        import QtQuick.Controls 2.15
        import "interfaces/desktop/qml/components"

        ApplicationWindow {
            id: win
            width: 800
            height: 600
            visible: true

            Component.onCompleted: {
                Overlay.overlay.x = 0;
                Overlay.overlay.y = 0;
                Overlay.overlay.width = width;
                Overlay.overlay.height = height;
            }

            BoundingBoxOverlay {
                id: overlay
                objectName: "regionOverlay"
                anchors.fill: parent
                controller: testDocCtrl
            }
        }
        """
        repo_root = Path(__file__).parent.parent.parent

        doc_service = MagicMock(spec=DocumentViewerService)
        reg_id = "01234567-89ab-4cde-8f01-23456789abcd"
        r = VisualRegionDTO(
            id=1, region_id=reg_id, job_id=1, page_number=1, display_order=1,
            origin=RegionOrigin.AI_DETECTED.value, review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(100, 100, 400, 400),
            detected_bbox=BoundingBox(100, 100, 400, 400),
            reviewed_bbox=None, active_artifact_version=1, active_artifact_uri="art/1.png",
            is_modified=False, is_deleted=False, created_at=None, updated_at=None
        )
        doc_service.get_active_page_regions.return_value = [r]
        doc_service.get_page_raster.return_value = PageRasterDTO(
            job_id=1, page_number=1, total_pages=1, raster_width=1000, raster_height=1000,
            image_uri="", dpi=150
        )
        doc_ctrl = DocumentViewerController(viewer_service=doc_service)
        doc_ctrl.loadPageSync(1, 1)

        try:
            self.engine.rootContext().setContextProperty("testDocCtrl", doc_ctrl)
            self.engine.loadData(qml_code.encode("utf-8"), QUrl.fromLocalFile(str(repo_root / "test_overlay.qml")))
            self.app.processEvents()

            win = self.engine.rootObjects()[0]
            win.show()
            self.app.processEvents()

            overlay = win.findChild(QObject, "regionOverlay")
            self.assertIsNotNone(overlay)

            menu = overlay.findChild(QObject, "regionContextMenu")
            self.assertIsNotNone(menu)

            # Initially no selection and menu closed
            self.assertEqual(doc_ctrl.selectedRegionId, "")
            self.assertFalse(menu.property("opened"))

            # Find bodyDragArea using visual traversal
            body_area = find_quick_item(overlay, f"bodyDragArea_{reg_id}")
            self.assertIsNotNone(body_area)

            # Send right button press
            local_pt = QPointF(20.0, 20.0)
            ev = QMouseEvent(QEvent.MouseButtonPress, local_pt, local_pt, Qt.RightButton, Qt.RightButton, Qt.NoModifier)
            self.app.sendEvent(body_area, ev)
            self.app.processEvents()

            # Region selected and menu opened targeting reg_id
            self.assertEqual(doc_ctrl.selectedRegionId, reg_id)
            self.assertEqual(menu.property("contextRegionId"), reg_id)
            self.assertTrue(menu.property("opened"))

            # Clean up
            menu.close()
            self.app.processEvents()
        finally:
            doc_ctrl.shutdown()

    def test_right_click_on_selection_manipulator_targets_selected_region(self):
        """Verifies that right-clicking selectionManipulator opens context menu targeting selected region."""
        qml_code = """
        import QtQuick 2.15
        import QtQuick.Controls 2.15
        import "interfaces/desktop/qml/components"

        ApplicationWindow {
            id: win
            width: 800
            height: 600
            visible: true

            Component.onCompleted: {
                Overlay.overlay.x = 0;
                Overlay.overlay.y = 0;
                Overlay.overlay.width = width;
                Overlay.overlay.height = height;
            }

            BoundingBoxOverlay {
                id: overlay
                objectName: "regionOverlay"
                anchors.fill: parent
                controller: testDocCtrl
            }
        }
        """
        repo_root = Path(__file__).parent.parent.parent

        doc_service = MagicMock(spec=DocumentViewerService)
        reg_id = "01234567-89ab-4cde-8f01-23456789abcd"
        r = VisualRegionDTO(
            id=1, region_id=reg_id, job_id=1, page_number=1, display_order=1,
            origin=RegionOrigin.AI_DETECTED.value, review_status=ReviewStatus.UNREVIEWED.value,
            sync_status=SyncStatus.SYNCED.value,
            effective_bbox=BoundingBox(100, 100, 400, 400),
            detected_bbox=BoundingBox(100, 100, 400, 400),
            reviewed_bbox=None, active_artifact_version=1, active_artifact_uri="art/1.png",
            is_modified=False, is_deleted=False, created_at=None, updated_at=None
        )
        doc_service.get_active_page_regions.return_value = [r]
        doc_service.get_page_raster.return_value = PageRasterDTO(
            job_id=1, page_number=1, total_pages=1, raster_width=1000, raster_height=1000,
            image_uri="", dpi=150
        )
        doc_ctrl = DocumentViewerController(viewer_service=doc_service)
        doc_ctrl.loadPageSync(1, 1)

        try:
            self.engine.rootContext().setContextProperty("testDocCtrl", doc_ctrl)
            self.engine.loadData(qml_code.encode("utf-8"), QUrl.fromLocalFile(str(repo_root / "test_manip.qml")))
            self.app.processEvents()

            win = self.engine.rootObjects()[0]
            win.show()
            self.app.processEvents()

            overlay = win.findChild(QObject, "regionOverlay")
            menu = overlay.findChild(QObject, "regionContextMenu")

            # Select the region
            doc_ctrl.selectRegion(reg_id)
            self.app.processEvents()

            manip_area = find_quick_item(overlay, "manipulatorDragArea")
            self.assertIsNotNone(manip_area)

            # Send right button press to manipulator
            local_pt = QPointF(15.0, 15.0)
            ev = QMouseEvent(QEvent.MouseButtonPress, local_pt, local_pt, Qt.RightButton, Qt.RightButton, Qt.NoModifier)
            self.app.sendEvent(manip_area, ev)
            self.app.processEvents()

            self.assertEqual(menu.property("contextRegionId"), reg_id)
            self.assertTrue(menu.property("opened"))

            menu.close()
            self.app.processEvents()
        finally:
            doc_ctrl.shutdown()

    def test_right_click_on_empty_canvas_preserves_canvas_panning_without_opening_menu(self):
        """Verifies that right-clicking empty canvas engages canvas pan without opening the context menu."""
        qml_code = """
        import QtQuick 2.15
        import QtQuick.Controls 2.15
        import "interfaces/desktop/qml/components"

        ApplicationWindow {
            id: win
            width: 800
            height: 600
            visible: true

            Component.onCompleted: {
                Overlay.overlay.x = 0;
                Overlay.overlay.y = 0;
                Overlay.overlay.width = width;
                Overlay.overlay.height = height;
            }

            BoundingBoxOverlay {
                id: overlay
                objectName: "regionOverlay"
                anchors.fill: parent
                controller: testDocCtrl
            }
        }
        """
        repo_root = Path(__file__).parent.parent.parent

        doc_service = MagicMock(spec=DocumentViewerService)
        doc_service.get_active_page_regions.return_value = []
        doc_service.get_page_raster.return_value = PageRasterDTO(
            job_id=1, page_number=1, total_pages=1, raster_width=1000, raster_height=1000,
            image_uri="", dpi=150
        )
        doc_ctrl = DocumentViewerController(viewer_service=doc_service)
        doc_ctrl.loadPageSync(1, 1)

        try:
            self.engine.rootContext().setContextProperty("testDocCtrl", doc_ctrl)
            self.engine.loadData(qml_code.encode("utf-8"), QUrl.fromLocalFile(str(repo_root / "test_empty.qml")))
            self.app.processEvents()

            win = self.engine.rootObjects()[0]
            win.show()
            self.app.processEvents()

            overlay = win.findChild(QObject, "regionOverlay")
            menu = overlay.findChild(QObject, "regionContextMenu")
            empty_area = find_quick_item(overlay, "emptyArea")
            self.assertIsNotNone(empty_area)

            # Send right button press to empty canvas
            local_pt = QPointF(50.0, 50.0)
            ev = QMouseEvent(QEvent.MouseButtonPress, local_pt, local_pt, Qt.RightButton, Qt.RightButton, Qt.NoModifier)
            self.app.sendEvent(empty_area, ev)
            self.app.processEvents()

            # Canvas pan is engaged (isPanDragging == true)
            self.assertTrue(empty_area.property("isPanDragging"))
            # Context menu is NOT opened
            self.assertFalse(menu.property("opened"))
            self.assertEqual(menu.property("contextRegionId"), "")
        finally:
            doc_ctrl.shutdown()
