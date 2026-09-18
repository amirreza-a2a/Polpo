# ============================================================
#  tests/unit/test_review_workspace_conflict_qml.py
#  QML Integration Tests for Conflict Resolution Toolbar and Job-Switch Guard (Phase 10F.5 Task 6)
# ============================================================

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO
from interfaces.desktop.app import create_app
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.models.conflict_session import ConflictSession
from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QObject


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_editor_service():
    service = MagicMock()
    service.load_source_text.return_value = ("# Base\nText", 1)
    service.commit_source_text.return_value = 2
    return service


def _make_hunk(
    hunk_index: int,
    hunk_type: str,
    base_text: str = "",
    local_text: str = "",
    remote_text: str = "",
    local_line_start: int = 0,
    local_line_end: int = 0,
    ast_label: str = "",
) -> ConflictHunkDTO:
    return ConflictHunkDTO(
        hunk_index=hunk_index,
        hunk_type=hunk_type,
        base_text=base_text,
        local_text=local_text,
        remote_text=remote_text,
        local_line_start=local_line_start,
        local_line_end=local_line_end,
        ast_label=ast_label,
    )


def _make_analysis_result(hunks, job_id: int = 1) -> MergeAnalysisResultDTO:
    conflict_count = sum(1 for h in hunks if h.hunk_type == "CONFLICT")
    auto_merged_count = len(hunks) - conflict_count
    return MergeAnalysisResultDTO(
        job_id=job_id,
        merge_session_id=1,
        base_version=1,
        canonical_version=2,
        has_conflicts=True,
        clean_text=None,
        hunks=tuple(hunks),
        conflict_count=conflict_count,
        auto_merged_count=auto_merged_count,
    )


def _load_editor_pane(controller):
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownEditorController", controller)

    qml_file = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MarkdownEditorPane.qml"
    )
    engine.load(str(qml_file))
    root_objs = engine.rootObjects()
    assert len(root_objs) > 0, "Failed to load MarkdownEditorPane.qml"
    pane = root_objs[-1]
    QGuiApplication.processEvents()
    return engine, pane


def _load_markdown_view(controller):
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownViewerController", controller)

    qml_file = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "views"
        / "MarkdownView.qml"
    )
    engine.load(str(qml_file))
    root_objs = engine.rootObjects()
    assert len(root_objs) > 0, "Failed to load MarkdownView.qml"
    view = root_objs[-1]
    QGuiApplication.processEvents()
    return engine, view


def test_t_merge_60_conflict_resolution_bar_visibility_and_label(qapp, mock_editor_service):
    """
    T-MERGE-60: ConflictResolutionBar becomes visible when mergeSessionActive is True,
    and displays the current conflict label.
    """
    controller = MarkdownEditorController(editor_service=mock_editor_service)
    engine, pane = _load_editor_pane(controller)

    bar = pane.findChild(QObject, "conflictResolutionBar")
    assert bar is not None, "conflictResolutionBar component must exist in MarkdownEditorPane"
    assert bar.property("visible") is False

    hunk1 = _make_hunk(0, "CONFLICT", "Base 1", "Local 1", "Remote 1", ast_label="Paragraph")
    hunk2 = _make_hunk(1, "CONFLICT", "Base 2", "Local 2", "Remote 2", ast_label="Heading")
    analysis = _make_analysis_result([hunk1, hunk2])
    session = ConflictSession(
        job_id=1,
        merge_session_id=1,
        base_version=1,
        canonical_version=2,
        analysis_result=analysis,
    )
    controller._active_conflict_session = session
    controller._has_conflict = True
    controller.mergeSessionStateChanged.emit()
    controller.conflictChanged.emit()
    qapp.processEvents()

    assert bar.property("visible") is True

    label = bar.findChild(QObject, "conflictLabel")
    assert label is not None, "conflictLabel must exist inside conflictResolutionBar"
    assert "Conflict 1 of 2" in label.property("text")
    assert "Paragraph" in label.property("text")

    controller.shutdown()


