# ============================================================
#  tests/unit/test_markdown_document_model_reconcile.py
#  Unit Tests for MarkdownDocumentModel Structural Reconciliation
# ============================================================

import pytest
from unittest.mock import MagicMock

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import QGuiApplication, QModelIndex


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def _make_node(node_id: str, node_type: str = "paragraph", content: str = "", region_id: str = "") -> MarkdownNodeDTO:
    regions = ()
    segments = ()
    if region_id:
        vref = VisualRegionRefDTO(
            occurrence_id=f"{node_id}_img_0",
            source=f"crop_{region_id}.jpg",
            image_path=f"/path/to/crop_{region_id}.jpg",
            alt_text=f"Image {region_id}",
            region_id=region_id,
            is_associated=True,
            display_order=1,
            page_number=1,
        )
        regions = (vref,)
        segments = (InlineSegmentDTO(segment_type="image", image_ref=vref),)
    return MarkdownNodeDTO(
        node_id=node_id,
        node_type=node_type,
        content=content,
        regions=regions,
        segments=segments,
    )


def test_reconcile_document_inserts_node_without_reset(qapp):
    """
    Test 1: Structural insertion without model reset.
    Initial: [A, B, C]
    Reconciled against: [A, X, B, C] (X inserted at index 1).
    Assert:
    - rowsInserted emitted for (1, 1)
    - modelReset / modelAboutToBeReset NOT emitted
    - rowCount() == 4
    - node at row 1 is X
    - occurrencesOfRegion and lookup dictionaries are updated
    """
    model = MarkdownDocumentModel()

    node_a = _make_node("node_a", "paragraph", "Paragraph A")
    node_b = _make_node("node_b", "paragraph", "Paragraph B", region_id="reg_b")
    node_c = _make_node("node_c", "paragraph", "Paragraph C")

    initial_dto = MarkdownDocumentDTO(
        job_id=10,
        version=1,
        nodes=(node_a, node_b, node_c),
        region_to_occurrences={
            "reg_b": (RegionOccurrenceRef(node_index=1, occurrence_id="node_b_img_0"),)
        },
    )
    model.set_document(initial_dto)
    assert model.rowCount() == 3

    # Track signals
    resets = []
    insertions = []
    data_changes = []

    model.modelAboutToBeReset.connect(lambda: resets.append("reset"))
    model.rowsInserted.connect(lambda parent, first, last: insertions.append((first, last)))
    model.dataChanged.connect(lambda tl, br, roles: data_changes.append((tl.row(), br.row())))

    # New canonical DTO: [A, X, B, C]
    node_x = _make_node("img_reg_x", "image", "New Image", region_id="reg_x")
    new_dto = MarkdownDocumentDTO(
        job_id=10,
        version=2,
        nodes=(node_a, node_x, node_b, node_c),
        region_to_occurrences={
            "reg_x": (RegionOccurrenceRef(node_index=1, occurrence_id="img_reg_x_img_0"),),
            "reg_b": (RegionOccurrenceRef(node_index=2, occurrence_id="node_b_img_0"),),
        },
    )

    model.reconcile_document(new_dto)

    # Invariants
    assert len(resets) == 0, "modelReset must NOT be emitted during structural reconciliation"
    assert len(insertions) == 1
    assert insertions[0] == (1, 1), f"Expected insertion at (1, 1), got {insertions[0]}"
    assert model.rowCount() == 4

    # Verify rows order
    assert model.data(model.index(0, 0), MarkdownDocumentModel.NodeIdRole) == "node_a"
    assert model.data(model.index(1, 0), MarkdownDocumentModel.NodeIdRole) == "img_reg_x"
    assert model.data(model.index(2, 0), MarkdownDocumentModel.NodeIdRole) == "node_b"
    assert model.data(model.index(3, 0), MarkdownDocumentModel.NodeIdRole) == "node_c"

    # Verify occurrence & region mappings
    occs_x = model.occurrencesOfRegion("reg_x")
    assert len(occs_x) == 1
    assert occs_x[0]["nodeIndex"] == 1
    assert occs_x[0]["occurrenceId"] == "img_reg_x_img_0"
    assert model.indexOfRegion("reg_x") == 1
    assert model.indexOfOccurrence("img_reg_x_img_0") == 1

    # Verify previously existing region B mapping shifted from index 1 to 2
    occs_b = model.occurrencesOfRegion("reg_b")
    assert len(occs_b) == 1
    assert occs_b[0]["nodeIndex"] == 2
    assert model.indexOfRegion("reg_b") == 2
    assert model.indexOfOccurrence("node_b_img_0") == 2


