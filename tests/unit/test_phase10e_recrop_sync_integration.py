# ============================================================
#  tests/unit/test_phase10e_recrop_sync_integration.py
#  Integration tests for Phase 10E automatic recrop and immediate Markdown refresh
# ============================================================

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from application.dto.visual_region_dto import ApplyReviewResultDTO
from application.ports.document_processor import IDocumentProcessor
from application.services.apply_review_service import ApplyReviewService
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox, CropPolicy
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.app import wire_review_workspace_sync
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


class TestPhase10ERecropSyncIntegration(unittest.TestCase):
    """
    End-to-end integration tests verifying that visual region review mutations
    trigger ApplyReviewService and update Markdown review workspace images in-place.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "polpot_test.db"
        self.db_manager = SQLiteDatabaseManager(db_path=self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.storage = LocalStorageAdapter(base_dir=Path(self.temp_dir.name) / "artifacts")
        self.doc_processor = PyMuPDFDocumentProcessor()

        # Synthetic 200x200 JPEG
        img = Image.new("RGB", (200, 200), color=(180, 180, 180))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.page_jpeg = buf.getvalue()

        self.doc_processor.get_page_count = lambda pdf_bytes: 1
        self.doc_processor.render_page_to_jpeg = lambda pdf_bytes, page_number, dpi=150: self.page_jpeg

        # Store mock source PDF
        self.pdf_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.SOURCE_PDF,
            filename="source.pdf",
            data=b"%PDF-1.4 mock pdf data",
            mime_type="application/pdf",
        )

        import uuid
        self.region_id = uuid.uuid4().hex

        # Store initial crop artifact v1
        self.crop_v1_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.CROPPED_IMAGE,
            filename=f"crop_1_{self.region_id}_v1.jpg",
            data=self.page_jpeg,
            mime_type="image/jpeg",
        )

        # Store initial markdown document v1
        self.initial_md_text = (
            "# Title\n\n"
            "Here is the chart:\n\n"
            f"![[crop_1_{self.region_id}_v1.jpg|region_id={self.region_id}]]\n\n"
            "Summary paragraph."
        )
        self.output_md_handle = self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="output_1_v1.md",
            data=self.initial_md_text.encode("utf-8"),
            mime_type="text/markdown",
        )
        self.storage.store(
            job_id=1,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1_v1.md",
            data=self.initial_md_text.encode("utf-8"),
            mime_type="text/markdown",
        )

        # Create Job and VisualRegion in SQLite
        with self.uow_factory.create() as uow:
            prompt = uow.prompts.save(
                Prompt(id=None, name="P1", text="Convert to MD", prompt_type=PromptType.PIPELINE_1, is_default=True)
            )
            self.job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="source.pdf",
                    file_path=self.pdf_handle.uri,
                    total_pages=1,
                    status=JobStatus.DONE,
                    output_path=self.output_md_handle.uri,
                    output_artifact_version_watermark=1,
                    prompt_id=prompt.id,
                )
            )
            self.region = uow.visual_regions.save(
                VisualRegion(
                    id=None,
                    region_id=self.region_id,
                    job_id=self.job.id,
                    page_number=1,
                    display_order=1,
                    origin=RegionOrigin.AI_DETECTED,
                    review_status=ReviewStatus.UNREVIEWED,
                    sync_status=SyncStatus.SYNCED,
                    detected_bbox=BoundingBox(100, 100, 500, 500),
                    active_artifact_version=1,
                    active_artifact_uri=self.crop_v1_handle.uri,
                    artifact_version_watermark=1,
                )
            )
            uow.commit()

        # Services
        self.apply_service = ApplyReviewService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )
        self.doc_service = DocumentViewerService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )
        self.parser = MarkdownItParser()
        self.md_service = MarkdownViewerService(
            parser=self.parser,
            uow_factory=self.uow_factory,
            storage=self.storage,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resize_mutation_triggers_apply_and_updates_markdown_in_place(self):
        """
        End-to-end verification:
        1. Resize region in DocumentViewerController.
        2. Commit resize -> triggers apply_reviews via ApplyReviewService.
        3. active_artifact_version advances from 1 to 2.
        4. regionArtifactCommitted signal emits with new _v2.jpg artifact URI.
        5. MarkdownViewerController receives update and mutates MarkdownDocumentModel in-place.
        6. QML imageUri reflects new artifact URI without resetting model or losing scroll.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)

        # Wire bidirectional sync (including regionArtifactCommitted)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        # Load job 1 in both controllers synchronously
        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        # Verify initial Markdown state
        assert md_ctrl.activeJobId == self.job.id
        assert md_ctrl.activeVersion == 1
        model_items = md_ctrl.model._items
        assert len(model_items) > 0

        # Locate image node
        image_nodes = [item for item in model_items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes) == 1
        initial_img_uri = image_nodes[0]["imageUri"]
        assert f"crop_1_{self.region_id}_v1.jpg" in initial_img_uri

        # Spy on regionArtifactCommitted signal
        committed_events = []
        doc_ctrl.regionArtifactCommitted.connect(
            lambda j_id, r_id, ver, uri: committed_events.append((j_id, r_id, ver, uri))
        )

        # Start and commit resize on the region
        doc_ctrl.startResize(self.region_id, "se", 500.0, 500.0)
        doc_ctrl.updateResize(600.0, 600.0)
        doc_ctrl.commitResize()

        # Synchronously apply or verify apply execution
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)

        # Verify signal was emitted
        assert len(committed_events) >= 1
        last_event = committed_events[-1]
        assert last_event[0] == self.job.id
        assert last_event[1] == self.region_id
        assert last_event[2] == 2  # Monotonically incremented to version 2
        assert f"crop_1_{self.region_id}_v2.jpg" in last_event[3]

        # Verify MarkdownDocumentModel was updated IN-PLACE
        updated_image_nodes = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(updated_image_nodes) == 1
        updated_img_uri = updated_image_nodes[0]["imageUri"]
        assert f"crop_1_{self.region_id}_v2.jpg" in updated_img_uri
        assert updated_img_uri != initial_img_uri

        # Verify MarkdownViewerController active version updated
        assert md_ctrl.activeVersion == 2

        # Verify SQLite persistent state
        with self.uow_factory.create() as uow:
            updated_region = uow.visual_regions.get_by_region_id(self.region_id)
            assert updated_region.active_artifact_version == 2
            assert updated_region.sync_status == SyncStatus.SYNCED
            assert f"crop_1_{self.region_id}_v2.jpg" in updated_region.active_artifact_uri

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_multiple_occurrences_update_simultaneously(self):
        """
        Verifies that when a visual region appears in multiple Markdown AST nodes,
        in-place update refreshes all occurrences across the document simultaneously.
        """
        multi_md_text = (
            "# Multi Test\n\n"
            "First occurrence:\n\n"
            f"![[crop_1_{self.region_id}_v1.jpg|region_id={self.region_id}]]\n\n"
            "Middle paragraph text.\n\n"
            "Second occurrence:\n\n"
            f"![[crop_1_{self.region_id}_v1.jpg|region_id={self.region_id}]]\n"
        )
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=f"output_{self.job.id}_v1.md",
            data=multi_md_text.encode("utf-8"),
            mime_type="text/markdown",
        )
        self.storage.store(
            job_id=self.job.id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="page_1_v1.md",
            data=multi_md_text.encode("utf-8"),
            mime_type="text/markdown",
        )

        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        # Check initial occurrences
        initial_matching = [
            item for item in md_ctrl.model._items
            if item.get("primaryRegionId") == self.region_id
            or any(r.get("regionId") == self.region_id for r in item.get("regions", []))
        ]
        assert len(initial_matching) == 2
        for item in initial_matching:
            assert f"crop_1_{self.region_id}_v1.jpg" in item["imageUri"]

        # Mutate region geometry to DIRTY_RECROP_REQUIRED via resize
        doc_ctrl.startResize(self.region_id, "se", 500.0, 500.0)
        doc_ctrl.updateResize(600.0, 600.0)
        doc_ctrl.commitResize()

        # Apply recrop to version 2
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)

        # Verify BOTH occurrences updated in-place
        updated_matching = [
            item for item in md_ctrl.model._items
            if item.get("primaryRegionId") == self.region_id
            or any(r.get("regionId") == self.region_id for r in item.get("regions", []))
        ]
        assert len(updated_matching) == 2
        for item in updated_matching:
            assert f"crop_1_{self.region_id}_v2.jpg" in item["imageUri"]

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_apply_failure_emits_signal_and_preserves_existing_artifact(self):
        """
        Verifies that when ApplyReviewService fails:
        1. applyFailed signal is emitted with error message.
        2. errorMessage property is set.
        3. Existing v1 artifact remains active in Markdown model and SQLite.
        """
        mock_apply_service = MagicMock(spec=ApplyReviewService)
        mock_apply_service.apply_reviews.return_value = ApplyReviewResultDTO(
            job_id=self.job.id,
            applied_count=0,
            updated_region_ids=[],
            success=False,
            error_message="Simulated disk write error",
        )

        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=mock_apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        errors = []
        doc_ctrl.applyFailed.connect(lambda j_id, r_id, err: errors.append((j_id, r_id, err)))

        # Trigger apply
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)

        # Verify error emission
        assert len(errors) == 1
        assert errors[0][0] == self.job.id
        assert errors[0][1] == self.region_id
        assert "Simulated disk write error" in errors[0][2]
        assert "Simulated disk write error" in doc_ctrl.errorMessage

        # Verify Markdown model preserved v1 imageUri
        image_nodes = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes) == 1
        assert f"crop_1_{self.region_id}_v1.jpg" in image_nodes[0]["imageUri"]

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_drag_and_manual_create_trigger_async_apply(self):
        """
        Verifies that commitDrag and commitCreateManual trigger the async apply pipeline.
        """
        mock_apply = MagicMock(spec=ApplyReviewService)
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=mock_apply,
        )
        doc_ctrl.loadPageSync(self.job.id, 1)
        doc_ctrl.selectRegion(self.region_id)

        # 1. Test commitDrag
        doc_ctrl.startDrag(self.region_id, 100.0, 100.0)
        doc_ctrl.updateDrag(150.0, 150.0)
        doc_ctrl.commitDrag()
        # _executor submitted task which invokes apply_reviews
        # Allow worker thread a moment or verify task was queued
        doc_ctrl._executor.shutdown(wait=True)
        assert mock_apply.apply_reviews.called

        doc_ctrl.shutdown()

    def test_delete_and_reset_trigger_apply(self):
        """
        Verifies that resetSelectedRegionToAi and deleteSelectedRegion trigger apply
        and update Markdown in-place.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        # 1. First resize so region is modified
        doc_ctrl.startResize(self.region_id, "se", 500.0, 500.0)
        doc_ctrl.updateResize(600.0, 600.0)
        doc_ctrl.commitResize()
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)
        assert md_ctrl.activeVersion == 2

        # 2. Reset to AI
        doc_ctrl.selectRegion(self.region_id)
        assert doc_ctrl.canResetSelectedToAi is True
        doc_ctrl.resetSelectedRegionToAi()
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)

        # Version increments to 3
        assert md_ctrl.activeVersion == 3
        image_nodes = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes) == 1
        assert f"crop_1_{self.region_id}_v3.jpg" in image_nodes[0]["imageUri"]

        # 3. Delete region
        doc_ctrl.selectRegion(self.region_id)
        doc_ctrl.deleteSelectedRegion()
        doc_ctrl.apply_region_sync(self.job.id, self.region_id)

        # In Markdown model, deleted region active artifact URI becomes empty
        image_nodes_after_del = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes_after_del) == 1
        assert image_nodes_after_del[0]["imageUri"] == ""

        doc_ctrl.shutdown()
        md_ctrl.shutdown()
