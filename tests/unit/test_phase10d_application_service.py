# ============================================================
#  tests/unit/test_phase10d_application_service.py
#  Phase 10D Application Service Domain Operations Verification
# ============================================================

import tempfile
import unittest
from pathlib import Path

from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError
from application.services.document_viewer_service import DocumentViewerService
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor


class TestPhase10DApplicationService(unittest.TestCase):
    """Verifies Phase 10D application service domain operations: update, reject, restore, reset_to_ai, manual creation."""

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

    def test_ai_region_edit_preserves_detected_bbox_and_updates_reviewed_bbox(self):
        """Editing an AI detected region preserves immutable detected_bbox and marks it MODIFIED."""
        detected = BoundingBox(ymin=100, xmin=100, ymax=300, xmax=400)
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=detected,
            )
            saved = uow.visual_regions.save(region)
            uow.commit()
            r_id = saved.region_id

        new_bbox = BoundingBox(ymin=120, xmin=150, ymax=350, xmax=450)
        dto = self.service.update_region_geometry(r_id, new_bbox)

        self.assertEqual(dto.region_id, r_id)
        self.assertEqual(dto.origin, RegionOrigin.AI_DETECTED.value)
        self.assertEqual(dto.detected_bbox, detected)  # Immutable original preserved!
        self.assertEqual(dto.reviewed_bbox, new_bbox)  # Reviewed bbox updated
        self.assertEqual(dto.effective_bbox, new_bbox)
        self.assertEqual(dto.review_status, ReviewStatus.MODIFIED.value)
        self.assertEqual(dto.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED.value)
        self.assertTrue(dto.is_modified)

    def test_create_manual_region_provenance_and_unique_id(self):
        """Manual region has origin = USER_MANUAL, detected_bbox = None, and unique region_id."""
        manual_bbox = BoundingBox(ymin=200, xmin=200, ymax=400, xmax=500)
        dto = self.service.create_manual_region(
            job_id=self.job.id,
            page_number=1,
            bbox=manual_bbox,
        )

        self.assertIsNotNone(dto.region_id)
        self.assertEqual(dto.origin, RegionOrigin.USER_MANUAL.value)
        self.assertIsNone(dto.detected_bbox)  # Zero AI detection
        self.assertEqual(dto.reviewed_bbox, manual_bbox)
        self.assertEqual(dto.effective_bbox, manual_bbox)
        self.assertEqual(dto.review_status, ReviewStatus.MANUAL.value)
        self.assertEqual(dto.display_order, 1)

        # Create second manual region on same page
        dto2 = self.service.create_manual_region(
            job_id=self.job.id,
            page_number=1,
            bbox=BoundingBox(ymin=500, xmin=500, ymax=600, xmax=600),
        )
        self.assertNotEqual(dto.region_id, dto2.region_id)
        self.assertEqual(dto2.display_order, 2)  # Incremented display order

    def test_reject_region_excludes_from_active_overlays_while_preserving_sqlite(self):
        """reject_region marks is_deleted = True; excluded from active regions but recoverable."""
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(50, 50, 150, 150),
            )
            uow.visual_regions.save(r)
            uow.commit()
            r_id = r.region_id

        # Verify active initially
        active_before = self.service.get_active_page_regions(self.job.id, 1)
        self.assertEqual(len(active_before), 1)

        # Reject region
        dto = self.service.reject_region(r_id)
        self.assertEqual(dto.review_status, ReviewStatus.REJECTED.value)
        self.assertTrue(dto.is_deleted)

        # Must disappear immediately from active overlay query
        active_after = self.service.get_active_page_regions(self.job.id, 1)
        self.assertEqual(len(active_after), 0)

        # Verify still exists in SQLite for historical audit and recovery
        with self.uow_factory.create() as uow:
            persisted = uow.visual_regions.get_by_region_id(r_id)
            self.assertIsNotNone(persisted)
            self.assertTrue(persisted.is_deleted)

    def test_restore_region_recovers_active_status(self):
        """restore_region restores a rejected region back into active query view."""
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(50, 50, 150, 150),
            )
            uow.visual_regions.save(r)
            uow.commit()
            r_id = r.region_id

        self.service.reject_region(r_id)
        self.assertEqual(len(self.service.get_active_page_regions(self.job.id, 1)), 0)

        restored_dto = self.service.restore_region(r_id)
        self.assertFalse(restored_dto.is_deleted)
        self.assertEqual(restored_dto.review_status, ReviewStatus.UNREVIEWED.value)

        active_restored = self.service.get_active_page_regions(self.job.id, 1)
        self.assertEqual(len(active_restored), 1)
        self.assertEqual(active_restored[0].region_id, r_id)

    def test_reset_region_to_ai(self):
        """reset_region_to_ai discards user reviewed_bbox, restoring original detected_bbox."""
        detected = BoundingBox(100, 100, 200, 200)
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=detected,
            )
            uow.visual_regions.save(r)
            uow.commit()
            r_id = r.region_id

        # Modify geometry
        self.service.update_region_geometry(r_id, BoundingBox(150, 150, 300, 300))
        mod_dto = self.service.get_active_page_regions(self.job.id, 1)[0]
        self.assertTrue(mod_dto.is_modified)
        self.assertEqual(mod_dto.effective_bbox, BoundingBox(150, 150, 300, 300))

        # Reset to AI
        reset_dto = self.service.reset_region_to_ai(r_id)
        self.assertFalse(reset_dto.is_modified)
        self.assertIsNone(reset_dto.reviewed_bbox)
        self.assertEqual(reset_dto.effective_bbox, detected)
        self.assertEqual(reset_dto.review_status, ReviewStatus.UNREVIEWED.value)

    def test_reset_manual_region_to_ai_raises_domain_error(self):
        """Calling reset_region_to_ai on a manual region raises DomainError because no AI detection exists."""
        dto = self.service.create_manual_region(
            job_id=self.job.id,
            page_number=1,
            bbox=BoundingBox(100, 100, 200, 200),
        )
        with self.assertRaises(DomainError):
            self.service.reset_region_to_ai(dto.region_id)

    def test_nonexistent_region_raises_entity_not_found(self):
        """Operations on non-existent region_id raise EntityNotFoundError."""
        with self.assertRaises(EntityNotFoundError):
            self.service.update_region_geometry("non-existent-uuid", BoundingBox(10, 10, 20, 20))

        with self.assertRaises(EntityNotFoundError):
            self.service.reject_region("non-existent-uuid")

        with self.assertRaises(EntityNotFoundError):
            self.service.restore_region("non-existent-uuid")

        with self.assertRaises(EntityNotFoundError):
            self.service.reset_region_to_ai("non-existent-uuid")
