# ============================================================
#  tests/unit/test_phase10e_integration.py
#  Integration Tests for Phase 10E.6 Workspace Integration & Sync
# ============================================================

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.artifact import ArtifactType
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from interfaces.desktop.app import create_app, wire_review_workspace_sync
from interfaces.desktop.composition import DesktopAppContainer
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


# ---------------------------------------------------------------------------
# 1. Composition Root Wiring Tests
# ---------------------------------------------------------------------------

def test_composition_root_wires_markdown_components():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        container = DesktopAppContainer(
            db_path=db_path,
            artifacts_dir=os.path.join(tmp_dir, "artifacts"),
            vault_path=os.path.join(tmp_dir, "vault.enc"),
            passphrase="test_passphrase_12345",
        )

        assert hasattr(container, "markdown_parser")
        assert isinstance(container.markdown_parser, MarkdownItParser)

        assert hasattr(container, "markdown_viewer_service")
        assert isinstance(container.markdown_viewer_service, MarkdownViewerService)
        assert container.markdown_viewer_service.parser is container.markdown_parser
        assert container.markdown_viewer_service.uow_factory is container.uow_factory
        assert container.markdown_viewer_service.storage is container.storage


# ---------------------------------------------------------------------------
# 2. Desktop App Shell & Context Property Registration Tests
# ---------------------------------------------------------------------------

def test_create_app_registers_markdown_controller(qapp):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        app, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            artifacts_dir=os.path.join(tmp_dir, "artifacts"),
            vault_path=os.path.join(tmp_dir, "vault.enc"),
            passphrase="test_passphrase_12345",
            start_background_runtime=False,
        )

        assert hasattr(container, "markdown_viewer_controller")
        assert isinstance(container.markdown_viewer_controller, MarkdownViewerController)

        ctx = engine.rootContext()
        # Verify markdownViewerController is exposed to QML
        registered = ctx.contextProperty("markdownViewerController")
        assert registered is container.markdown_viewer_controller

        container.shutdown()


# ---------------------------------------------------------------------------
# 3. Bidirectional Workspace Synchronization Tests
# ---------------------------------------------------------------------------

def test_bidirectional_sync_markdown_to_pdf(qapp):
    mock_doc_service = MagicMock(spec=DocumentViewerService)
    mock_md_service = MagicMock(spec=MarkdownViewerService)

    doc_ctrl = DocumentViewerController(viewer_service=mock_doc_service)
    md_ctrl = MarkdownViewerController(viewer_service=mock_md_service)

    wire_review_workspace_sync(doc_ctrl, md_ctrl)

    # Populate active regions in doc_ctrl
    doc_ctrl._active_regions = [
        {
            "region_id": "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d",
            "display_order": 1,
            "origin": "ai_detected",
            "review_status": "unreviewed",
            "effective_ymin": 100,
            "effective_xmin": 100,
            "effective_ymax": 300,
            "effective_xmax": 300,
        }
    ]

    # Action: User selects region in Markdown Viewer
    md_ctrl.selectRegion("a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d", "occ_01")

    # Verification: PDF Document Viewer selected region is synchronized!
    assert doc_ctrl.selectedRegionId == "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d"


