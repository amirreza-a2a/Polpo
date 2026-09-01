# ============================================================
#  tests/unit/test_phase10b_apply_review.py
#  Phase 10B — Apply & Re-crop Transaction Engine Tests
# ============================================================

import io
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from PIL import Image

import tests.characterization.conftest_base
from application.dto.visual_region_dto import ApplyReviewResultDTO
from application.ports.document_processor import ExtractedCrop, IDocumentProcessor
from application.services.apply_review_service import ApplyReviewService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox, CropPolicy
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from core.exceptions.domain_exceptions import DomainError, EntityNotFoundError
from infrastructure.document.coordinate_mapper import CoordinateMapper
from infrastructure.document.image_cropper import ImageCropper
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter


class TestApplyReviewDomainAndService(unittest.TestCase):
    """Verifies domain rules, effective bounding boxes, rejected/manual regions, and idempotency."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "polpot_phase10b.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir.name) / "artifacts")
        self.doc_processor = PyMuPDFDocumentProcessor()

        # Create synthetic test page image (200x200 RGB)
        img = Image.new("RGB", (200, 200), color=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.page_jpeg = buf.getvalue()

        # Store mock source PDF artifact
        self.pdf_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="test_doc.pdf",
            data=b"%PDF-1.4 mock pdf data",
            mime_type="application/pdf",
        )

        # Mock page rendering
        self.doc_processor.get_page_count = lambda pdf_bytes: 2
        self.doc_processor.render_page_to_jpeg = lambda pdf_bytes, page_number, dpi=150: self.page_jpeg

        # Create Job in SQLite
        with self.uow_factory.create() as uow:
            prompt = uow.prompts.save(
                Prompt(id=None, name="P1", text="Convert to MD", prompt_type=PromptType.PIPELINE_1, is_default=True)
            )
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="test_doc.pdf",
                    file_path=self.pdf_handle.uri,
                    total_pages=2,
                    status=JobStatus.DONE,
                    prompt_id=prompt.id,
                )
            )
            uow.commit()

        self.service = ApplyReviewService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_effective_bbox_selection_and_version_increment(self):
        """Modified region uses reviewed_bbox, produces v2 crop, and transitions to ACCEPTED and SYNCED."""
        ai_box = BoundingBox(100, 100, 300, 300)
        user_box = BoundingBox(120, 120, 400, 400)

        # Store initial v1 crop file
        v1_handle = self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.CROPPED_IMAGE,
            filename="crop_1_initial.jpg",
            data=b"v1 crop bytes",
            mime_type="image/jpeg",
        )

        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=ai_box,
            )
            region.active_artifact_version = 1
            region.active_artifact_uri = v1_handle.uri
            region.update_geometry(user_box)  # Sets MODIFIED & DIRTY_RECROP_REQUIRED
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        # Apply reviews
        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)
        self.assertEqual(result.applied_count, 1)

        with self.uow_factory.create() as uow:
            updated_region = uow.visual_regions.get_by_region_id(region_id)
            self.assertIsNotNone(updated_region)
            self.assertEqual(updated_region.active_artifact_version, 2)
            self.assertEqual(updated_region.review_status, ReviewStatus.ACCEPTED)
            self.assertEqual(updated_region.sync_status, SyncStatus.SYNCED)
            self.assertEqual(updated_region.detected_bbox, ai_box)  # Provenance preserved
            self.assertEqual(updated_region.reviewed_bbox, user_box)
            self.assertTrue(updated_region.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v2.jpg"))

    def test_rejected_region_handling_omitted_from_markdown(self):
        """Rejected regions are omitted from output markdown and marked SYNCED without deleting provenance."""
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 300, 300),
            )
            region.reject()
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        # Store initial page markdown containing the tag
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1.md",
            data=f"Header text\n![[crop_{self.job.id}_p1_1.jpg]]\nFooter text".encode("utf-8"),
        )

        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)

        with self.uow_factory.create() as uow:
            updated_region = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(updated_region.review_status, ReviewStatus.REJECTED)
            self.assertEqual(updated_region.sync_status, SyncStatus.SYNCED)
            self.assertIsNone(updated_region.active_artifact_uri)  # Explicit invariant: zero active doc artifact

        # Check regenerated markdown: tag must be gone
        output_bytes = self.storage.retrieve(
            ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri=result.output_markdown_uri,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                job_id=self.job.id,
                filename="output_1.md",
            )
        )
        output_text = output_bytes.decode("utf-8")
        self.assertNotIn("crop_1_p1_1.jpg", output_text)
        self.assertIn("Header text", output_text)
        self.assertIn("Footer text", output_text)

    def test_reject_after_multiple_versions_and_deterministic_cleanup(self):
        """Rejecting a region that previously had multiple versions clears active_artifact_uri and allows deterministic GC."""
        # 1. Create region and produce v1
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        self.service.apply_reviews(self.job.id)

        # 2. Modify to produce v2
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.update_geometry(BoundingBox(120, 120, 220, 220))
            uow.visual_regions.save(loaded)
            uow.commit()

        self.service.apply_reviews(self.job.id)

        # 3. Reject region
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.reject()
            uow.visual_regions.save(loaded)
            uow.commit()

        res = self.service.apply_reviews(self.job.id)
        self.assertTrue(res.success)

        # Invariant checks:
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.review_status, ReviewStatus.REJECTED)
            self.assertEqual(loaded.sync_status, SyncStatus.SYNCED)
            self.assertIsNone(loaded.active_artifact_uri)

        # Both v1 and v2 files exist on disk before GC
        v1_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v1.jpg")
        v2_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v2.jpg")
        self.assertTrue(self.storage.exists(v1_handle))
        self.assertTrue(self.storage.exists(v2_handle))

        # 4. GC prunes both inactive crop versions deterministically
        pruned = self.service.cleanup_superseded_artifacts(self.job.id)
        self.assertGreaterEqual(pruned, 2)  # At least the 2 crop versions
        self.assertFalse(self.storage.exists(v1_handle))
        self.assertFalse(self.storage.exists(v2_handle))

    def test_restore_rejected_region_and_reapply(self):
        """Restoring a never-generated rejected region re-activates it and produces v1 crop."""
        # 1. Create and reject before any crop was generated
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            self.assertEqual(r.active_artifact_version, 0)  # Canonical: 0 = never generated
            self.assertIsNone(r.active_artifact_uri)
            r.reject()
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        self.service.apply_reviews(self.job.id)

        # 2. Restore region
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 0)  # Preserved 0
            self.assertIsNone(loaded.active_artifact_uri)
            loaded.restore()
            self.assertEqual(loaded.review_status, ReviewStatus.UNREVIEWED)
            self.assertEqual(loaded.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)
            uow.visual_regions.save(loaded)
            uow.commit()

        # 3. Apply -> MUST produce v1
        res = self.service.apply_reviews(self.job.id)
        self.assertTrue(res.success)
        self.assertEqual(res.applied_count, 1)

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.review_status, ReviewStatus.UNREVIEWED)
            self.assertEqual(loaded.sync_status, SyncStatus.SYNCED)
            self.assertEqual(loaded.active_artifact_version, 1)  # Highest version ever generated = 1
            self.assertIsNotNone(loaded.active_artifact_uri)
            self.assertTrue(loaded.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v1.jpg"))

    def test_monotonic_versioning_across_modification_rejection_gc_and_restoration(self):
        """
        P1 Regression Test:
        create AI region -> Apply (v1) -> modify -> Apply (v2) -> reject -> Apply
        -> GC deletes v1/v2 -> restore -> Apply -> MUST produce v3 (v1/v2 never reused).
        """
        # 1. Create AI region and Apply -> produces v1
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        res1 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res1.success)

        with self.uow_factory.create() as uow:
            r1 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r1.active_artifact_version, 1)
            self.assertTrue(r1.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v1.jpg"))

        # 2. Modify and Apply -> produces v2
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.update_geometry(BoundingBox(110, 110, 210, 210))
            uow.visual_regions.save(loaded)
            uow.commit()

        res2 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res2.success)

        with self.uow_factory.create() as uow:
            r2 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r2.active_artifact_version, 2)
            self.assertTrue(r2.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v2.jpg"))

        # 3. Reject and Apply
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.reject()
            uow.visual_regions.save(loaded)
            uow.commit()

        res3 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res3.success)

        with self.uow_factory.create() as uow:
            r3 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r3.review_status, ReviewStatus.REJECTED)
            self.assertIsNone(r3.active_artifact_uri)
            self.assertEqual(r3.active_artifact_version, 2)  # Monotonic watermark preserved!

        # 4. GC deletes historical v1 and v2 files
        pruned = self.service.cleanup_superseded_artifacts(self.job.id)
        self.assertGreaterEqual(pruned, 2)

        v1_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v1.jpg")
        v2_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v2.jpg")
        self.assertFalse(self.storage.exists(v1_handle))
        self.assertFalse(self.storage.exists(v2_handle))

        # 5. Restore region and Apply -> MUST produce v3
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.restore()
            uow.visual_regions.save(loaded)
            uow.commit()

        res4 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res4.success)

        # Invariant checks:
        with self.uow_factory.create() as uow:
            r4 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r4.active_artifact_version, 3)  # Strictly monotonic v3!
            self.assertIsNotNone(r4.active_artifact_uri)
            self.assertTrue(r4.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v3.jpg"))
            self.assertEqual(r4.region_id, region_id)  # Identity strictly preserved

        # v3 exists on disk, v1/v2 remain absent
        v3_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v3.jpg")
        self.assertTrue(self.storage.exists(v3_handle))
        self.assertFalse(self.storage.exists(v1_handle))
        self.assertFalse(self.storage.exists(v2_handle))

        # Markdown references v3
        out_bytes = self.storage.retrieve(
            ArtifactHandle(StorageBackendType.LOCAL_FS, res4.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, self.job.id, f"output_{self.job.id}_v4.md")
        )
        self.assertIn(f"crop_{self.job.id}_{region_id}_v3.jpg", out_bytes.decode("utf-8"))

    def test_crash_safe_region_version_watermark_after_failed_commit_and_retry(self):
        """
        P0 Regression Test:
        create -> Apply v1 -> edit -> reserve/stage v2 -> simulate SQLite commit failure
        -> verify: active_artifact_version == 1, artifact_version_watermark == 2
        -> retry Apply -> MUST produce v3 (v2 burned and never regenerated).
        """
        # 1. Create and Apply v1
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        res1 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res1.success)

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 1)
            self.assertEqual(loaded.artifact_version_watermark, 1)
            self.assertTrue(loaded.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v1.jpg"))

        # 2. Edit geometry
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.update_geometry(BoundingBox(110, 110, 210, 210))
            uow.visual_regions.save(loaded)
            uow.commit()

        # 3. Simulate active commit failure (step 7)
        original_uow_create = self.uow_factory.create
        call_count = [0]

        def failing_uow_create():
            uow = original_uow_create()
            call_count[0] += 1
            if call_count[0] == 3:
                def failing_commit():
                    raise sqlite3.OperationalError("Simulated SQLite disk I/O error during active state commit")
                uow.commit = failing_commit
            return uow

        self.uow_factory.create = failing_uow_create

        with self.assertRaises(sqlite3.OperationalError):
            self.service.apply_reviews(self.job.id)

        self.uow_factory.create = original_uow_create

        # 4. Verify durable watermark state in SQLite:
        # active_artifact_version remains 1, while artifact_version_watermark is advanced to 2!
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 1)
            self.assertEqual(loaded.artifact_version_watermark, 2)
            self.assertTrue(loaded.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v1.jpg"))

        # Staged v2 crop file exists on disk as an orphan candidate
        v2_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v2.jpg")
        self.assertTrue(self.storage.exists(v2_handle))

        # 5. Retry Apply -> MUST advance to v3!
        res_retry = self.service.apply_reviews(self.job.id)
        self.assertTrue(res_retry.success)

        # 6. Verify region active version is v3 and watermark is 3!
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 3)
            self.assertEqual(loaded.artifact_version_watermark, 3)
            self.assertTrue(loaded.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v3.jpg"))

        # v3 exists on disk, v2 was NOT overwritten or reused
        v3_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, f"crop_{self.job.id}_{region_id}_v3.jpg")
        self.assertTrue(self.storage.exists(v3_handle))

        # GC safely deletes orphaned v1 and burned v2 while preserving active v3
        pruned = self.service.cleanup_superseded_artifacts(self.job.id)
        self.assertGreaterEqual(pruned, 2)
        self.assertFalse(self.storage.exists(v2_handle))
        self.assertTrue(self.storage.exists(v3_handle))

    def test_crash_safe_markdown_version_watermark_after_failed_commit_and_retry(self):
        """
        P0 Regression Test:
        output_v1 active -> Apply stages output_v2 -> simulate SQLite commit failure
        -> verify job watermark == 2 -> retry Apply -> MUST produce output_v3.
        """
        # 1. First Apply generates output_v1
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        res1 = self.service.apply_reviews(self.job.id)
        self.assertTrue(res1.success)
        self.assertTrue(res1.output_markdown_uri.endswith(f"output_{self.job.id}_v1.md"))

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(self.job.id)
            self.assertEqual(job.output_artifact_version_watermark, 1)

        # 2. Modify region to trigger Apply
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            loaded.update_geometry(BoundingBox(120, 120, 220, 220))
            uow.visual_regions.save(loaded)
            uow.commit()

        # 3. Simulate failure in active commit
        original_uow_create = self.uow_factory.create
        call_count = [0]

        def failing_uow_create():
            uow = original_uow_create()
            call_count[0] += 1
            if call_count[0] == 3:
                def failing_commit():
                    raise sqlite3.OperationalError("Simulated crash during active commit")
                uow.commit = failing_commit
            return uow

        self.uow_factory.create = failing_uow_create

        with self.assertRaises(sqlite3.OperationalError):
            self.service.apply_reviews(self.job.id)

        self.uow_factory.create = original_uow_create

        # 4. Verify durable job watermark in SQLite is 2, while active output_path is still output_v1.md
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(self.job.id)
            self.assertEqual(job.output_artifact_version_watermark, 2)
            self.assertTrue(job.output_path.endswith(f"output_{self.job.id}_v1.md"))

        # 5. Retry Apply -> MUST produce output_v3!
        res_retry = self.service.apply_reviews(self.job.id)
        self.assertTrue(res_retry.success)
        self.assertTrue(res_retry.output_markdown_uri.endswith(f"output_{self.job.id}_v3.md"))

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(self.job.id)
            self.assertEqual(job.output_artifact_version_watermark, 3)
            self.assertTrue(job.output_path.endswith(f"output_{self.job.id}_v3.md"))

    def test_user_manual_region_creation_and_markdown_append(self):
        """Manual region gets v1 crop, ACCEPTED status, and is appended to page markdown."""
        manual_box = BoundingBox(50, 50, 250, 250)
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_user_manual(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                reviewed_bbox=manual_box,
            )
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)

        with self.uow_factory.create() as uow:
            updated = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(updated.origin, RegionOrigin.USER_MANUAL)
            self.assertEqual(updated.active_artifact_version, 1)
            self.assertEqual(updated.review_status, ReviewStatus.ACCEPTED)
            self.assertEqual(updated.sync_status, SyncStatus.SYNCED)
            self.assertTrue(updated.active_artifact_uri.endswith(f"crop_{self.job.id}_{region_id}_v1.jpg"))

        # Verify markdown contains manual region token
        output_bytes = self.storage.retrieve(
            ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri=result.output_markdown_uri,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                job_id=self.job.id,
                filename="output_1.md",
            )
        )
        self.assertIn(f"crop_{self.job.id}_{region_id}_v1.jpg", output_bytes.decode("utf-8"))

    def test_reset_to_ai_regenerates_crop_from_detected_bbox(self):
        """Reset-to-AI uses original detected_bbox and generates new version crop."""
        ai_box = BoundingBox(100, 100, 200, 200)
        user_box = BoundingBox(500, 500, 800, 800)

        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=ai_box,
            )
            region.update_geometry(user_box)
            region.active_artifact_version = 1
            region.active_artifact_uri = "file:///dummy/v1.jpg"
            region.reset_to_ai()  # Sets reviewed_bbox=None, review_status=UNREVIEWED, sync_status=DIRTY
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)

        with self.uow_factory.create() as uow:
            updated = uow.visual_regions.get_by_region_id(region_id)
            self.assertIsNone(updated.reviewed_bbox)
            self.assertEqual(updated.effective_bbox, ai_box)
            self.assertEqual(updated.active_artifact_version, 2)
            self.assertEqual(updated.sync_status, SyncStatus.SYNCED)

    def test_immutable_markdown_versioning_never_overwrites_active_files(self):
        """Pre-existing output_1.md and page_1.md are never overwritten during Apply; distinct v2 files are staged."""
        init_md_bytes = b"Initial Active Output Content"
        init_out_handle = self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=f"output_{self.job.id}.md",
            data=init_md_bytes,
            mime_type="text/markdown",
        )
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1.md",
            data=b"Initial Page 1 Content",
            mime_type="text/markdown",
        )
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(self.job.id)
            job.output_path = init_out_handle.uri
            uow.jobs.save(job)

            # Create a dirty region
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()

        # Apply
        res = self.service.apply_reviews(self.job.id)
        self.assertTrue(res.success)
        self.assertTrue(res.output_markdown_uri.endswith(f"output_{self.job.id}_v2.md"))

        # Invariant check: Original output_1.md still exists and has its exact original bytes!
        self.assertTrue(self.storage.exists(init_out_handle))
        self.assertEqual(self.storage.retrieve(init_out_handle), init_md_bytes)

        # New v2 file exists with updated content
        new_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, res.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, self.job.id, f"output_{self.job.id}_v2.md")
        self.assertTrue(self.storage.exists(new_handle))
        self.assertNotEqual(self.storage.retrieve(new_handle), init_md_bytes)

    def test_reconcile_orphaned_artifacts(self):
        """reconcile_orphaned_artifacts identifies unreferenced files on disk."""
        # Create an orphan file on disk
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.CROPPED_IMAGE,
            filename="orphan_crop.jpg",
            data=b"orphan",
        )
        orphans = self.service.reconcile_orphaned_artifacts(self.job.id)
        self.assertIn("orphan_crop.jpg", orphans)

    def test_idempotency_running_twice_does_not_increment_version(self):
        """Running apply_reviews twice with no dirty state produces 0 changes and leaves versions identical."""
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        # First Apply
        res1 = self.service.apply_reviews(self.job.id)
        self.assertEqual(res1.applied_count, 1)

        with self.uow_factory.create() as uow:
            r1 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r1.active_artifact_version, 1)
            self.assertEqual(r1.sync_status, SyncStatus.SYNCED)

        # Second Apply (No edits made)
        res2 = self.service.apply_reviews(self.job.id)
        self.assertEqual(res2.applied_count, 0)

        with self.uow_factory.create() as uow:
            r2 = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(r2.active_artifact_version, 1)  # Version NOT incremented!
            self.assertEqual(r2.sync_status, SyncStatus.SYNCED)


class TestApplyReviewCrashMatrixAndArtifactSafety(unittest.TestCase):
    """Verifies crash consistency, transaction safety, and orphan candidate protocols."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "polpot_crash.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir.name) / "artifacts")
        self.doc_processor = PyMuPDFDocumentProcessor()

        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.page_jpeg = buf.getvalue()

        self.pdf_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="doc.pdf",
            data=b"%PDF-1.4 mock",
            mime_type="application/pdf",
        )

        self.doc_processor.get_page_count = lambda pdf_bytes: 1
        self.doc_processor.render_page_to_jpeg = lambda pdf_bytes, page_number, dpi=150: self.page_jpeg

        with self.uow_factory.create() as uow:
            prompt = uow.prompts.save(
                Prompt(id=None, name="P1", text="Prompt", prompt_type=PromptType.PIPELINE_1, is_default=True)
            )
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="doc.pdf",
                    file_path=self.pdf_handle.uri,
                    total_pages=1,
                    status=JobStatus.DONE,
                    prompt_id=prompt.id,
                )
            )
            uow.commit()

        self.service = ApplyReviewService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_crash_case_c_crop_failure_all_or_nothing(self):
        """Case C: If one region crop fails, zero SQLite changes are committed and previous state remains valid."""
        with self.uow_factory.create() as uow:
            r1 = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            r1.active_artifact_version = 1
            r1.active_artifact_uri = "file:///job_1/crop_1_v1.jpg"
            r1.sync_status = SyncStatus.SYNCED

            r2 = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=2,
                detected_bbox=BoundingBox(300, 300, 400, 400),
            )
            r2.active_artifact_version = 1
            r2.active_artifact_uri = "file:///job_1/crop_2_v1.jpg"
            r2.sync_status = SyncStatus.SYNCED

            # Modify both regions
            r1.update_geometry(BoundingBox(120, 120, 220, 220))
            r2.update_geometry(BoundingBox(320, 320, 420, 420))
            uow.visual_regions.save_all([r1, r2])
            uow.commit()

        # Simulate cropper failing on region 2
        original_crop = self.doc_processor.crop_region_image
        call_count = [0]

        def failing_crop(page_jpeg_bytes, box, policy=None):
            call_count[0] += 1
            if call_count[0] == 2:
                return None  # Failure
            return original_crop(page_jpeg_bytes, box, policy)

        self.doc_processor.crop_region_image = failing_crop

        with self.assertRaises(DomainError):
            self.service.apply_reviews(self.job.id)

        # Assert zero database changes committed (All-or-Nothing)
        with self.uow_factory.create() as uow:
            loaded_r1 = uow.visual_regions.get_by_region_id(r1.region_id)
            loaded_r2 = uow.visual_regions.get_by_region_id(r2.region_id)

            self.assertEqual(loaded_r1.active_artifact_version, 1)
            self.assertEqual(loaded_r1.active_artifact_uri, "file:///job_1/crop_1_v1.jpg")
            self.assertEqual(loaded_r1.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)

            self.assertEqual(loaded_r2.active_artifact_version, 1)
            self.assertEqual(loaded_r2.active_artifact_uri, "file:///job_1/crop_2_v1.jpg")
            self.assertEqual(loaded_r2.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)

    def test_crash_case_a_failure_before_staging_leaves_db_and_files_untouched(self):
        """Case A: Crash before any staged artifact is created leaves database and disk untouched."""
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            r.update_geometry(BoundingBox(120, 120, 220, 220))
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        # Simulate rendering crash before staging
        def broken_render(pdf_bytes, page_num, dpi=150):
            raise IOError("Simulated render crash before staging")

        self.doc_processor.render_page_to_jpeg = broken_render

        with self.assertRaises(DomainError):
            self.service.apply_reviews(self.job.id)

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 0)
            self.assertIsNone(loaded.active_artifact_uri)
            self.assertEqual(loaded.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)

    def test_crash_case_b_failure_during_staging_orphans_staged_file_without_corrupting_db(self):
        """Case B: Crash after first crop file is stored leaves DB untouched and staged file unreferenced."""
        with self.uow_factory.create() as uow:
            r1 = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 300, 300),
            )
            r2 = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=2,
                detected_bbox=BoundingBox(400, 400, 600, 600),
            )
            r1.update_geometry(BoundingBox(110, 110, 310, 310))
            r2.update_geometry(BoundingBox(410, 410, 610, 610))
            uow.visual_regions.save_all([r1, r2])
            uow.commit()

        # Crop r1 succeeds, crop r2 fails
        original_crop = self.doc_processor.crop_region_image
        count = [0]
        def partial_crop(page_jpeg_bytes, box, policy=None):
            count[0] += 1
            if count[0] == 2:
                raise RuntimeError("Simulated crash during second crop staging")
            return original_crop(page_jpeg_bytes, box, policy)

        self.doc_processor.crop_region_image = partial_crop

        with self.assertRaises(RuntimeError):
            self.service.apply_reviews(self.job.id)

        # Database is completely uncorrupted (previous state remains)
        with self.uow_factory.create() as uow:
            loaded1 = uow.visual_regions.get_by_region_id(r1.region_id)
            loaded2 = uow.visual_regions.get_by_region_id(r2.region_id)
            self.assertEqual(loaded1.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)
            self.assertEqual(loaded2.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)

    def test_crash_case_e_successful_commit_references_immutable_prepared_artifacts(self):
        """Case E: SQLite commit references fully prepared immutable artifacts that exist on disk."""
        with self.uow_factory.create() as uow:
            r = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(r)
            uow.commit()
            region_id = r.region_id

        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)

        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.sync_status, SyncStatus.SYNCED)
            self.assertIsNotNone(loaded.active_artifact_uri)
            # Verify the committed URI genuinely exists on disk
            handle = ArtifactHandle(StorageBackendType.LOCAL_FS, loaded.active_artifact_uri, ArtifactType.CROPPED_IMAGE, self.job.id, "crop.jpg")
            self.assertTrue(self.storage.exists(handle))

    def test_crash_case_c_failure_after_markdown_staging_leaves_db_pointing_to_v1(self):
        """Case C: If a crash happens during pre-commit validation after staging, old markdown remains active."""
        init_md_bytes = b"Initial Active Output Content"
        init_out_handle = self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=f"output_{self.job.id}.md",
            data=init_md_bytes,
            mime_type="text/markdown",
        )
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(self.job.id)
            job.output_path = init_out_handle.uri
            uow.jobs.save(job)
            r = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=1, detected_bbox=BoundingBox(100, 100, 200, 200))
            uow.visual_regions.save(r)
            uow.commit()

        # Pre-commit validation failure
        original_exists = self.storage.exists
        def broken_exists(handle):
            if "output" in handle.filename and "_v2" in handle.filename:
                return False  # Simulate pre-commit validation failure
            return original_exists(handle)

        self.storage.exists = broken_exists

        with self.assertRaises(DomainError):
            self.service.apply_reviews(self.job.id)

        # Invariant: SQLite still points to v1, and old markdown file is unchanged on disk
        with self.uow_factory.create() as uow:
            loaded_job = uow.jobs.get_by_id(self.job.id)
            self.assertEqual(loaded_job.output_path, init_out_handle.uri)
            self.assertEqual(self.storage.retrieve(init_out_handle), init_md_bytes)

    def test_crash_case_d_sqlite_transaction_rollback(self):
        """Case D: Crash during SQLite commit rolls back; previous active state remains authoritative."""
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            region.active_artifact_version = 1
            region.active_artifact_uri = "file:///job_1/crop_initial.jpg"
            region.update_geometry(BoundingBox(150, 150, 250, 250))
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        # Mock uow.commit to raise an exception during SQLite transaction
        from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork

        class BrokenUnitOfWork(SQLiteUnitOfWork):
            def commit(self):
                if getattr(self, "should_fail", False):
                    raise sqlite3.OperationalError("Simulated disk error during SQLite commit")
                super().commit()

        class BrokenUowFactory(SQLiteUnitOfWorkFactory):
            def __init__(self, db_manager):
                super().__init__(db_manager)
                self.count = 0

            def create(self):
                uow = BrokenUnitOfWork(self.db_manager)
                self.count += 1
                if self.count >= 2:
                    uow.should_fail = True
                return uow

        broken_service = ApplyReviewService(
            uow_factory=BrokenUowFactory(self.db_manager),
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

        with self.assertRaises(sqlite3.OperationalError):
            broken_service.apply_reviews(self.job.id)

        # Verify rollback: database still points to v1 and DIRTY
        with self.uow_factory.create() as uow:
            loaded = uow.visual_regions.get_by_region_id(region_id)
            self.assertEqual(loaded.active_artifact_version, 1)
            self.assertEqual(loaded.active_artifact_uri, "file:///job_1/crop_initial.jpg")
            self.assertEqual(loaded.sync_status, SyncStatus.DIRTY_RECROP_REQUIRED)

    def test_crash_case_f_garbage_collection_prunes_superseded_versions(self):
        """Case F: cleanup_superseded_artifacts prunes older version files while keeping active version."""
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(100, 100, 200, 200),
            )
            uow.visual_regions.save(region)
            uow.commit()
            region_id = region.region_id

        # Initial Apply (produces v1)
        self.service.apply_reviews(self.job.id)

        # Modify and Apply again (produces v2)
        with self.uow_factory.create() as uow:
            r = uow.visual_regions.get_by_region_id(region_id)
            r.update_geometry(BoundingBox(110, 110, 210, 210))
            uow.visual_regions.save(r)
            uow.commit()

        self.service.apply_reviews(self.job.id)

        # Check that both v1 and v2 files exist on disk
        v1_filename = f"crop_{self.job.id}_{region_id}_v1.jpg"
        v2_filename = f"crop_{self.job.id}_{region_id}_v2.jpg"
        v1_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, v1_filename)
        v2_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.CROPPED_IMAGE, self.job.id, v2_filename)

        self.assertTrue(self.storage.exists(v1_handle))
        self.assertTrue(self.storage.exists(v2_handle))

        # Run GC
        pruned = self.service.cleanup_superseded_artifacts(self.job.id)
        self.assertEqual(pruned, 4)  # 1 crop + 1 output_md + 2 page_md files (total_pages=2)

        # v1 crop is pruned, v2 remains intact
        self.assertFalse(self.storage.exists(v1_handle))
        self.assertTrue(self.storage.exists(v2_handle))

        # v1 markdown is pruned, v2 remains intact
        old_out_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, self.job.id, f"output_{self.job.id}_v1.md")
        new_out_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, self.job.id, f"output_{self.job.id}_v2.md")
        self.assertFalse(self.storage.exists(old_out_handle))
        self.assertTrue(self.storage.exists(new_out_handle))

        # Idempotent: running again prunes 0
        pruned2 = self.service.cleanup_superseded_artifacts(self.job.id)
        self.assertEqual(pruned2, 0)


