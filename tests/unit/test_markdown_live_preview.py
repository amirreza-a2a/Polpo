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
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
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


def test_canonical_01_typing_does_not_advance_active_version(qapp):
    """
    T-F3-CANONICAL-01: Typing keystrokes schedules live preview and advances
    _draft_revision but leaves activeVersion strictly unchanged and never emits
    activeVersionChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._active_version = 1

    version_changed_emissions = []
    ctrl.activeVersionChanged.connect(lambda: version_changed_emissions.append(ctrl.activeVersion))

    assert ctrl.activeVersion == 1
    assert ctrl.draft_revision == 0
    assert ctrl.has_active_draft is False

    # Simulate typing multiple keystrokes in the editor
    ctrl.scheduleLivePreview(job_id=42, raw_text="# Live Heading\nTyped line 1", base_version=1)
    assert ctrl.draft_revision == 1
    assert ctrl.has_active_draft is True
    assert ctrl.activeVersion == 1

    ctrl.scheduleLivePreview(job_id=42, raw_text="# Live Heading\nTyped line 1 and more", base_version=1)
    assert ctrl.draft_revision == 2
    assert ctrl.has_active_draft is True
    assert ctrl.activeVersion == 1

    # Verify zero activeVersionChanged emissions
    assert len(version_changed_emissions) == 0

    # Simulate render completion for draft revision 2
    node = _make_node("n1", "paragraph", "Typed line 1 and more")
    draft_dto = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node,), region_to_occurrences={})
    ctrl._on_internal_preview_loaded(42, 2, draft_dto)

    # Preview model is updated
    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Typed line 1 and more"

    # Invariant: activeVersion is STILL 1 and activeVersionChanged was NEVER emitted
    assert ctrl.activeVersion == 1
    assert len(version_changed_emissions) == 0


def test_race_01_option_b_strict_latest_revision_matching(qapp):
    """
    T-F3-RACE-01: Option B strict latest revision matching:
    Simulate Revision 1 completing after Revision 2 under Option B;
    assert Revision 1 is dropped and Revision 2 is retained.
    Also assert an in-flight obsolete revision arriving while a newer revision is pending
    is dropped immediately without updating the model.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._active_version = 1

    # User types Revision 1
    ctrl.scheduleLivePreview(job_id=42, raw_text="Draft Revision 1", base_version=1)
    assert ctrl.draft_revision == 1

    # User immediately types Revision 2 before worker 1 finishes
    ctrl.scheduleLivePreview(job_id=42, raw_text="Draft Revision 2", base_version=1)
    assert ctrl.draft_revision == 2

    node1 = _make_node("n1", "paragraph", "Draft Revision 1 Content")
    dto_rev1 = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node1,), region_to_occurrences={})

    node2 = _make_node("n2", "paragraph", "Draft Revision 2 Content")
    dto_rev2 = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node2,), region_to_occurrences={})

    # Case A: Worker 1 completes while draft_revision is 2 (newer revision pending)
    # Under Option B: draft_revision (1) != self._draft_revision (2) -> DROPPED!
    ctrl._on_internal_preview_loaded(42, 1, dto_rev1)
    assert ctrl.last_applied_draft_revision == 0
    assert ctrl.model.rowCount() == 0

    # Worker 2 completes for draft_revision 2 -> ACCEPTED!
    ctrl._on_internal_preview_loaded(42, 2, dto_rev2)
    assert ctrl.last_applied_draft_revision == 2
    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft Revision 2 Content"

    # Case B: Stale arrival of Revision 1 after Revision 2 has already applied
    ctrl._on_internal_preview_loaded(42, 1, dto_rev1)
    # Model remains Revision 2
    assert ctrl.last_applied_draft_revision == 2
    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft Revision 2 Content"


