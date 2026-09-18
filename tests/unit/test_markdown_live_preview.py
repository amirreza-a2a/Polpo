# ============================================================
#  tests/unit/test_markdown_live_preview.py
#  Unit Tests for Phase 10F.3 Live Dual-Pane Synchronized Preview
# ============================================================

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
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job
from core.entities.visual_region import (
    RegionOrigin,
    ReviewStatus,
    SyncStatus,
    VisualRegion,
)
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def _make_node(
    node_id: str,
    node_type: str = "paragraph",
    content: str = "",
    region_id: str = "",
) -> MarkdownNodeDTO:
    regions = ()
    segments = ()
    if region_id:
        vref = VisualRegionRefDTO(
            occurrence_id=f"{node_id}_img_0",
            source=f"crop_{region_id}.jpg",
            image_path=f"/data/crop_{region_id}.jpg",
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


def test_canonical_03_render_preview_executes_zero_writes():
    """
    T-F3-CANONICAL-03: render_preview() executes zero SQLite write transactions.
    uow.commit() is never called, and job canonical version/path are not mutated.
    """
    parser = MarkdownItParser()
    job = Job(
        id=42,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path="file:///data/output_42_v1.md",
    )
    mock_uow = MagicMock()
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.visual_regions.get_by_job_id.return_value = []
    mock_uow.commit = MagicMock()

    mock_uow_factory = MagicMock()
    mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

    mock_storage = MagicMock()
    service = MarkdownViewerService(
        parser=parser,
        uow_factory=mock_uow_factory,
        storage=mock_storage,
    )

    dto = service.render_preview(
        job_id=42,
        raw_text="# Live Preview Heading\nSome live preview content.",
        base_version=1,
    )

    assert dto is not None
    assert len(dto.nodes) == 2
    # Verify zero write transactions
    mock_uow.commit.assert_not_called()
    # Verify read-only access
    mock_uow.jobs.get_by_id.assert_called_once_with(42)
    mock_uow.visual_regions.get_by_job_id.assert_called_once_with(42)
    # Verify job properties are unmutated
    assert job.output_path == "file:///data/output_42_v1.md"
    assert job.active_markdown_version == 1


def test_canonical_04_render_preview_creates_zero_disk_files(tmp_path):
    """
    T-F3-CANONICAL-04: render_preview() creates zero files in artifact storage directory.
    """
    parser = MarkdownItParser()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    canonical_file = artifact_dir / "output_42_v1.md"
    canonical_file.write_text("# Canonical Document V1\nBody", encoding="utf-8")

    files_before = sorted(os.listdir(str(artifact_dir)))

    job = Job(
        id=42,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=f"file://{canonical_file}",
    )
    mock_uow = MagicMock()
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.visual_regions.get_by_job_id.return_value = []

    mock_uow_factory = MagicMock()
    mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

    mock_storage = MagicMock()
    service = MarkdownViewerService(
        parser=parser,
        uow_factory=mock_uow_factory,
        storage=mock_storage,
    )

    dto = service.render_preview(
        job_id=42,
        raw_text="# Ephemeral Draft Header\nKeystroke live preview.",
        base_version=1,
    )

    assert dto is not None
    files_after = sorted(os.listdir(str(artifact_dir)))
    # Storage directory file count and listing must be completely identical
    assert files_after == files_before
    # Storage port must not have any store/write calls
    assert not hasattr(mock_storage, "store") or mock_storage.store.call_count == 0


def test_state_01_apply_transient_preview_updates_rows_without_version_mutation(qapp):
    """
    T-F3-STATE-01: apply_transient_preview() updates model.rowCount() and block content
    without altering any canonical version metadata.
    """
    model = MarkdownDocumentModel()

    node_a = _make_node("node_a", "paragraph", "Paragraph A")
    node_b = _make_node("node_b", "paragraph", "Paragraph B")
    initial_dto = MarkdownDocumentDTO(
        job_id=42,
        version=1,
        nodes=(node_a, node_b),
        region_to_occurrences={},
    )
    model.set_document(initial_dto)
    assert model.rowCount() == 2

    node_c = _make_node("node_c", "paragraph", "Draft Paragraph C")
    preview_dto = MarkdownDocumentDTO(
        job_id=42,
        version=1,
        nodes=(node_a, node_b, node_c),
        region_to_occurrences={},
    )

    # Invariant: model must not expose or mutate canonical activeVersion
    assert not hasattr(model, "activeVersion")
    assert not hasattr(model, "activeVersionChanged")

    # Apply transient preview projection
    model.apply_transient_preview(preview_dto)

    assert model.rowCount() == 3
    assert model.data(model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Paragraph A"
    assert model.data(model.index(1, 0), MarkdownDocumentModel.ContentRole) == "Paragraph B"
    assert model.data(model.index(2, 0), MarkdownDocumentModel.ContentRole) == "Draft Paragraph C"

    # Invariant: still no canonical version properties on presentation model
    assert not hasattr(model, "activeVersion")


def test_qt_compat_exposes_qtimer():
    """Verify that qt_compat exports QTimer."""
    from interfaces.desktop.qt_compat import QTimer
    assert QTimer is not None


def test_state_02_render_preview_resolves_visual_region_tokens():
    """
    T-F3-STATE-02: render_preview() resolves visual region tokens against base version regions.
    """
    parser = MarkdownItParser()
    region_uuid = "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d"
    test_region = VisualRegion(
        id=1,
        region_id=region_uuid,
        job_id=42,
        page_number=2,
        display_order=5,
        origin=RegionOrigin.AI_DETECTED,
        detected_bbox=BoundingBox(ymin=100, xmin=100, ymax=500, xmax=500),
        active_artifact_uri="file:///data/artifacts/crop_42_reg1_v1.jpg",
        review_status=ReviewStatus.ACCEPTED,
        sync_status=SyncStatus.SYNCED,
        active_artifact_version=1,
    )

    job = Job(
        id=42,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path="file:///data/artifacts/output_42_v1.md",
    )
    mock_uow = MagicMock()
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.visual_regions.get_by_job_id.return_value = [test_region]

    mock_uow_factory = MagicMock()
    mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

    mock_storage = MagicMock()
    service = MarkdownViewerService(
        parser=parser,
        uow_factory=mock_uow_factory,
        storage=mock_storage,
    )

    raw_text = f"Live draft figure ![[crop.jpg|region_id={region_uuid}]] displayed inline."
    dto = service.render_preview(
        job_id=42,
        raw_text=raw_text,
        base_version=2,
    )

    assert dto.job_id == 42
    assert dto.version == 2
    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert len(node.regions) == 1
    vref = node.regions[0]
    assert vref.region_id == region_uuid
    assert vref.is_associated is True
    assert vref.image_path == "/data/artifacts/crop_42_reg1_v1.jpg"
    assert vref.page_number == 2
    assert vref.display_order == 5
    assert region_uuid in dto.region_to_occurrences
