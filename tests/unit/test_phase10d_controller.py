# ============================================================
#  tests/unit/test_phase10d_controller.py
#  Phase 10D Presentation Controller Interaction Lifecycle Tests
# ============================================================

import unittest
from unittest.mock import MagicMock

from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import RegionOrigin, ReviewStatus
from core.geometry.coordinates import FitMode
from application.dto.document_viewer_dto import PageRasterDTO, VisualRegionOverlayItemDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from application.services.document_viewer_service import DocumentViewerService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.qt_compat import QGuiApplication


class TestPhase10DDocumentViewerController(unittest.TestCase):
    """Verifies DocumentViewerController selection, drag, resize, manual creation, and deletion lifecycles."""

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.controller = DocumentViewerController(viewer_service=self.mock_service)

        # Configure default viewport & raster dimensions
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setItemDimensions(800.0, 600.0)

        # Setup mock active regions for page 1
        self.r1_dto = VisualRegionDTO(
            id=1,
            region_id="uuid-region-1",
            job_id=42,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(100, 100, 300, 400),
            detected_bbox=BoundingBox(100, 100, 300, 400),
            reviewed_bbox=None,
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=False,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.r2_dto = VisualRegionDTO(
            id=2,
            region_id="uuid-region-2",
            job_id=42,
            page_number=1,
            display_order=2,
            origin=RegionOrigin.USER_MANUAL.value,
            review_status=ReviewStatus.MANUAL.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(500, 500, 700, 700),
            detected_bbox=None,
            reviewed_bbox=BoundingBox(500, 500, 700, 700),
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=True,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.mock_service.get_active_page_regions.return_value = [self.r1_dto, self.r2_dto]
        self.mock_service.get_page_raster.return_value = PageRasterDTO(
            job_id=42,
            page_number=1,
            total_pages=3,
            raster_width=1000,
            raster_height=1000,
            image_uri="file:///tmp/page_1.png",
            dpi=150,
        )

        # Load page synchronously to populate controller
        self.controller.loadPageSync(42, 1)

    def test_selection_lifecycle(self):
        """Selecting and deselecting regions updates state and emits signals."""
        self.assertFalse(self.controller.hasSelection)
        self.assertEqual(self.controller.selectedRegionId, "")
        self.assertEqual(self.controller.editorState, "idle")

        # Select r1
        self.controller.selectRegion("uuid-region-1")
        self.assertTrue(self.controller.hasSelection)
        self.assertEqual(self.controller.selectedRegionId, "uuid-region-1")
        self.assertEqual(self.controller.editorState, "selected")
        self.assertEqual(self.controller.selectedRegion["region_id"], "uuid-region-1")
        self.assertFalse(self.controller.canResetSelectedToAi)  # Not modified yet

        # Clear selection
        self.controller.clearSelection()
        self.assertFalse(self.controller.hasSelection)
        self.assertEqual(self.controller.selectedRegionId, "")
        self.assertEqual(self.controller.editorState, "idle")

    def test_drag_lifecycle_and_commit_on_release(self):
        """Drag translates in-memory transient bbox without saving; commit persists on release."""
        # Initial overlay rects before drag
        initial_rects = self.controller.getOverlayRects(800.0, 600.0)
        self.assertEqual(len(initial_rects), 2)
        r1_init = initial_rects[0]

        # Start drag at center of region
        self.controller.startDrag("uuid-region-1", vp_x=400.0, vp_y=300.0)
        self.assertEqual(self.controller.editorState, "dragging")
        self.assertEqual(self.controller.selectedRegionId, "uuid-region-1")
        self.assertIsNotNone(self.controller.transientBox)

        # Update drag (mouse move)
        self.controller.updateDrag(vp_x=450.0, vp_y=350.0)
        # Service MUST NOT have been called yet (zero persistence on move!)
        self.mock_service.update_region_geometry.assert_not_called()

        # Transient box has moved
        self.assertNotEqual(
            (self.controller.transientBox["ymin"], self.controller.transientBox["xmin"]),
            (r1_init["effective_ymin"], r1_init["effective_xmin"]),
        )

        # Commit drag (mouse release)
        self.controller.commitDrag()

        # Service was called ONCE on release with transient box
        self.mock_service.update_region_geometry.assert_called_once()
        call_args = self.mock_service.update_region_geometry.call_args[0]
        self.assertEqual(call_args[0], "uuid-region-1")
        self.assertIsInstance(call_args[1], BoundingBox)

        # Editor state returned to selected
        self.assertEqual(self.controller.editorState, "selected")
        self.assertEqual(self.controller.transientBox, {})

    def test_drag_cancel_discards_transient_box(self):
        """Canceling drag discards in-memory transient modifications without calling persistence."""
        self.controller.startDrag("uuid-region-1", vp_x=400.0, vp_y=300.0)
        self.controller.updateDrag(vp_x=500.0, vp_y=400.0)
        self.assertEqual(self.controller.editorState, "dragging")

        self.controller.cancelDrag()
        self.mock_service.update_region_geometry.assert_not_called()
        self.assertEqual(self.controller.editorState, "selected")
        self.assertEqual(self.controller.transientBox, {})

    def test_resize_lifecycle_and_commit_on_release(self):
        """Resize via handle updates in-memory transient box and persists on release."""
        # Start resize with SE handle
        self.controller.startResize("uuid-region-1", "se", vp_x=300.0, vp_y=200.0)
        self.assertEqual(self.controller.editorState, "resizing")
        self.assertEqual(self.controller.activeHandle, "se")

        # Update resize
        self.controller.updateResize(vp_x=350.0, vp_y=250.0)
        self.mock_service.update_region_geometry.assert_not_called()

        # Commit resize
        self.controller.commitResize()
        self.mock_service.update_region_geometry.assert_called_once()
        self.assertEqual(self.controller.editorState, "selected")
        self.assertEqual(self.controller.activeHandle, "")

    def test_manual_creation_lifecycle(self):
        """Dragging over empty page area creates a manual visual region."""
        new_region_dto = VisualRegionDTO(
            id=3,
            region_id="uuid-new-manual",
            job_id=42,
            page_number=1,
            display_order=3,
            origin=RegionOrigin.USER_MANUAL.value,
            review_status=ReviewStatus.MANUAL.value,
            sync_status="synchronized",
            effective_bbox=BoundingBox(150, 150, 350, 350),
            detected_bbox=None,
            reviewed_bbox=BoundingBox(150, 150, 350, 350),
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=True,
            is_deleted=False,
            created_at=None,
            updated_at=None,
        )
        self.mock_service.create_manual_region.return_value = new_region_dto
        self.mock_service.get_active_page_regions.return_value = [self.r1_dto, self.r2_dto, new_region_dto]

        # Start manual creation on empty space
        self.controller.startCreateManual(vp_x=200.0, vp_y=200.0)
        self.assertEqual(self.controller.editorState, "creating")

        # Drag cursor to draw box
        self.controller.updateCreateManual(vp_x=350.0, vp_y=350.0)
        self.mock_service.create_manual_region.assert_not_called()
        self.assertIsNotNone(self.controller.transientBox)
        self.assertIn("width", self.controller.transientItemRect)

        # Commit on mouse release
        self.controller.commitCreateManual()
        self.mock_service.create_manual_region.assert_called_once()
        # Created region becomes automatically selected
        self.assertEqual(self.controller.selectedRegionId, "uuid-new-manual")
        self.assertEqual(self.controller.editorState, "selected")

    def test_manual_creation_too_small_cancels_cleanly(self):
        """Clicking or dragging smaller than minimum size aborts without creating a region."""
        self.controller.startCreateManual(vp_x=200.0, vp_y=200.0)
        # Mouse moved only 1px
        self.controller.updateCreateManual(vp_x=201.0, vp_y=201.0)
        self.assertEqual(self.controller.transientBox, {})

        self.controller.commitCreateManual()
        self.mock_service.create_manual_region.assert_not_called()
        self.assertEqual(self.controller.editorState, "idle")

    def test_delete_selected_region(self):
        """deleteSelectedRegion rejects the region, clears selection, and reloads overlay."""
        self.controller.selectRegion("uuid-region-1")
        self.assertTrue(self.controller.hasSelection)

        # Configure mock to exclude deleted region
        self.mock_service.get_active_page_regions.return_value = [self.r2_dto]

        self.controller.deleteSelectedRegion()
        self.mock_service.reject_region.assert_called_once_with("uuid-region-1")
        self.assertFalse(self.controller.hasSelection)
        self.assertEqual(self.controller.editorState, "idle")
        self.assertEqual(len(self.controller.activeRegions), 1)
        self.assertEqual(self.controller.activeRegions[0]["region_id"], "uuid-region-2")

    def test_reset_selected_region_to_ai(self):
        """resetSelectedRegionToAi resets modified region back to AI detected geometry."""
        # Mark r1 as modified in controller's active regions
        self.controller._active_regions[0]["is_modified"] = True
        self.controller.selectRegion("uuid-region-1")
        self.assertTrue(self.controller.canResetSelectedToAi)

        self.controller.resetSelectedRegionToAi()
        self.mock_service.reset_region_to_ai.assert_called_once_with("uuid-region-1")

    def test_persistence_failure_surfaces_error_without_corrupting_state(self):
        """Persistence failure sets errorMessage and recovers without partially-committed state."""
        self.mock_service.update_region_geometry.side_effect = RuntimeError("SQLite disk full")

        self.controller.startDrag("uuid-region-1", vp_x=400.0, vp_y=300.0)
        self.controller.updateDrag(vp_x=450.0, vp_y=350.0)
        self.controller.commitDrag()

        self.assertIn("SQLite disk full", self.controller.errorMessage)
        self.assertEqual(self.controller.editorState, "selected")
        self.assertEqual(self.controller.transientBox, {})

    def test_get_handle_rects_slot(self):
        """getHandleRects returns 8 handle dictionaries for QML."""
        rects = self.controller.getHandleRects(100.0, 100.0, 200.0, 150.0, 8.0)
        self.assertEqual(len(rects), 8)
        handles_found = {r["handle"] for r in rects}
        self.assertEqual(handles_found, {"nw", "n", "ne", "w", "e", "sw", "s", "se"})

    def test_get_displayed_rect_slot(self):
        """getDisplayedRect accurately calculates letterboxed/pillarboxed rendering bounds."""
        # 1000x1000 raster in 1000x800 container (preserve aspect fit)
        self.controller._raster_width = 1000
        self.controller._raster_height = 1000
        rect = self.controller.getDisplayedRect(1000.0, 800.0, "preserve_aspect_fit")
        self.assertEqual(rect["x"], 100.0)
        self.assertEqual(rect["y"], 0.0)
        self.assertEqual(rect["width"], 800.0)
        self.assertEqual(rect["height"], 800.0)

        # Stretch fit
        rect_stretch = self.controller.getDisplayedRect(1000.0, 800.0, "stretch")
        self.assertEqual(rect_stretch["x"], 0.0)
        self.assertEqual(rect_stretch["y"], 0.0)
        self.assertEqual(rect_stretch["width"], 1000.0)
        self.assertEqual(rect_stretch["height"], 800.0)

    def tearDown(self):
        self.controller.shutdown()

    def test_canonical_document_advance_emitted_on_async_apply(self):
        """Async apply publishes canonical document version via canonicalDocumentPublished signal."""
        mock_pub = MagicMock(spec=VisualRegionPublicationService)
        mock_pub.publish_region_review.return_value = RegionPublicationResultDTO(
            job_id=42,
            region_id="uuid-region-1",
            success=True,
            document_version=4,
            artifact_version=2,
            artifact_uri="crops/crop_1.jpg",
        )
        self.controller.region_publication_service = mock_pub

        canonical_events = []
        artifact_events = []
        self.controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))
        self.controller.regionArtifactCommitted.connect(lambda j, r, v, u: artifact_events.append((j, r, v, u)))

        self.controller._trigger_async_apply(42, "uuid-region-1")
        self.controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        self.assertEqual(len(canonical_events), 1)
        self.assertEqual(canonical_events[0], (42, 4))
        self.assertEqual(len(artifact_events), 1)
        self.assertEqual(artifact_events[0], (42, "uuid-region-1", 2, "crops/crop_1.jpg"))

    def test_canonical_document_advance_emitted_on_sync_apply(self):
        """Sync apply publishes canonical document version via canonicalDocumentPublished signal."""
        mock_pub = MagicMock(spec=VisualRegionPublicationService)
        mock_pub.publish_region_review.return_value = RegionPublicationResultDTO(
            job_id=42,
            region_id="uuid-region-1",
            success=True,
            document_version=5,
            artifact_version=3,
            artifact_uri="crops/crop_2.jpg",
        )
        self.controller.region_publication_service = mock_pub

        canonical_events = []
        self.controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))

        res = self.controller.apply_region_sync(42, "uuid-region-1")
        self.assertIsNotNone(res)
        self.assertTrue(res.success)
        self.assertEqual(len(canonical_events), 1)
        self.assertEqual(canonical_events[0], (42, 5))

    def test_artifact_and_document_versions_remain_strictly_distinct(self):
        """Artifact version and document version are never swapped or conflated."""
        mock_pub = MagicMock(spec=VisualRegionPublicationService)
        # artifact_version is 10, document_version is 99
        mock_pub.publish_region_review.return_value = RegionPublicationResultDTO(
            job_id=42,
            region_id="uuid-region-1",
            success=True,
            document_version=99,
            artifact_version=10,
            artifact_uri="crops/crop_distinct.jpg",
        )
        self.controller.region_publication_service = mock_pub

        canonical_events = []
        artifact_events = []
        self.controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))
        self.controller.regionArtifactCommitted.connect(lambda j, r, v, u: artifact_events.append((j, r, v, u)))

        self.controller.apply_region_sync(42, "uuid-region-1")

        self.assertEqual(canonical_events[0], (42, 99))
        self.assertEqual(artifact_events[0][2], 10)  # artifact version is 10, not 99

    def test_missing_document_version_fails_and_never_emits_fake_zero(self):
        """Absence of document_version is an invariant violation and never emits version 0."""
        mock_pub = MagicMock(spec=VisualRegionPublicationService)
        mock_pub.publish_region_review.return_value = RegionPublicationResultDTO(
            job_id=42,
            region_id="uuid-region-1",
            success=True,
            document_version=None,  # Missing version!
            artifact_version=1,
            artifact_uri="crops/crop.jpg",
        )
        self.controller.region_publication_service = mock_pub

        canonical_events = []
        failed_events = []
        self.controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))
        self.controller.applyFailed.connect(lambda j, r, err: failed_events.append((j, r, err)))

        res = self.controller.apply_region_sync(42, "uuid-region-1")
        self.assertEqual(len(canonical_events), 0)
        self.assertEqual(len(failed_events), 1)
        self.assertIn("missing canonical document version", failed_events[0][2])

    def test_publication_failure_does_not_emit_canonical_signal(self):
        """When publication fails (success=False), canonicalDocumentPublished is never emitted."""
        mock_pub = MagicMock(spec=VisualRegionPublicationService)
        mock_pub.publish_region_review.return_value = RegionPublicationResultDTO(
            job_id=42,
            region_id="uuid-region-1",
            success=False,
            document_version=5,
            status_message="OCC retry exhausted",
        )
        self.controller.region_publication_service = mock_pub

        canonical_events = []
        failed_events = []
        self.controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))
        self.controller.applyFailed.connect(lambda j, r, err: failed_events.append((j, r, err)))

        self.controller.apply_region_sync(42, "uuid-region-1")
        self.assertEqual(len(canonical_events), 0)
        self.assertEqual(len(failed_events), 1)
