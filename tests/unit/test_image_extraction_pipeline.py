# ============================================================
#  tests/unit/test_image_extraction_pipeline.py
#  Comprehensive Phase 9 Bounding Box & Image Extraction Tests
# ============================================================

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image

from core.entities.bounding_box import (
    BoundingBox,
    PixelRectangle,
    CropPolicy,
    InvalidBoundingBoxError,
)
from core.entities.job import Job, JobStatus
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import ArtifactType
from core.ai.types import AIResponse
from infrastructure.document.bounding_box_parser import (
    BoundingBoxParser,
    ParsedBoundingBoxMatch,
)
from infrastructure.document.coordinate_mapper import CoordinateMapper
from infrastructure.document.image_cropper import ImageCropper
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from application.services.job_execution import JobExecutionService


def create_synthetic_page_jpeg(width: int = 1000, height: int = 1500) -> bytes:
    """Helper creating a valid synthetic test JPEG with distinct dimensions."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class TestImageExtractionPipeline(unittest.TestCase):
    """
    Comprehensive Phase 9 verification test suite covering:
      - Phase 9A: Canonical BoundingBox invariants and parser behavior.
      - Phase 9B: Positional Markdown substitution (handling duplicates & adjacent tags).
      - Phase 9C: Coordinate mapping, boundary clamping, safety padding, and min-size filtering.
      - Phase 9D: Multi-page artifact isolation and deterministic naming.
      - Phase 9E/F: Full real pipeline execution without mocking extract_and_crop_images.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.db_path = self.base_dir / "extraction_test.db"
        self.artifacts_dir = self.base_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_mgr = SQLiteDatabaseManager(self.db_path)
        SQLiteMigrationRunner(self.db_mgr).run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_mgr)
        self.storage = LocalStorageAdapter(self.artifacts_dir)
        self.event_bus = InMemoryEventBus()

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------
    # Phase 9A: BoundingBox Domain Value Object Invariants
    # -------------------------------------------------------------
    def test_bounding_box_valid_invariants(self):
        """Verifies valid canonical BoundingBox construction and properties."""
        box = BoundingBox(ymin=150, xmin=200, ymax=450, xmax=800)
        self.assertEqual(box.ymin, 150)
        self.assertEqual(box.xmin, 200)
        self.assertEqual(box.ymax, 450)
        self.assertEqual(box.xmax, 800)
        self.assertEqual(box.width, 600)
        self.assertEqual(box.height, 300)
        self.assertEqual(box.area, 180000)
        self.assertFalse(box.is_zero_area)
        self.assertEqual(box.as_tuple, (150, 200, 450, 800))

    def test_bounding_box_invalid_invariants_raise(self):
        """Verifies that out-of-range, negative, inverted, or non-integer coordinates raise."""
        # Out of bounds > 1000
        with self.assertRaises(InvalidBoundingBoxError):
            BoundingBox(ymin=0, xmin=0, ymax=1001, xmax=500)

        # Negative
        with self.assertRaises(InvalidBoundingBoxError):
            BoundingBox(ymin=-1, xmin=0, ymax=500, xmax=500)

        # Inverted Y
        with self.assertRaises(InvalidBoundingBoxError):
            BoundingBox(ymin=600, xmin=100, ymax=400, xmax=500)

        # Inverted X
        with self.assertRaises(InvalidBoundingBoxError):
            BoundingBox(ymin=100, xmin=800, ymax=400, xmax=200)

        # Non-integer / boolean
        with self.assertRaises(InvalidBoundingBoxError):
            BoundingBox(ymin=True, xmin=100, ymax=400, xmax=500)  # type: ignore

    # -------------------------------------------------------------
    # Phase 9A: BoundingBoxParser Behavior
    # -------------------------------------------------------------
    def test_parser_valid_integer_and_decimal_coordinates(self):
        """Verifies parser handles integers, decimals (rounded), and arbitrary whitespace."""
        md = (
            "Section 1:\n"
            "Here is figure 1: [[150, 200, 450, 800]]\n"
            "Here is figure 2 with whitespace: [[  100  ,  50  ,  300  ,  400  ]]\n"
            "Here is figure 3 with decimals: [[ 50.4 , 60.6 , 250.2 , 350.8 ]]\n"
        )
        matches = BoundingBoxParser.parse_matches(md)
        self.assertEqual(len(matches), 3)

        self.assertEqual(matches[0].box, BoundingBox(150, 200, 450, 800))
        self.assertEqual(matches[1].box, BoundingBox(100, 50, 300, 400))
        # 50.4 -> 50, 60.6 -> 61, 250.2 -> 250, 350.8 -> 351
        self.assertEqual(matches[2].box, BoundingBox(50, 61, 250, 351))

    def test_parser_skips_malformed_and_invalid_boxes(self):
        """Verifies parser silently and deterministically skips malformed, negative, or inverted tags."""
        md = (
            "Text [[not_numbers]] more text\n"
            "Broken bracket [100, 200, 300, 400]\n"
            "Negative coordinate [[-10, 200, 300, 400]]\n"
            "Out of bounds coordinate [[100, 200, 1500, 400]]\n"
            "Inverted box [[500, 200, 200, 400]]\n"
            "Zero-area box [[200, 200, 200, 400]]\n"
            "Valid box [[100, 100, 300, 300]]\n"
        )
        matches = BoundingBoxParser.parse_matches(md)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].box, BoundingBox(100, 100, 300, 300))

    # -------------------------------------------------------------
    # Phase 9B: Positional Markdown Substitution
    # -------------------------------------------------------------
    def test_positional_substitution_with_duplicate_coordinate_strings(self):
        """
        Regression Test for Bug P0.2:
        Verifies that two IDENTICAL bounding box strings on the same page are substituted
        strictly at their respective character positions without replacing earlier text.
        """
        identical_tag = "[[100, 100, 400, 400]]"
        md = f"Top diagram: {identical_tag}\nSome middle notes.\nBottom diagram: {identical_tag}\nEnd."

        matches = BoundingBoxParser.parse_matches(md)
        self.assertEqual(len(matches), 2)

        replacements = [
            (matches[0].start, matches[0].end, "![[crop_job1_p1_1.jpg]]"),
            (matches[1].start, matches[1].end, "![[crop_job1_p1_2.jpg]]"),
        ]

        result = BoundingBoxParser.substitute_positional(md, replacements)

        expected = (
            "Top diagram: ![[crop_job1_p1_1.jpg]]\n"
            "Some middle notes.\n"
            "Bottom diagram: ![[crop_job1_p1_2.jpg]]\n"
            "End."
        )
        self.assertEqual(result, expected)

    def test_positional_substitution_adjacent_and_surrounding_preservation(self):
        """Verifies text before, adjacent to, and after tags is perfectly preserved."""
        md = "Prefix[[100, 100, 200, 200]][[300, 300, 400, 400]]Suffix"
        matches = BoundingBoxParser.parse_matches(md)
        self.assertEqual(len(matches), 2)

        replacements = [
            (matches[0].start, matches[0].end, "[IMG1]"),
            (matches[1].start, matches[1].end, "[IMG2]"),
        ]
        result = BoundingBoxParser.substitute_positional(md, replacements)
        self.assertEqual(result, "Prefix[IMG1][IMG2]Suffix")

    # -------------------------------------------------------------
    # Phase 9C: Coordinate Mapping, Clamping & Padding
    # -------------------------------------------------------------
    def test_coordinate_mapping_corners_and_non_square(self):
        """Verifies coordinate scaling across non-square raster dimensions."""
        W, H = 1200, 1800
        policy = CropPolicy(padding_percent=0.0)  # No padding for raw mapping check

        # Full page box
        full_box = BoundingBox(ymin=0, xmin=0, ymax=1000, xmax=1000)
        rect = CoordinateMapper.map_to_pixels(full_box, W, H, policy)
        self.assertIsNotNone(rect)
        self.assertEqual(rect.as_tuple, (0, 0, 1200, 1800))

        # Center quarter box [250, 250, 750, 750]
        center_box = BoundingBox(ymin=250, xmin=250, ymax=750, xmax=750)
        rect_center = CoordinateMapper.map_to_pixels(center_box, W, H, policy)
        self.assertIsNotNone(rect_center)
        # left = 250 * 1200 / 1000 = 300, top = 250 * 1800 / 1000 = 450
        # right = 750 * 1200 / 1000 = 900, bottom = 750 * 1800 / 1000 = 1350
        self.assertEqual(rect_center.as_tuple, (300, 450, 900, 1350))

    def test_coordinate_mapping_padding_and_clamping(self):
        """Verifies 1.5% padding expansion and strict boundary clamping to [0, W] and [0, H]."""
        W, H = 1000, 1000
        policy = CropPolicy(padding_percent=0.015)  # 15px pad in 1000x1000

        # Box touching left/top edge: [0, 0, 200, 200]
        edge_box = BoundingBox(ymin=0, xmin=0, ymax=200, xmax=200)
        rect = CoordinateMapper.map_to_pixels(edge_box, W, H, policy)
        self.assertIsNotNone(rect)
        # left = max(0, 0 - 15) = 0, top = max(0, 0 - 15) = 0
        # right = min(1000, 200 + 15) = 215, bottom = min(1000, 200 + 15) = 215
        self.assertEqual(rect.as_tuple, (0, 0, 215, 215))

    def test_coordinate_mapping_minimum_size_filtering(self):
        """Verifies sub-minimum crop boxes (e.g. 5x5 px) are rejected."""
        W, H = 1000, 1000
        policy = CropPolicy(padding_percent=0.0, min_width=16, min_height=16)

        tiny_box = BoundingBox(ymin=100, xmin=100, ymax=105, xmax=105)  # 5x5 px
        rect = CoordinateMapper.map_to_pixels(tiny_box, W, H, policy)
        self.assertIsNone(rect)

    # -------------------------------------------------------------
    # Phase 9D & 9E: Real Image Cropping & Multi-Page Filename Isolation
    # -------------------------------------------------------------
    def test_pymupdf_processor_real_cropping_and_multipage_isolation(self):
        """
        Verifies real Pillow raster cropping and multi-page artifact naming without mocks.
        Proves Page 1 and Page 2 crops have distinct, collision-free filenames.
        """
        processor = PyMuPDFDocumentProcessor(default_dpi=150)
        page_jpeg = create_synthetic_page_jpeg(width=1000, height=1500)

        # Page 1 with 2 boxes
        p1_md = "Page 1 start\nFig 1: [[150, 200, 450, 800]]\nFig 2: [[600, 100, 1000, 500]]\nPage 1 end"
        p1_result_md, p1_crops = processor.extract_and_crop_images(
            markdown_text=p1_md,
            page_jpeg_bytes=page_jpeg,
            job_id=99,
            page_number=1,
        )

        self.assertEqual(len(p1_crops), 2)
        self.assertEqual(p1_crops[0][0], "crop_99_p1_1.jpg")
        self.assertEqual(p1_crops[1][0], "crop_99_p1_2.jpg")
        self.assertIn("![[crop_99_p1_1.jpg]]", p1_result_md)
        self.assertIn("![[crop_99_p1_2.jpg]]", p1_result_md)

        # Page 2 with identical coordinate box [[150, 200, 450, 800]]
        p2_md = "Page 2 start\nRepeated Fig: [[150, 200, 450, 800]]\nPage 2 end"
        p2_result_md, p2_crops = processor.extract_and_crop_images(
            markdown_text=p2_md,
            page_jpeg_bytes=page_jpeg,
            job_id=99,
            page_number=2,
        )

        self.assertEqual(len(p2_crops), 1)
        # CRITICAL ASSERTION: Filename is crop_99_p2_1.jpg (NOT crop_99_1.jpg or crop_99_p1_1.jpg)
        self.assertEqual(p2_crops[0][0], "crop_99_p2_1.jpg")
        self.assertIn("![[crop_99_p2_1.jpg]]", p2_result_md)
        self.assertNotIn("crop_99_p1_1.jpg", p2_result_md)

        # Verify cropped image bytes are valid readable JPEGs
        for fname, c_bytes in p1_crops + p2_crops:
            with Image.open(io.BytesIO(c_bytes)) as c_img:
                self.assertEqual(c_img.format, "JPEG")
                self.assertGreater(c_img.width, 100)
                self.assertGreater(c_img.height, 100)

    # -------------------------------------------------------------
    # Phase 9F: End-to-End Multi-Page Job Execution Integration
    # -------------------------------------------------------------
    def test_multipage_job_execution_real_extraction_integration(self):
        """
        Full end-to-end integration test exercising JobExecutionService with real
        PyMuPDFDocumentProcessor and LocalStorageAdapter over a 2-page document.
        Proves 0 filename collisions and 100% artifact preservation in storage.
        """
        # 1. Setup real processor and mock AI responses
        doc_processor = PyMuPDFDocumentProcessor(default_dpi=150)
        page1_jpeg = create_synthetic_page_jpeg(width=1000, height=1500)
        page2_jpeg = create_synthetic_page_jpeg(width=1000, height=1500)

        # Mock PDF page count and render methods on doc_processor
        doc_processor.get_page_count = MagicMock(return_value=2)
        doc_processor.render_page_to_jpeg = MagicMock(
            side_effect=lambda pdf_bytes, page_num, **kwargs: page1_jpeg if page_num == 1 else page2_jpeg
        )

        # Mock AI adapter returning vision response with diagrams on both pages
        mock_ai_adapter = MagicMock()
        mock_ai_adapter.generate_vision.side_effect = [
            AIResponse(
                content="## Page 1\nDiagram 1.1: [[150, 200, 450, 800]]\nDiagram 1.2: [[600, 100, 1000, 500]]"
            ),
            AIResponse(
                content="## Page 2\nRepeated Diagram: [[150, 200, 450, 800]]\nNew Diagram: [[200, 200, 400, 600]]"
            ),
        ]

        ai_executor = RateLimitedAIExecutor(
            adapter_factory=lambda slot: mock_ai_adapter,
            rate_limiter=MagicMock(),
        )

        execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=doc_processor,
            ai_executor=ai_executor,
            event_publisher=self.event_bus,
        )

        # 2. Ingest source PDF
        pdf_handle = self.storage.store(
            job_id=101,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="test_doc.pdf",
            data=b"%PDF-1.4 Mock PDF Data",
            mime_type="application/pdf",
        )

        slot = ApiSlot(
            id=1,
            provider="google",
            label="Integration Google Slot",
            selected_model="gemini-2.0-flash",
            credential_ref=CredentialRef("cred_int_1", "google", "byok"),
        )

        job = Job(
            id=None,
            file_name="test_doc.pdf",
            file_path=pdf_handle.uri,
            total_pages=2,
            processed_pages=0,
            status=JobStatus.PROCESSING,
            api_chain=[slot],
        )

        with self.uow_factory.create() as uow:
            uow.apis.save(slot)
            saved_job = uow.jobs.save(job)
            uow.commit()

        job_id = saved_job.id

        # 3. Execute the job
        execution_service.execute_claimed_job(job_id)

        # 4. Verify all 4 unique cropped image artifacts exist in storage
        expected_crops = [
            f"crop_{job_id}_p1_1.jpg",
            f"crop_{job_id}_p1_2.jpg",
            f"crop_{job_id}_p2_1.jpg",
            f"crop_{job_id}_p2_2.jpg",
        ]
        job_dir = self.artifacts_dir / f"job_{job_id}"
        self.assertTrue(job_dir.exists())

        for crop_filename in expected_crops:
            crop_path = job_dir / crop_filename
            self.assertTrue(
                crop_path.exists(),
                f"Missing expected cropped artifact {crop_filename} in job directory!",
            )
            self.assertGreater(crop_path.stat().st_size, 1000)

        # 5. Verify final output markdown content contains all 4 distinct image tags
        out_md_path = job_dir / f"output_{job_id}_v1.md"
        self.assertTrue(out_md_path.exists())
        final_md_text = out_md_path.read_text(encoding="utf-8")

        self.assertIn(f"![[crop_{job_id}_p1_1.jpg]]", final_md_text)
        self.assertIn(f"![[crop_{job_id}_p1_2.jpg]]", final_md_text)
        self.assertIn(f"![[crop_{job_id}_p2_1.jpg]]", final_md_text)
        self.assertIn(f"![[crop_{job_id}_p2_2.jpg]]", final_md_text)

        # Confirm job status is DONE
        with self.uow_factory.create() as uow:
            db_job = uow.jobs.get_by_id(job_id)
            self.assertEqual(db_job.status, JobStatus.DONE)
            self.assertEqual(db_job.processed_pages, 2)


if __name__ == "__main__":
    unittest.main()
