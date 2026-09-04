# ============================================================
#  tests/unit/test_phase10d_box_editor.py
#  Pure Geometry & Bounding Box Editor Invariant Tests
# ============================================================

import unittest

from core.entities.bounding_box import BoundingBox, InvalidBoundingBoxError
from core.geometry.coordinates import PointF, RectF, FitMode, DisplayedImageMetrics, ViewportMetrics, CoordinateTransformer
from core.geometry.box_editor import (
    BoxGeometryEditor,
    HandleType,
    MIN_NORMALIZED_DIMENSION,
    PAGE_BOUND_MIN,
    PAGE_BOUND_MAX,
)


class TestBoxGeometryEditor(unittest.TestCase):
    """Verifies pure mathematical operations for translating, resizing, and creating bounding boxes."""

    def setUp(self):
        self.initial_bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)

    def test_translate_bbox_basic(self):
        """Translating by (delta_x, delta_y) preserves width and height strictly."""
        w = self.initial_bbox.width
        h = self.initial_bbox.height

        translated = BoxGeometryEditor.translate_bbox(self.initial_bbox, delta_x=50.0, delta_y=80.0)
        self.assertEqual(translated.xmin, 150)
        self.assertEqual(translated.ymin, 180)
        self.assertEqual(translated.xmax, 450)
        self.assertEqual(translated.ymax, 380)
        self.assertEqual(translated.width, w)
        self.assertEqual(translated.height, h)

    def test_translate_bbox_clamping_page_bounds(self):
        """Translation cannot push any edge outside [0, 1000], preserving box dimensions."""
        w = self.initial_bbox.width   # 300
        h = self.initial_bbox.height  # 200

        # Push past top-left
        tl = BoxGeometryEditor.translate_bbox(self.initial_bbox, delta_x=-200.0, delta_y=-300.0)
        self.assertEqual(tl.xmin, 0)
        self.assertEqual(tl.ymin, 0)
        self.assertEqual(tl.xmax, w)
        self.assertEqual(tl.ymax, h)

        # Push past bottom-right
        br = BoxGeometryEditor.translate_bbox(self.initial_bbox, delta_x=800.0, delta_y=900.0)
        self.assertEqual(br.xmax, 1000)
        self.assertEqual(br.ymax, 1000)
        self.assertEqual(br.xmin, 1000 - w)
        self.assertEqual(br.ymin, 1000 - h)

    def test_resize_corner_nw(self):
        """NW handle modifies xmin and ymin, preserving opposite corner (xmax, ymax)."""
        target = PointF(x=50.0, y=80.0)
        resized = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.NW.value, target)
        self.assertEqual(resized.xmin, 50)
        self.assertEqual(resized.ymin, 80)
        self.assertEqual(resized.xmax, 400)
        self.assertEqual(resized.ymax, 300)

    def test_resize_corner_ne(self):
        """NE handle modifies xmax and ymin, preserving opposite corner (xmin, ymax)."""
        target = PointF(x=500.0, y=50.0)
        resized = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.NE.value, target)
        self.assertEqual(resized.xmin, 100)
        self.assertEqual(resized.ymin, 50)
        self.assertEqual(resized.xmax, 500)
        self.assertEqual(resized.ymax, 300)

    def test_resize_corner_sw(self):
        """SW handle modifies xmin and ymax, preserving opposite corner (xmax, ymin)."""
        target = PointF(x=80.0, y=450.0)
        resized = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.SW.value, target)
        self.assertEqual(resized.xmin, 80)
        self.assertEqual(resized.ymin, 100)
        self.assertEqual(resized.xmax, 400)
        self.assertEqual(resized.ymax, 450)

    def test_resize_corner_se(self):
        """SE handle modifies xmax and ymax, preserving opposite corner (xmin, ymin)."""
        target = PointF(x=600.0, y=500.0)
        resized = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.SE.value, target)
        self.assertEqual(resized.xmin, 100)
        self.assertEqual(resized.ymin, 100)
        self.assertEqual(resized.xmax, 600)
        self.assertEqual(resized.ymax, 500)

    def test_resize_center_handles_single_axis(self):
        """Center handles (n, s, w, e) modify strictly their respective axis."""
        # N handle modifies only ymin
        n_res = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.N.value, PointF(x=999, y=50))
        self.assertEqual(n_res.ymin, 50)
        self.assertEqual(n_res.ymax, 300)
        self.assertEqual(n_res.xmin, 100)
        self.assertEqual(n_res.xmax, 400)

        # S handle modifies only ymax
        s_res = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.S.value, PointF(x=999, y=450))
        self.assertEqual(s_res.ymin, 100)
        self.assertEqual(s_res.ymax, 450)
        self.assertEqual(s_res.xmin, 100)
        self.assertEqual(s_res.xmax, 400)

        # W handle modifies only xmin
        w_res = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.W.value, PointF(x=20, y=999))
        self.assertEqual(w_res.xmin, 20)
        self.assertEqual(w_res.xmax, 400)
        self.assertEqual(w_res.ymin, 100)
        self.assertEqual(w_res.ymax, 300)

        # E handle modifies only xmax
        e_res = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.E.value, PointF(x=550, y=999))
        self.assertEqual(e_res.xmin, 100)
        self.assertEqual(e_res.xmax, 550)
        self.assertEqual(e_res.ymin, 100)
        self.assertEqual(e_res.ymax, 300)

    def test_resize_prevent_inversion_and_enforce_min_dimension(self):
        """Dragging handles past opposite bounds does not invert rectangle; enforces MIN_NORMALIZED_DIMENSION."""
        # Drag NW far past bottom-right (e.g. to (800, 800))
        res = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.NW.value, PointF(x=800, y=800))
        self.assertLessEqual(res.xmin, res.xmax)
        self.assertLessEqual(res.ymin, res.ymax)
        self.assertGreaterEqual(res.width, MIN_NORMALIZED_DIMENSION)
        self.assertGreaterEqual(res.height, MIN_NORMALIZED_DIMENSION)
        self.assertEqual(res.xmax, 400)
        self.assertEqual(res.ymax, 300)
        self.assertEqual(res.xmin, 400 - MIN_NORMALIZED_DIMENSION)
        self.assertEqual(res.ymin, 300 - MIN_NORMALIZED_DIMENSION)

        # Drag SE far past top-left (e.g. to (0, 0))
        res_se = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.SE.value, PointF(x=0, y=0))
        self.assertEqual(res_se.xmin, 100)
        self.assertEqual(res_se.ymin, 100)
        self.assertEqual(res_se.xmax, 100 + MIN_NORMALIZED_DIMENSION)
        self.assertEqual(res_se.ymax, 100 + MIN_NORMALIZED_DIMENSION)

    def test_resize_clamping_to_page_edges(self):
        """Dragging handles beyond page edges [0, 1000] clamps safely."""
        res_nw = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.NW.value, PointF(x=-100, y=-50))
        self.assertEqual(res_nw.xmin, 0)
        self.assertEqual(res_nw.ymin, 0)

        res_se = BoxGeometryEditor.resize_bbox(self.initial_bbox, HandleType.SE.value, PointF(x=1200, y=1500))
        self.assertEqual(res_se.xmax, 1000)
        self.assertEqual(res_se.ymax, 1000)

    def test_manual_bbox_creation_all_drag_directions(self):
        """Manual creation normalizes coordinates regardless of drag direction."""
        # Top-left to bottom-right
        b1 = BoxGeometryEditor.create_manual_bbox(PointF(100, 150), PointF(300, 400))
        self.assertIsNotNone(b1)
        self.assertEqual(b1.xmin, 100)
        self.assertEqual(b1.ymin, 150)
        self.assertEqual(b1.xmax, 300)
        self.assertEqual(b1.ymax, 400)

        # Bottom-right to top-left (reverse)
        b2 = BoxGeometryEditor.create_manual_bbox(PointF(300, 400), PointF(100, 150))
        self.assertIsNotNone(b2)
        self.assertEqual(b2.xmin, 100)
        self.assertEqual(b2.ymin, 150)
        self.assertEqual(b2.xmax, 300)
        self.assertEqual(b2.ymax, 400)

        # Top-right to bottom-left
        b3 = BoxGeometryEditor.create_manual_bbox(PointF(300, 150), PointF(100, 400))
        self.assertIsNotNone(b3)
        self.assertEqual(b3.xmin, 100)
        self.assertEqual(b3.ymin, 150)
        self.assertEqual(b3.xmax, 300)
        self.assertEqual(b3.ymax, 400)

    def test_manual_bbox_creation_min_dimension_rejection(self):
        """Manual creation returns None if width or height is below MIN_NORMALIZED_DIMENSION."""
        # Zero area click
        self.assertIsNone(BoxGeometryEditor.create_manual_bbox(PointF(100, 100), PointF(100, 100)))

        # Tiny sliver (width = 3 < MIN_NORMALIZED_DIMENSION)
        self.assertIsNone(BoxGeometryEditor.create_manual_bbox(PointF(100, 100), PointF(103, 200)))

        # Tiny sliver (height = 2 < MIN_NORMALIZED_DIMENSION)
        self.assertIsNone(BoxGeometryEditor.create_manual_bbox(PointF(100, 100), PointF(200, 102)))

        # Exactly MIN_NORMALIZED_DIMENSION
        valid = BoxGeometryEditor.create_manual_bbox(
            PointF(100, 100),
            PointF(100 + MIN_NORMALIZED_DIMENSION, 100 + MIN_NORMALIZED_DIMENSION),
        )
        self.assertIsNotNone(valid)
        self.assertEqual(valid.width, MIN_NORMALIZED_DIMENSION)
        self.assertEqual(valid.height, MIN_NORMALIZED_DIMENSION)

    def test_manual_bbox_creation_clamped_at_page_edges(self):
        """Manual dragging outside page [0, 1000] clamps to page boundaries."""
        b = BoxGeometryEditor.create_manual_bbox(PointF(-50, -30), PointF(1100, 1200))
        self.assertIsNotNone(b)
        self.assertEqual(b.xmin, 0)
        self.assertEqual(b.ymin, 0)
        self.assertEqual(b.xmax, 1000)
        self.assertEqual(b.ymax, 1000)

    def test_compute_8_handles_geometry(self):
        """compute_8_handles returns 8 correctly placed, centered handle rectangles."""
        item_rect = RectF(x=100.0, y=200.0, width=400.0, height=300.0)
        handles = BoxGeometryEditor.compute_8_handles(item_rect, handle_size=8.0)

        self.assertEqual(len(handles), 8)
        for h_type in HandleType:
            self.assertIn(h_type.value, handles)
            rect = handles[h_type.value]
            self.assertEqual(rect.width, 8.0)
            self.assertEqual(rect.height, 8.0)

        # nw centered at (100, 200) -> x = 100 - 4 = 96, y = 200 - 4 = 196
        self.assertAlmostEqual(handles["nw"].x, 96.0)
        self.assertAlmostEqual(handles["nw"].y, 196.0)

        # n centered at (100 + 200, 200) = (300, 200) -> x = 296, y = 196
        self.assertAlmostEqual(handles["n"].x, 296.0)
        self.assertAlmostEqual(handles["n"].y, 196.0)

        # se centered at (500, 500) -> x = 496, y = 496
        self.assertAlmostEqual(handles["se"].x, 496.0)
        self.assertAlmostEqual(handles["se"].y, 496.0)

    def test_zoomed_panned_letterboxed_viewport_conversion(self):
        """End-to-end integration of viewport coordinates back to semantic normalized bbox."""
        raster_w, raster_h = 1000.0, 1000.0
        item_w, item_h = 800.0, 800.0
        metrics = DisplayedImageMetrics(
            raster_width=raster_w,
            raster_height=raster_h,
            item_width=item_w,
            item_height=item_h,
            fit_mode=FitMode.PRESERVE_ASPECT_FIT,
        )
        viewport = ViewportMetrics(zoom=2.0, pan_x=100.0, pan_y=50.0)

        # Create an original bbox in S_norm
        orig_bbox = BoundingBox(ymin=200, xmin=200, ymax=400, xmax=600)

        # Forward projection to S_viewport
        vp_rect = CoordinateTransformer.normalized_to_viewport_rect(orig_bbox, metrics, viewport)

        # Backward inverse projection back to S_norm
        inv_bbox = CoordinateTransformer.viewport_to_normalized_bbox(vp_rect, metrics, viewport)

        self.assertEqual(inv_bbox.ymin, orig_bbox.ymin)
        self.assertEqual(inv_bbox.xmin, orig_bbox.xmin)
        self.assertEqual(inv_bbox.ymax, orig_bbox.ymax)
        self.assertEqual(inv_bbox.xmax, orig_bbox.xmax)
