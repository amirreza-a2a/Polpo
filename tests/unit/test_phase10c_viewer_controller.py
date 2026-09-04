# ============================================================
#  tests/unit/test_phase10c_viewer_controller.py
#  DocumentViewerController Presentation & Threading Tests
# ============================================================

import time
import unittest
from unittest.mock import MagicMock

from core.entities.bounding_box import BoundingBox
from application.dto.document_viewer_dto import PageRasterDTO, VisualRegionOverlayItemDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


class TestDocumentViewerController(unittest.TestCase):
    """Verifies DocumentViewerController state management, generation tracking, and coordinate slots."""

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.controller = DocumentViewerController(viewer_service=self.mock_service)

    def test_initial_state(self):
        self.assertEqual(self.controller.currentJobId, 0)
        self.assertEqual(self.controller.currentPage, 1)
        self.assertEqual(self.controller.totalPages, 1)
        self.assertEqual(self.controller.pageImageUri, "")
        self.assertEqual(self.controller.zoom, 1.0)
        self.assertEqual(self.controller.panX, 0.0)
        self.assertEqual(self.controller.panY, 0.0)
        self.assertFalse(self.controller.isLoading)
        self.assertEqual(self.controller.errorMessage, "")
        self.assertEqual(len(self.controller.activeRegions), 0)

    def test_zoom_and_pan_bounds_and_reset(self):
        self.controller.setViewportDimensions(400.0, 300.0)
        self.controller.setItemDimensions(800.0, 600.0)

        # Zoom updates within bounds
        self.controller.setZoom(2.5)
        self.assertAlmostEqual(self.controller.zoom, 2.5)

        self.controller.setZoom(15.0)  # Clamped to 10.0
        self.assertAlmostEqual(self.controller.zoom, 10.0)

        self.controller.setZoom(0.01)  # Clamped to 0.1
        self.assertAlmostEqual(self.controller.zoom, 0.1)

        # Reset view
        self.controller.resetView()
        self.assertAlmostEqual(self.controller.zoom, 1.0)
        # At zoom 1.0, content is 800x600, viewport 400x300 -> pan is clamped to valid range [0, 400]
        self.assertTrue(0.0 <= self.controller.panX <= 400.0)
        self.assertTrue(0.0 <= self.controller.panY <= 300.0)

    def test_pan_bounds_clamping(self):
        # Content: 800x600, Viewport: 400x300 -> valid pan X: [0, 400], Y: [0, 300]
        self.controller.setViewportDimensions(400.0, 300.0)
        self.controller.setItemDimensions(800.0, 600.0)
        self.controller._zoom = 1.0

        # Attempt negative pan -> clamped to 0.0
        self.controller.setPan(-100.0, -50.0)
        self.assertAlmostEqual(self.controller.panX, 0.0)
        self.assertAlmostEqual(self.controller.panY, 0.0)

        # Attempt excessive pan -> clamped to (400, 300)
        self.controller.setPan(9999.0, 8888.0)
        self.assertAlmostEqual(self.controller.panX, 400.0)
        self.assertAlmostEqual(self.controller.panY, 300.0)

        # Valid in-bounds pan
        self.controller.setPan(250.0, 150.0)
        self.assertAlmostEqual(self.controller.panX, 250.0)
        self.assertAlmostEqual(self.controller.panY, 150.0)

    def test_centering_when_content_smaller_than_viewport(self):
        # Content: 400x300, Viewport: 800x600 -> content smaller than viewport
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setItemDimensions(400.0, 300.0)
        self.controller._zoom = 1.0

        # When content is smaller, pan is locked to centered offset: (400 - 800)/2 = -200
        self.assertAlmostEqual(self.controller.minPanX, -200.0)
        self.assertAlmostEqual(self.controller.maxPanX, -200.0)
        self.assertAlmostEqual(self.controller.minPanY, -150.0)
        self.assertAlmostEqual(self.controller.maxPanY, -150.0)

        self.controller.setPan(0.0, 0.0)
        self.assertAlmostEqual(self.controller.panX, -200.0)
        self.assertAlmostEqual(self.controller.panY, -150.0)

    def test_zoom_around_anchor_preserves_spatial_stability(self):
        # Viewport: 800x600, Item: 800x600, initial zoom: 1.0, pan: (0, 0)
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setItemDimensions(800.0, 600.0)
        self.controller._zoom = 1.0
        self.controller._pan_x = 0.0
        self.controller._pan_y = 0.0

        anchor_x = 400.0
        anchor_y = 300.0

        # Before zoom, item coordinate at anchor (400, 300) is (400, 300)
        # Zoom to 2.0 around anchor (400, 300)
        self.controller.zoomAt(2.0, anchor_x, anchor_y)
        self.assertAlmostEqual(self.controller.zoom, 2.0)

        # After zoom, item coordinate (400, 300) transformed to viewport must STILL be (400, 300)
        vp_x = (400.0 * self.controller.zoom) - self.controller.panX
        vp_y = (300.0 * self.controller.zoom) - self.controller.panY
        self.assertAlmostEqual(vp_x, anchor_x)
        self.assertAlmostEqual(vp_y, anchor_y)

    def test_zoom_and_pan_alignment_at_multiple_zoom_levels(self):
        """
        Required Integration Test 1, 2, 3:
        Verifies that image-space and overlay-space coordinates undergo the exact same
        affine transform across zoom = 0.5, 1.0, 2.0, 5.0 and under non-zero pan.
        """
        self.controller._raster_width = 1000
        self.controller._raster_height = 1000
        self.controller._item_width = 800.0
        self.controller._item_height = 800.0
        self.controller._viewport_width = 600.0
        self.controller._viewport_height = 600.0

        self.controller._active_regions = [
            {
                "region_id": "r1",
                "display_order": 1,
                "origin": "ai_detected",
                "review_status": "unreviewed",
                "effective_ymin": 200,
                "effective_xmin": 200,
                "effective_ymax": 600,
                "effective_xmax": 600,
            }
        ]

        # Test at zoom 0.5, 1.0, 2.0, 5.0
        for z in [0.5, 1.0, 2.0, 5.0]:
            self.controller.setZoom(z)
            self.controller.setPan(100.0, 75.0)

            # 1. S_item overlay rect
            item_rects = self.controller.getOverlayRects(800.0, 800.0, "preserve_aspect_fit")
            self.assertEqual(len(item_rects), 1)
            item_box = item_rects[0]

            # 2. S_viewport overlay rect calculated directly
            vp_rects = self.controller.getOverlayRectsInViewport(800.0, 800.0, "preserve_aspect_fit")
            self.assertEqual(len(vp_rects), 1)
            vp_box = vp_rects[0]

            # 3. Model B QML Scene Graph transform: (item_x * zoom) - pan_x
            scene_x = (item_box["x"] * self.controller.zoom) - self.controller.panX
            scene_y = (item_box["y"] * self.controller.zoom) - self.controller.panY
            scene_w = item_box["width"] * self.controller.zoom
            scene_h = item_box["height"] * self.controller.zoom

            # Verify: scene transform matches closed-form viewport rect to floating point tolerance!
            self.assertAlmostEqual(scene_x, vp_box["x"], places=4)
            self.assertAlmostEqual(scene_y, vp_box["y"], places=4)
            self.assertAlmostEqual(scene_w, vp_box["width"], places=4)
            self.assertAlmostEqual(scene_h, vp_box["height"], places=4)

    def test_initial_viewport_and_scene_synchronization(self):
        """Test 1: Initial viewport WxH synchronizes with controller viewport and item WxH."""
        self.controller.setViewportDimensions(900.0, 700.0)
        self.controller.setItemDimensions(900.0, 700.0)
        self.assertEqual(self.controller.viewportWidth, 900.0)
        self.assertEqual(self.controller.viewportHeight, 700.0)
        self.assertEqual(self.controller.itemWidth, 900.0)
        self.assertEqual(self.controller.itemHeight, 700.0)

    def test_resize_synchronization(self):
        """Test 2: Viewport resize 500x500 -> 1200x800 updates both viewport and item dimensions."""
        self.controller.setViewportDimensions(500.0, 500.0)
        self.assertEqual(self.controller.viewportWidth, 500.0)
        self.assertEqual(self.controller.itemWidth, 500.0)

        self.controller.setViewportDimensions(1200.0, 800.0)
        self.assertEqual(self.controller.viewportWidth, 1200.0)
        self.assertEqual(self.controller.viewportHeight, 800.0)
        self.assertEqual(self.controller.itemWidth, 1200.0)
        self.assertEqual(self.controller.itemHeight, 800.0)

    def test_resize_under_zoom_and_pan_with_scene_synchronization(self):
        """Test 3: Resize under zoom=2.0 and non-zero pan revalidates bounds and clamps pan correctly."""
        self.controller._raster_width = 1000
        self.controller._raster_height = 1000
        self.controller.setViewportDimensions(500.0, 500.0)
        self.controller.setZoom(2.0)  # content: 1000x1000, viewport: 500x500 -> max pan = 500
        self.controller.setPan(300.0, 250.0)
        self.assertEqual(self.controller.panX, 300.0)
        self.assertEqual(self.controller.panY, 250.0)

        # Resize to 1200x800: content becomes 2400x1600
        self.controller.setViewportDimensions(1200.0, 800.0)
        self.assertEqual(self.controller.viewportWidth, 1200.0)
        self.assertEqual(self.controller.viewportHeight, 800.0)
        self.assertEqual(self.controller.itemWidth, 1200.0)
        self.assertEqual(self.controller.itemHeight, 800.0)

        # Under zoom 2.0 on 1200x800 item, content is 2400x1600
        # maxPanX = 2400 - 1200 = 1200, maxPanY = 1600 - 800 = 800
        # Pan (300, 250) is valid in [0, 1200] and [0, 800], so it remains (300, 250)
        self.assertEqual(self.controller.panX, 300.0)
        self.assertEqual(self.controller.panY, 250.0)

    def test_fit_to_page_semantics(self):
        """Test 4: Fit-to-page resets to zoom=1.0 and pan=(0.0, 0.0)."""
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setZoom(3.5)
        self.controller.setPan(500.0, 400.0)
        self.assertNotEqual(self.controller.zoom, 1.0)

        self.controller.fitToPage()
        self.assertEqual(self.controller.zoom, 1.0)
        self.assertEqual(self.controller.panX, 0.0)
        self.assertEqual(self.controller.panY, 0.0)

    def test_overlay_alignment_and_semantic_invariance_after_resize(self):
        """Test 5: Semantic BoundingBox remains untouched during resize while presentation coordinates update."""
        self.controller._raster_width = 1200
        self.controller._raster_height = 1800
        self.controller._active_regions = [
            {
                "region_id": "r1",
                "display_order": 1,
                "origin": "ai_detected",
                "review_status": "unreviewed",
                "effective_ymin": 150,
                "effective_xmin": 200,
                "effective_ymax": 450,
                "effective_xmax": 700,
            }
        ]

        self.controller.setViewportDimensions(600.0, 600.0)
        self.controller.setZoom(1.5)
        self.controller.setPan(100.0, 50.0)

        # Resize viewport to 1200x900
        self.controller.setViewportDimensions(1200.0, 900.0)

        # 1. Semantic BoundingBox is strictly invariant
        self.assertEqual(self.controller._active_regions[0]["effective_ymin"], 150)
        self.assertEqual(self.controller._active_regions[0]["effective_xmin"], 200)
        self.assertEqual(self.controller._active_regions[0]["effective_ymax"], 450)
        self.assertEqual(self.controller._active_regions[0]["effective_xmax"], 700)

        # 2. Presentation overlay rect updates to match new item/viewport metrics
        rects = self.controller.getOverlayRects(1200.0, 900.0, "preserve_aspect_fit")
        self.assertEqual(len(rects), 1)

        # 3. Inverse transform from viewport recovers exact original normalized coordinates
        vp_rects = self.controller.getOverlayRectsInViewport(1200.0, 900.0, "preserve_aspect_fit")
        vp_box = vp_rects[0]
        recovered = self.controller.transformViewportToNormalized(
            vp_x=vp_box["x"],
            vp_y=vp_box["y"],
            vp_w=vp_box["width"],
            vp_h=vp_box["height"],
            item_width=1200.0,
            item_height=900.0,
            zoom=self.controller.zoom,
            pan_x=self.controller.panX,
            pan_y=self.controller.panY,
        )
        self.assertEqual(recovered["ymin"], 150)
        self.assertEqual(recovered["xmin"], 200)
        self.assertEqual(recovered["ymax"], 450)
        self.assertEqual(recovered["xmax"], 700)

    def test_load_page_sync_updates_state(self):
        raster_dto = PageRasterDTO(
            job_id=42,
            page_number=2,
            total_pages=5,
            raster_width=1200,
            raster_height=1600,
            image_uri="/tmp/polpot/artifacts/42/page_render_2.jpg",
            dpi=150,
        )
        region_dto = VisualRegionDTO(
            id=1,
            region_id="reg_123",
            job_id=42,
            page_number=2,
            display_order=1,
            origin="ai_detected",
            review_status="unreviewed",
            sync_status="synced",
            effective_bbox=BoundingBox(100, 100, 200, 200),
            detected_bbox=BoundingBox(100, 100, 200, 200),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="/tmp/crop_1.jpg",
            is_modified=False,
            is_deleted=False,
        )

        self.mock_service.get_page_raster.return_value = raster_dto
        self.mock_service.get_active_page_regions.return_value = [region_dto]

        self.controller.loadPageSync(42, 2)

        self.assertEqual(self.controller.currentJobId, 42)
        self.assertEqual(self.controller.currentPage, 2)
        self.assertEqual(self.controller.totalPages, 5)
        self.assertEqual(self.controller.rasterWidth, 1200)
        self.assertEqual(self.controller.rasterHeight, 1600)
        self.assertIn("page_render_2.jpg", self.controller.pageImageUri)
        self.assertFalse(self.controller.isLoading)
        self.assertEqual(len(self.controller.activeRegions), 1)
        self.assertEqual(self.controller.activeRegions[0]["region_id"], "reg_123")

    def test_stale_generation_request_dropped(self):
        """Simulate request 1 taking longer than request 2; result of request 1 must be dropped."""
        # Request 1 (req_id=1)
        self.controller._request_id = 1
        raster_1 = PageRasterDTO(1, 1, 5, 1000, 1000, "/tmp/p1.jpg", 150)

        # Request 2 (req_id=2) finishes first
        self.controller._request_id = 2
        raster_2 = PageRasterDTO(1, 2, 5, 2000, 2000, "/tmp/p2.jpg", 150)
        self.controller._on_internal_page_loaded(2, (raster_2, []))

        # Stale request 1 arrives later with req_id=1
        self.controller._on_internal_page_loaded(1, (raster_1, []))

        # Must display page 2 with raster width 2000, NOT stale page 1
        self.assertEqual(self.controller.currentPage, 2)
        self.assertEqual(self.controller.rasterWidth, 2000)
        self.assertIn("p2.jpg", self.controller.pageImageUri)

    def test_get_overlay_rects_transformation(self):
        # Manually populate controller state
        self.controller._raster_width = 1000
        self.controller._raster_height = 1000
        self.controller._active_regions = [
            {
                "region_id": "r1",
                "display_order": 1,
                "origin": "ai_detected",
                "review_status": "unreviewed",
                "effective_ymin": 100,
                "effective_xmin": 100,
                "effective_ymax": 300,
                "effective_xmax": 300,
            }
        ]

        # Item size 500x500
        rects = self.controller.getOverlayRects(500.0, 500.0, "preserve_aspect_fit")
        self.assertEqual(len(rects), 1)
        self.assertEqual(rects[0]["region_id"], "r1")
        self.assertAlmostEqual(rects[0]["x"], 50.0)
        self.assertAlmostEqual(rects[0]["y"], 50.0)
        self.assertAlmostEqual(rects[0]["width"], 100.0)
        self.assertAlmostEqual(rects[0]["height"], 100.0)

    def test_transform_viewport_to_normalized_inverse_slot(self):
        self.controller._raster_width = 1000
        self.controller._raster_height = 1000

        # Viewport rect (50, 50, 100, 100) on 500x500 item with zoom=1.0, pan=0.0
        bbox_dict = self.controller.transformViewportToNormalized(
            vp_x=50.0,
            vp_y=50.0,
            vp_w=100.0,
            vp_h=100.0,
            item_width=500.0,
            item_height=500.0,
            zoom=1.0,
            pan_x=0.0,
            pan_y=0.0,
        )
        self.assertEqual(bbox_dict["xmin"], 100)
        self.assertEqual(bbox_dict["ymin"], 100)
        self.assertEqual(bbox_dict["xmax"], 300)
        self.assertEqual(bbox_dict["ymax"], 300)
