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


# ---------------------------------------------------------------------------
# 7. R2 Hardening Tests: PrimaryOccurrenceIdRole, Executor Shutdown, Multi-Occurrence
# ---------------------------------------------------------------------------

def test_model_primary_occurrence_id_and_lookup_slots(qapp):
    """
    R2.1 Verification: PrimaryOccurrenceIdRole exposes deterministic occurrence identity
    and model provides O(1) occurrence lookup slots.
    """
    model = MarkdownDocumentModel()
    doc_dto = create_sample_document_dto()
    model.set_document(doc_dto)

    idx1 = model.index(1, 0)
    assert model.data(idx1, MarkdownDocumentModel.PrimaryOccurrenceIdRole) == "p_hash_1_img_0"

    idx2 = model.index(2, 0)
    assert model.data(idx2, MarkdownDocumentModel.PrimaryOccurrenceIdRole) == "p_hash_2_img_0"

    # Row 0 has no regions
    idx0 = model.index(0, 0)
    assert model.data(idx0, MarkdownDocumentModel.PrimaryOccurrenceIdRole) == ""

    # Slot lookups
    assert model.primaryOccurrenceOfRegion("rid_001") == "p_hash_1_img_0"
    assert model.primaryOccurrenceOfRegion("rid_002") == "p_hash_2_img_0"
    assert model.primaryOccurrenceOfRegion("unknown") == ""

    assert model.indexOfOccurrence("p_hash_1_img_0") == 1
    assert model.indexOfOccurrence("p_hash_2_img_0") == 2
    assert model.indexOfOccurrence("unknown") == -1


def test_controller_shutdown_lifecycle(qapp):
    """
    R2.2 Verification: Explicit shutdown lifecycle invalidates in-flight requests
    and cleanly stops the executor without crashing.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    init_req_id = ctrl._request_id
    ctrl.shutdown()

    # Generation token incremented to invalidate pending/in-flight work
    assert ctrl._request_id > init_req_id
    # Executor shutdown flag set
    assert ctrl._executor._shutdown is True


def test_controller_exact_occurrence_selection_and_primary_fallback(qapp):
    """
    R2.3 Controller Verification: Selecting an exact occurrence scrolls to its specific node,
    while omitting occurrence_id falls back to the region's primary occurrence.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    vref_occ1 = VisualRegionRefDTO(
        occurrence_id="node1_occ_1",
        source="crop.jpg",
        region_id="multi_reg",
        is_associated=True,
    )
    vref_occ2 = VisualRegionRefDTO(
        occurrence_id="node2_occ_2",
        source="crop.jpg",
        region_id="multi_reg",
        is_associated=True,
    )

    node1 = MarkdownNodeDTO(
        node_id="p_1",
        node_type="paragraph",
        regions=(vref_occ1,),
    )
    node2 = MarkdownNodeDTO(
        node_id="p_2",
        node_type="paragraph",
        regions=(vref_occ2,),
    )

    doc_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(node1, node2),
        region_to_occurrences={
            "multi_reg": (
                RegionOccurrenceRef(node_index=0, occurrence_id="node1_occ_1"),
                RegionOccurrenceRef(node_index=1, occurrence_id="node2_occ_2"),
            )
        },
    )
    ctrl._model.set_document(doc_dto)

    # 1. Exact occurrence targeting selects node index 1
    ctrl.selectRegion("multi_reg", "node2_occ_2")
    assert ctrl.highlightedRegionId == "multi_reg"
    assert ctrl.highlightedOccurrenceId == "node2_occ_2"
    assert ctrl.selectedNodeIndex == 1

    # 2. Fallback to primary occurrence when occurrence_id is omitted
    ctrl.selectRegion("multi_reg")
    assert ctrl.highlightedRegionId == "multi_reg"
    assert ctrl.highlightedOccurrenceId == "node1_occ_1"
    assert ctrl.selectedNodeIndex == 0


