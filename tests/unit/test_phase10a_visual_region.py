# ============================================================
#  tests/unit/test_phase10a_visual_region.py
#  Phase 10A — Persistence & Stable VisualRegion Domain Foundation Tests
# ============================================================

import io
import os
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from PIL import Image

import tests.characterization.conftest_base
from core.entities.bounding_box import BoundingBox
from core.entities.artifact import ArtifactType
from core.entities.job import Job, JobStatus, JobType
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import (
    VisualRegion,
    RegionOrigin,
    ReviewStatus,
    SyncStatus,
)
from core.exceptions.domain_exceptions import DomainError
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from application.services.job_execution import JobExecutionService
from application.ports.ai_executor import IAIExecutionService


class TestVisualRegionDomainModel(unittest.TestCase):
    """Verifies domain invariants, state behaviors, and provenance rules for VisualRegion."""

    def test_ai_detected_region_creation_and_invariants(self):
        bbox = BoundingBox(ymin=100, xmin=200, ymax=400, xmax=600)
        region = VisualRegion.create_ai_detected(
            job_id=42,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )

        self.assertIsNotNone(region.region_id)
        self.assertEqual(len(region.region_id), 32)  # UUID4 hex format
        self.assertEqual(region.job_id, 42)
        self.assertEqual(region.page_number, 1)
        self.assertEqual(region.display_order, 1)
        self.assertEqual(region.origin, RegionOrigin.AI_DETECTED)
        self.assertEqual(region.detected_bbox, bbox)
        self.assertIsNone(region.reviewed_bbox)
        self.assertEqual(region.review_status, ReviewStatus.UNREVIEWED)
        self.assertEqual(region.sync_status, SyncStatus.PENDING_INITIAL_CROP)
        self.assertEqual(region.active_artifact_version, 1)
        self.assertIsNone(region.active_artifact_uri)
        self.assertEqual(region.effective_bbox, bbox)
        self.assertFalse(region.is_modified)
        self.assertFalse(region.is_deleted)

    def test_ai_detected_requires_detected_bbox(self):
        with self.assertRaises(DomainError):
            VisualRegion(
                id=None,
                region_id=uuid.uuid4().hex,
                job_id=1,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                detected_bbox=None,  # Forbidden for AI_DETECTED
            )

    def test_user_manual_region_creation_and_invariants(self):
        bbox = BoundingBox(ymin=50, xmin=50, ymax=300, xmax=300)
        region = VisualRegion.create_user_manual(
            job_id=42,
            page_number=2,
            display_order=3,
            reviewed_bbox=bbox,
        )

        self.assertIsNotNone(region.region_id)
        self.assertEqual(region.job_id, 42)
        self.assertEqual(region.page_number, 2)
        self.assertEqual(region.display_order, 3)
        self.assertEqual(region.origin, RegionOrigin.USER_MANUAL)
        self.assertIsNone(region.detected_bbox)
        self.assertEqual(region.reviewed_bbox, bbox)
        self.assertEqual(region.review_status, ReviewStatus.MANUAL)
        self.assertEqual(region.sync_status, SyncStatus.PENDING_INITIAL_CROP)
        self.assertEqual(region.effective_bbox, bbox)
        self.assertTrue(region.is_modified)
        self.assertFalse(region.is_deleted)

    def test_user_manual_forbids_detected_bbox(self):
        bbox = BoundingBox(ymin=10, xmin=10, ymax=100, xmax=100)
        with self.assertRaises(DomainError):
            VisualRegion(
                id=None,
                region_id=uuid.uuid4().hex,
                job_id=1,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.USER_MANUAL,
                detected_bbox=bbox,  # Forbidden for USER_MANUAL
                reviewed_bbox=bbox,
            )

    def test_user_manual_requires_reviewed_bbox(self):
        with self.assertRaises(DomainError):
            VisualRegion(
                id=None,
                region_id=uuid.uuid4().hex,
                job_id=1,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.USER_MANUAL,
                detected_bbox=None,
                reviewed_bbox=None,  # Required for USER_MANUAL
            )

    def test_uuid4_domain_enforcement(self):
        # Valid UUID4 hex
        valid_id = uuid.uuid4().hex
        region = VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 50, 50), region_id=valid_id)
        self.assertEqual(region.region_id, valid_id)

        # Invalid string
        with self.assertRaises(DomainError):
            VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 50, 50), region_id="invalid-id")

        # UUID1 (non-UUID4)
        uuid1_hex = uuid.uuid1().hex
        with self.assertRaises(DomainError):
            VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 50, 50), region_id=uuid1_hex)

        # Invalid length
        with self.assertRaises(DomainError):
            VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 50, 50), region_id="12345")

    def test_update_geometry_behavior(self):
        ai_box = BoundingBox(ymin=100, xmin=100, ymax=200, xmax=200)
        region = VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=ai_box)

        new_box = BoundingBox(ymin=110, xmin=110, ymax=220, xmax=220)
        region.update_geometry(new_box)

        self.assertEqual(region.reviewed_bbox, new_box)
        self.assertEqual(region.effective_bbox, new_box)
        self.assertEqual(region.detected_bbox, ai_box)  # Provenance untouched
        self.assertEqual(region.review_status, ReviewStatus.MODIFIED)
        self.assertEqual(region.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)
        self.assertTrue(region.is_modified)

    def test_reset_to_ai_success_and_failure(self):
        ai_box = BoundingBox(ymin=100, xmin=100, ymax=200, xmax=200)
        ai_region = VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=ai_box)
        ai_region.update_geometry(BoundingBox(ymin=150, xmin=150, ymax=250, xmax=250))
        self.assertTrue(ai_region.is_modified)

        ai_region.reset_to_ai()
        self.assertIsNone(ai_region.reviewed_bbox)
        self.assertEqual(ai_region.effective_bbox, ai_box)
        self.assertEqual(ai_region.review_status, ReviewStatus.UNREVIEWED)
        self.assertEqual(ai_region.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)
        self.assertFalse(ai_region.is_modified)

        # Manual region must fail reset_to_ai
        manual_box = BoundingBox(ymin=50, xmin=50, ymax=100, xmax=100)
        manual_region = VisualRegion.create_user_manual(job_id=1, page_number=1, display_order=1, reviewed_bbox=manual_box)
        with self.assertRaises(DomainError):
            manual_region.reset_to_ai()

    def test_reject_behavior(self):
        ai_box = BoundingBox(ymin=100, xmin=100, ymax=200, xmax=200)
        region = VisualRegion.create_ai_detected(job_id=1, page_number=1, display_order=1, detected_bbox=ai_box)
        region.reject()

        self.assertTrue(region.is_deleted)
        self.assertEqual(region.review_status, ReviewStatus.REJECTED)
        self.assertEqual(region.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)


