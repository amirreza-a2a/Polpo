# ============================================================
#  tests/unit/test_phase10c_coordinates.py
#  Pure Coordinate Transformation Pipeline & Service Verification
# ============================================================

import ast
import math
import tempfile
import unittest
from pathlib import Path

from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
from core.entities.job import Job, JobStatus
from core.geometry.coordinates import (
    PointF,
    RectF,
    FitMode,
    DisplayedImageMetrics,
    ViewportMetrics,
    CoordinateTransformer,
)
from application.services.document_viewer_service import DocumentViewerService
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor


class TestCoordinateTransformations(unittest.TestCase):
    """Verifies closed-form mathematical transformations across S_norm, S_raster, S_item, S_viewport."""

    def test_normalized_to_raster_cases(self):
        """Test A: Top-left, center, bottom-right, small bbox, full-page bbox."""
        W, H = 1000.0, 2000.0

        # 1. Top-left: [0, 0, 100, 100]
        tl = BoundingBox(ymin=0, xmin=0, ymax=100, xmax=100)
        tl_rect = CoordinateTransformer.normalized_to_raster_rect(tl, W, H)
        self.assertAlmostEqual(tl_rect.x, 0.0)
        self.assertAlmostEqual(tl_rect.y, 0.0)
        self.assertAlmostEqual(tl_rect.width, 100.0)
        self.assertAlmostEqual(tl_rect.height, 200.0)

        # 2. Center: [450, 450, 550, 550]
        center = BoundingBox(ymin=450, xmin=450, ymax=550, xmax=550)
        center_rect = CoordinateTransformer.normalized_to_raster_rect(center, W, H)
        self.assertAlmostEqual(center_rect.x, 450.0)
        self.assertAlmostEqual(center_rect.y, 900.0)
        self.assertAlmostEqual(center_rect.width, 100.0)
        self.assertAlmostEqual(center_rect.height, 200.0)

        # 3. Bottom-right: [900, 900, 1000, 1000]
        br = BoundingBox(ymin=900, xmin=900, ymax=1000, xmax=1000)
        br_rect = CoordinateTransformer.normalized_to_raster_rect(br, W, H)
        self.assertAlmostEqual(br_rect.x, 900.0)
        self.assertAlmostEqual(br_rect.y, 1800.0)
        self.assertAlmostEqual(br_rect.width, 100.0)
        self.assertAlmostEqual(br_rect.height, 200.0)

        # 4. Small bbox: [10, 10, 15, 15]
        small = BoundingBox(ymin=10, xmin=10, ymax=15, xmax=15)
        small_rect = CoordinateTransformer.normalized_to_raster_rect(small, W, H)
        self.assertAlmostEqual(small_rect.x, 10.0)
        self.assertAlmostEqual(small_rect.y, 20.0)
        self.assertAlmostEqual(small_rect.width, 5.0)
        self.assertAlmostEqual(small_rect.height, 10.0)

        # 5. Full-page bbox: [0, 0, 1000, 1000]
        full = BoundingBox(ymin=0, xmin=0, ymax=1000, xmax=1000)
        full_rect = CoordinateTransformer.normalized_to_raster_rect(full, W, H)
        self.assertAlmostEqual(full_rect.x, 0.0)
        self.assertAlmostEqual(full_rect.y, 0.0)
        self.assertAlmostEqual(full_rect.width, W)
        self.assertAlmostEqual(full_rect.height, H)

    def test_raster_to_item_non_square_and_fit_modes(self):
        """Test B: Non-square images and items with PreserveAspectFit vs Stretch."""
        raster_w, raster_h = 1000.0, 1500.0
        item_w, item_h = 800.0, 600.0

        # FitMode.PRESERVE_ASPECT_FIT
        # sx = 800/1000 = 0.8, sy = 600/1500 = 0.4 -> scale = 0.4 (pillarboxed horizontally)
        metrics_fit = DisplayedImageMetrics(
            raster_width=raster_w,
            raster_height=raster_h,
            item_width=item_w,
            item_height=item_h,
            fit_mode=FitMode.PRESERVE_ASPECT_FIT,
        )
        self.assertAlmostEqual(metrics_fit.uniform_scale, 0.4)
        self.assertAlmostEqual(metrics_fit.displayed_width, 400.0)
        self.assertAlmostEqual(metrics_fit.displayed_height, 600.0)
        self.assertAlmostEqual(metrics_fit.offset_x, 200.0)  # (800 - 400) / 2
        self.assertAlmostEqual(metrics_fit.offset_y, 0.0)

        raster_rect = RectF(x=100.0, y=150.0, width=200.0, height=300.0)
        item_rect = CoordinateTransformer.raster_to_item_rect(raster_rect, metrics_fit)
        self.assertAlmostEqual(item_rect.x, 200.0 + (100.0 * 0.4))
        self.assertAlmostEqual(item_rect.y, 0.0 + (150.0 * 0.4))
        self.assertAlmostEqual(item_rect.width, 200.0 * 0.4)
        self.assertAlmostEqual(item_rect.height, 300.0 * 0.4)

        # FitMode.STRETCH
        metrics_stretch = DisplayedImageMetrics(
            raster_width=raster_w,
            raster_height=raster_h,
            item_width=item_w,
            item_height=item_h,
            fit_mode=FitMode.STRETCH,
        )
        item_rect_stretch = CoordinateTransformer.raster_to_item_rect(raster_rect, metrics_stretch)
        self.assertAlmostEqual(item_rect_stretch.x, 100.0 * (800.0 / 1000.0))
        self.assertAlmostEqual(item_rect_stretch.y, 150.0 * (600.0 / 1500.0))
        self.assertAlmostEqual(item_rect_stretch.width, 200.0 * 0.8)
        self.assertAlmostEqual(item_rect_stretch.height, 300.0 * 0.4)

    def test_fit_and_letterbox_offsets(self):
        """Test C: Aspect ratio differences producing letterbox (top/bottom) and pillarbox (left/right)."""
        # 1. Letterbox case: Wide image (1200x600) in square container (600x600)
        # sx = 600/1200 = 0.5, sy = 600/600 = 1.0 -> scale = 0.5
        # displayed: 600 x 300 -> offset_x = 0, offset_y = 150
        m_letterbox = DisplayedImageMetrics(1200, 600, 600, 600, FitMode.PRESERVE_ASPECT_FIT)
        self.assertAlmostEqual(m_letterbox.uniform_scale, 0.5)
        self.assertAlmostEqual(m_letterbox.displayed_width, 600.0)
        self.assertAlmostEqual(m_letterbox.displayed_height, 300.0)
        self.assertAlmostEqual(m_letterbox.offset_x, 0.0)
        self.assertAlmostEqual(m_letterbox.offset_y, 150.0)

        # 2. Pillarbox case: Tall image (600x1200) in square container (600x600)
        # sx = 600/600 = 1.0, sy = 600/1200 = 0.5 -> scale = 0.5
        # displayed: 300 x 600 -> offset_x = 150, offset_y = 0
        m_pillarbox = DisplayedImageMetrics(600, 1200, 600, 600, FitMode.PRESERVE_ASPECT_FIT)
        self.assertAlmostEqual(m_pillarbox.uniform_scale, 0.5)
        self.assertAlmostEqual(m_pillarbox.displayed_width, 300.0)
        self.assertAlmostEqual(m_pillarbox.displayed_height, 600.0)
        self.assertAlmostEqual(m_pillarbox.offset_x, 150.0)
        self.assertAlmostEqual(m_pillarbox.offset_y, 0.0)

    def test_zoom_scaling(self):
        """Test D: Zoom factors (0.5, 1.0, 2.0) scale item coordinates proportionally."""
        item_rect = RectF(x=50.0, y=100.0, width=200.0, height=300.0)

        for zoom in [0.5, 1.0, 2.0]:
            viewport = ViewportMetrics(zoom=zoom, pan_x=0.0, pan_y=0.0)
            vp_rect = CoordinateTransformer.item_to_viewport_rect(item_rect, viewport)
            self.assertAlmostEqual(vp_rect.x, item_rect.x * zoom)
            self.assertAlmostEqual(vp_rect.y, item_rect.y * zoom)
            self.assertAlmostEqual(vp_rect.width, item_rect.width * zoom)
            self.assertAlmostEqual(vp_rect.height, item_rect.height * zoom)

    def test_pan_offset_preserves_geometry(self):
        """Test E: Panning shifts viewport coordinates without altering underlying geometry."""
        item_rect = RectF(x=100.0, y=100.0, width=200.0, height=200.0)
        viewport = ViewportMetrics(zoom=1.5, pan_x=50.0, pan_y=30.0)

        vp_rect = CoordinateTransformer.item_to_viewport_rect(item_rect, viewport)
        self.assertAlmostEqual(vp_rect.x, (100.0 * 1.5) - 50.0)
        self.assertAlmostEqual(vp_rect.y, (100.0 * 1.5) - 30.0)
        self.assertAlmostEqual(vp_rect.width, 200.0 * 1.5)
        self.assertAlmostEqual(vp_rect.height, 200.0 * 1.5)

        # Inverse mapping returns exact original item rect
        inv_item_rect = CoordinateTransformer.viewport_to_item_rect(vp_rect, viewport)
        self.assertAlmostEqual(inv_item_rect.x, item_rect.x)
        self.assertAlmostEqual(inv_item_rect.y, item_rect.y)
        self.assertAlmostEqual(inv_item_rect.width, item_rect.width)
        self.assertAlmostEqual(inv_item_rect.height, item_rect.height)

    def test_round_trip_accuracy(self):
        """
        Test F: S_norm -> S_raster -> S_item -> S_viewport -> inverse -> S_norm.
        Verified within floating-point tolerance (< 1e-4) and exact integer BoundingBox recovery.
        """
        original_bbox = BoundingBox(ymin=123, xmin=234, ymax=678, xmax=789)
        raster_w, raster_h = 1200.0, 1800.0
        item_w, item_h = 900.0, 700.0
        metrics = DisplayedImageMetrics(raster_w, raster_h, item_w, item_h, FitMode.PRESERVE_ASPECT_FIT)
        viewport = ViewportMetrics(zoom=1.75, pan_x=45.0, pan_y=60.0)

        # Forward
        vp_rect = CoordinateTransformer.normalized_to_viewport_rect(original_bbox, metrics, viewport)

        # Inverse
        recovered_bbox = CoordinateTransformer.viewport_to_normalized_bbox(vp_rect, metrics, viewport)

        self.assertEqual(recovered_bbox.ymin, original_bbox.ymin)
        self.assertEqual(recovered_bbox.xmin, original_bbox.xmin)
        self.assertEqual(recovered_bbox.ymax, original_bbox.ymax)
        self.assertEqual(recovered_bbox.xmax, original_bbox.xmax)

    def test_viewport_metrics_pan_bounds_and_clamping(self):
        """Test G: Pan bounds calculation and clamping for larger/smaller content."""
        # Content > Viewport: 1000 >= 600 -> min_pan = 0, max_pan = 400
        min_p, max_p = ViewportMetrics.compute_axis_pan_bounds(1000.0, 600.0)
        self.assertAlmostEqual(min_p, 0.0)
        self.assertAlmostEqual(max_p, 400.0)

        # Content < Viewport: 400 < 800 -> min_pan = max_pan = -200 (centered)
        min_p, max_p = ViewportMetrics.compute_axis_pan_bounds(400.0, 800.0)
        self.assertAlmostEqual(min_p, -200.0)
        self.assertAlmostEqual(max_p, -200.0)

        # Clamping via ViewportMetrics instance
        vm = ViewportMetrics(zoom=2.0, pan_x=0.0, pan_y=0.0, viewport_width=500.0, viewport_height=400.0)
        # Content base size 400x300 -> zoomed content size 800x600
        # Valid bounds: X in [0, 300], Y in [0, 200]
        (min_x, max_x), (min_y, max_y) = vm.get_pan_bounds(400.0, 300.0)
        self.assertAlmostEqual(min_x, 0.0)
        self.assertAlmostEqual(max_x, 300.0)
        self.assertAlmostEqual(min_y, 0.0)
        self.assertAlmostEqual(max_y, 200.0)

        clamped_x, clamped_y = vm.clamp_pan(-50.0, 999.0, 400.0, 300.0)
        self.assertAlmostEqual(clamped_x, 0.0)
        self.assertAlmostEqual(clamped_y, 200.0)

    def test_zoom_around_anchor_mathematical_invariance(self):
        """Test H: Anchor point in viewport remains at identical viewport position after zoom."""
        vm = ViewportMetrics(zoom=1.0, pan_x=50.0, pan_y=30.0, viewport_width=800.0, viewport_height=600.0)
        anchor_x, anchor_y = 200.0, 150.0

        # Anchor maps to item space before zoom:
        item_pt = CoordinateTransformer.viewport_to_item_point(PointF(anchor_x, anchor_y), vm)

        # Zoom to 2.5
        new_vm = vm.zoom_around_anchor(
            new_zoom=2.5,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            content_base_width=1000.0,
            content_base_height=800.0,
        )

        # Forward transform of item_pt with new_vm must recover anchor (anchor_x, anchor_y)
        new_vp_pt = CoordinateTransformer.item_to_viewport_point(item_pt, new_vm)
        self.assertAlmostEqual(new_vp_pt.x, anchor_x)
        self.assertAlmostEqual(new_vp_pt.y, anchor_y)


