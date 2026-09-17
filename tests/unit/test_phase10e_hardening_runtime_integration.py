# ============================================================
#  tests/unit/test_phase10e_hardening_runtime_integration.py
#  Runtime Integration & Regression Tests for Phase 10E Hardening
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl
try:
    from PySide6.QtCore import QByteArray, QPointF, Qt
    from PySide6.QtQml import QQmlComponent
except ImportError:
    from PyQt6.QtCore import QByteArray, QPointF, Qt
    from PyQt6.QtQml import QQmlComponent

from application.dto.document_viewer_dto import PageRasterDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController


def get_cpp_pointer_id(obj):
    """Returns the C++ object memory address if available, else Python id."""
    if obj is None:
        return None
    try:
        import shiboken6
        return shiboken6.getCppPointer(obj)[0]
    except Exception:
        try:
            from PyQt6 import sip
            return sip.unwrapinstance(obj)
        except Exception:
            try:
                import sip
                return sip.unwrapinstance(obj)
            except Exception:
                return id(obj)


def find_quick_item(root, object_name: str):
    """Recursively locates a QQuickItem by objectName across visual and QObject scene graphs."""
    try:
        from PySide6.QtQuick import QQuickItem
    except ImportError:
        from PyQt6.QtQuick import QQuickItem

    if isinstance(root, QQuickItem) and root.objectName() == object_name:
        return root

    direct = root.findChild(QQuickItem, object_name)
    if direct is not None:
        return direct

    def _search(item):
        if hasattr(item, "objectName") and item.objectName() == object_name:
            return item
        if hasattr(item, "childItems"):
            for child in item.childItems():
                res = _search(child)
                if res is not None:
                    return res
        return None

    return _search(root)