def test_bidirectional_sync_pdf_to_markdown(qapp):
    mock_doc_service = MagicMock(spec=DocumentViewerService)
    mock_md_service = MagicMock(spec=MarkdownViewerService)

    doc_ctrl = DocumentViewerController(viewer_service=mock_doc_service)
    md_ctrl = MarkdownViewerController(viewer_service=mock_md_service)

    # Setup sample document in Markdown model
    vref = VisualRegionRefDTO(
        occurrence_id="p_1_img_0",
        source="crop.jpg",
        region_id="f1e2d3c4b5a64987ba654321fedcba98",
        is_associated=True,
        display_order=2,
    )
    node = MarkdownNodeDTO(
        node_id="p_sample_1",
        node_type="paragraph",
        regions=(vref,),
    )
    doc_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(node,),
        region_to_occurrences={"f1e2d3c4b5a64987ba654321fedcba98": (RegionOccurrenceRef(0, "p_1_img_0"),)},
    )
    md_ctrl._model.set_document(doc_dto)

    wire_review_workspace_sync(doc_ctrl, md_ctrl)

    doc_ctrl._active_regions = [
        {
            "region_id": "f1e2d3c4b5a64987ba654321fedcba98",
            "display_order": 2,
            "origin": "ai_detected",
            "review_status": "unreviewed",
            "effective_ymin": 200,
            "effective_xmin": 200,
            "effective_ymax": 400,
            "effective_xmax": 400,
        }
    ]

    # Action: User clicks bounding box in PDF viewer
    doc_ctrl.selectRegion("f1e2d3c4b5a64987ba654321fedcba98")

    # Verification: Markdown Viewer highlighted region and node index synchronized!
    assert md_ctrl.highlightedRegionId == "f1e2d3c4b5a64987ba654321fedcba98"
    assert md_ctrl.selectedNodeIndex == 0


# ---------------------------------------------------------------------------
# 4. End-to-End Full Flow Test
# ---------------------------------------------------------------------------

def test_full_end_to_end_workspace_flow(qapp):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        app, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            artifacts_dir=os.path.join(tmp_dir, "artifacts"),
            vault_path=os.path.join(tmp_dir, "vault.enc"),
            passphrase="test_passphrase_12345",
            start_background_runtime=False,
        )

        region_id_hex = "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d"
        # 1. Seed database with a completed job and active visual region
        with container.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="paper.pdf",
                file_path="/tmp/paper.pdf",
                status=JobStatus.DONE,
            )
            saved_job = uow.jobs.save(job)
            job_id = saved_job.id

            region = VisualRegion(
                id=None,
                region_id=region_id_hex,
                job_id=job_id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                detected_bbox=BoundingBox(ymin=100, xmin=100, ymax=300, xmax=300),
                review_status=ReviewStatus.ACCEPTED,
                sync_status=SyncStatus.SYNCED,
                active_artifact_version=1,
                active_artifact_uri=f"file:///tmp/crop_{job_id}_{region_id_hex}_v1.jpg",
            )
            uow.visual_regions.save(region)

            # Store Markdown artifact
            md_content = f"# Introduction\n\nFigure: ![[crop.jpg|region_id={region_id_hex}]]\n"
            handle = container.storage.store(
                job_id=job_id,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                filename=f"output_{job_id}_v1.md",
                data=md_content.encode("utf-8"),
                mime_type="text/markdown",
            )
            saved_job.output_path = handle.uri
            uow.jobs.save(saved_job)
            uow.commit()

        # 2. Load document through MarkdownViewerController
        md_ctrl = container.markdown_viewer_controller
        md_ctrl.load_document_sync(job_id)

        # 3. Assert document loaded and populated
        assert md_ctrl.hasDocument is True
        assert md_ctrl.activeJobId == job_id
        assert md_ctrl.model.rowCount() == 2

        # Node 0: Heading
        assert md_ctrl.model.data(md_ctrl.model.index(0, 0), md_ctrl.model.NodeTypeRole) == "heading"
        # Node 1: Paragraph with associated region
        assert md_ctrl.model.data(md_ctrl.model.index(1, 0), md_ctrl.model.NodeTypeRole) == "paragraph"
        assert md_ctrl.model.data(md_ctrl.model.index(1, 0), md_ctrl.model.PrimaryRegionIdRole) == region_id_hex
        assert md_ctrl.model.data(md_ctrl.model.index(1, 0), md_ctrl.model.IsAssociatedRole) is True

        # 4. Assert bidirectional sync to document viewer controller
        doc_ctrl = container.document_viewer_controller
        doc_ctrl._active_regions = [{"region_id": region_id_hex, "display_order": 1}]
        md_ctrl.selectRegion(region_id_hex)
        assert doc_ctrl.selectedRegionId == region_id_hex

        container.shutdown()