class TestVisualRegionSQLitePersistence(unittest.TestCase):
    """Verifies SQLite persistence round-trip, indexes, unique constraints, and cascades."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_polpot.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        # Create a test job in SQLite
        with self.uow_factory.create() as uow:
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="test.pdf",
                    file_path="/tmp/test.pdf",
                    total_pages=5,
                    status=JobStatus.PENDING,
                )
            )
            uow.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_retrieve_ai_detected_region(self):
        bbox = BoundingBox(ymin=100, xmin=150, ymax=500, xmax=550)
        region = VisualRegion.create_ai_detected(
            job_id=self.job.id,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
        )

        with self.uow_factory.create() as uow:
            saved = uow.visual_regions.save(region)
            uow.commit()
            self.assertIsNotNone(saved.id)
            self.assertEqual(saved.region_id, region.region_id)

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region.region_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.id, saved.id)
            self.assertEqual(loaded.region_id, region.region_id)
            self.assertEqual(loaded.job_id, self.job.id)
            self.assertEqual(loaded.page_number, 1)
            self.assertEqual(loaded.display_order, 1)
            self.assertEqual(loaded.origin, RegionOrigin.AI_DETECTED)
            self.assertEqual(loaded.detected_bbox, bbox)
            self.assertIsNone(loaded.reviewed_bbox)
            self.assertEqual(loaded.review_status, ReviewStatus.UNREVIEWED)
            self.assertEqual(loaded.sync_status, SyncStatus.PENDING_INITIAL_CROP)
            self.assertEqual(loaded.active_artifact_version, 1)

    def test_save_and_retrieve_user_manual_region(self):
        bbox = BoundingBox(ymin=200, xmin=250, ymax=600, xmax=650)
        region = VisualRegion.create_user_manual(
            job_id=self.job.id,
            page_number=2,
            display_order=1,
            reviewed_bbox=bbox,
        )

        with self.uow_factory.create() as uow:
            saved = uow.visual_regions.save(region)
            uow.commit()

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_id(saved.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.origin, RegionOrigin.USER_MANUAL)
            self.assertIsNone(loaded.detected_bbox)
            self.assertEqual(loaded.reviewed_bbox, bbox)
            self.assertEqual(loaded.review_status, ReviewStatus.MANUAL)
            self.assertEqual(loaded.sync_status, SyncStatus.PENDING_INITIAL_CROP)

    def test_global_region_id_unique_constraint(self):
        bbox = BoundingBox(ymin=10, xmin=10, ymax=100, xmax=100)
        shared_region_id = uuid.uuid4().hex

        region1 = VisualRegion.create_ai_detected(
            job_id=self.job.id,
            page_number=1,
            display_order=1,
            detected_bbox=bbox,
            region_id=shared_region_id,
        )

        with self.uow_factory.create() as uow:
            uow.visual_regions.save(region1)
            uow.commit()

        # Attempt to insert another region with same region_id must fail uniqueness
        region2 = VisualRegion.create_ai_detected(
            job_id=self.job.id,
            page_number=2,
            display_order=1,
            detected_bbox=bbox,
            region_id=shared_region_id,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.uow_factory.create() as uow:
                uow.visual_regions.save(region2)
                uow.commit()

    def test_query_by_job_and_page_ordering(self):
        b1 = BoundingBox(ymin=10, xmin=10, ymax=50, xmax=50)
        b2 = BoundingBox(ymin=60, xmin=60, ymax=100, xmax=100)
        b3 = BoundingBox(ymin=110, xmin=110, ymax=150, xmax=150)

        r1 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=2, detected_bbox=b1)
        r2 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=1, detected_bbox=b2)
        r3 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=2, display_order=1, detected_bbox=b3)

        with self.uow_factory.create() as uow:
            uow.visual_regions.save_all([r1, r2, r3])
            uow.commit()

        with self.uow_factory.create() as uow:
            p1_regions = uow.visual_regions.get_by_job_and_page(self.job.id, page_number=1)
            self.assertEqual(len(p1_regions), 2)
            # Must be ordered by display_order ASC
            self.assertEqual(p1_regions[0].display_order, 1)
            self.assertEqual(p1_regions[1].display_order, 2)

            all_job_regions = uow.visual_regions.get_by_job_id(self.job.id)
            self.assertEqual(len(all_job_regions), 3)

    def test_cascade_delete_on_job_deletion(self):
        bbox = BoundingBox(ymin=10, xmin=10, ymax=100, xmax=100)
        region = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=1, detected_bbox=bbox)

        with self.uow_factory.create() as uow:
            uow.visual_regions.save(region)
            uow.commit()

        # Delete the job directly in SQLite with foreign keys ON
        conn = self.db_manager.create_connection()
        try:
            conn.execute("DELETE FROM jobs WHERE id = ?", (self.job.id,))
            conn.commit()

            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM visual_regions WHERE job_id = ?", (self.job.id,))
            row = cur.fetchone()
            self.assertEqual(row["cnt"], 0)
        finally:
            conn.close()

    def test_transaction_rollback_preserves_integrity(self):
        bbox = BoundingBox(ymin=10, xmin=10, ymax=100, xmax=100)
        region = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=1, detected_bbox=bbox)

        try:
            with self.uow_factory.create() as uow:
                uow.visual_regions.save(region)
                raise RuntimeError("Simulated transaction crash")
        except RuntimeError:
            pass

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region.region_id)
            self.assertIsNone(loaded)


class MockAIExecutor(IAIExecutionService):
    def __init__(self, responses_per_page: List[str]):
        self.responses = responses_per_page
        self.call_count = 0

    def execute_vision_with_fallback(self, chain, image_bytes: bytes, prompt: str, at_page: int = 1, mime_type: str = "image/jpeg", on_switch=None):
        idx = min(self.call_count, len(self.responses) - 1)
        self.call_count += 1
        slot = chain[0] if chain else None
        return self.responses[idx], slot

    def execute_text_with_fallback(self, chain, prompt: str, input_text: Optional[str] = None, at_page: int = 0, on_switch=None):
        idx = min(self.call_count, len(self.responses) - 1)
        self.call_count += 1
        slot = chain[0] if chain else None
        return self.responses[idx], slot


class TestVisualRegionJobExecutionIntegration(unittest.TestCase):
    """Verifies that JobExecutionService parses AI detections and canonically persists VisualRegions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "polpot_integration.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir.name) / "artifacts")
        self.doc_processor = PyMuPDFDocumentProcessor()

        # Generate a valid 2-page synthetic JPEG
        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.page_jpeg = buf.getvalue()

        # Mock render_page_to_jpeg on doc_processor
        self.doc_processor.get_page_count = lambda pdf_bytes: 2
        self.doc_processor.render_page_to_jpeg = lambda pdf_bytes, page_num, dpi=150: self.page_jpeg

        # Store source PDF artifact
        handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="doc.pdf",
            data=b"%PDF-1.4 mock pdf data",
            mime_type="application/pdf",
        )

        # Create prompt and job
        with self.uow_factory.create() as uow:
            prompt = uow.prompts.save(
                Prompt(id=None, name="P1", text="Convert to MD", prompt_type=PromptType.PIPELINE_1, is_default=True)
            )
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="doc.pdf",
                    file_path=handle.uri,
                    total_pages=2,
                    status=JobStatus.PENDING,
                    prompt_id=prompt.id,
                )
            )
            uow.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_execution_persists_canonical_visual_regions(self):
        # AI returns markdown with 1 box on page 1 and 2 boxes on page 2
        page1_ai_md = "Here is a diagram [[100, 150, 400, 600]] in the text."
        page2_ai_md = "Chart 1 [[200, 250, 700, 800]] and Chart 2 [[800, 100, 950, 900]]."
        mock_ai = MockAIExecutor([page1_ai_md, page2_ai_md])

        service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=mock_ai,
        )

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(self.job.id)
            uow.commit()

        # Execute the job
        completed_job = service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)
        self.assertEqual(completed_job.processed_pages, 2)

        # Verify visual regions in SQLite
        with self.uow_factory.create() as uow:
            regions = uow.visual_regions.get_by_job_id(self.job.id)
            self.assertEqual(len(regions), 3)

            # Page 1 region
            p1_r = [r for r in regions if r.page_number == 1]
            self.assertEqual(len(p1_r), 1)
            self.assertEqual(p1_r[0].display_order, 1)
            self.assertEqual(p1_r[0].origin, RegionOrigin.AI_DETECTED)
            self.assertEqual(p1_r[0].detected_bbox, BoundingBox(100, 150, 400, 600))
            self.assertEqual(p1_r[0].sync_status, SyncStatus.SYNCED)
            self.assertIsNotNone(p1_r[0].active_artifact_uri)
            self.assertTrue(p1_r[0].active_artifact_uri.startswith("file://"))

            # Page 2 regions
            p2_r = [r for r in regions if r.page_number == 2]
            self.assertEqual(len(p2_r), 2)
            self.assertEqual(p2_r[0].display_order, 1)
            self.assertEqual(p2_r[0].detected_bbox, BoundingBox(200, 250, 700, 800))
            self.assertEqual(p2_r[0].sync_status, SyncStatus.SYNCED)

            self.assertEqual(p2_r[1].display_order, 2)
            self.assertEqual(p2_r[1].detected_bbox, BoundingBox(800, 100, 950, 900))
            self.assertEqual(p2_r[1].sync_status, SyncStatus.SYNCED)

            # Verify all region IDs are distinct UUID4 strings
            r_ids = {r.region_id for r in regions}
            self.assertEqual(len(r_ids), 3)

    def test_association_case_a_three_regions_two_crops_middle_skipped(self):
        """Case A: Three visual regions on a page, but middle crop is rejected/skipped by processor."""
        page_md = "Box 1 [[100, 100, 200, 200]], Box 2 [[300, 300, 305, 305]], Box 3 [[400, 400, 500, 500]]."
        mock_ai = MockAIExecutor([page_md, ""])

        # Override doc_processor.extract_and_crop_images to simulate cropper rejecting box 2 (sub-minimum)
        from application.ports.document_processor import ExtractedCrop

        def mock_extract(markdown_text, page_jpeg_bytes, job_id, page_number=1):
            if page_number == 1:
                return (
                    "Box 1 ![[crop_1_p1_1.jpg]], Box 2 [[300, 300, 305, 305]], Box 3 ![[crop_1_p1_3.jpg]].",
                    [
                        ExtractedCrop(filename="crop_1_p1_1.jpg", data=b"crop1", display_order=1),
                        ExtractedCrop(filename="crop_1_p1_3.jpg", data=b"crop3", display_order=3),
                    ],
                )
            return markdown_text, []

        self.doc_processor.extract_and_crop_images = mock_extract

        service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=mock_ai,
        )

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(self.job.id)
            uow.commit()

        completed_job = service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)

        with self.uow_factory.create() as uow:
            p1_regions = uow.visual_regions.get_by_job_and_page(self.job.id, page_number=1)
            self.assertEqual(len(p1_regions), 3)

            r1, r2, r3 = p1_regions[0], p1_regions[1], p1_regions[2]
            # Region 1 -> Crop 1
            self.assertEqual(r1.display_order, 1)
            self.assertEqual(r1.sync_status, SyncStatus.SYNCED)
            self.assertTrue(r1.active_artifact_uri.endswith("crop_1_p1_1.jpg"))

            # Region 2 -> No artifact (never wrongly assigned to Crop 3!)
            self.assertEqual(r2.display_order, 2)
            self.assertEqual(r2.sync_status, SyncStatus.PENDING_INITIAL_CROP)
            self.assertIsNone(r2.active_artifact_uri)
            self.assertFalse(str(r2.active_artifact_uri).endswith("crop_1_p1_3.jpg"))

            # Region 3 -> Crop 3
            self.assertEqual(r3.display_order, 3)
            self.assertEqual(r3.sync_status, SyncStatus.SYNCED)
            self.assertTrue(r3.active_artifact_uri.endswith("crop_1_p1_3.jpg"))

    def test_association_case_b_duplicate_coordinates(self):
        """Case B: Duplicate coordinate values across multiple regions must preserve distinct identities."""
        page_md = "Box 1 [[100, 100, 200, 200]] and Box 2 [[100, 100, 200, 200]]."
        mock_ai = MockAIExecutor([page_md, ""])

        from application.ports.document_processor import ExtractedCrop

        def mock_extract(markdown_text, page_jpeg_bytes, job_id, page_number=1):
            if page_number == 1:
                return (
                    "Box 1 ![[crop_1_p1_1.jpg]] and Box 2 ![[crop_1_p1_2.jpg]].",
                    [
                        ExtractedCrop(filename="crop_1_p1_1.jpg", data=b"crop1", display_order=1),
                        ExtractedCrop(filename="crop_1_p1_2.jpg", data=b"crop2", display_order=2),
                    ],
                )
            return markdown_text, []

        self.doc_processor.extract_and_crop_images = mock_extract

        service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=mock_ai,
        )

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(self.job.id)
            uow.commit()

        completed_job = service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)

        with self.uow_factory.create() as uow:
            p1_regions = uow.visual_regions.get_by_job_and_page(self.job.id, page_number=1)
            self.assertEqual(len(p1_regions), 2)
            r1, r2 = p1_regions[0], p1_regions[1]

            # Same bounding box geometry
            self.assertEqual(r1.detected_bbox, r2.detected_bbox)
            # Distinct region IDs
            self.assertNotEqual(r1.region_id, r2.region_id)
            # Correct artifact bindings
            self.assertTrue(r1.active_artifact_uri.endswith("crop_1_p1_1.jpg"))
            self.assertTrue(r2.active_artifact_uri.endswith("crop_1_p1_2.jpg"))

    def test_association_case_c_reversed_crop_output_order(self):
        """Case C: Crops returned out of order must associate correctly by display_order."""
        page_md = "Box 1 [[100, 100, 200, 200]] and Box 2 [[300, 300, 400, 400]]."
        mock_ai = MockAIExecutor([page_md, ""])

        from application.ports.document_processor import ExtractedCrop

        def mock_extract(markdown_text, page_jpeg_bytes, job_id, page_number=1):
            if page_number == 1:
                # Deliberately reversed order in the returned list
                return (
                    "Box 1 ![[crop_1_p1_1.jpg]] and Box 2 ![[crop_1_p1_2.jpg]].",
                    [
                        ExtractedCrop(filename="crop_1_p1_2.jpg", data=b"crop2", display_order=2),
                        ExtractedCrop(filename="crop_1_p1_1.jpg", data=b"crop1", display_order=1),
                    ],
                )
            return markdown_text, []

        self.doc_processor.extract_and_crop_images = mock_extract

        service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=mock_ai,
        )

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(self.job.id)
            uow.commit()

        completed_job = service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)

        with self.uow_factory.create() as uow:
            p1_regions = uow.visual_regions.get_by_job_and_page(self.job.id, page_number=1)
            r1, r2 = p1_regions[0], p1_regions[1]
            self.assertEqual(r1.display_order, 1)
            self.assertEqual(r2.display_order, 2)
            self.assertTrue(r1.active_artifact_uri.endswith("crop_1_p1_1.jpg"))
            self.assertTrue(r2.active_artifact_uri.endswith("crop_1_p1_2.jpg"))

    def test_association_case_d_all_crops_failed_preserves_provenance(self):
        """Case D: When all crops fail or are rejected, all VisualRegions remain persisted as provenance."""
        page_md = "Box 1 [[100, 100, 200, 200]] and Box 2 [[300, 300, 400, 400]]."
        mock_ai = MockAIExecutor([page_md, ""])

        def mock_extract(markdown_text, page_jpeg_bytes, job_id, page_number=1):
            return markdown_text, []  # Zero crops

        self.doc_processor.extract_and_crop_images = mock_extract

        service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=mock_ai,
        )

        with self.uow_factory.create() as uow:
            claimed_job = uow.jobs.claim_job(self.job.id)
            uow.commit()

        completed_job = service.execute_claimed_job(claimed_job.id)
        self.assertEqual(completed_job.status, JobStatus.DONE)

        with self.uow_factory.create() as uow:
            p1_regions = uow.visual_regions.get_by_job_and_page(self.job.id, page_number=1)
            self.assertEqual(len(p1_regions), 2)
            for r in p1_regions:
                self.assertIsNotNone(r.detected_bbox)
                self.assertEqual(r.sync_status, SyncStatus.PENDING_INITIAL_CROP)
                self.assertIsNone(r.active_artifact_uri)


if __name__ == "__main__":
    unittest.main()
