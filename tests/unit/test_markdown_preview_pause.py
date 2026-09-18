# ============================================================
#  tests/unit/test_markdown_preview_pause.py
#  Unit tests for Preview Pause / Resume & Overlay Coordination (Phase 10F.5 Task 5)
# ============================================================

from unittest.mock import MagicMock
import pytest

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO
from interfaces.desktop.app import wire_review_workspace_sync
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.coordinators.review_workspace_sync_coordinator import ReviewWorkspaceSyncCoordinator
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_viewer_service():
    service = MagicMock()
    dummy_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(
            MarkdownNodeDTO(
                node_id="n1",
                node_type="heading",
                content="Title",
                raw_markdown="# Title",
                source_start_line=1,
                source_end_line=1,
            ),
            MarkdownNodeDTO(
                node_id="n2",
                node_type="paragraph",
                content="Body line",
                raw_markdown="Body line",
                source_start_line=3,
                source_end_line=3,
            ),
        ),
        region_to_occurrences={},
    )
    service.load_document.return_value = dummy_dto
    service.render_preview.return_value = dummy_dto
    service.render_text.return_value = dummy_dto
    return service


@pytest.fixture
def mock_editor_service():
    service = MagicMock()
    service.load_source_text.return_value = ("# Base Document\nLine 1\nLine 2", 1)
    service.commit_source_text.return_value = 2
    return service


@pytest.fixture
def mock_doc_viewer():
    viewer = MagicMock()
    viewer.currentPage = 1
    viewer.currentJobId = 1
    viewer.selectedRegionId = ""
    return viewer


def test_t_merge_50_set_preview_paused_halts_scheduling_and_sets_properties(qapp, mock_viewer_service):
    """
    T-MERGE-50: setPreviewPaused(True, reason) halts live preview scheduling,
    stops active live preview timer, and updates previewPaused and previewPausedReason properties.
    """
    viewer = MarkdownViewerController(viewer_service=mock_viewer_service)

    assert viewer.previewPaused is False
    assert viewer.previewPausedReason == ""

    signal_emissions = []
    viewer.previewPausedChanged.connect(lambda: signal_emissions.append((viewer.previewPaused, viewer.previewPausedReason)))

    reason = "Preview paused during conflict resolution"
    viewer.setPreviewPaused(True, reason)

    assert viewer.previewPaused is True
    assert viewer.previewPausedReason == reason
    assert len(signal_emissions) == 1
    assert signal_emissions[-1] == (True, reason)

    # Idempotent call should not re-emit
    viewer.setPreviewPaused(True, reason)
    assert len(signal_emissions) == 1

    # While paused, scheduleLivePreview must return immediately without scheduling
    viewer.scheduleLivePreview(job_id=1, raw_text="# Unrendered edit", base_version=1)
    assert viewer._live_preview_timer.isActive() is False
    assert viewer.has_active_draft is False

    # Resuming preview resets properties and emits signal
    viewer.setPreviewPaused(False)
    assert viewer.previewPaused is False
    assert viewer.previewPausedReason == ""
    assert len(signal_emissions) == 2
    assert signal_emissions[-1] == (False, "")

    viewer.shutdown()


def test_t_merge_51_typing_in_editor_while_paused_does_not_dispatch_renders(
    qapp, mock_viewer_service, mock_editor_service, mock_doc_viewer
):
    """
    T-MERGE-51: While preview is paused during active conflict, typing in the editor
    does not dispatch live preview renders.
    """
    viewer = MarkdownViewerController(viewer_service=mock_viewer_service)
    editor = MarkdownEditorController(editor_service=mock_editor_service)
    editor.loadSource(1)

    wire_review_workspace_sync(
        document_viewer_controller=mock_doc_viewer,
        markdown_viewer_controller=viewer,
        markdown_editor_controller=editor,
    )

    # Simulate conflict state on editor
    editor._has_conflict = True
    editor.conflictChanged.emit()

    assert viewer.previewPaused is True
    assert viewer.previewPausedReason == "Preview paused during conflict resolution"

    # User types in editor while conflict is active
    editor.setSourceText("# Conflicted Text - Editing line 1")

    # Live preview timer should not be started
    assert viewer._live_preview_timer.isActive() is False
    mock_viewer_service.render_preview.assert_not_called()

    viewer.shutdown()
    editor.shutdown()