class TestDocumentViewerServiceAndOverlay(unittest.TestCase):
    """Verifies DocumentViewerService queries, page isolation, multiple regions, and rejected region filtering."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.artifacts_dir = Path(self.tmp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.doc_processor = PyMuPDFDocumentProcessor()
        self.service = DocumentViewerService(self.uow_factory, self.storage, self.doc_processor)

        # Create sample test job
        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="sample.pdf",
                file_path="sample.pdf",
                total_pages=3,
                status=JobStatus.DONE,
            )
            self.job = uow.jobs.save(job)
            uow.commit()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_multiple_regions_with_identical_coordinates_independently_identified(self):
        """Test G: Two regions with duplicate coordinates but distinct region_id remain distinct."""
        bbox = BoundingBox(100, 100, 300, 300)
        with self.uow_factory.create() as uow:
            r1 = VisualRegion.create_ai_detected(self.job.id, page_number=1, display_order=1, detected_bbox=bbox)
            r2 = VisualRegion.create_ai_detected(self.job.id, page_number=1, display_order=2, detected_bbox=bbox)
            uow.visual_regions.save_all([r1, r2])
            uow.commit()
            r1_id = r1.region_id
            r2_id = r2.region_id

        overlay_rects = self.service.calculate_page_overlay_rects(
            job_id=self.job.id,
            page_number=1,
            item_width=800,
            item_height=600,
            raster_width=1000,
            raster_height=1000,
        )

        self.assertEqual(len(overlay_rects), 2)
        self.assertEqual(overlay_rects[0].region_id, r1_id)
        self.assertEqual(overlay_rects[1].region_id, r2_id)
        self.assertNotEqual(overlay_rects[0].region_id, overlay_rects[1].region_id)
        self.assertEqual(overlay_rects[0].x, overlay_rects[1].x)
        self.assertEqual(overlay_rects[0].y, overlay_rects[1].y)

    def test_page_isolation(self):
        """Test H: A region on page N never appears in page N+1."""
        with self.uow_factory.create() as uow:
            r_p1 = VisualRegion.create_ai_detected(
                self.job.id, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 20, 20)
            )
            r_p2 = VisualRegion.create_ai_detected(
                self.job.id, page_number=2, display_order=1, detected_bbox=BoundingBox(30, 30, 40, 40)
            )
            uow.visual_regions.save_all([r_p1, r_p2])
            uow.commit()

        p1_regions = self.service.get_active_page_regions(self.job.id, page_number=1)
        p2_regions = self.service.get_active_page_regions(self.job.id, page_number=2)
        p3_regions = self.service.get_active_page_regions(self.job.id, page_number=3)

        self.assertEqual(len(p1_regions), 1)
        self.assertEqual(p1_regions[0].region_id, r_p1.region_id)

        self.assertEqual(len(p2_regions), 1)
        self.assertEqual(p2_regions[0].region_id, r_p2.region_id)

        self.assertEqual(len(p3_regions), 0)

    def test_rejected_regions_excluded_from_active_overlay(self):
        """Test I: Rejected regions (is_deleted=True) are not rendered as active overlays."""
        with self.uow_factory.create() as uow:
            r_active = VisualRegion.create_ai_detected(
                self.job.id, page_number=1, display_order=1, detected_bbox=BoundingBox(100, 100, 200, 200)
            )
            r_rejected = VisualRegion.create_ai_detected(
                self.job.id, page_number=1, display_order=2, detected_bbox=BoundingBox(300, 300, 400, 400)
            )
            r_rejected.reject()
            uow.visual_regions.save_all([r_active, r_rejected])
            uow.commit()

        active_regions = self.service.get_active_page_regions(self.job.id, page_number=1)
        self.assertEqual(len(active_regions), 1)
        self.assertEqual(active_regions[0].region_id, r_active.region_id)


class TestPhase10CArchitectureGuards(unittest.TestCase):
    """Test J: Enforces clean architecture rules and verifies zero Qt imports in core and application."""

    def test_zero_qt_imports_in_core_geometry(self):
        geom_path = Path(__file__).parents[2] / "core" / "geometry" / "coordinates.py"
        self.assertTrue(geom_path.exists())
        tree = ast.parse(geom_path.read_text(encoding="utf-8"))

        forbidden = {"PySide6", "PyQt6", "PyQt5", "QtCore", "QtGui", "QtWidgets", "QtQml", "QtQuick"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for f in forbidden:
                        self.assertNotIn(f, alias.name, f"Forbidden Qt import '{alias.name}' found in {geom_path}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for f in forbidden:
                        self.assertNotIn(f, node.module, f"Forbidden Qt import '{node.module}' found in {geom_path}")

    def test_zero_qt_imports_in_document_viewer_service(self):
        svc_path = Path(__file__).parents[2] / "application" / "services" / "document_viewer_service.py"
        self.assertTrue(svc_path.exists())
        tree = ast.parse(svc_path.read_text(encoding="utf-8"))

        forbidden = {"PySide6", "PyQt6", "PyQt5", "QtCore", "QtGui", "QtWidgets", "QtQml", "QtQuick"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for f in forbidden:
                        self.assertNotIn(f, alias.name, f"Forbidden Qt import '{alias.name}' found in {svc_path}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for f in forbidden:
                        self.assertNotIn(f, node.module, f"Forbidden Qt import '{node.module}' found in {svc_path}")