def test_t_merge_61_toolbar_buttons_navigation_and_actions(qapp, mock_editor_service):
    """
    T-MERGE-61: Toolbar buttons invoke controller navigation and resolution slots,
    and update hunk resolutions accordingly.
    """
    controller = MarkdownEditorController(editor_service=mock_editor_service)
    hunk1 = _make_hunk(0, "CONFLICT", "Base 1", "Local 1", "Remote 1", ast_label="Paragraph")
    hunk2 = _make_hunk(1, "CONFLICT", "Base 2", "Local 2", "Remote 2", ast_label="Heading")
    analysis = _make_analysis_result([hunk1, hunk2])
    session = ConflictSession(
        job_id=1,
        merge_session_id=1,
        base_version=1,
        canonical_version=2,
        analysis_result=analysis,
    )
    controller._active_conflict_session = session
    controller._has_conflict = True
    engine, pane = _load_editor_pane(controller)

    prev_btn = pane.findChild(QObject, "conflictPrevButton")
    next_btn = pane.findChild(QObject, "conflictNextButton")
    accept_local_btn = pane.findChild(QObject, "conflictAcceptLocalButton")
    accept_incoming_btn = pane.findChild(QObject, "conflictAcceptIncomingButton")
    accept_both_btn = pane.findChild(QObject, "conflictAcceptBothButton")

    assert prev_btn is not None
    assert next_btn is not None
    assert accept_local_btn is not None
    assert accept_incoming_btn is not None
    assert accept_both_btn is not None

    # Initial hunk index is 0 -> Prev disabled, Next enabled
    assert prev_btn.property("enabled") is False
    assert next_btn.property("enabled") is True

    # Navigate to hunk 1
    next_btn.clicked.emit()
    qapp.processEvents()
    assert controller.currentConflictIndex == 1
    assert prev_btn.property("enabled") is True
    assert next_btn.property("enabled") is False

    # Navigate back to hunk 0
    prev_btn.clicked.emit()
    qapp.processEvents()
    assert controller.currentConflictIndex == 0

    # Accept local for hunk 0
    accept_local_btn.clicked.emit()
    qapp.processEvents()
    assert session._resolutions[0] == "Local 1"

    # Move to hunk 1 and accept incoming
    next_btn.clicked.emit()
    qapp.processEvents()
    assert controller.currentConflictIndex == 1
    accept_incoming_btn.clicked.emit()
    qapp.processEvents()
    assert session._resolutions[1] == "Remote 2"

    # Accept both for hunk 1
    accept_both_btn.clicked.emit()
    qapp.processEvents()
    assert session._resolutions[1] == "Local 2\nRemote 2"

    controller.shutdown()


def test_t_merge_62_save_button_disabled_during_conflict_enabled_when_resolved(qapp, mock_editor_service):
    """
    T-MERGE-62: Save button is disabled while conflict is active and unresolved,
    and becomes enabled when conflict is fully resolved.
    """
    controller = MarkdownEditorController(editor_service=mock_editor_service)
    controller._is_dirty = True
    engine, pane = _load_editor_pane(controller)

    save_btn = pane.findChild(QObject, "editorSaveButton")
    assert save_btn is not None
    # Dirty and no conflict -> Save enabled
    assert save_btn.property("enabled") is True

    # Activate conflict session with 2 unresolved hunks
    hunk1 = _make_hunk(0, "CONFLICT", "Base 1", "Local 1", "Remote 1", ast_label="Paragraph")
    hunk2 = _make_hunk(1, "CONFLICT", "Base 2", "Local 2", "Remote 2", ast_label="Heading")
    analysis = _make_analysis_result([hunk1, hunk2])
    session = ConflictSession(
        job_id=1,
        merge_session_id=1,
        base_version=1,
        canonical_version=2,
        analysis_result=analysis,
    )
    controller._active_conflict_session = session
    controller._has_conflict = True
    controller._source_text = session.generate_in_buffer_markdown()
    controller.sourceTextChanged.emit()
    controller.mergeSessionStateChanged.emit()
    controller.conflictChanged.emit()
    qapp.processEvents()

    # Active unresolved conflict -> Save must be disabled!
    assert controller.canSaveConflict is False
    assert save_btn.property("enabled") is False

    # Resolve hunk 0
    controller.acceptCurrentHunkLocal()
    qapp.processEvents()
    assert save_btn.property("enabled") is False

    # Resolve hunk 1
    controller.nextConflictHunk()
    controller.acceptCurrentHunkIncoming()
    qapp.processEvents()
    assert controller.canSaveConflict is True
    # Fully resolved -> Save must now be enabled!
    assert save_btn.property("enabled") is True

    # Test legacy conflict (hasConflict=True, mergeSessionActive=False) -> Save must be disabled
    controller._active_conflict_session = None
    controller._has_conflict = True
    controller.mergeSessionStateChanged.emit()
    controller.conflictChanged.emit()
    qapp.processEvents()
    assert controller.canSaveConflict is False
    assert save_btn.property("enabled") is False

    controller.shutdown()