def test_model_indexed_lookups_and_page_number(qapp):
    """
    R2.1 & R2.3 Verification: Model exposes fast O(1) indexed lookups for
    indexOfOccurrence and pageNumberOfRegion.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    model = ctrl.model

    vref = VisualRegionRefDTO(
        occurrence_id="occ_page3",
        source="crop.jpg",
        region_id="reg_p3",
        is_associated=True,
        page_number=3,
    )
    node = MarkdownNodeDTO(
        node_id="p_0",
        node_type="paragraph",
        regions=(vref,),
    )
    doc_dto = MarkdownDocumentDTO(
        job_id=42,
        version=1,
        nodes=(node,),
        region_to_occurrences={"reg_p3": (RegionOccurrenceRef(node_index=0, occurrence_id="occ_page3"),)},
    )
    model.set_document(doc_dto)

    assert model.indexOfOccurrence("occ_page3") == 0
    assert model.indexOfOccurrence("nonexistent") == -1
    assert model.indexOfOccurrence("") == -1

    assert model.pageNumberOfRegion("reg_p3") == 3
    assert model.pageNumberOfRegion("nonexistent") == 0
    assert model.pageNumberOfRegion("") == 0


def test_controller_shutdown_idempotent(qapp):
    """
    R2.2 Verification: MarkdownViewerController.shutdown() is idempotent.
    Calling shutdown once marks controller as shutdown and closes executor.
    Calling shutdown multiple times is harmless and does not repeatedly shut down executor.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    mock_executor = MagicMock()
    ctrl._executor = mock_executor

    assert ctrl.is_shutdown is False

    # First shutdown call
    ctrl.shutdown()
    assert ctrl.is_shutdown is True
    assert mock_executor.shutdown.call_count == 1
    mock_executor.shutdown.assert_called_with(wait=False, cancel_futures=True)

    # Second shutdown call (idempotent no-op)
    ctrl.shutdown()
    assert ctrl.is_shutdown is True
    assert mock_executor.shutdown.call_count == 1


def test_model_exposes_quote_children_role(qapp):
    """
    R1.2 & R3 Verification: MarkdownDocumentModel exposes QuoteChildrenRole
    with structured child blocks and native inline image references.
    """
    from application.dto.markdown_dto import QuoteChildBlockDTO

    model = MarkdownDocumentModel()
    vref = VisualRegionRefDTO(
        occurrence_id="quote_node_p1_img_0",
        source="crop.jpg",
        region_id="reg_quote",
        is_associated=True,
        page_number=1,
    )
    img_seg = InlineSegmentDTO(segment_type="image", image_ref=vref)
    text_seg = InlineSegmentDTO(segment_type="text", text_html="Quote text")

    q_child0 = QuoteChildBlockDTO(child_type="heading", content="Quote Heading", level=2, segments=(text_seg,))
    q_child1 = QuoteChildBlockDTO(child_type="paragraph", content="Quote Paragraph", level=0, segments=(img_seg,))

    node = MarkdownNodeDTO(
        node_id="quote_1",
        node_type="blockquote",
        quote_children=(q_child0, q_child1),
    )
    doc_dto = MarkdownDocumentDTO(
        job_id=42,
        version=1,
        nodes=(node,),
        region_to_occurrences={"reg_quote": (RegionOccurrenceRef(node_index=0, occurrence_id="quote_node_p1_img_0"),)},
    )
    model.set_document(doc_dto)

    assert model.rowCount() == 1
    idx = model.index(0, 0)
    children = model.data(idx, MarkdownDocumentModel.QuoteChildrenRole)
    assert len(children) == 2
    assert children[0]["childType"] == "heading"
    assert children[0]["level"] == 2
    assert children[1]["childType"] == "paragraph"
    assert len(children[1]["segments"]) == 1
    assert children[1]["segments"][0]["segmentType"] == "image"
    assert children[1]["segments"][0]["imageRef"]["regionId"] == "reg_quote"
