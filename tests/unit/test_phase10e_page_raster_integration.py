# ============================================================
#  tests/unit/test_phase10e_page_raster_integration.py
#  Integration tests for PDF page-image reliability and cache hits
# ============================================================

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf
from PIL import Image

from application.services.document_viewer_service import DocumentViewerService
from core.entities.artifact import ArtifactType, ArtifactHandle, StorageBackendType
from core.entities.job import Job, JobStatus
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController


class TestPhase10EPageRasterIntegration(unittest.TestCase):
    """
    Verifies PDF page-image loading reliability across cache-miss, cache-hit,
    navigation cycle, controller presentation, and atomic storage writes.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.db_path = self.tmp_path / "test.db"
        self.artifacts_dir = self.tmp_path / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.doc_processor = PyMuPDFDocumentProcessor()
        self.service = DocumentViewerService(self.uow_factory, self.storage, self.doc_processor)

        # Generate a synthetic 3-page PDF with distinct content per page
        doc = pymupdf.open()
        for i in range(1, 4):
            page = doc.new_page(width=400, height=600)
            page.draw_rect(pymupdf.Rect(20 * i, 20 * i, 100 * i, 100 * i), color=(1, 0, 0))
        self.pdf_bytes = doc.write()
        doc.close()

        # Ingest PDF into job 1 directory
        job_dir = self.artifacts_dir / "job_1"
        job_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_file = job_dir / "document.pdf"
        self.pdf_file.write_bytes(self.pdf_bytes)

        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="document.pdf",
                file_path=str(self.pdf_file),
                total_pages=3,
                status=JobStatus.DONE,
            )
            saved = uow.jobs.save(job)
            uow.commit()
            self.job_id = saved.id

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_1_cache_miss_renders_and_returns_valid_populated_uri(self):
        """Test 1: Initial render creates the artifact and returns a valid, non-empty, decodable URI."""
        dto = self.service.get_page_raster(self.job_id, page_number=1, dpi=150)

        self.assertIsNotNone(dto)
        self.assertEqual(dto.job_id, self.job_id)
        self.assertEqual(dto.page_number, 1)
        self.assertEqual(dto.total_pages, 3)
        self.assertTrue(dto.image_uri.startswith("file://"), f"Expected file URI, got: {dto.image_uri}")

        # Verify the file exists on disk at the resolved path
        local_path = Path(dto.image_uri.replace("file://", ""))
        self.assertTrue(local_path.exists(), f"Image file does not exist: {local_path}")
        self.assertGreater(local_path.stat().st_size, 0)

        # Verify the file is a valid decodable JPEG matching returned dimensions
        with Image.open(local_path) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (dto.raster_width, dto.raster_height))

    def test_2_cache_hit_resolves_existing_uri_without_re_rendering(self):
        """Test 2: Repeated call for same page resolves cached URI without re-rendering or returning empty string."""
        # Spy on render_page_to_jpeg to verify cache utilization
        original_render = self.doc_processor.render_page_to_jpeg
        render_mock = MagicMock(side_effect=original_render)
        self.doc_processor.render_page_to_jpeg = render_mock

        # First call: cache miss
        dto1 = self.service.get_page_raster(self.job_id, page_number=1, dpi=150)
        self.assertEqual(render_mock.call_count, 1)
        self.assertTrue(dto1.image_uri.startswith("file://"))

        # Second call: cache hit
        dto2 = self.service.get_page_raster(self.job_id, page_number=1, dpi=150)
        self.assertEqual(render_mock.call_count, 1, "Cache hit must not invoke renderer a second time")
        self.assertTrue(dto2.image_uri.startswith("file://"), "Cache hit must return a valid non-empty URI")
        self.assertEqual(dto1.image_uri, dto2.image_uri, "Both calls must resolve to the identical artifact URI")
        self.assertEqual(dto1.raster_width, dto2.raster_width)
        self.assertEqual(dto1.raster_height, dto2.raster_height)

    def test_3_navigation_cycle_preserves_valid_uris(self):
        """Test 3: Navigation cycle Page 1 -> Page 2 -> Page 1 maintains non-empty URIs for all pages."""
        # Visit Page 1 (miss)
        dto_p1_initial = self.service.get_page_raster(self.job_id, page_number=1, dpi=150)
        self.assertTrue(dto_p1_initial.image_uri.startswith("file://"))

        # Navigate to Page 2 (miss)
        dto_p2 = self.service.get_page_raster(self.job_id, page_number=2, dpi=150)
        self.assertTrue(dto_p2.image_uri.startswith("file://"))
        self.assertNotEqual(dto_p1_initial.image_uri, dto_p2.image_uri)

        # Return to Page 1 (hit)
        dto_p1_revisit = self.service.get_page_raster(self.job_id, page_number=1, dpi=150)
        self.assertTrue(dto_p1_revisit.image_uri.startswith("file://"), "Revisiting Page 1 must return valid URI")
        self.assertEqual(dto_p1_initial.image_uri, dto_p1_revisit.image_uri)

        # Confirm both distinct page files exist on disk
        path_p1 = Path(dto_p1_initial.image_uri.replace("file://", ""))
        path_p2 = Path(dto_p2.image_uri.replace("file://", ""))
        self.assertTrue(path_p1.exists())
        self.assertTrue(path_p2.exists())

    def test_4_controller_integration_cache_hit_populates_page_image_uri(self):
        """Test 4: DocumentViewerController retains non-empty pageImageUri when revisiting cached pages."""
        controller = DocumentViewerController(viewer_service=self.service)

        # Visit Page 1
        controller.loadPageSync(self.job_id, 1)
        uri1 = controller.pageImageUri
        self.assertTrue(uri1.startswith("file://"), f"Page 1 initial visit must be non-empty, got: {uri1}")

        # Visit Page 2
        controller.loadPageSync(self.job_id, 2)
        uri2 = controller.pageImageUri
        self.assertTrue(uri2.startswith("file://"), f"Page 2 initial visit must be non-empty, got: {uri2}")
        self.assertNotEqual(uri1, uri2)

        # Revisit Page 1 (cache hit)
        controller.loadPageSync(self.job_id, 1)
        uri1_revisit = controller.pageImageUri
        self.assertTrue(uri1_revisit.startswith("file://"), f"Page 1 revisit must not be empty, got: {uri1_revisit}")
        self.assertEqual(uri1, uri1_revisit, "Revisited page must reference the same cached file URI")

    def test_5_atomic_storage_write_publication_and_cleanup(self):
        """Test 5: Storage writes atomically publish final artifact and clean up temporary files on error."""
        test_data = b"FAKE_JPEG_IMAGE_DATA_PAYLOAD_1234567890"
        filename = "atomic_test.jpg"

        handle = self.storage.store(
            job_id=self.job_id,
            artifact_type=ArtifactType.PAGE_IMAGE,
            filename=filename,
            data=test_data,
            mime_type="image/jpeg",
        )

        final_path = Path(handle.metadata["path"])
        self.assertTrue(final_path.exists())
        self.assertEqual(final_path.read_bytes(), test_data)

        # Verify no temporary .tmp files linger in the job directory
        job_dir = final_path.parent
        tmp_files = list(job_dir.glob(".*.tmp"))
        self.assertEqual(len(tmp_files), 0, f"Found lingering temporary files: {tmp_files}")

        # Verify error handling cleans up temp file if write fails before rename
        error_filename = "fail_test.jpg"
        with patch("os.replace", side_effect=OSError("Disk write simulated failure")):
            with self.assertRaises(OSError):
                self.storage.store(
                    job_id=self.job_id,
                    artifact_type=ArtifactType.PAGE_IMAGE,
                    filename=error_filename,
                    data=test_data,
                    mime_type="image/jpeg",
                )

        # Destination must not exist
        failed_dest = job_dir / error_filename
        self.assertFalse(failed_dest.exists(), "Failed atomic write must not leave partial destination artifact")

        # Temporary files must be cleaned up
        lingering_tmp = list(job_dir.glob(f".{error_filename}.*.tmp"))
        self.assertEqual(len(lingering_tmp), 0, "Failed atomic write must clean up temporary file")