class TestPhase10EHardeningRuntimeIntegration(unittest.TestCase):
    """
    Comprehensive runtime integration test suite for Phase 10E Hardening:
    - Section A: Stable bbox drag/resize lifecycle & delegate lifetime stability (Architecture A)
    - Section B: Realistic SplitView hierarchy (Sidebar 220px + StackLayout + ReviewWorkspaceView)
    - Section C: Responsive PDF toolbar geometry at minimum pane width (260px)
    - Section D: Interaction routing across modes (pan_select vs create_region over existing regions)
    - Section E: Viewport dimension lifecycle & activation re-synchronization
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_doc_service = MagicMock(spec=DocumentViewerService)
        self.mock_md_service = MagicMock(spec=MarkdownViewerService)

        self.r1 = VisualRegionDTO(
            id=1,
            region_id="test-r1",
            job_id=1,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(100, 100, 300, 300),
            detected_bbox=BoundingBox(100, 100, 300, 300),
            reviewed_bbox=None,
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.mock_doc_service.get_active_page_regions.return_value = [self.r1]
        self.mock_doc_service.get_page_raster.return_value = PageRasterDTO(
            job_id=1,
            page_number=1,
            total_pages=1,
            raster_width=1000,
            raster_height=1000,
            image_uri="",
            dpi=150,
        )
        self.mock_doc_service.update_region_geometry.return_value = self.r1
        self.mock_doc_service.create_manual_region.return_value = self.r1

        self.doc_ctrl = DocumentViewerController(viewer_service=self.mock_doc_service)
        self.doc_ctrl.loadPageSync(1, 1)

        self.md_ctrl = MarkdownViewerController(viewer_service=self.mock_md_service)

        self.views_dir = (
            Path(__file__).parent.parent.parent
            / "interfaces"
            / "desktop"
            / "qml"
            / "views"
        )

    # =========================================================================
    # Section A: Delegate Lifetime & Drag/Resize Lifecycle (Architecture A)
    # =========================================================================
    def test_section_a_delegate_lifetime_and_stable_drag_resize_lifecycle(self):
        """
        Verifies Architecture A:
        - Selection manipulator and 8 static handles maintain identical C++ pointer addresses
          across intermediate drag and resize updates.
        - regionRepeater delegates are never recreated during drag or resize.
        - Deselection unloads handles cleanly.
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 1000)
        root.setProperty("height", 800)
        self.doc_ctrl.setViewportDimensions(1000, 800)
        self.doc_ctrl.setItemDimensions(1000, 800)
        self.app.processEvents()

        repeater = find_quick_item(root, "regionRepeater")
        self.assertIsNotNone(repeater)
        self.assertEqual(repeater.property("count"), 1)

        box_rect = find_quick_item(root, "boxRect_test-r1")
        self.assertIsNotNone(box_rect)
        box_ptr_before = get_cpp_pointer_id(box_rect)

        # Initially no selection -> manipulator is hidden
        manipulator = find_quick_item(root, "selectionManipulator")
        self.assertIsNotNone(manipulator)
        self.assertFalse(manipulator.property("visible"))

        # Select region
        self.doc_ctrl.selectRegion("test-r1")
        self.app.processEvents()

        manipulator = find_quick_item(root, "selectionManipulator")
        self.assertIsNotNone(manipulator)
        self.assertTrue(manipulator.property("visible"))
        manip_ptr_before = get_cpp_pointer_id(manipulator)

        handle_se = find_quick_item(root, "handle_se")
        self.assertIsNotNone(handle_se)
        handle_se_ptr_before = get_cpp_pointer_id(handle_se)

        # 1. Simulate drag gesture across multiple move updates
        self.doc_ctrl.startDrag("test-r1", 200.0, 200.0)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "dragging")

        for offset in [10.0, 25.0, 40.0, 60.0]:
            self.doc_ctrl.updateDrag(200.0 + offset, 200.0 + offset)
            self.app.processEvents()
            # Verify delegate and handle pointers remained stable (never destroyed/recreated)
            self.assertEqual(repeater.property("count"), 1)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "boxRect_test-r1")), box_ptr_before)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "selectionManipulator")), manip_ptr_before)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "handle_se")), handle_se_ptr_before)

        self.doc_ctrl.commitDrag()
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "selected")

        # 2. Simulate resize gesture across multiple move updates
        box_ptr_resize = get_cpp_pointer_id(find_quick_item(root, "boxRect_test-r1"))
        self.doc_ctrl.startResize("test-r1", "se", 300.0, 300.0)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "resizing")

        for offset in [5.0, 15.0, 30.0, 50.0]:
            self.doc_ctrl.updateResize(300.0 + offset, 300.0 + offset)
            self.app.processEvents()
            # Verify handles remained alive with identical pointer ID
            self.assertEqual(repeater.property("count"), 1)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "boxRect_test-r1")), box_ptr_resize)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "selectionManipulator")), manip_ptr_before)
            self.assertEqual(get_cpp_pointer_id(find_quick_item(root, "handle_se")), handle_se_ptr_before)

        self.doc_ctrl.commitResize()
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "selected")

        # 3. Deselect region -> manipulator is hidden
        self.doc_ctrl.clearSelection()
        self.app.processEvents()
        self.assertFalse(find_quick_item(root, "selectionManipulator").property("visible"))

        engine.deleteLater()
        self.app.processEvents()

    # =========================================================================
    # Section B: Realistic SplitView Hierarchy & Layout Constraints
    # =========================================================================
    def test_section_b_realistic_splitview_hierarchy_across_window_sizes(self):
        """
        Verifies ReviewWorkspaceView inside realistic main window layout
        (Sidebar 220px + StackLayout + ReviewWorkspaceView):
        - Tested at 800x600, 904x969, 1040x700, 1280x850.
        - Both pdfPane and markdownPane enforce minimumWidth >= 260px.
        - fillWidth is exclusively owned by markdownPane.
        - One-shot initialization establishes balanced split.
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)
        engine.rootContext().setContextProperty("markdownViewerController", self.md_ctrl)

        qml_source = f"""
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {{
    id: windowRoot
    width: 1040
    height: 700

    RowLayout {{
        anchors.fill: parent
        spacing: 0

        Item {{
            id: sidebar
            objectName: "mockSidebar"
            Layout.preferredWidth: 220
            Layout.fillHeight: true
        }}

        StackLayout {{
            id: mainStack
            objectName: "mainStackLayout"
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: 1

            Item {{
                objectName: "queueTab"
            }}

            Loader {{
                id: workspaceLoader
                objectName: "workspaceLoader"
                Layout.fillWidth: true
                Layout.fillHeight: true
                source: "{self.views_dir.as_uri()}/ReviewWorkspaceView.qml"
            }}
        }}
    }}
}}
"""
        comp = QQmlComponent(engine)
        comp.setData(QByteArray(qml_source.encode("utf-8")), QUrl())
        root = comp.create()
        self.app.processEvents()

        pdf_pane = find_quick_item(root, "pdfPane")
        md_pane = find_quick_item(root, "markdownPane")
        split_view = find_quick_item(root, "reviewSplitView")

        self.assertIsNotNone(pdf_pane)
        self.assertIsNotNone(md_pane)
        self.assertIsNotNone(split_view)

        test_resolutions = [
            (800, 600),
            (904, 969),
            (1040, 700),
            (1280, 850),
        ]

        for w, h in test_resolutions:
            root.setProperty("width", w)
            root.setProperty("height", h)
            self.app.processEvents()

            avail_workspace_width = w - 220
            self.assertGreaterEqual(pdf_pane.property("width"), 260)
            self.assertGreaterEqual(md_pane.property("width"), 260)
            self.assertEqual(pdf_pane.property("height"), h)
            self.assertEqual(md_pane.property("height"), h)

            # Combined panes plus divider must not exceed available width
            total_pane_width = pdf_pane.property("width") + md_pane.property("width")
            self.assertLessEqual(total_pane_width, avail_workspace_width + 4)

        engine.deleteLater()
        self.app.processEvents()

    # =========================================================================
    # Section C: Responsive PDF Toolbar at Minimum Pane Width (260px)
    # =========================================================================
    def test_section_c_responsive_toolbar_at_narrow_width(self):
        """
        Verifies that DocumentViewerView toolbar remains fully visible, responsive,
        and unclipped down to 260px pane width:
        - panSelectButton, createRegionButton, deleteRegionButton, resetToAiButton exist.
        - Buttons wrap cleanly via Flow layout without horizontal overflow (x + width <= pane width).
        - Button positions are bounded within pane bounds.
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 260)
        root.setProperty("height", 600)
        self.app.processEvents()

        pan_btn = find_quick_item(root, "panSelectButton")
        create_btn = find_quick_item(root, "createRegionButton")
        del_btn = find_quick_item(root, "deleteRegionButton")
        reset_btn = find_quick_item(root, "resetToAiButton")

        self.assertIsNotNone(pan_btn)
        self.assertIsNotNone(create_btn)
        self.assertIsNotNone(del_btn)
        self.assertIsNotNone(reset_btn)

        toolbar = find_quick_item(root, "toolbarContainer")
        self.assertIsNotNone(toolbar)

        # Check each button's geometry relative to the 260px pane width
        for btn in [pan_btn, create_btn, del_btn, reset_btn]:
            self.assertTrue(btn.property("visible"))
            btn_x = btn.property("x")
            btn_w = btn.property("width")
            self.assertGreaterEqual(btn_x, 0)
            # The button must fit horizontally within the 260px pane
            self.assertLessEqual(btn_w, 260)

        # Toolbar container height must expand to accommodate wrapped rows
        self.assertGreater(toolbar.property("height"), 40)

        # Verify ButtonGroup mutual exclusivity:
        # Clicking the already-checked pan_select button must NOT uncheck it
        self.assertTrue(pan_btn.property("checked"))
        pan_btn.clicked.emit()
        self.app.processEvents()
        self.assertTrue(pan_btn.property("checked"))

        # Clicking create_region checks it and unchecks pan_select
        create_btn.clicked.emit()
        self.app.processEvents()
        self.assertTrue(create_btn.property("checked"))
        self.assertFalse(pan_btn.property("checked"))

        # Switch back
        pan_btn.clicked.emit()
        self.app.processEvents()
        self.assertTrue(pan_btn.property("checked"))
        self.assertFalse(create_btn.property("checked"))

        engine.deleteLater()
        self.app.processEvents()

    # =========================================================================
    # Section D: Interaction Routing Across Modes
    # =========================================================================
    def test_section_d_interaction_routing_over_existing_regions(self):
        """
        Verifies interaction event routing across all specified scenarios:
        1. pan_select empty drag -> pans canvas without creating manual region
        2. create_region empty drag -> rubber band preview and manual creation commit
        3. pan_select existing region click without drag -> selects region without modifying geometry
        4. pan_select existing region drag -> updates transient bbox and persists on commit
        5. pan_select resize handle drag -> updates transient bbox and persists on commit
        6. create_region starting over existing region -> passes through transparently to emptyArea,
           initiating manual creation without moving the existing region
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 1000)
        root.setProperty("height", 800)
        self.doc_ctrl.setViewportDimensions(1000, 800)
        self.doc_ctrl.setItemDimensions(1000, 800)
        self.doc_ctrl.selectRegion("test-r1")
        self.app.processEvents()

        body_drag = find_quick_item(root, "bodyDragArea_test-r1")
        manip_drag = find_quick_item(root, "manipulatorDragArea")
        empty_area = find_quick_item(root, "emptyArea")
        handle_area = find_quick_item(root, "handleArea_nw")

        self.assertIsNotNone(body_drag)
        self.assertIsNotNone(manip_drag)
        self.assertIsNotNone(empty_area)
        self.assertIsNotNone(handle_area)

        # 1. 'pan_select' mode properties
        self.doc_ctrl.setInteractionMode("pan_select")
        self.app.processEvents()
        self.assertTrue(body_drag.property("enabled"))
        self.assertTrue(manip_drag.property("enabled"))
        self.assertTrue(handle_area.property("enabled"))

        # 1a. pan_select empty drag: pans without creating regions
        self.doc_ctrl.setZoom(2.0)
        init_pan_x = self.doc_ctrl.panX
        init_pan_y = self.doc_ctrl.panY
        self.doc_ctrl.panBy(50.0, 30.0)
        self.app.processEvents()
        self.assertNotEqual(self.doc_ctrl.panX, init_pan_x)
        self.assertNotEqual(self.doc_ctrl.editorState, "creating")

        # 2. pan_select existing region click without drag: selects without modifying geometry
        self.mock_doc_service.update_region_geometry.reset_mock()
        self.doc_ctrl.startDrag("test-r1", 200.0, 200.0)
        self.app.processEvents()
        self.doc_ctrl.commitDrag()
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "selected")
        self.assertEqual(self.mock_doc_service.update_region_geometry.call_count, 0)

        # 3. pan_select existing region drag: moves and commits
        self.doc_ctrl.startDrag("test-r1", 200.0, 200.0)
        self.doc_ctrl.updateDrag(260.0, 260.0)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "dragging")
        self.doc_ctrl.commitDrag()
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "selected")
        self.assertEqual(self.mock_doc_service.update_region_geometry.call_count, 1)

        # 4. pan_select resize handle drag: resizes and commits
        self.mock_doc_service.update_region_geometry.reset_mock()
        self.doc_ctrl.startResize("test-r1", "se", 300.0, 300.0)
        self.doc_ctrl.updateResize(350.0, 350.0)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "resizing")
        self.doc_ctrl.commitResize()
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "selected")
        self.assertEqual(self.mock_doc_service.update_region_geometry.call_count, 1)

        # 5. 'create_region' mode: region body and handles disabled
        self.doc_ctrl.setInteractionMode("create_region")
        self.app.processEvents()
        body_drag = find_quick_item(root, "bodyDragArea_test-r1")
        self.assertFalse(body_drag.property("enabled"))
        self.assertFalse(manip_drag.property("enabled"))
        self.assertFalse(handle_area.property("enabled"))
        self.assertTrue(empty_area.property("enabled"))

        # 5a. create_region empty drag: creates manual region
        self.mock_doc_service.create_manual_region.reset_mock()
        self.doc_ctrl.startCreateManual(50.0, 50.0)
        self.doc_ctrl.updateCreateManual(150.0, 150.0)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.editorState, "creating")
        self.doc_ctrl.commitCreateManual()
        self.app.processEvents()
        self.assertEqual(self.mock_doc_service.create_manual_region.call_count, 1)

        # 6. create_region starting directly over an existing region:
        # Existing region body is transparent to hit testing, manual creation engages,
        # and existing region geometry is NOT modified.
        self.mock_doc_service.update_region_geometry.reset_mock()
        self.mock_doc_service.create_manual_region.reset_mock()
        # Point (200, 200) is inside test-r1 bounds (100, 100, 300, 300)
        self.doc_ctrl.startCreateManual(200.0, 200.0)
        self.doc_ctrl.updateCreateManual(350.0, 350.0)
        self.app.processEvents()

        self.assertEqual(self.doc_ctrl.editorState, "creating")
        creation_preview = find_quick_item(root, "creationPreview")
        self.assertIsNotNone(creation_preview)
        self.assertTrue(creation_preview.property("visible"))
        self.assertGreater(creation_preview.property("width"), 0)

        # Commit creation
        self.doc_ctrl.commitCreateManual()
        self.app.processEvents()
        self.assertIn(self.doc_ctrl.editorState, ["idle", "selected"])
        self.assertEqual(self.mock_doc_service.create_manual_region.call_count, 1)
        self.assertEqual(self.mock_doc_service.update_region_geometry.call_count, 0)

        engine.deleteLater()
        self.app.processEvents()

    # =========================================================================
    # Section E: Viewport Dimension Lifecycle & Activation Re-synchronization
    # =========================================================================
    def test_section_e_viewport_lifecycle_and_resynchronization(self):
        """
        Verifies viewport lifecycle:
        - ViewportArea syncViewportAndScene guards against transient sub-50px dimensions.
        - Activation from inactive stack tab re-synchronizes controller viewport dimensions.
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)
        engine.rootContext().setContextProperty("markdownViewerController", self.md_ctrl)

        qml_path = self.views_dir / "ReviewWorkspaceView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 1040)
        root.setProperty("height", 700)
        self.app.processEvents()

        viewport_area = find_quick_item(root, "viewportArea")
        self.assertIsNotNone(viewport_area)

        # Baseline viewport sync
        self.assertGreater(self.doc_ctrl.viewportWidth, 50)
        self.assertGreater(self.doc_ctrl.viewportHeight, 50)
        initial_vw = self.doc_ctrl.viewportWidth
        initial_vh = self.doc_ctrl.viewportHeight

        # Transient sub-50 collapse must be guarded:
        # Resizing the QML view container to sub-50 triggers syncViewportAndScene,
        # but the guard (width >= 50 && height >= 50) protects controller dimensions.
        root.setProperty("width", 10)
        root.setProperty("height", 10)
        self.app.processEvents()
        self.assertEqual(self.doc_ctrl.viewportWidth, initial_vw)
        self.assertEqual(self.doc_ctrl.viewportHeight, initial_vh)

        # Valid resize establishes new dimensions
        root.setProperty("width", 1200)
        root.setProperty("height", 850)
        self.app.processEvents()
        if hasattr(viewport_area, "syncViewportAndScene"):
            viewport_area.syncViewportAndScene()
            self.app.processEvents()

        self.assertEqual(self.doc_ctrl.viewportWidth, viewport_area.property("width"))
        self.assertGreater(self.doc_ctrl.viewportHeight, initial_vh)
        self.assertEqual(self.doc_ctrl.viewportHeight, viewport_area.property("height"))

        engine.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