def test_race_02_preview_render_from_old_job_is_dropped(qapp):
    """
    T-F3-RACE-02: Preview render from Job A completing after switch to Job B is dropped.
    Model for Job B is untouched, and no errors from Job A leak into Job B.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._active_version = 1

    ctrl.scheduleLivePreview(job_id=42, raw_text="Job 42 draft", base_version=1)
    assert ctrl.draft_revision == 1

    # User switches to Job 99
    node_b = _make_node("nb", "paragraph", "Job 99 Canonical Content")
    dto_job99 = MarkdownDocumentDTO(job_id=99, version=1, nodes=(node_b,), region_to_occurrences={})
    ctrl._on_internal_doc_loaded(ctrl._request_id, dto_job99)
    assert ctrl.activeJobId == 99
    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Job 99 Canonical Content"

    # Delayed worker for Job 42 returns loaded preview
    node_a = _make_node("na", "paragraph", "Job 42 Draft Leak")
    dto_job42 = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node_a,), region_to_occurrences={})
    ctrl._on_internal_preview_loaded(42, 1, dto_job42)

    # Must be dropped! Job 99 content preserved
    assert ctrl.activeJobId == 99
    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Job 99 Canonical Content"

    # Delayed worker for Job 42 returns error
    ctrl._on_internal_preview_error(42, 1, "Job 42 render failed")
    # Must be dropped! Job 99 hasPreviewError remains False
    assert ctrl.hasPreviewError is False
    assert ctrl.previewErrorMessage == ""


def test_error_01_preview_render_exception_preserves_model_and_exposes_error(qapp):
    """
    T-F3-ERROR-01: Parsing/rendering exception preserves previous valid preview model
    (never blanked) and exposes error properties.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._active_version = 1

    # Setup an initial valid preview model with 2 nodes
    node1 = _make_node("n1", "paragraph", "Initial Valid Paragraph 1")
    node2 = _make_node("n2", "paragraph", "Initial Valid Paragraph 2")
    initial_dto = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node1, node2), region_to_occurrences={})
    ctrl.scheduleLivePreview(job_id=42, raw_text="Valid Initial", base_version=1)
    ctrl._on_internal_preview_loaded(42, 1, initial_dto)

    assert ctrl.model.rowCount() == 2
    assert ctrl.hasPreviewError is False
    assert ctrl.previewErrorMessage == ""

    # Monitor error signals
    error_signals = []
    has_error_signals = []
    ctrl.previewErrorChanged.connect(lambda: error_signals.append(ctrl.previewErrorMessage))
    ctrl.hasPreviewErrorChanged.connect(lambda: has_error_signals.append(ctrl.hasPreviewError))

    # User types broken syntax; render worker emits error for revision 2
    ctrl.scheduleLivePreview(job_id=42, raw_text="Broken syntax...", base_version=1)
    assert ctrl.draft_revision == 2

    ctrl._on_internal_preview_error(42, 2, "Markdown render failure: syntax error")

    # Invariants:
    # 1. Previous valid model is preserved (NOT blanked!)
    assert ctrl.model.rowCount() == 2
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Initial Valid Paragraph 1"
    # 2. Error properties are populated
    assert ctrl.hasPreviewError is True
    assert ctrl.previewErrorMessage == "Markdown render failure: syntax error"
    # 3. Error signals emitted
    assert len(has_error_signals) >= 1 and has_error_signals[-1] is True
    assert len(error_signals) >= 1 and error_signals[-1] == "Markdown render failure: syntax error"

    # User corrects syntax; render worker emits loaded for revision 3
    ctrl.scheduleLivePreview(job_id=42, raw_text="Corrected syntax", base_version=1)
    assert ctrl.draft_revision == 3

    node3 = _make_node("n3", "paragraph", "Corrected Paragraph")
    fixed_dto = MarkdownDocumentDTO(job_id=42, version=1, nodes=(node3,), region_to_occurrences={})
    ctrl._on_internal_preview_loaded(42, 3, fixed_dto)

    assert ctrl.model.rowCount() == 1
    assert ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Corrected Paragraph"
    assert ctrl.hasPreviewError is False
    assert ctrl.previewErrorMessage == ""


def test_preview_debounce_coalescing_and_flush(qapp):
    """
    Verifies that rapid typing restarts the 250ms debounce timer,
    and flushLivePreview immediately dispatches the latest draft revision.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Rapid typing
    ctrl.scheduleLivePreview(42, "draft 1", 1)
    ctrl.scheduleLivePreview(42, "draft 2", 1)
    ctrl.scheduleLivePreview(42, "draft 3", 1)
    assert ctrl.draft_revision == 3
    assert ctrl._live_preview_timer.isActive() is True

    # Flush live preview immediately cancels timer and dispatches worker
    ctrl.flushLivePreview()
    assert ctrl._live_preview_timer.isActive() is False


def test_cancel_pending_live_preview_and_reconcile(qapp):
    """
    Verifies cancelPendingLivePreviewAndReconcile stops debounce timer,
    increments draft revision, and clears active draft flag.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    ctrl.scheduleLivePreview(42, "uncommitted draft", 1)
    assert ctrl.has_active_draft is True
    assert ctrl._live_preview_timer.isActive() is True
    rev_before = ctrl.draft_revision

    ctrl.cancelPendingLivePreviewAndReconcile(42, "saved canonical text", 1)
    assert ctrl._live_preview_timer.isActive() is False
    assert ctrl.has_active_draft is False
    assert ctrl.draft_revision == rev_before + 1


def test_preview_lifecycle_clear_and_shutdown(qapp):
    """
    Verifies clear() and shutdown() stop the debounce timer and reset preview state.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    ctrl.scheduleLivePreview(42, "draft", 1)
    ctrl._has_preview_error = True
    ctrl._preview_error_message = "Some error"

    ctrl.clear()
    assert ctrl._live_preview_timer.isActive() is False
    assert ctrl.has_active_draft is False
    assert ctrl.hasPreviewError is False
    assert ctrl.previewErrorMessage == ""

    # Shutdown
    ctrl.scheduleLivePreview(42, "draft after clear", 1)
    ctrl.shutdown()
    assert ctrl.is_shutdown is True
    assert ctrl._live_preview_timer.isActive() is False
    assert ctrl.has_active_draft is False

    # Calling scheduleLivePreview after shutdown is a no-op
    rev_after_shutdown = ctrl.draft_revision
    ctrl.scheduleLivePreview(42, "another draft", 1)
    assert ctrl.draft_revision == rev_after_shutdown


def test_schedule_live_preview_invalid_job_id(qapp):
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl.scheduleLivePreview(0, "text", 1)
    assert ctrl.draft_revision == 0
    assert ctrl.has_active_draft is False