def test_reconcile_document_existing_node_updates_targeted(qapp):
    """
    Test 2: Reconciling when an existing region's image URI changes.
    Assert:
    - no model reset
    - no rows inserted
    - targeted dataChanged emitted
    """
    model = MarkdownDocumentModel()
    node_a = _make_node("node_a", "paragraph", "A", region_id="reg_a")
    initial_dto = MarkdownDocumentDTO(
        job_id=10,
        version=1,
        nodes=(node_a,),
        region_to_occurrences={
            "reg_a": (RegionOccurrenceRef(node_index=0, occurrence_id="node_a_img_0"),)
        },
    )
    model.set_document(initial_dto)

    resets = []
    insertions = []
    data_changes = []

    model.modelAboutToBeReset.connect(lambda: resets.append("reset"))
    model.rowsInserted.connect(lambda parent, first, last: insertions.append((first, last)))
    model.dataChanged.connect(lambda tl, br, roles: data_changes.append((tl.row(), br.row())))

    # Updated node_a with new v2 URI
    vref_v2 = VisualRegionRefDTO(
        occurrence_id="node_a_img_0",
        source="crop_reg_a_v2.jpg",
        image_path="/path/to/crop_reg_a_v2.jpg",
        alt_text="Image reg_a v2",
        region_id="reg_a",
        is_associated=True,
        display_order=1,
        page_number=1,
    )
    node_a_v2 = MarkdownNodeDTO(
        node_id="node_a",
        node_type="paragraph",
        content="A",
        regions=(vref_v2,),
        segments=(InlineSegmentDTO(segment_type="image", image_ref=vref_v2),),
    )
    updated_dto = MarkdownDocumentDTO(
        job_id=10,
        version=2,
        nodes=(node_a_v2,),
        region_to_occurrences={
            "reg_a": (RegionOccurrenceRef(node_index=0, occurrence_id="node_a_img_0"),)
        },
    )

    model.reconcile_document(updated_dto)

    assert len(resets) == 0
    assert len(insertions) == 0
    assert len(data_changes) == 1
    assert data_changes[0] == (0, 0)
    assert "crop_reg_a_v2.jpg" in model.data(model.index(0, 0), MarkdownDocumentModel.ImageUriRole)


def test_reconcile_document_multiple_insertions_at_arbitrary_positions(qapp):
    """
    Test multiple insertions:
    Initial: [A, B]
    New: [A, X, B, Y]
    Assert:
    - X inserted at 1
    - Y inserted at 3
    - no reset
    - final order [A, X, B, Y]
    """
    model = MarkdownDocumentModel()
    node_a = _make_node("node_a", "paragraph", "A")
    node_b = _make_node("node_b", "paragraph", "B")

    initial_dto = MarkdownDocumentDTO(job_id=10, version=1, nodes=(node_a, node_b), region_to_occurrences={})
    model.set_document(initial_dto)

    node_x = _make_node("img_x", "image", "X", region_id="rx")
    node_y = _make_node("img_y", "image", "Y", region_id="ry")

    new_dto = MarkdownDocumentDTO(
        job_id=10,
        version=2,
        nodes=(node_a, node_x, node_b, node_y),
        region_to_occurrences={
            "rx": (RegionOccurrenceRef(node_index=1, occurrence_id="img_x_img_0"),),
            "ry": (RegionOccurrenceRef(node_index=3, occurrence_id="img_y_img_0"),),
        },
    )

    insertions = []
    resets = []
    model.rowsInserted.connect(lambda p, f, l: insertions.append((f, l)))
    model.modelAboutToBeReset.connect(lambda: resets.append("reset"))

    model.reconcile_document(new_dto)

    assert len(resets) == 0
    assert model.rowCount() == 4
    assert [model.data(model.index(i, 0), MarkdownDocumentModel.NodeIdRole) for i in range(4)] == [
        "node_a", "img_x", "node_b", "img_y"
    ]
    assert model.indexOfRegion("rx") == 1
    assert model.indexOfRegion("ry") == 3


def test_reconcile_document_unsupported_reordering_falls_back_safely(qapp):
    """
    If canonical document contains deletions or unexpected reordering:
    reconcile_document falls back safely to set_document with modelReset
    rather than corrupting state.
    """
    model = MarkdownDocumentModel()
    node_a = _make_node("node_a", "paragraph", "A")
    node_b = _make_node("node_b", "paragraph", "B")
    initial_dto = MarkdownDocumentDTO(job_id=10, version=1, nodes=(node_a, node_b), region_to_occurrences={})
    model.set_document(initial_dto)

    # Node A was deleted! Only Node B remains.
    resets = []
    model.modelAboutToBeReset.connect(lambda: resets.append("reset"))

    node_c = _make_node("node_c", "paragraph", "C")
    new_dto = MarkdownDocumentDTO(job_id=10, version=2, nodes=(node_b, node_c), region_to_occurrences={})

    model.reconcile_document(new_dto)

    # Safe fallback performed modelReset
    assert len(resets) == 1
    assert model.rowCount() == 2
    assert model.data(model.index(0, 0), MarkdownDocumentModel.NodeIdRole) == "node_b"
    assert model.data(model.index(1, 0), MarkdownDocumentModel.NodeIdRole) == "node_c"
