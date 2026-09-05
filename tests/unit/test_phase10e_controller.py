# ============================================================
#  tests/unit/test_phase10e_controller.py
#  Unit Tests for Phase 10E.4 Markdown Viewer Controller & Model
# ============================================================

import ast
import os
from unittest.mock import MagicMock

import pytest

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from core.exceptions.domain_exceptions import DomainError, EntityNotFoundError
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def create_sample_document_dto() -> MarkdownDocumentDTO:
    vref1 = VisualRegionRefDTO(
        occurrence_id="p_hash_1_img_0",
        source="crop_100_rid1_v1.jpg",
        image_path="/data/artifacts/job_100/crop_100_rid1_v1.jpg",
        alt_text="Figure 1",
        region_id="rid_001",
        is_associated=True,
        display_order=1,
        page_number=1,
    )
    vref2 = VisualRegionRefDTO(
        occurrence_id="p_hash_2_img_0",
        source="crop_100_rid2_v1.jpg",
        image_path="/data/artifacts/job_100/crop_100_rid2_v1.jpg",
        alt_text="Figure 2",
        region_id="rid_002",
        is_associated=True,
        display_order=2,
        page_number=2,
    )

    node0 = MarkdownNodeDTO(
        node_id="h1_title_1",
        node_type="heading",
        level=1,
        content="Title Heading",
        raw_markdown="# Title Heading",
    )

    node1 = MarkdownNodeDTO(
        node_id="p_hash_1_1",
        node_type="paragraph",
        content='Before <a href="region://rid_001">[#1]</a> after.',
        raw_markdown="Before ![[crop.jpg]] after.",
        regions=(vref1,),
        segments=(
            InlineSegmentDTO(segment_type="text", text_html="Before "),
            InlineSegmentDTO(segment_type="image", image_ref=vref1),
            InlineSegmentDTO(segment_type="text", text_html=" after."),
        ),
    )

    node2 = MarkdownNodeDTO(
        node_id="p_hash_2_1",
        node_type="paragraph",
        content='Second paragraph with <a href="region://rid_002">[#2]</a>.',
        regions=(vref2,),
        segments=(
            InlineSegmentDTO(segment_type="text", text_html="Second paragraph with "),
            InlineSegmentDTO(segment_type="image", image_ref=vref2),
            InlineSegmentDTO(segment_type="text", text_html="."),
        ),
    )

    return MarkdownDocumentDTO(
        job_id=100,
        version=2,
        nodes=(node0, node1, node2),
        region_to_occurrences={
            "rid_001": (RegionOccurrenceRef(node_index=1, occurrence_id="p_hash_1_img_0"),),
            "rid_002": (RegionOccurrenceRef(node_index=2, occurrence_id="p_hash_2_img_0"),),
        },
    )


# ---------------------------------------------------------------------------
# 1. Model Role Access & O(1) Index Tests
# ---------------------------------------------------------------------------

def test_model_initial_state(qapp):
    model = MarkdownDocumentModel()
    assert model.rowCount() == 0
    assert model.indexOfRegion("any") == -1
    assert model.occurrencesOfRegion("any") == []
    assert model.getNode(0) is None


def test_model_set_document_and_roles(qapp):
    model = MarkdownDocumentModel()
    doc_dto = create_sample_document_dto()

    model.set_document(doc_dto)

    assert model.rowCount() == 3

    # Row 0: Heading
    idx0 = model.index(0, 0)
    assert model.data(idx0, MarkdownDocumentModel.NodeIdRole) == "h1_title_1"
    assert model.data(idx0, MarkdownDocumentModel.NodeTypeRole) == "heading"
    assert model.data(idx0, MarkdownDocumentModel.LevelRole) == 1
    assert model.data(idx0, MarkdownDocumentModel.ContentRole) == "Title Heading"
    assert model.data(idx0, MarkdownDocumentModel.IsAssociatedRole) is False

    # Row 1: Paragraph with visual region
    idx1 = model.index(1, 0)
    assert model.data(idx1, MarkdownDocumentModel.NodeIdRole) == "p_hash_1_1"
    assert model.data(idx1, MarkdownDocumentModel.NodeTypeRole) == "paragraph"
    assert model.data(idx1, MarkdownDocumentModel.PrimaryRegionIdRole) == "rid_001"
    assert model.data(idx1, MarkdownDocumentModel.IsAssociatedRole) is True
    assert model.data(idx1, MarkdownDocumentModel.DisplayOrderRole) == 1
    assert model.data(idx1, MarkdownDocumentModel.PageNumberRole) == 1

    # ImageUri converted to file://
    uri = model.data(idx1, MarkdownDocumentModel.ImageUriRole)
    assert uri.startswith("file://")
    assert "/data/artifacts/job_100/crop_100_rid1_v1.jpg" in uri

    # Segments
    segments = model.data(idx1, MarkdownDocumentModel.SegmentsRole)
    assert len(segments) == 3
    assert segments[0]["segmentType"] == "text"
    assert segments[1]["segmentType"] == "image"
    assert segments[1]["imageRef"]["regionId"] == "rid_001"
    assert segments[1]["imageRef"]["imageUri"].startswith("file://")
    assert segments[2]["segmentType"] == "text"

    # Fast O(1) Index Lookups
    assert model.indexOfRegion("rid_001") == 1
    assert model.indexOfRegion("rid_002") == 2
    assert model.indexOfRegion("unknown") == -1

    occs = model.occurrencesOfRegion("rid_001")
    assert len(occs) == 1
    assert occs[0]["nodeIndex"] == 1
    assert occs[0]["occurrenceId"] == "p_hash_1_img_0"

    # Reset model
    model.set_document(None)
    assert model.rowCount() == 0
    assert model.indexOfRegion("rid_001") == -1


