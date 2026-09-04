# ============================================================
#  tests/unit/test_phase10c_qml_smoke.py
#  Phase 10C QML Runtime Smoke Test & Model B Binding Verification
# ============================================================

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl
from application.services.document_viewer_service import DocumentViewerService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


class TestDocumentViewerQmlSmoke(unittest.TestCase):
    """
    Verifies that DocumentViewerView.qml and BoundingBoxOverlay.qml load cleanly
    in the real Qt Quick runtime with zero syntax, import, or binding errors,
    and that Model B scene bindings dynamically reflect controller state.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.mock_service.get_active_page_regions.return_value = []
        self.controller = DocumentViewerController(viewer_service=self.mock_service)

        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("documentViewerController", self.controller)

        # Track QML warnings/errors
        self.qml_warnings = []
        self.engine.warnings.connect(self._on_qml_warning)

        # Resolve path to DocumentViewerView.qml
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
        self.assertEqual(len(root_objects), 1, "Expected exactly 1 root object loaded from DocumentViewerView.qml")
        self.view_root = root_objects[0]

    def tearDown(self):
        self.engine.deleteLater()
        self.app.processEvents()

    def _on_qml_warning(self, warnings):
        for w in warnings:
            self.qml_warnings.append(w.toString())

    def test_qml_loads_without_errors_and_resolves_overlay_component(self):
        """1. Verifies DocumentViewerView.qml and BoundingBoxOverlay.qml resolve without syntax/import errors."""
        self.assertEqual(self.view_root.objectName(), "documentViewerView")

        # Verify BoundingBoxOverlay child component resolved and instantiated
        overlay = self.view_root.findChild(object, "regionOverlay")
        self.assertIsNotNone(overlay, "BoundingBoxOverlay child component must resolve and instantiate")
        self.assertEqual(overlay.objectName(), "regionOverlay")

        # Verify pageImage component instantiated
        page_image = self.view_root.findChild(object, "pageImage")
        self.assertIsNotNone(page_image, "Image item must resolve and instantiate")

        # Filter out harmless benign warnings if any, assert no critical QML syntax or reference errors
        critical_errors = [
            w for w in self.qml_warnings
            if "ReferenceError" in w or "TypeError" in w or "SyntaxError" in w or "Cannot assign to" in w
        ]
        self.assertEqual(critical_errors, [], f"QML runtime produced critical errors: {critical_errors}")

    def test_model_b_runtime_bindings_track_controller_state(self):
        """
        2. Verifies that Model B bindings on pageScene dynamically follow controller properties:
           pageScene.scale  == controller.zoom
           pageScene.x      == -controller.panX
           pageScene.y      == -controller.panY
           pageScene.width  == controller.itemWidth
           pageScene.height == controller.itemHeight
        """
        page_scene = self.view_root.findChild(object, "pageScene")
        self.assertIsNotNone(page_scene, "pageScene must exist in QML hierarchy")

        # Baseline dimensions & zoom
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setItemDimensions(800.0, 600.0)
        self.app.processEvents()

        self.assertAlmostEqual(page_scene.property("width"), 800.0)
        self.assertAlmostEqual(page_scene.property("height"), 600.0)
        self.assertAlmostEqual(page_scene.property("scale"), 1.0)
        self.assertAlmostEqual(page_scene.property("x"), -self.controller.panX)
        self.assertAlmostEqual(page_scene.property("y"), -self.controller.panY)

        # Update zoom -> pageScene.scale updates dynamically via binding
        self.controller.setZoom(2.5)
        self.app.processEvents()
        self.assertAlmostEqual(page_scene.property("scale"), 2.5)
        self.assertAlmostEqual(self.controller.zoom, 2.5)

        # Update pan -> pageScene.x and y update dynamically via binding
        self.controller.setPan(150.0, 80.0)
        self.app.processEvents()
        self.assertAlmostEqual(page_scene.property("x"), -150.0)
        self.assertAlmostEqual(page_scene.property("y"), -80.0)

        # Resize dimensions -> pageScene.width and height update dynamically
        self.controller.setViewportDimensions(1200.0, 900.0)
        self.app.processEvents()
        self.assertAlmostEqual(page_scene.property("width"), 1200.0)
        self.assertAlmostEqual(page_scene.property("height"), 900.0)
        self.assertAlmostEqual(page_scene.property("x"), -self.controller.panX)
        self.assertAlmostEqual(page_scene.property("y"), -self.controller.panY)
