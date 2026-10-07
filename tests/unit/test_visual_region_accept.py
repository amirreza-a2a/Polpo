# ============================================================
#  tests/unit/test_visual_region_accept.py
#  TICK-P03A — VisualRegion Accept Lifecycle, Sync DTO Projection & Controller Slots
# ============================================================

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.visual_region import (
    VisualRegion,
    RegionOrigin,
    ReviewStatus,
    SyncStatus,
)
from core.exceptions.domain_exceptions import DomainError, EntityNotFoundError
from application.dto.document_viewer_dto import PageRasterDTO, VisualRegionOverlayItemDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.services.document_viewer_service import DocumentViewerService
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


class TestVisualRegionAcceptDomain(unittest.TestCase):
    """Verifies domain rules, invariants, and transitions for VisualRegion.accept()."""

    def test_accept_ai_unreviewed_transitions_to_accepted(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        region = VisualRegion.create_ai_detected(
            job_id=1,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )
        self.assertEqual(region.review_status, ReviewStatus.UNREVIEWED)
        self.assertEqual(region.sync_status, SyncStatus.PENDING_INITIAL_CROP)
        prev_updated_at = region.updated_at

        region.accept()

        self.assertEqual(region.review_status, ReviewStatus.ACCEPTED)
        self.assertEqual(region.sync_status, SyncStatus.PENDING_INITIAL_CROP)  # No crop side effect
        self.assertEqual(region.effective_bbox, bbox)
        self.assertEqual(region.detected_bbox, bbox)
        self.assertIsNone(region.reviewed_bbox)
        self.assertIsNotNone(region.updated_at)
        if prev_updated_at is not None:
            self.assertGreaterEqual(region.updated_at, prev_updated_at)

    def test_accept_is_idempotent(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        region = VisualRegion.create_ai_detected(
            job_id=1,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )
        region.accept()
        self.assertEqual(region.review_status, ReviewStatus.ACCEPTED)
        ts_after_first = region.updated_at

        region.accept()

        self.assertEqual(region.review_status, ReviewStatus.ACCEPTED)
        self.assertEqual(region.updated_at, ts_after_first)

    def test_accept_manual_region_remains_manual(self):
        bbox = BoundingBox(ymin=50, xmin=50, ymax=200, xmax=200)
        region = VisualRegion.create_user_manual(
            job_id=1,
            page_number=1,
            display_order=1,
            reviewed_bbox=bbox,
        )
        self.assertEqual(region.review_status, ReviewStatus.MANUAL)
        prev_updated_at = region.updated_at

        region.accept()

        self.assertEqual(region.review_status, ReviewStatus.MANUAL)
        self.assertEqual(region.updated_at, prev_updated_at)

    def test_accept_rejected_raises_domain_error(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        region = VisualRegion.create_ai_detected(
            job_id=1,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )
        region.reject()
        self.assertEqual(region.review_status, ReviewStatus.REJECTED)

        with self.assertRaises(DomainError):
            region.accept()

    def test_accept_modified_raises_domain_error(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        region = VisualRegion.create_ai_detected(
            job_id=1,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )
        region.update_geometry(BoundingBox(ymin=120, xmin=120, ymax=320, xmax=420))
        self.assertEqual(region.review_status, ReviewStatus.MODIFIED)

        with self.assertRaises(DomainError):
            region.accept()

    def test_accept_preserves_synced_status_and_no_crop(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        region = VisualRegion.create_ai_detected(
            job_id=1,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )
        region.sync_status = SyncStatus.SYNCED
        region.accept()

        self.assertEqual(region.review_status, ReviewStatus.ACCEPTED)
        self.assertEqual(region.sync_status, SyncStatus.SYNCED)


class TestDocumentViewerServiceAcceptAndProjection(unittest.TestCase):
    """Verifies application service accept_region operation and DTO projection fields."""

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

        with self.uow_factory.create() as uow:
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="sample.pdf",
                    file_path="sample.pdf",
                    total_pages=3,
                    status=JobStatus.DONE,
                )
            )
            uow.commit()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_service_accept_region_success_and_persistence(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=bbox,
            )
            saved = uow.visual_regions.save(region)
            uow.commit()
            r_id = saved.region_id

        dto = self.service.accept_region(r_id)

        self.assertEqual(dto.region_id, r_id)
        self.assertEqual(dto.review_status, ReviewStatus.ACCEPTED.value)

        # Verify persisted in SQLite
        with self.uow_factory.create() as uow:
            reloaded = uow.visual_regions.get_by_region_id(r_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.review_status, ReviewStatus.ACCEPTED)

    def test_service_accept_nonexistent_raises_entity_not_found(self):
        with self.assertRaises(EntityNotFoundError):
            self.service.accept_region("00000000000000000000000000000000")

    def test_overlay_dto_includes_sync_status_and_artifact_uri(self):
        bbox = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=bbox,
            )
            region.sync_status = SyncStatus.SYNCED
            region.active_artifact_uri = "artifacts/job_1/p1_r1.png"
            uow.visual_regions.save(region)
            uow.commit()

        overlays = self.service.calculate_page_overlay_rects(
            job_id=self.job.id,
            page_number=1,
            item_width=800.0,
            item_height=600.0,
            raster_width=1000.0,
            raster_height=1000.0,
        )

        self.assertEqual(len(overlays), 1)
        item = overlays[0]
        self.assertEqual(item.sync_status, "synced")
        self.assertEqual(item.active_artifact_uri, "artifacts/job_1/p1_r1.png")


class TestDocumentViewerControllerSlotsAndCapabilities(unittest.TestCase):
    """Verifies controller slots, properties, signals, and session undo lifecycle."""

    def setUp(self):
        self.mock_service = MagicMock(spec=DocumentViewerService)
        self.mock_pub_service = MagicMock()
        self.controller = DocumentViewerController(
            viewer_service=self.mock_service,
            region_publication_service=self.mock_pub_service,
        )
        self.controller.setViewportDimensions(800.0, 600.0)
        self.controller.setItemDimensions(800.0, 600.0)

        self.r_ai = VisualRegionDTO(
            id=1,
            region_id="uuid-ai-1",
            job_id=42,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.UNREVIEWED.value,
            sync_status="synced",
            effective_bbox=BoundingBox(100, 100, 300, 400),
            detected_bbox=BoundingBox(100, 100, 300, 400),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="artifacts/job_42/p1_r1.png",
            is_modified=False,
            is_deleted=False,
        )
        self.r_manual = VisualRegionDTO(
            id=2,
            region_id="uuid-manual-2",
            job_id=42,
            page_number=1,
            display_order=2,
            origin=RegionOrigin.USER_MANUAL.value,
            review_status=ReviewStatus.MANUAL.value,
            sync_status="sync_failed",
            effective_bbox=BoundingBox(500, 500, 700, 700),
            detected_bbox=None,
            reviewed_bbox=BoundingBox(500, 500, 700, 700),
            active_artifact_version=0,
            active_artifact_uri=None,
            is_modified=True,
            is_deleted=False,
        )

        self.mock_service.get_active_page_regions.return_value = [self.r_ai, self.r_manual]
        self.mock_service.get_page_raster.return_value = PageRasterDTO(
            job_id=42,
            page_number=1,
            total_pages=3,
            raster_width=1000,
            raster_height=1000,
            image_uri="file:///tmp/page_1.png",
            dpi=150,
        )

        self.controller.loadPageSync(42, 1)

    def test_active_regions_projection_preserves_sync_status_and_artifact_uri(self):
        active = self.controller.activeRegions
        self.assertEqual(len(active), 2)
        ai_dict = next(r for r in active if r["region_id"] == "uuid-ai-1")
        self.assertEqual(ai_dict["sync_status"], "synced")
        self.assertEqual(ai_dict["active_artifact_uri"], "artifacts/job_42/p1_r1.png")

        manual_dict = next(r for r in active if r["region_id"] == "uuid-manual-2")
        self.assertEqual(manual_dict["sync_status"], "sync_failed")
        self.assertIsNone(manual_dict["active_artifact_uri"])

    def test_capability_properties_selection_states(self):
        # Initial: no selection
        self.assertFalse(self.controller.canAcceptSelected)
        self.assertFalse(self.controller.canDeleteSelected)
        self.assertFalse(self.controller.canRetrySelectedSync)
        self.assertFalse(self.controller.canCopySelectedToken)

        # Select AI unreviewed + synced region
        self.controller.selectRegion("uuid-ai-1")
        self.assertTrue(self.controller.canAcceptSelected)
        self.assertTrue(self.controller.canDeleteSelected)
        self.assertFalse(self.controller.canRetrySelectedSync)
        self.assertTrue(self.controller.canCopySelectedToken)

        # Select manual + sync_failed region
        self.controller.selectRegion("uuid-manual-2")
        self.assertFalse(self.controller.canAcceptSelected)
        self.assertTrue(self.controller.canDeleteSelected)
        self.assertTrue(self.controller.canRetrySelectedSync)
        self.assertFalse(self.controller.canCopySelectedToken)

    def test_slot_accept_selected_region(self):
        self.controller.selectRegion("uuid-ai-1")

        accepted_dto = VisualRegionDTO(
            id=1,
            region_id="uuid-ai-1",
            job_id=42,
            page_number=1,
            display_order=1,
            origin=RegionOrigin.AI_DETECTED.value,
            review_status=ReviewStatus.ACCEPTED.value,
            sync_status="synced",
            effective_bbox=BoundingBox(100, 100, 300, 400),
            detected_bbox=BoundingBox(100, 100, 300, 400),
            reviewed_bbox=None,
            active_artifact_version=1,
            active_artifact_uri="artifacts/job_42/p1_r1.png",
            is_modified=False,
            is_deleted=False,
        )
        self.mock_service.accept_region.return_value = accepted_dto
        self.mock_service.get_active_page_regions.return_value = [accepted_dto, self.r_manual]

        updated_signal_args = []
        self.controller.regionUpdated.connect(lambda rid: updated_signal_args.append(rid))

        self.controller.acceptSelectedRegion()

        self.mock_service.accept_region.assert_called_once_with("uuid-ai-1")
        self.assertEqual(updated_signal_args, ["uuid-ai-1"])
        self.assertFalse(self.controller.canAcceptSelected)
        # Verify no publication apply was dispatched
        self.mock_pub_service.publish_region_review.assert_not_called()

    def test_slot_restore_region(self):
        restored_dto = self.r_ai
        self.mock_service.restore_region.return_value = restored_dto

        with patch.object(self.controller, "_trigger_async_apply") as mock_apply:
            self.controller.restoreRegion("uuid-ai-1")
            self.mock_service.restore_region.assert_called_once_with("uuid-ai-1")
            mock_apply.assert_called_once_with(42, "uuid-ai-1")

    def test_slot_retry_region_sync(self):
        with patch.object(self.controller, "_trigger_async_apply") as mock_apply:
            self.controller.retryRegionSync("uuid-manual-2")
            mock_apply.assert_called_once_with(42, "uuid-manual-2")

    def test_session_scoped_undo_delete_lifecycle(self):
        self.assertFalse(self.controller.canUndoDelete)
        self.controller.selectRegion("uuid-ai-1")

        undo_signal_emissions = []
        self.controller.canUndoDeleteChanged.connect(lambda: undo_signal_emissions.append(self.controller.canUndoDelete))

        # Delete selected region
        with patch.object(self.controller, "_trigger_async_apply"):
            self.controller.deleteSelectedRegion()

        self.mock_service.reject_region.assert_called_once_with("uuid-ai-1")
        self.assertTrue(self.controller.canUndoDelete)
        self.assertIn(True, undo_signal_emissions)
        self.assertFalse(self.controller.hasSelection)

        # Invoke undoDelete()
        with patch.object(self.controller, "_trigger_async_apply") as mock_apply:
            self.controller.undoDelete()
            self.mock_service.restore_region.assert_called_once_with("uuid-ai-1")
            mock_apply.assert_called_once_with(42, "uuid-ai-1")
            self.assertFalse(self.controller.canUndoDelete)
            self.assertEqual(undo_signal_emissions[-1], False)

    def test_undo_delete_failure_preserves_undo_target(self):
        self.controller.selectRegion("uuid-ai-1")
        with patch.object(self.controller, "_trigger_async_apply"):
            self.controller.deleteSelectedRegion()

        self.assertTrue(self.controller.canUndoDelete)

        # Simulate restore failure
        self.mock_service.restore_region.side_effect = RuntimeError("Database locked")
        self.controller.undoDelete()

        # Target must remain preserved!
        self.assertTrue(self.controller.canUndoDelete)
        self.assertIn("Failed to restore region", self.controller.errorMessage)

    def test_page_navigation_clears_undo_target(self):
        self.controller.selectRegion("uuid-ai-1")
        with patch.object(self.controller, "_trigger_async_apply"):
            self.controller.deleteSelectedRegion()

        self.assertTrue(self.controller.canUndoDelete)

        # Navigating to another page clears undo state
        self.mock_service.get_active_page_regions.return_value = []
        self.controller.loadPageSync(42, 2)

        self.assertFalse(self.controller.canUndoDelete)