class TestApplyReviewConcurrencyAndIntegration(unittest.TestCase):
    """Verifies per-job serialization under concurrent threads and end-to-end multi-page execution."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "polpot_concurrency.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir.name) / "artifacts")
        self.doc_processor = PyMuPDFDocumentProcessor()

        img = Image.new("RGB", (300, 300), color=(180, 180, 180))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.page_jpeg = buf.getvalue()

        self.pdf_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="multipage.pdf",
            data=b"%PDF-1.4 multipage mock",
            mime_type="application/pdf",
        )

        self.doc_processor.get_page_count = lambda pdf_bytes: 2
        self.doc_processor.render_page_to_jpeg = lambda pdf_bytes, page_number, dpi=150: self.page_jpeg

        with self.uow_factory.create() as uow:
            prompt = uow.prompts.save(
                Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True)
            )
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="multipage.pdf",
                    file_path=self.pdf_handle.uri,
                    total_pages=2,
                    status=JobStatus.DONE,
                    prompt_id=prompt.id,
                )
            )
            uow.commit()

        self.service = ApplyReviewService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_concurrent_apply_same_job_serialized_safely(self):
        """Simultaneous Apply calls for the same job serialize cleanly without race conditions."""
        with self.uow_factory.create() as uow:
            region = VisualRegion.create_ai_detected(
                job_id=self.job.id,
                page_number=1,
                display_order=1,
                detected_bbox=BoundingBox(50, 50, 150, 150),
            )
            uow.visual_regions.save(region)
            uow.commit()

        results = []
        errors = []

        def worker():
            try:
                res = self.service.apply_reviews(self.job.id)
                results.append(res)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers encountered errors: {errors}")
        self.assertEqual(len(results), 5)
        # Exactly one worker applied the changes, the rest were idempotent 0-count passes
        applied_counts = [r.applied_count for r in results]
        self.assertEqual(sum(applied_counts), 1)

    def test_multipage_full_integration(self):
        """Comprehensive multi-page integration: modified, rejected, manual, and duplicate coordinates."""
        # Initial page 1 & 2 markdown
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1.md",
            data="Page 1: ![[crop_1_p1_1.jpg]] and ![[crop_1_p1_2.jpg]]".encode("utf-8"),
        )
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_2.md",
            data="Page 2: Duplicate 1 ![[crop_1_p2_1.jpg]] and Duplicate 2 ![[crop_1_p2_2.jpg]]".encode("utf-8"),
        )

        with self.uow_factory.create() as uow:
            # Page 1: Region 1 (modified)
            r1 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=1, detected_bbox=BoundingBox(10, 10, 50, 50))
            r1.active_artifact_version = 1
            r1.active_artifact_uri = f"file:///job_{self.job.id}/crop_1_p1_1.jpg"
            r1.update_geometry(BoundingBox(15, 15, 60, 60))

            # Page 1: Region 2 (rejected)
            r2 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=1, display_order=2, detected_bbox=BoundingBox(100, 100, 150, 150))
            r2.active_artifact_version = 1
            r2.active_artifact_uri = f"file:///job_{self.job.id}/crop_1_p1_2.jpg"
            r2.reject()

            # Page 1: Region 3 (manual)
            r3 = VisualRegion.create_user_manual(job_id=self.job.id, page_number=1, display_order=3, reviewed_bbox=BoundingBox(200, 200, 250, 250))

            # Page 2: Region 4 & 5 (duplicate coordinates [[50, 50, 100, 100]])
            r4 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=2, display_order=1, detected_bbox=BoundingBox(50, 50, 100, 100))
            r5 = VisualRegion.create_ai_detected(job_id=self.job.id, page_number=2, display_order=2, detected_bbox=BoundingBox(50, 50, 100, 100))

            uow.visual_regions.save_all([r1, r2, r3, r4, r5])
            uow.commit()

        # Apply all reviews
        result = self.service.apply_reviews(self.job.id)
        self.assertTrue(result.success)
        self.assertEqual(result.applied_count, 5)  # 4 new crops + 1 rejected sync

        with self.uow_factory.create() as uow:
            all_r = {r.region_id: r for r in uow.visual_regions.get_by_job_id(self.job.id)}

            # Region 1: v2, ACCEPTED
            self.assertEqual(all_r[r1.region_id].active_artifact_version, 2)
            self.assertEqual(all_r[r1.region_id].review_status, ReviewStatus.ACCEPTED)
            self.assertEqual(all_r[r1.region_id].sync_status, SyncStatus.SYNCED)

            # Region 2: REJECTED, SYNCED
            self.assertEqual(all_r[r2.region_id].review_status, ReviewStatus.REJECTED)
            self.assertEqual(all_r[r2.region_id].sync_status, SyncStatus.SYNCED)

            # Region 3: v1, ACCEPTED, MANUAL
            self.assertEqual(all_r[r3.region_id].active_artifact_version, 1)
            self.assertEqual(all_r[r3.region_id].review_status, ReviewStatus.ACCEPTED)
            self.assertEqual(all_r[r3.region_id].origin, RegionOrigin.USER_MANUAL)

            # Regions 4 & 5: distinct IDs, SYNCED
            self.assertNotEqual(r4.region_id, r5.region_id)
            self.assertEqual(all_r[r4.region_id].sync_status, SyncStatus.SYNCED)
            self.assertEqual(all_r[r5.region_id].sync_status, SyncStatus.SYNCED)

        # Inspect final unified Markdown
        out_bytes = self.storage.retrieve(
            ArtifactHandle(StorageBackendType.LOCAL_FS, result.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, self.job.id, "output_1.md")
        )
        md_content = out_bytes.decode("utf-8")

        # Region 1 token present (v2)
        self.assertIn(f"crop_{self.job.id}_{r1.region_id}_v2.jpg", md_content)
        # Region 2 token removed completely
        self.assertNotIn("crop_1_p1_2.jpg", md_content)
        # Region 3 token appended
        self.assertIn(f"crop_{self.job.id}_{r3.region_id}_v1.jpg", md_content)
        # Region 4 & 5 tokens present with unique region IDs
        self.assertIn(f"crop_{self.job.id}_{r4.region_id}_v1.jpg", md_content)
        self.assertIn(f"crop_{self.job.id}_{r5.region_id}_v1.jpg", md_content)


if __name__ == "__main__":
    unittest.main()