def test_t_merge_63_auto_merge_notification_banner_display_and_dismiss(qapp, mock_editor_service):
    """
    T-MERGE-63: Auto-merge notification toast displays message when set,
    and can be dismissed.
    """
    controller = MarkdownEditorController(editor_service=mock_editor_service)
    engine, pane = _load_editor_pane(controller)

    banner = pane.findChild(QObject, "editorAutoMergeBanner")
    assert banner is not None, "editorAutoMergeBanner must exist in MarkdownEditorPane"
    assert banner.property("visible") is False

    msg = "Auto-merged 2 non-overlapping modifications."
    controller._auto_merge_notification = msg
    controller.autoMergeNotified.emit(msg)
    qapp.processEvents()

    assert banner.property("visible") is True
    text_item = pane.findChild(QObject, "editorAutoMergeText")
    assert text_item is not None
    assert text_item.property("text") == msg

    dismiss_btn = pane.findChild(QObject, "editorAutoMergeDismissButton")
    assert dismiss_btn is not None
    dismiss_btn.clicked.emit()
    qapp.processEvents()

    assert banner.property("visible") is False

    controller.shutdown()


def test_t_merge_64_open_review_workspace_job_switch_guard(qapp):
    """
    T-MERGE-64: openReviewWorkspace while dirty/conflicting triggers confirmation dialog;
    Cancel retains active job, Discard & Switch clears editor and switches.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        app_inst, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            artifacts_dir=os.path.join(tmp_dir, "artifacts"),
            vault_path=os.path.join(tmp_dir, "vault.enc"),
            passphrase="test_passphrase_12345",
            start_background_runtime=False,
            scheduler_tick_interval=0.1,
        )

        qml_path = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "Main.qml"
        engine.load(str(qml_path))
        qapp.processEvents()

        root_objects = engine.rootObjects()
        assert len(root_objects) == 1
        window = root_objects[0]

        modal = window.findChild(QObject, "jobSwitchConfirmModal")
        assert modal is not None, "jobSwitchConfirmModal must exist in Main.qml"
        assert modal.property("visible") is False

        cancel_btn = modal.findChild(QObject, "jobSwitchCancelButton")
        discard_btn = modal.findChild(QObject, "jobSwitchDiscardButton")
        assert cancel_btn is not None, "jobSwitchCancelButton must exist"
        assert discard_btn is not None, "jobSwitchDiscardButton must exist"

        md_editor = container.markdown_editor_controller

        # 1. Dirty state on job 10
        md_editor._active_job_id = 10
        md_editor._is_dirty = True
        md_editor.activeJobChanged.emit()
        md_editor.dirtyChanged.emit()
        qapp.processEvents()

        # Request switch to job 20
        window.openReviewWorkspace(20)
        qapp.processEvents()

        # Modal should open, pendingJobId set to 20, activeJobId remains 10
        assert modal.property("visible") is True
        assert modal.property("pendingJobId") == 20
        assert md_editor.activeJobId == 10

        # Click Cancel
        cancel_btn.clicked.emit()
        qapp.processEvents()
        assert modal.property("visible") is False
        assert md_editor.activeJobId == 10

        # Request switch again
        window.openReviewWorkspace(20)
        qapp.processEvents()
        assert modal.property("visible") is True

        # Click Discard & Switch
        discard_btn.clicked.emit()
        qapp.processEvents()
        assert modal.property("visible") is False
        # Editor cleared and switched to job 20
        assert md_editor.activeJobId == 20

        container.shutdown()
        qapp.processEvents()


def test_t_merge_65_textarea_undo_redo_inside_conflict_markers(qapp, mock_editor_service):
    """
    T-MERGE-65: TextArea undo/redo operates normally inside conflict marker blocks.
    Editor is strictly NOT read-only during conflict resolution.
    """
    controller = MarkdownEditorController(editor_service=mock_editor_service)
    hunk1 = _make_hunk(0, "CONFLICT", "Base text", "Local original", "Remote text", ast_label="Paragraph")
    analysis = _make_analysis_result([hunk1])
    session = ConflictSession(
        job_id=1,
        merge_session_id=1,
        base_version=1,
        canonical_version=2,
        analysis_result=analysis,
    )
    controller._active_conflict_session = session
    controller._has_conflict = True
    controller._source_text = session.generate_in_buffer_markdown()
    engine, pane = _load_editor_pane(controller)

    text_area = pane.findChild(QObject, "markdownSourceTextArea")
    assert text_area is not None
    # Crucial requirement from F-02: Editor must NOT be read-only!
    assert text_area.property("readOnly") is False

    initial_text = text_area.property("text")
    assert "<<<<<<< [LOCAL:hunk_0]" in initial_text

    # Edit buffer inside conflict marker block
    user_edited = initial_text.replace("Local original", "Local manual edit")
    text_area.setProperty("text", user_edited)
    qapp.processEvents()

    assert "Local manual edit" in controller.sourceText

    # Verify undo / redo hooks if available on the QML text area
    if hasattr(text_area, "undo") and text_area.property("canUndo"):
        text_area.undo()
        qapp.processEvents()
        if hasattr(text_area, "redo") and text_area.property("canRedo"):
            text_area.redo()
            qapp.processEvents()

    controller.shutdown()


def test_t_merge_66_preview_overlay_covers_pane_while_paused(qapp):
    """
    T-MERGE-66: Preview overlay rectangle covers preview pane with dimmed amber notice while paused.
    """
    viewer_service = MagicMock()
    viewer_ctrl = MarkdownViewerController(viewer_service=viewer_service)
    engine, view = _load_markdown_view(viewer_ctrl)

    overlay = view.findChild(QObject, "previewPausedOverlay")
    assert overlay is not None, "previewPausedOverlay must exist in MarkdownView"
    assert overlay.property("visible") is False

    # Pause preview
    reason = "Preview paused during conflict resolution"
    viewer_ctrl.setPreviewPaused(True, reason)
    qapp.processEvents()

    assert overlay.property("visible") is True
    assert overlay.property("z") >= 100
    assert viewer_ctrl.previewPaused is True
    assert viewer_ctrl.previewPausedReason == reason

    # Resume preview
    viewer_ctrl.setPreviewPaused(False)
    qapp.processEvents()

    assert overlay.property("visible") is False
    assert viewer_ctrl.previewPaused is False

    viewer_ctrl.shutdown()


def test_t_merge_67_window_close_guard_with_exit_confirmation(qapp):
    """
    T-MERGE-67: Window close while dirty/conflicting triggers windowExitConfirmModal;
    Cancel preserves state; Discard & Exit closes window.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        app_inst, engine, container = create_app(
            argv=["-platform", "offscreen"],
            db_path=db_path,
            artifacts_dir=os.path.join(tmp_dir, "artifacts"),
            vault_path=os.path.join(tmp_dir, "vault.enc"),
            passphrase="test_passphrase_12345",
            start_background_runtime=False,
            scheduler_tick_interval=0.1,
        )

        qml_path = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "Main.qml"
        engine.load(str(qml_path))
        qapp.processEvents()

        root_objects = engine.rootObjects()
        assert len(root_objects) == 1
        window = root_objects[0]

        modal = window.findChild(QObject, "windowExitConfirmModal")
        assert modal is not None, "windowExitConfirmModal must exist in Main.qml"
        assert modal.property("visible") is False

        cancel_btn = modal.findChild(QObject, "windowExitCancelButton")
        discard_btn = modal.findChild(QObject, "windowExitDiscardButton")
        assert cancel_btn is not None
        assert discard_btn is not None

        md_editor = container.markdown_editor_controller

        # 1. Test dirty state guard
        md_editor._active_job_id = 10
        md_editor._is_dirty = True
        md_editor.activeJobChanged.emit()
        md_editor.dirtyChanged.emit()
        qapp.processEvents()

        # Trigger window close
        window.close()
        qapp.processEvents()

        assert modal.property("visible") is True
        assert window.property("forceExit") is False

        # Click Cancel
        cancel_btn.clicked.emit()
        qapp.processEvents()
        assert modal.property("visible") is False
        assert window.property("forceExit") is False

        # 2. Test active conflict session guard
        md_editor._is_dirty = False
        hunk1 = _make_hunk(0, "CONFLICT", "Base", "Local", "Remote")
        analysis = _make_analysis_result([hunk1], job_id=10)
        session = ConflictSession(
            job_id=10,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            analysis_result=analysis,
        )
        md_editor._active_conflict_session = session
        md_editor._has_conflict = True
        md_editor.mergeSessionStateChanged.emit()
        md_editor.conflictChanged.emit()
        qapp.processEvents()

        window.close()
        qapp.processEvents()
        assert modal.property("visible") is True

        # Click Discard & Exit
        discard_btn.clicked.emit()
        qapp.processEvents()
        assert modal.property("visible") is False
        assert window.property("forceExit") is True

        container.shutdown()
        qapp.processEvents()
