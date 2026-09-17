# ============================================================
#  tests/unit/test_phase10e_hardening_controller.py
#  Unit Tests for DocumentViewerController Interaction Mode & Kinematics
# ============================================================

import unittest
from unittest.mock import MagicMock

from application.services.document_viewer_service import DocumentViewerService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


class TestDocumentViewerControllerHardening(unittest.TestCase):
    """
    Verifies that DocumentViewerController manages explicit interaction modes
    ('pan_select' vs 'create_region') and enforces pan bounds without regressions.
    """

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.controller = DocumentViewerController(viewer_service=self.mock_service)

    def test_default_interaction_mode_is_pan_select(self):
        """Default interaction mode must be 'pan_select'."""
        self.assertEqual(self.controller.interactionMode, "pan_select")

    def test_set_interaction_mode_transitions_and_emits_signal(self):
        """Valid mode changes must update the property and emit interactionModeChanged."""
        signal_emissions = []
        self.controller.interactionModeChanged.connect(lambda: signal_emissions.append(self.controller.interactionMode))

        self.controller.setInteractionMode("create_region")
        self.assertEqual(self.controller.interactionMode, "create_region")
        self.assertEqual(signal_emissions, ["create_region"])

        self.controller.setInteractionMode("pan_select")
        self.assertEqual(self.controller.interactionMode, "pan_select")
        self.assertEqual(signal_emissions, ["create_region", "pan_select"])

    def test_set_interaction_mode_idempotent(self):
        """Setting the same mode repeatedly must not emit redundant signals."""
        signal_emissions = []
        self.controller.interactionModeChanged.connect(lambda: signal_emissions.append(self.controller.interactionMode))

        self.controller.setInteractionMode("pan_select")
        self.assertEqual(signal_emissions, [])

    def test_invalid_interaction_mode_is_safely_rejected(self):
        """Invalid interaction mode strings must be rejected without modifying state."""
        signal_emissions = []
        self.controller.interactionModeChanged.connect(lambda: signal_emissions.append(self.controller.interactionMode))

        self.controller.setInteractionMode("invalid_mode")
        self.assertEqual(self.controller.interactionMode, "pan_select")
        self.assertEqual(signal_emissions, [])

        self.controller.setInteractionMode("")
        self.assertEqual(self.controller.interactionMode, "pan_select")
        self.assertEqual(signal_emissions, [])

    def test_pan_clamping_at_fit_to_page_zoom_1(self):
        """At zoom 1.0 (fit to page baseline), pan is clamped to 0.0, 0.0."""
        self.controller.setViewportDimensions(800, 600)
        self.controller.setItemDimensions(800, 600)
        self.controller.setZoom(1.0)

        # Try to pan
        self.controller.panBy(50.0, 50.0)
        self.assertEqual(self.controller.panX, 0.0)
        self.assertEqual(self.controller.panY, 0.0)

    def test_pan_movement_at_zoom_2(self):
        """At zoom 2.0, panBy translates within valid content bounds."""
        self.controller.setViewportDimensions(800, 600)
        self.controller.setItemDimensions(800, 600)
        self.controller.setZoom(2.0)

        # Content size is 1600x1200; viewport is 800x600. Max pan is 800x600.
        initial_pan_x = self.controller.panX
        initial_pan_y = self.controller.panY
        self.controller.panBy(100.0, 50.0)
        self.assertAlmostEqual(self.controller.panX, initial_pan_x + 100.0)
        self.assertAlmostEqual(self.controller.panY, initial_pan_y + 50.0)


if __name__ == "__main__":
    unittest.main()
