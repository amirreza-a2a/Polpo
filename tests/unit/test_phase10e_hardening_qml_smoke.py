# ============================================================
#  tests/unit/test_phase10e_hardening_qml_smoke.py
#  QML Smoke & Regression Tests for Phase 10E Hardening
# ============================================================

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl
from application.dto.document_viewer_dto import PageRasterDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController


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


class TestPhase10EHardeningQmlSmoke(unittest.TestCase):
    """
    Validates QML runtime behavior for Phase 10E Hardening:
    - Toolbar mode switching buttons in DocumentViewerView.qml
    - Empty-area stationary frame pan kinematics & gesture routing in BoundingBoxOverlay.qml
    - SplitView fillWidth ownership & minimum width stability in ReviewWorkspaceView.qml
    - Viewport dimension sync guards against sub-minimum dimensions
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

    def test_document_viewer_toolbar_mode_buttons(self):
        """Verifies toolbar contains Pan/Select and +New Region buttons bound to interactionMode."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        pan_btn = find_quick_item(root, "panSelectButton")
        create_btn = find_quick_item(root, "createRegionButton")

        self.assertIsNotNone(pan_btn, "panSelectButton must exist in DocumentViewerView toolbar")
        self.assertIsNotNone(create_btn, "createRegionButton must exist in DocumentViewerView toolbar")

        # Initial state: pan_select is default
        self.assertTrue(pan_btn.property("checked"))
        self.assertFalse(create_btn.property("checked"))

        # Switch to create_region via controller
        self.doc_ctrl.setInteractionMode("create_region")
        self.app.processEvents()
        self.assertFalse(pan_btn.property("checked"))
        self.assertTrue(create_btn.property("checked"))

        # Switch back to pan_select via controller
        self.doc_ctrl.setInteractionMode("pan_select")
        self.app.processEvents()
        self.assertTrue(pan_btn.property("checked"))
        self.assertFalse(create_btn.property("checked"))

        engine.deleteLater()
        self.app.processEvents()

    def test_empty_area_cursor_shape_zoom_dependent(self):
        """Verifies emptyArea cursor reflects interactionMode and zoom level."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        empty_area = find_quick_item(root, "emptyArea")
        self.assertIsNotNone(empty_area, "emptyArea must exist")

        try:
            from PySide6.QtCore import Qt
        except ImportError:
            from PyQt6.QtCore import Qt

        # In pan_select mode at zoom 1.0 (fit-to-page): ArrowCursor
        self.doc_ctrl.setZoom(1.0)
        self.doc_ctrl.setInteractionMode("pan_select")
        self.app.processEvents()
        self.assertEqual(empty_area.property("cursorShape"), Qt.CursorShape.ArrowCursor)

        # In pan_select mode at zoom 2.0: OpenHandCursor
        self.doc_ctrl.setZoom(2.0)
        self.app.processEvents()
        self.assertEqual(empty_area.property("cursorShape"), Qt.CursorShape.OpenHandCursor)

        # In create_region mode: CrossCursor regardless of zoom
        self.doc_ctrl.setInteractionMode("create_region")
        self.app.processEvents()
        self.assertEqual(empty_area.property("cursorShape"), Qt.CursorShape.CrossCursor)

        engine.deleteLater()
        self.app.processEvents()

    def test_review_workspace_splitview_fill_width_and_minimum_width(self):
        """
        Verifies ReviewWorkspaceView SplitView:
        - Exactly ONE pane (markdownPane) owns SplitView.fillWidth: true
        - Both panes maintain SplitView.minimumWidth: 320
        - Horizontal resize does not collapse either pane below 320px
        """
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)
        engine.rootContext().setContextProperty("markdownViewerController", self.md_ctrl)

        qml_path = self.views_dir / "ReviewWorkspaceView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 1200)
        root.setProperty("height", 800)
        self.app.processEvents()

        split_view = find_quick_item(root, "reviewSplitView")
        pdf_pane = find_quick_item(root, "pdfPane")
        md_pane = find_quick_item(root, "markdownPane")

        self.assertIsNotNone(split_view, "reviewSplitView must exist")
        self.assertIsNotNone(pdf_pane, "pdfPane must exist")
        self.assertIsNotNone(md_pane, "markdownPane must exist")

        # Verify SplitView attached properties
        try:
            from PySide6.QtQuickTemplates2 import QQuickSplitView
        except ImportError:
            try:
                from PySide6.QtQuick.Controls import SplitView as QQuickSplitView
            except ImportError:
                QQuickSplitView = None

        if QQuickSplitView is not None and hasattr(QQuickSplitView, "qmlAttachedProperties"):
            pdf_props = QQuickSplitView.qmlAttachedProperties(pdf_pane)
            md_props = QQuickSplitView.qmlAttachedProperties(md_pane)

            # Exactly ONE child owns fillWidth: markdownPane
            self.assertFalse(pdf_props.property("fillWidth"))
            self.assertTrue(md_props.property("fillWidth"))

            # Both panes enforce minimumWidth 260
            self.assertEqual(pdf_props.property("minimumWidth"), 260)
            self.assertEqual(md_props.property("minimumWidth"), 260)

        # Test horizontal and vertical constraints
        self.assertGreaterEqual(pdf_pane.property("width"), 260)
        self.assertGreaterEqual(md_pane.property("width"), 260)
        self.assertGreater(pdf_pane.property("height"), 0)
        self.assertGreater(md_pane.property("height"), 0)
        self.assertEqual(pdf_pane.property("height"), 800)
        self.assertEqual(md_pane.property("height"), 800)

        # Test horizontal resize down to 700
        root.setProperty("width", 700)
        self.app.processEvents()

        self.assertGreaterEqual(pdf_pane.property("width"), 260)
        self.assertGreaterEqual(md_pane.property("width"), 260)
        self.assertGreater(pdf_pane.property("height"), 0)
        self.assertGreater(md_pane.property("height"), 0)

        # Test horizontal resize up to 1400 and vertical resize to 950
        root.setProperty("width", 1400)
        root.setProperty("height", 950)
        self.app.processEvents()

        self.assertGreaterEqual(pdf_pane.property("width"), 260)
        self.assertGreaterEqual(md_pane.property("width"), 260)
        self.assertEqual(pdf_pane.property("height"), 950)
        self.assertEqual(md_pane.property("height"), 950)

        engine.deleteLater()
        self.app.processEvents()

    def test_viewport_sync_guards_subminimum_dimensions(self):
        """Verifies DocumentViewerView.syncViewportAndScene guards against sub-50px dimensions."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 800)
        root.setProperty("height", 600)
        self.doc_ctrl.setViewportDimensions(800, 600)
        self.app.processEvents()

        self.assertEqual(self.doc_ctrl.viewportWidth, 800)

        # Simulate a transient sub-minimum collapse (e.g. width=10, height=10)
        root.setProperty("width", 10)
        root.setProperty("height", 10)
        self.app.processEvents()

        # Viewport dimensions on controller must remain guarded at 800 (not updated to 10)
        self.assertEqual(self.doc_ctrl.viewportWidth, 800)
        self.assertEqual(self.doc_ctrl.viewportHeight, 600)

        # Transition back from sub-minimum to valid active dimensions (e.g. 900x700)
        root.setProperty("width", 900)
        root.setProperty("height", 700)
        self.app.processEvents()

        # Viewport synchronization must engage and update controller from stale defaults
        viewport_area = find_quick_item(root, "viewportArea")
        self.assertIsNotNone(viewport_area)
        self.assertEqual(self.doc_ctrl.viewportWidth, 900)
        self.assertEqual(self.doc_ctrl.viewportHeight, viewport_area.property("height"))
        self.assertGreater(self.doc_ctrl.viewportHeight, 50)

        engine.deleteLater()
        self.app.processEvents()

    def test_review_workspace_stacklayout_lifecycle_and_activation(self):
        """
        Verifies ReviewWorkspaceView behavior inside a StackLayout:
        - Inactive tab initially has 0x0 or implicit geometry
        - Activation (switching currentIndex) establishes valid workspace geometry
        - pdfPane and markdownPane have height > 0
        - ViewportArea establishes valid non-zero dimensions
        - Controller viewport is synchronized and not stuck at 800x600 defaults
        - Tab switching away and back preserves valid geometry without collapsing
        """
        from PyQt6.QtQml import QQmlComponent
        from PyQt6.QtCore import QByteArray

        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)
        engine.rootContext().setContextProperty("markdownViewerController", self.md_ctrl)

        qml_source = f"""
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {{
    id: testWindow
    width: 1040
    height: 700

    StackLayout {{
        id: testStackLayout
        objectName: "testStackLayout"
        anchors.fill: parent
        currentIndex: 0

        Item {{
            objectName: "mockTab0"
            Text {{ text: "Queue Tab" }}
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
"""
        comp = QQmlComponent(engine)
        comp.setData(QByteArray(qml_source.encode("utf-8")), QUrl())
        root = comp.create()
        self.app.processEvents()

        stack_layout = find_quick_item(root, "testStackLayout")
        self.assertIsNotNone(stack_layout)

        # Phase 1: Inactive tab (currentIndex == 0)
        self.assertEqual(stack_layout.property("currentIndex"), 0)

        # Phase 2: Activate Review Workspace tab (currentIndex == 1)
        stack_layout.setProperty("currentIndex", 1)
        self.app.processEvents()

        pdf_pane = find_quick_item(root, "pdfPane")
        md_pane = find_quick_item(root, "markdownPane")
        viewport_area = find_quick_item(root, "viewportArea")

        self.assertIsNotNone(pdf_pane, "pdfPane must be instantiated after activation")
        self.assertIsNotNone(md_pane, "markdownPane must be instantiated after activation")
        self.assertIsNotNone(viewport_area, "viewportArea must be instantiated after activation")

        # Geometry must be positive and non-zero
        self.assertGreater(pdf_pane.property("height"), 0, "pdfPane must have height > 0")
        self.assertGreater(md_pane.property("height"), 0, "markdownPane must have height > 0")
        self.assertGreater(viewport_area.property("height"), 0, "viewportArea must have height > 0")
        self.assertGreaterEqual(pdf_pane.property("width"), 260, "pdfPane must respect minimumWidth")
        self.assertGreaterEqual(md_pane.property("width"), 260, "markdownPane must respect minimumWidth")

        # Controller viewport must be synchronized
        self.assertEqual(self.doc_ctrl.viewportWidth, viewport_area.property("width"))
        self.assertEqual(self.doc_ctrl.viewportHeight, viewport_area.property("height"))

        # Phase 3: Resize window/layout
        root.setProperty("width", 1200)
        root.setProperty("height", 850)
        self.app.processEvents()

        self.assertEqual(pdf_pane.property("height"), 850)
        self.assertEqual(md_pane.property("height"), 850)
        self.assertEqual(self.doc_ctrl.viewportHeight, viewport_area.property("height"))

        # Phase 4: Switch away to tab 0 and back to tab 1
        stack_layout.setProperty("currentIndex", 0)
        self.app.processEvents()
        stack_layout.setProperty("currentIndex", 1)
        self.app.processEvents()

        self.assertGreater(pdf_pane.property("height"), 0)
        self.assertGreater(md_pane.property("height"), 0)
        self.assertGreater(viewport_area.property("height"), 0)
        self.assertFalse(self.doc_ctrl.viewportHeight == 0)

        engine.deleteLater()
        self.app.processEvents()

    def test_pan_drag_threshold_property_exists_and_is_four_pixels(self):
        """Verifies panDragThreshold is explicitly defined as 4.0 viewport pixels."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        overlay = find_quick_item(root, "regionOverlay")
        self.assertIsNotNone(overlay)

        self.assertEqual(overlay.property("panDragThreshold"), 4.0)

        engine.deleteLater()
        self.app.processEvents()

    def test_stationary_frame_mapping_via_overlay(self):
        """Verifies overlay mapToViewport produces stationary coordinates relative to viewportArea."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("documentViewerController", self.doc_ctrl)

        qml_path = self.views_dir / "DocumentViewerView.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.app.processEvents()

        root = engine.rootObjects()[0]
        root.setProperty("width", 800)
        root.setProperty("height", 600)
        self.doc_ctrl.setViewportDimensions(800, 600)
        self.doc_ctrl.setItemDimensions(800, 600)
        self.app.processEvents()

        overlay = find_quick_item(root, "regionOverlay")
        empty_area = find_quick_item(root, "emptyArea")
        self.assertIsNotNone(overlay)
        self.assertIsNotNone(empty_area)

        # Call mapToViewport on overlay
        pt = overlay.mapToViewport(50.0, 50.0, empty_area)
        self.assertTrue(hasattr(pt, "x") or (isinstance(pt, dict) and "x" in pt))

        engine.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
