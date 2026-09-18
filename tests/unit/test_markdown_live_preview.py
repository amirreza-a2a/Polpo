# ============================================================
#  tests/unit/test_markdown_live_preview.py
#  Unit Tests for Phase 10F.3 Live Dual-Pane Synchronized Preview
# ============================================================

import os
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.apply_review_service import ApplyReviewService
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_editor_service import MarkdownEditorService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.artifact import ArtifactType
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job

from core.entities.visual_region import (
    RegionOrigin,
    ReviewStatus,
    SyncStatus,
    VisualRegion,
)
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.app import wire_review_workspace_sync
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
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


def _wait_for_condition(predicate, timeout=3.0, interval=0.02):
    start = time.time()
    while time.time() - start < timeout:
        QGuiApplication.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    QGuiApplication.processEvents()
    return predicate()


def _setup_wired_workspace(tmp_path):
    db_path = tmp_path / "workspace_test.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorageAdapter(base_dir=str(artifacts_dir))
    doc_proc = PyMuPDFDocumentProcessor()
    md_parser = MarkdownItParser()

    apply_service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    doc_viewer_service = DocumentViewerService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    md_viewer_service = MarkdownViewerService(parser=md_parser, uow_factory=uow_factory, storage=storage)
    md_editor_service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    doc_ctrl = DocumentViewerController(viewer_service=doc_viewer_service, apply_review_service=apply_service)
    md_viewer_ctrl = MarkdownViewerController(viewer_service=md_viewer_service)
    md_editor_ctrl = MarkdownEditorController(editor_service=md_editor_service)

    wire_review_workspace_sync(doc_ctrl, md_viewer_ctrl, md_editor_ctrl)

    return {
        "db_path": db_path,
        "uow_factory": uow_factory,
        "artifacts_dir": artifacts_dir,
        "storage": storage,
        "doc_ctrl": doc_ctrl,
        "md_viewer_ctrl": md_viewer_ctrl,
        "md_editor_ctrl": md_editor_ctrl,
        "md_viewer_service": md_viewer_service,
        "md_editor_service": md_editor_service,
    }