def test_t_merge_52_coordinator_mutes_preview_scroll_synchronization_while_paused(
    qapp, mock_viewer_service, mock_editor_service
):
    """
    T-MERGE-52: While paused, ReviewWorkspaceSyncCoordinator mutes preview scroll synchronization
    and editor cursor tracking to preview.
    """
    viewer = MarkdownViewerController(viewer_service=mock_viewer_service)
    editor = MarkdownEditorController(editor_service=mock_editor_service)
    coord = ReviewWorkspaceSyncCoordinator(
        editor_controller=editor,
        viewer_controller=viewer,
        throttle_ms=0,
        debounce_source_ms=0,
    )
    coord.setDualPaneActive(True)

    # Pause preview
    viewer.setPreviewPaused(True, "Active conflict session")

    preview_progress_events = []
    coord.requestScrollPreviewToProgress.connect(preview_progress_events.append)

    # Source editor reports scroll progress -> must be muted
    coord.reportSourceScrollProgress(0.65)
    assert len(preview_progress_events) == 0

    # Editor cursor position change -> must be muted
    scroll_node_events = []
    viewer.requestScrollToNode.connect(scroll_node_events.append)

    editor.updateCursorPosition(5)
    assert len(scroll_node_events) == 0

    coord.shutdown()
    viewer.shutdown()
    editor.shutdown()


def test_t_merge_53_set_preview_resumed_allows_live_preview_and_updates_model(
    qapp, mock_viewer_service, mock_editor_service, mock_doc_viewer
):
    """
    T-MERGE-53: When conflict is resolved and setPreviewPaused(False) is called,
    live preview resumes and updates the presentation model.
    """
    viewer = MarkdownViewerController(viewer_service=mock_viewer_service)
    viewer.load_document_sync(1)
    editor = MarkdownEditorController(editor_service=mock_editor_service)
    editor.loadSource(1)

    wire_review_workspace_sync(
        document_viewer_controller=mock_doc_viewer,
        markdown_viewer_controller=viewer,
        markdown_editor_controller=editor,
    )

    # 1. Enter conflict
    editor._has_conflict = True
    editor.conflictChanged.emit()
    assert viewer.previewPaused is True

    # 2. Modify text in editor (dirty)
    editor.setSourceText("# Fully Resolved Text\nLine 1\nLine 2")
    assert viewer._live_preview_timer.isActive() is False

    # 3. Resolve conflict
    editor._has_conflict = False
    editor.conflictChanged.emit()

    assert viewer.previewPaused is False
    assert viewer.previewPausedReason == ""

    # When conflict clears, wire_review_workspace_sync schedules live preview
    assert viewer._live_preview_timer.isActive() is True
    assert viewer.has_active_draft is True

    # Flush preview and assert presentation model update
    resolved_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(
            MarkdownNodeDTO(
                node_id="res1",
                node_type="heading",
                content="Fully Resolved Text",
                raw_markdown="# Fully Resolved Text",
            ),
        ),
        region_to_occurrences={},
    )
    mock_viewer_service.render_preview.return_value = resolved_dto
    viewer.flushLivePreview()

    # The background worker runs via executor, wait for it or invoke handler
    viewer._on_internal_preview_loaded(1, viewer.draft_revision, resolved_dto)
    assert viewer.hasDocument is True
    assert viewer.model.rowCount() == 1
    assert viewer.model.data(viewer.model.index(0, 0), 0x0100 + 1) == "res1"

    viewer.shutdown()
    editor.shutdown()


def test_t_merge_54_out_of_order_preview_callbacks_completing_after_pause_are_dropped(
    qapp, mock_viewer_service
):
    """
    T-MERGE-54: Out-of-order preview callbacks completing after pause are dropped cleanly
    without modifying the presentation model.
    """
    viewer = MarkdownViewerController(viewer_service=mock_viewer_service)
    viewer.load_document_sync(1)
    assert viewer.model.rowCount() == 2

    # A live preview request was scheduled/in flight
    job_id = 1
    rev_id = viewer.draft_revision + 1
    viewer._draft_revision = rev_id

    # Now pause occurs before callback arrives
    viewer.setPreviewPaused(True, "Preview paused during conflict")

    # In-flight preview callback completes while paused
    stale_dto = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(
            MarkdownNodeDTO(
                node_id="stale1",
                node_type="paragraph",
                content="Stale In-Flight Content",
                raw_markdown="Stale In-Flight Content",
            ),
        ),
        region_to_occurrences={},
    )

    # Directly deliver the callback
    viewer._on_internal_preview_loaded(job_id, rev_id, stale_dto)

    # Callback must have been dropped: presentation model remains unchanged
    assert viewer.last_applied_draft_revision != rev_id
    assert viewer.model.rowCount() == 2
    assert viewer.model.data(viewer.model.index(0, 0), 0x0100 + 1) == "n1"

    viewer.shutdown()
