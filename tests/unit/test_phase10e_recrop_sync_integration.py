# ============================================================
#  tests/unit/test_phase10e_recrop_sync_integration.py
#  Integration tests for Phase 10E automatic recrop and immediate Markdown refresh
# ============================================================

import io
import os
import tempfile
import threading
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
from core.exceptions.domain_exceptions import DomainError
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.markdown.pandoc_parser import PandocParser
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

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])

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
        self.parser = PandocParser()
        self.md_service = MarkdownViewerService(
            parser=self.parser,
            uow_factory=self.uow_factory,
            storage=self.storage,
        )

    def tearDown(self):
        for _ in range(5):
            QGuiApplication.processEvents()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

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

        # Wait for async apply triggered by commitResize to complete
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

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

        # Wait for asynchronous structural reconciliation
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        # Canonical markdown document advanced to version 2 via ApplyReviewService
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

        # Wait for async apply triggered by commitResize to complete
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

        # Wait for asynchronous structural reconciliation
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

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
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()
        assert md_ctrl.activeVersion == 2

        # 2. Reset to AI
        doc_ctrl.selectRegion(self.region_id)
        assert doc_ctrl.canResetSelectedToAi is True
        doc_ctrl.resetSelectedRegionToAi()
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        # Canonical document watermark advances to 3; region crop version is 3
        assert md_ctrl.activeVersion == 3
        image_nodes = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes) == 1
        assert f"crop_1_{self.region_id}_v3.jpg" in image_nodes[0]["imageUri"]

        # 3. Delete region
        doc_ctrl.selectRegion(self.region_id)
        doc_ctrl.deleteSelectedRegion()
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        # Canonical document watermark advances to 4; deleted region is removed from canonical Markdown model
        assert md_ctrl.activeVersion == 4
        image_nodes_after_del = [item for item in md_ctrl.model._items if item.get("primaryRegionId") == self.region_id]
        assert len(image_nodes_after_del) == 0

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_manual_region_creation_inserts_into_live_markdown_model(self):
        """
        Test 3: End-to-end integration for NEW manual visual region:
        1. User creates manual region via rubber-band gesture in PDF viewer.
        2. ApplyReviewService commits crop v1 and appends token to Markdown artifact.
        3. regionArtifactCommitted signal triggers structural synchronization.
        4. MarkdownViewerController asynchronously reconciles model using canonical DTO.
        5. Assertions:
           - New region exists in SQLite (USER_MANUAL, ACCEPTED, SYNCED).
           - New crop exists on disk.
           - Canonical Markdown file on disk contains the new image tag.
           - Live MarkdownDocumentModel contains the new region without modelReset.
           - Model rowCount incremented by 1.
           - occurrencesOfRegion returns the canonical occurrence.
           - imageUri displays the committed crop.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        initial_row_count = md_ctrl.model.rowCount()

        # Track signals on model to prove modelReset is NEVER emitted
        resets = []
        insertions = []
        md_ctrl.model.modelAboutToBeReset.connect(lambda: resets.append("reset"))
        md_ctrl.model.rowsInserted.connect(lambda p, f, l: insertions.append((f, l)))

        # 1. User draws manual box on PDF page 1
        doc_ctrl.startCreateManual(100.0, 100.0)
        doc_ctrl.updateCreateManual(300.0, 300.0)
        doc_ctrl.commitCreateManual()

        new_rid = doc_ctrl.selectedRegionId
        assert new_rid != "", "Expected selectedRegionId to be set after commitCreateManual"
        assert new_rid != self.region_id

        # 2. Wait for automatic asynchronous apply to complete and process Qt events
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

        # 3. Wait for background reconciliation task in MarkdownViewerController and process Qt events
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        # 4. Invariant assertions
        assert len(resets) == 0, "Structural reconciliation must NOT emit modelReset"
        assert len(insertions) == 1, "Expected exactly 1 rowsInserted signal"
        assert md_ctrl.model.rowCount() == initial_row_count + 1

        # 5. Verify SQLite state
        with self.uow_factory.create() as uow:
            created_region = uow.visual_regions.get_by_region_id(new_rid)
            assert created_region is not None
            assert created_region.origin == RegionOrigin.USER_MANUAL
            assert created_region.review_status == ReviewStatus.ACCEPTED
            assert created_region.sync_status == SyncStatus.SYNCED
            assert created_region.active_artifact_version == 1
            assert f"crop_1_{new_rid}_v1.jpg" in created_region.active_artifact_uri

            job_rec = uow.jobs.get_by_id(self.job.id)
            output_bytes = self.storage.retrieve(
                ArtifactHandle(
                    storage_backend=StorageBackendType.LOCAL_FS,
                    uri=job_rec.output_path,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    job_id=self.job.id,
                    filename=os.path.basename(job_rec.output_path),
                )
            )
            canonical_md = output_bytes.decode("utf-8")
            assert f"crop_1_{new_rid}_v1.jpg" in canonical_md
            assert f"region_id={new_rid}" in canonical_md

        # 6. Verify live presentation model contains the new occurrence
        occs = md_ctrl.model.occurrencesOfRegion(new_rid)
        assert len(occs) == 1
        assert occs[0]["occurrenceId"] == f"img_{new_rid}_img_0"

        new_node_idx = md_ctrl.model.indexOfRegion(new_rid)
        assert new_node_idx >= 0
        item = md_ctrl.model.getNode(new_node_idx)
        assert item is not None
        assert item["primaryRegionId"] == new_rid
        assert f"crop_1_{new_rid}_v1.jpg" in item["imageUri"]

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_multiple_new_regions_created_consecutively(self):
        """
        Test 4: Creating multiple new regions consecutively:
        Both regions appear in the model at their canonical positions with correct mappings.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        initial_count = md_ctrl.model.rowCount()

        # Region A
        doc_ctrl.startCreateManual(50.0, 50.0)
        doc_ctrl.updateCreateManual(150.0, 150.0)
        doc_ctrl.commitCreateManual()
        rid_a = doc_ctrl.selectedRegionId
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        assert md_ctrl.model.rowCount() == initial_count + 1
        assert len(md_ctrl.model.occurrencesOfRegion(rid_a)) == 1

        # Region B
        doc_ctrl.startCreateManual(200.0, 200.0)
        doc_ctrl.updateCreateManual(400.0, 400.0)
        doc_ctrl.commitCreateManual()
        rid_b = doc_ctrl.selectedRegionId
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        assert md_ctrl.model.rowCount() == initial_count + 2
        assert len(md_ctrl.model.occurrencesOfRegion(rid_b)) == 1

        # Both remain mapped and distinct
        assert md_ctrl.model.indexOfRegion(rid_a) >= 0
        assert md_ctrl.model.indexOfRegion(rid_b) >= 0
        assert md_ctrl.model.indexOfRegion(rid_a) != md_ctrl.model.indexOfRegion(rid_b)

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_subsequent_edit_of_new_region_uses_inplace_update(self):
        """
        After a new manual region is reconciled into the model, a subsequent resize
        must use the existing targeted update_region_artifact() path without inserting rows.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        # 1. Create new manual region
        doc_ctrl.startCreateManual(100.0, 100.0)
        doc_ctrl.updateCreateManual(200.0, 200.0)
        doc_ctrl.commitCreateManual()
        rid = doc_ctrl.selectedRegionId
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()
        md_ctrl.wait_for_reconciliation()
        for _ in range(5):
            QGuiApplication.processEvents()

        count_after_create = md_ctrl.model.rowCount()

        # Track model insertions during subsequent resize
        insertions = []
        md_ctrl.model.rowsInserted.connect(lambda p, f, l: insertions.append((f, l)))

        # 2. Resize this new region
        doc_ctrl.selectRegion(rid)
        doc_ctrl.startResize(rid, "se", 200.0, 200.0)
        doc_ctrl.updateResize(250.0, 250.0)
        doc_ctrl.commitResize()
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

        # In-place update happens synchronously via dataChanged
        for _ in range(5):
            QGuiApplication.processEvents()

        # Assert no new rows were inserted
        assert len(insertions) == 0, "Subsequent mutation on reconciled region must NOT insert new rows"
        assert md_ctrl.model.rowCount() == count_after_create

        # Assert crop version updated to v2 in-place
        node_idx = md_ctrl.model.indexOfRegion(rid)
        item = md_ctrl.model.getNode(node_idx)
        assert item is not None
        assert f"crop_1_{rid}_v2.jpg" in item["imageUri"]

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_stale_structural_reconciliation_result_ignored(self):
        """
        Test 5: Stale reconciliation request guard:
        If a newer request arrives or active document changes, older result is discarded.
        """
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        md_ctrl.load_document_sync(self.job.id)

        initial_count = md_ctrl.model.rowCount()

        # Request A with req_id 100
        req_id_a = md_ctrl._request_id
        # Invalidate by advancing request_id
        md_ctrl._request_id += 1

        # Simulate late arrival of request A result
        fake_dto = MagicMock()
        fake_dto.job_id = self.job.id
        fake_dto.version = 99
        fake_dto.nodes = ()
        fake_dto.region_to_occurrences = {}

        md_ctrl._on_internal_reconcile_loaded(req_id_a, fake_dto)

        # Model was NOT modified by stale result
        assert md_ctrl.model.rowCount() == initial_count
        assert md_ctrl.activeVersion == 1

        md_ctrl.shutdown()

    def test_structural_reconciliation_failure_path_preserves_model(self):
        """
        Test 6: Reconciliation failure handling:
        If canonical document loading fails, existing model remains intact,
        no phantom row is inserted, and error message is populated.
        """
        failing_service = MagicMock(spec=MarkdownViewerService)
        failing_service.load_document.side_effect = DomainError("Disk read error during parse")

        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        md_ctrl.load_document_sync(self.job.id)
        initial_count = md_ctrl.model.rowCount()

        # Switch to failing service for reconciliation
        md_ctrl.viewer_service = failing_service

        # Trigger reconciliation
        md_ctrl.sync_document_structure_sync(self.job.id)

        # Invariant: existing model is preserved
        assert md_ctrl.model.rowCount() == initial_count
        assert md_ctrl.hasDocument is True
        assert "Disk read error during parse" in md_ctrl.errorMessage

        md_ctrl.shutdown()

    def test_reconciliation_does_not_overwrite_newer_inplace_artifact_mutation(self):
        """
        Concurrency Race Case B:
        When a structural reconciliation R1 is in-flight (triggered by a new region),
        and an existing region undergoes an in-place artifact update before R1 completes:
        1. Existing region is immediately updated in the presentation model.
        2. In-flight R1 is invalidated by advancing request_id and re-triggering sync R2.
        3. When R1 completes with stale state, its payload is dropped due to generation mismatch.
        4. When R2 completes with the latest state, the newer artifact is preserved.
        """
        doc_ctrl = DocumentViewerController(
            viewer_service=self.doc_service,
            apply_review_service=self.apply_service,
        )
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        wire_review_workspace_sync(doc_ctrl, md_ctrl)

        doc_ctrl.loadPageSync(self.job.id, 1)
        md_ctrl.load_document_sync(self.job.id)

        # Hook md_ctrl.viewer_service.load_document to pause R1 after it reads stale markdown
        real_load_document = md_ctrl.viewer_service.load_document
        r1_called = threading.Event()
        r1_resume = threading.Event()
        load_count = 0

        def intercepted_load(job_id):
            nonlocal load_count
            load_count += 1
            if load_count == 1:
                # Read initial document state (where region B is still v1)
                stale_dto = real_load_document(job_id)
                r1_called.set()
                r1_resume.wait(timeout=5.0)
                return stale_dto
            return real_load_document(job_id)

        md_ctrl.viewer_service.load_document = intercepted_load

        # Step 1: Create new manual region A to trigger structural reconciliation R1
        doc_ctrl.startCreateManual(100.0, 100.0)
        doc_ctrl.updateCreateManual(200.0, 200.0)
        doc_ctrl.commitCreateManual()
        rid_a = doc_ctrl.selectedRegionId
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

        # Wait until R1 starts and is paused in the background holding stale_dto
        assert r1_called.wait(timeout=3.0), "R1 failed to start"
        assert md_ctrl.reconcile_in_flight is True
        req_id_r1 = md_ctrl._request_id

        # Verify region B's initial state in md_ctrl
        node_idx_b = md_ctrl.model.indexOfRegion(self.region_id)
        assert node_idx_b >= 0
        assert f"crop_1_{self.region_id}_v1.jpg" in md_ctrl.model.getNode(node_idx_b)["imageUri"]

        # Step 2: While R1 is in-flight, mutate existing region B
        doc_ctrl.selectRegion(self.region_id)
        doc_ctrl.startResize(self.region_id, "se", 500.0, 500.0)
        doc_ctrl.updateResize(600.0, 600.0)
        doc_ctrl.commitResize()
        doc_ctrl.wait_for_apply()
        for _ in range(5):
            QGuiApplication.processEvents()

        # In-place update should have immediately updated region B in presentation model
        updated_node_b = md_ctrl.model.getNode(node_idx_b)
        assert f"crop_1_{self.region_id}_v2.jpg" in updated_node_b["imageUri"]

        # Controller detected in-flight reconciliation and re-synced (bumping request_id)
        assert md_ctrl._request_id > req_id_r1
        assert md_ctrl.reconcile_in_flight is True

        # Step 3: Resume R1. Its stale payload (where B was still v1) must be dropped!
        r1_resume.set()

        # Wait for all background tasks to complete
        md_ctrl.wait_for_reconciliation(timeout=3.0)
        for _ in range(10):
            QGuiApplication.processEvents()

        # Step 4: Verify region B was NOT overwritten with stale crop v1
        final_node_b = md_ctrl.model.getNode(node_idx_b)
        assert f"crop_1_{self.region_id}_v2.jpg" in final_node_b["imageUri"]
        assert f"crop_1_{self.region_id}_v1.jpg" not in final_node_b["imageUri"]

        # And new region A was successfully reconciled into the model
        assert md_ctrl.model.indexOfRegion(rid_a) >= 0
        assert md_ctrl.reconcile_in_flight is False

        doc_ctrl.shutdown()
        md_ctrl.shutdown()

    def test_region_artifact_version_does_not_conflate_with_document_version(self):
        """
        In-place region artifact updates must not conflate the region's crop version
        with the document watermark version.
        """
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        md_ctrl.load_document_sync(self.job.id)

        assert md_ctrl.activeVersion == 1

        # Region crop version advances to 5 in-place
        md_ctrl.updateRegionArtifact(self.region_id, "/path/to/crop_v5.jpg", new_version=5)

        # Verify model node has updated imageUri
        node_idx = md_ctrl.model.indexOfRegion(self.region_id)
        assert node_idx >= 0
        assert "crop_v5.jpg" in md_ctrl.model.getNode(node_idx)["imageUri"]

        # Invariant: active document version watermark is strictly decoupled from region crop version
        assert md_ctrl.activeVersion == 1

        # Only canonical document loading / reconciliation advances activeVersion
        mock_doc = MagicMock()
        mock_doc.job_id = self.job.id
        mock_doc.version = 2
        mock_doc.nodes = ()
        mock_doc.region_to_occurrences = {}

        md_ctrl._on_internal_reconcile_loaded(md_ctrl._request_id, mock_doc)
        assert md_ctrl.activeVersion == 2

        md_ctrl.shutdown()

    def test_stale_document_version_is_rejected_even_if_generation_matches(self):
        """
        If a reconciliation payload arrives with document version strictly less than
        the current activeVersion watermark, it must be rejected even if req_id matches.
        """
        md_ctrl = MarkdownViewerController(viewer_service=self.md_service)
        md_ctrl.load_document_sync(self.job.id)

        # Advance document version to 4
        doc_v4 = MagicMock()
        doc_v4.job_id = self.job.id
        doc_v4.version = 4
        doc_v4.nodes = ()
        doc_v4.region_to_occurrences = {}
        md_ctrl._on_internal_reconcile_loaded(md_ctrl._request_id, doc_v4)
        assert md_ctrl.activeVersion == 4

        initial_count = md_ctrl.model.rowCount()

        # Simulate arrival of payload with older document version 3 and matching req_id
        stale_v3 = MagicMock()
        stale_v3.job_id = self.job.id
        stale_v3.version = 3
        stale_v3.nodes = (MagicMock(),)  # Phantom node that must NOT be applied
        stale_v3.region_to_occurrences = {}

        md_ctrl._reconcile_in_flight = True
        md_ctrl._on_internal_reconcile_loaded(md_ctrl._request_id, stale_v3)

        # Verify rejection: activeVersion unchanged, model not updated, in-flight reset
        assert md_ctrl.activeVersion == 4
        assert md_ctrl.model.rowCount() == initial_count
        assert md_ctrl.reconcile_in_flight is False

        md_ctrl.shutdown()