def test_canonical_02_typing_in_editor_triggers_preview_without_sqlite_mutation(qapp, tmp_path):
    """
    T-F3-CANONICAL-02: Typing in editor triggers debounced live preview without
    modifying SQLite jobs.output_path or jobs.active_markdown_version.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nInitial content.")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    assert viewer_ctrl.activeVersion == 1
    assert editor_ctrl.activeVersion == 1
    assert viewer_ctrl.has_active_draft is False

    # Simulate user typing in editor
    editor_ctrl.set_source_text("# Canonical V1\nInitial content.\nUncommitted user keystrokes.")
    assert editor_ctrl.isDirty is True

    # Live preview scheduled on viewer via wire_review_workspace_sync
    assert viewer_ctrl.has_active_draft is True
    assert viewer_ctrl.draft_revision >= 1

    viewer_ctrl.flushLivePreview()
    assert _wait_for_condition(lambda: viewer_ctrl.last_applied_draft_revision == viewer_ctrl.draft_revision)

    # Invariant check: SQLite records must be completely unmutated
    with uow_factory.create() as uow:
        persisted_job = uow.jobs.get_by_id(job_id)
        assert persisted_job.output_path == out_h.uri
        assert persisted_job.active_markdown_version == 1

    assert viewer_ctrl.activeVersion == 1
    assert editor_ctrl.activeVersion == 1

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_save_01_save_advances_canonical_version_and_updates_model(qapp, tmp_path):
    """
    T-F3-SAVE-01: Save click invokes commit_source_text(), writes canonical artifact N+1,
    advances canonical version, and updates viewer model without flicker.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]
    artifacts_dir = env["artifacts_dir"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nInitial text")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User edits in editor
    editor_ctrl.set_source_text("# Canonical V2\nNewly committed markdown.")
    assert editor_ctrl.isDirty is True
    assert viewer_ctrl.has_active_draft is True

    # User clicks save
    saved_versions = []
    editor_ctrl.saved.connect(lambda v: saved_versions.append(v))

    success = editor_ctrl.save_sync()
    assert success is True
    assert len(saved_versions) == 1 and saved_versions[0] == 2

    assert editor_ctrl.activeVersion == 2
    assert editor_ctrl.isDirty is False

    # Check disk artifact
    v2_file = artifacts_dir / f"job_{job_id}" / f"output_{job_id}_v2.md"
    assert v2_file.exists()
    assert v2_file.read_text(encoding="utf-8") == "# Canonical V2\nNewly committed markdown."

    # Check SQLite version
    with uow_factory.create() as uow:
        db_job = uow.jobs.get_by_id(job_id)
        assert db_job.active_markdown_version == 2
        assert db_job.output_path == f"file://{v2_file}"

    # Wait for viewer load triggered by _on_editor_saved to complete
    assert _wait_for_condition(lambda: viewer_ctrl.activeVersion == 2 and not viewer_ctrl.has_active_draft and not viewer_ctrl.isLoading)
    assert viewer_ctrl.activeVersion == 2
    assert viewer_ctrl.has_active_draft is False
    assert viewer_ctrl.model.rowCount() > 0
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Canonical V2"

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_save_02_save_failure_stale_occ_leaves_editor_in_conflict(qapp, tmp_path):
    """
    T-F3-SAVE-02: Save failure (stale OCC version) leaves editor in conflict,
    leaves viewer model unchanged, and does not emit saved.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nInitial text")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User types uncommitted draft
    editor_ctrl.set_source_text("# Draft To Preserve\nUnsaved lines.")
    viewer_ctrl.flushLivePreview()
    assert _wait_for_condition(lambda: viewer_ctrl.last_applied_draft_revision == viewer_ctrl.draft_revision)
    row_count_before = viewer_ctrl.model.rowCount()


    # External advance in storage & database: output_1_v2.md stored, job output_path updated to v2
    out_v2 = storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", b"# V2 External")
    with uow_factory.create() as uow:
        j = uow.jobs.get_by_id(job_id)
        j.output_path = out_v2.uri
        j.output_artifact_version_watermark = 2
        uow.jobs.save(j)
        uow.commit()

    saved_emissions = []
    editor_ctrl.saved.connect(lambda v: saved_emissions.append(v))

    # Save fails due to OCC conflict (editor base_version 1 != current 2)
    success = editor_ctrl.save_sync()
    assert success is False
    assert len(saved_emissions) == 0

    assert editor_ctrl.hasConflict is True
    assert editor_ctrl.isDirty is True
    assert editor_ctrl.sourceText == "# Draft To Preserve\nUnsaved lines."

    # Viewer model is not blanked, still has draft preview
    assert viewer_ctrl.model.rowCount() == row_count_before
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft To Preserve"

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_discard_01_discard_reverts_editor_and_preview(qapp, tmp_path):
    """
    T-F3-DISCARD-01: Discard click restores saved text in editor and immediately
    reverts preview model to saved canonical state.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nOriginal text.")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Canonical V1"

    # User types dirty draft
    editor_ctrl.set_source_text("# Dirty Header\nDraft discard me.")
    assert editor_ctrl.isDirty is True
    assert viewer_ctrl.has_active_draft is True
    assert viewer_ctrl._live_preview_timer.isActive() is True

    # User clicks discard
    editor_ctrl.discard()

    # Editor is restored
    assert editor_ctrl.isDirty is False
    assert editor_ctrl.sourceText == "# Canonical V1\nOriginal text."

    # Debounce timer stopped, active draft reset
    assert viewer_ctrl._live_preview_timer.isActive() is False
    assert viewer_ctrl.has_active_draft is False

    # Wait for cancelPendingLivePreviewAndReconcile to apply saved text
    assert _wait_for_condition(lambda: viewer_ctrl.model.rowCount() > 0 and viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Canonical V1")

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_ext_01_dirty_editor_external_advance_preserves_draft_preview(qapp, tmp_path):
    """
    T-F3-EXT-01: Dirty editor + external canonical advance (N -> N+1) preserves
    visible draft preview in MarkdownDocumentModel.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nOriginal text.")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User types dirty draft
    editor_ctrl.set_source_text("# Draft To Preserve\nDo not clobber this draft.")
    viewer_ctrl.flushLivePreview()
    assert _wait_for_condition(lambda: viewer_ctrl.last_applied_draft_revision == viewer_ctrl.draft_revision)
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft To Preserve"
    assert viewer_ctrl.has_active_draft is True

    # External advance occurs: canonical document N+1 arrives
    node_ext = _make_node("ext_1", "heading", "EXTERNAL CANONICAL V2")
    dto_v2 = MarkdownDocumentDTO(job_id=job_id, version=2, nodes=(node_ext,), region_to_occurrences={})

    # Canonical document loaded signal arrives
    viewer_ctrl._on_internal_doc_loaded(viewer_ctrl._request_id, dto_v2)

    # Canonical activeVersion must advance to 2
    assert viewer_ctrl.activeVersion == 2

    # CRITICAL INVARIANT: The draft preview MUST be preserved and NOT overwritten!
    assert viewer_ctrl.model.rowCount() > 0
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft To Preserve"
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) != "EXTERNAL CANONICAL V2"

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_ext_02_dirty_editor_external_advance_updates_version_and_triggers_conflict(qapp, tmp_path):
    """
    T-F3-EXT-02: Dirty editor + external canonical advance updates canonical
    activeVersion to N+1 and triggers editor conflict without replacing draft preview.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nOriginal text.")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User types uncommitted text
    editor_ctrl.set_source_text("# Dirty Draft\nWork in progress.")
    viewer_ctrl.flushLivePreview()
    assert _wait_for_condition(lambda: viewer_ctrl.last_applied_draft_revision == viewer_ctrl.draft_revision)
    assert editor_ctrl.isDirty is True
    assert viewer_ctrl.has_active_draft is True


    # External canonical advance arrives
    node_ext = _make_node("ext_1", "heading", "EXTERNAL CANONICAL V2")
    dto_v2 = MarkdownDocumentDTO(job_id=job_id, version=2, nodes=(node_ext,), region_to_occurrences={})
    viewer_ctrl._on_internal_doc_loaded(viewer_ctrl._request_id, dto_v2)

    # Viewer activeVersion updated to 2
    assert viewer_ctrl.activeVersion == 2

    # Editor should now have conflict via wire_review_workspace_sync
    assert editor_ctrl.hasConflict is True
    assert "version 2" in editor_ctrl.conflictMessage

    # Editor text and viewer preview remain intact
    assert editor_ctrl.sourceText == "# Dirty Draft\nWork in progress."
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Dirty Draft"

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_ext_03_inflight_draft_preview_task_completes_after_external_advance(qapp, tmp_path):
    """
    T-F3-EXT-03: In-flight pre-advance draft preview task completing after external
    canonical advance applies cleanly without regressing activeVersion.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User types draft
    viewer_ctrl.scheduleLivePreview(job_id, "# Draft In Flight", base_version=1)
    draft_rev = viewer_ctrl.draft_revision
    assert draft_rev >= 1

    # Before draft worker completes, canonical document advances externally to v2
    node_ext = _make_node("ext_1", "heading", "CANONICAL V2")
    dto_v2 = MarkdownDocumentDTO(job_id=job_id, version=2, nodes=(node_ext,), region_to_occurrences={})
    viewer_ctrl._on_internal_doc_loaded(viewer_ctrl._request_id, dto_v2)
    assert viewer_ctrl.activeVersion == 2

    # Now in-flight draft preview completes
    node_draft = _make_node("draft_1", "paragraph", "Draft In Flight Node")
    dto_draft = MarkdownDocumentDTO(job_id=job_id, version=1, nodes=(node_draft,), region_to_occurrences={})
    viewer_ctrl._on_internal_preview_loaded(job_id, draft_rev, dto_draft)

    # Preview applied to model
    assert viewer_ctrl.model.rowCount() == 1
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Draft In Flight Node"

    # CRITICAL INVARIANT: activeVersion MUST REMAIN 2 (not regress to 1)
    assert viewer_ctrl.activeVersion == 2

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()


def test_ext_04_discard_after_external_advance_reloads_canonical_v2(qapp, tmp_path):
    """
    T-F3-EXT-04: After external advance + conflict, clicking Discard reloads
    canonical source N+1, reconciles preview model to N+1, and clears conflict state.
    """
    env = _setup_wired_workspace(tmp_path)
    viewer_ctrl = env["md_viewer_ctrl"]
    editor_ctrl = env["md_editor_ctrl"]
    uow_factory = env["uow_factory"]
    storage = env["storage"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Canonical V1\nInitial content.")
    job = Job(
        id=None,
        file_name="test.pdf",
        file_path="file:///data/test.pdf",
        output_path=out_h.uri,
        output_artifact_version_watermark=1,
    )
    with uow_factory.create() as uow:
        job = uow.jobs.save(job)
        uow.commit()

    job_id = job.id
    viewer_ctrl.load_document_sync(job_id)
    editor_ctrl.load_source_sync(job_id)

    # User types dirty draft
    editor_ctrl.set_source_text("# Dirty Draft\nConflict pending.")
    viewer_ctrl.flushLivePreview()
    assert _wait_for_condition(lambda: viewer_ctrl.last_applied_draft_revision == viewer_ctrl.draft_revision)
    assert editor_ctrl.isDirty is True


    # External advance occurs: disk artifact v2 written, db updated to v2
    out_v2 = storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", b"# Canonical V2\nExternal writer content.")
    with uow_factory.create() as uow:
        j = uow.jobs.get_by_id(job_id)
        j.output_path = out_v2.uri
        j.output_artifact_version_watermark = 2
        uow.jobs.save(j)
        uow.commit()

    # Viewer receives external advance
    node_v2 = _make_node("v2_node", "heading", "Canonical V2")
    dto_v2 = MarkdownDocumentDTO(job_id=job_id, version=2, nodes=(node_v2,), region_to_occurrences={})
    viewer_ctrl._on_internal_doc_loaded(viewer_ctrl._request_id, dto_v2)

    # Verify conflict state
    assert viewer_ctrl.activeVersion == 2
    assert editor_ctrl.activeVersion == 1
    assert editor_ctrl.hasConflict is True

    # User clicks Discard
    editor_ctrl.discard()

    # In wire_review_workspace_sync, _on_editor_discarded sees
    # viewer_ctrl.activeVersion (2) > editor_ctrl.activeVersion (1),
    # so it reloads canonical source for editor, resets viewer draft, and reloads viewer canonical doc
    assert _wait_for_condition(
        lambda: not editor_ctrl.isLoading
        and not viewer_ctrl.isLoading
        and editor_ctrl.activeVersion == 2
        and not editor_ctrl.hasConflict
        and not viewer_ctrl.has_active_draft
    )

    assert editor_ctrl.hasConflict is False
    assert editor_ctrl.isDirty is False
    assert editor_ctrl.activeVersion == 2
    assert "Canonical V2" in editor_ctrl.sourceText

    assert viewer_ctrl.activeVersion == 2
    assert viewer_ctrl.has_active_draft is False
    assert viewer_ctrl.model.rowCount() > 0
    assert viewer_ctrl.model.data(viewer_ctrl.model.index(0, 0), MarkdownDocumentModel.ContentRole) == "Canonical V2"

    viewer_ctrl.shutdown()
    editor_ctrl.shutdown()