# ---------------------------------------------------------------------------
# 2. Controller Presentation & Zoom Tests
# ---------------------------------------------------------------------------

def test_controller_initial_state(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    assert ctrl.isLoading is False
    assert ctrl.hasDocument is False
    assert ctrl.errorMessage == ""
    assert ctrl.activeJobId == 0
    assert ctrl.activeVersion == 1
    assert ctrl.scaleFactor == 1.0
    assert ctrl.selectedNodeIndex == -1
    assert ctrl.highlightedRegionId == ""
    assert ctrl.highlightedOccurrenceId == ""
    assert ctrl.pageFilter == 0
    assert ctrl.model.rowCount() == 0


def test_controller_zoom_operations(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    # Zoom in
    ctrl.zoomIn()
    assert ctrl.scaleFactor == 1.1

    # Zoom out
    ctrl.zoomOut()
    assert ctrl.scaleFactor == 1.0

    # Upper clamp
    ctrl.setScaleFactor(5.0)
    assert ctrl.scaleFactor == 3.0

    # Lower clamp
    ctrl.setScaleFactor(0.1)
    assert ctrl.scaleFactor == 0.5

    # Reset
    ctrl.resetZoom()
    assert ctrl.scaleFactor == 1.0


# ---------------------------------------------------------------------------
# 3. Synchronous Document Loading & Error Handling Tests
# ---------------------------------------------------------------------------

def test_controller_load_document_success(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    doc_dto = create_sample_document_dto()
    mock_service.load_document.return_value = doc_dto

    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl.load_document_sync(100)

    assert ctrl.hasDocument is True
    assert ctrl.isLoading is False
    assert ctrl.errorMessage == ""
    assert ctrl.activeJobId == 100
    assert ctrl.activeVersion == 2
    assert ctrl.model.rowCount() == 3


def test_controller_load_document_error(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    mock_service.load_document.side_effect = DomainError("Artifact file missing")

    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl.load_document_sync(99)

    assert ctrl.hasDocument is False
    assert ctrl.isLoading is False
    assert "Artifact file missing" in ctrl.errorMessage
    assert ctrl.model.rowCount() == 0


# ---------------------------------------------------------------------------
# 4. Generation Token Stale Request Protection Tests
# ---------------------------------------------------------------------------

def test_controller_stale_generation_request_dropped(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    doc_v1 = create_sample_document_dto()
    doc_v2 = MarkdownDocumentDTO(
        job_id=200,
        version=5,
        nodes=(),
        region_to_occurrences={},
    )

    # Request 1 started
    ctrl._request_id = 1

    # Request 2 started and finishes first
    ctrl._request_id = 2
    ctrl._on_internal_doc_loaded(2, doc_v2)

    assert ctrl.activeJobId == 200
    assert ctrl.activeVersion == 5

    # Stale Request 1 finishes later
    ctrl._on_internal_doc_loaded(1, doc_v1)

    # State must remain Request 2 (job 200, version 5), NOT stale job 100
    assert ctrl.activeJobId == 200
    assert ctrl.activeVersion == 5


# ---------------------------------------------------------------------------
# 5. Region Selection & Hyperlink Navigation Tests
# ---------------------------------------------------------------------------

def test_controller_region_selection_and_link_routing(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    doc_dto = create_sample_document_dto()
    mock_service.load_document.return_value = doc_dto

    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl.load_document_sync(100)

    # Mock signal listeners
    scroll_events = []
    region_events = []
    external_links = []

    ctrl.requestScrollToNode.connect(lambda idx: scroll_events.append(idx))
    ctrl.regionSelected.connect(lambda r, o: region_events.append((r, o)))
    ctrl.externalLinkActivated.connect(lambda u: external_links.append(u))

    # Select region rid_001
    ctrl.selectRegion("rid_001", "occ_x")

    assert ctrl.highlightedRegionId == "rid_001"
    assert ctrl.highlightedOccurrenceId == "occ_x"
    assert ctrl.selectedNodeIndex == 1
    assert scroll_events == [1]
    assert region_events == [("rid_001", "occ_x")]

    # Select region rid_002 via handleLinkClicked("region://rid_002")
    ctrl.handleLinkClicked("region://rid_002")

    assert ctrl.highlightedRegionId == "rid_002"
    assert ctrl.selectedNodeIndex == 2
    assert scroll_events == [1, 2]
    assert region_events[-1] == ("rid_002", "")

    # External link activated
    ctrl.handleLinkClicked("https://example.com/docs")
    assert "https://example.com/docs" in external_links

    # Clear controller
    ctrl.clear()
    assert ctrl.hasDocument is False
    assert ctrl.selectedNodeIndex == -1
    assert ctrl.highlightedRegionId == ""
    assert ctrl.model.rowCount() == 0


# ---------------------------------------------------------------------------
# 6. Clean Architecture Boundary Tests (Zero sqlite3/keyring/vendor SDKs)
# ---------------------------------------------------------------------------

def test_clean_architecture_controller_imports():
    disallowed_modules = {"sqlite3", "pymysql", "keyring", "google", "openai", "anthropic"}

    files_to_check = [
        "interfaces/desktop/models/markdown_document_model.py",
        "interfaces/desktop/controllers/markdown_viewer_controller.py",
    ]

    for rel_path in files_to_check:
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg not in disallowed_modules, f"Disallowed import '{alias.name}' in {rel_path}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert root_pkg not in disallowed_modules, f"Disallowed import from '{node.module}' in {rel_path}"
