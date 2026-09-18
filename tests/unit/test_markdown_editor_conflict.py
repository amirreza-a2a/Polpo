# ============================================================
#  tests/unit/test_markdown_editor_conflict.py
#  Unit tests for MarkdownEditorController Three-Way Merge Integration (Phase 10F.5 Task 4)
# ============================================================

import threading
from typing import Optional, Sequence
from unittest.mock import MagicMock
import pytest

from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_editor_service():
    service = MagicMock()
    service.load_source_text.return_value = ("# Base Document\nLine 1\nLine 2", 1)
    service.commit_source_text.return_value = 2
    return service


@pytest.fixture
def mock_merge_service():
    service = MagicMock()
    service.storage = MagicMock()
    service.storage.exists.return_value = True
    service.storage.retrieve.return_value = b"# Remote Document\nLine 1\nLine 2 remote"
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


def _make_analysis_result(
    hunks: Sequence[ConflictHunkDTO],
    job_id: int = 1,
    merge_session_id: int = 1,
    base_version: int = 1,
    canonical_version: int = 2,
    has_conflicts: bool = True,
    clean_text: Optional[str] = None,
    canonical_text: Optional[str] = None,
) -> MergeAnalysisResultDTO:
    conflict_count = sum(1 for h in hunks if (h.hunk_type or "").upper() == "CONFLICT")
    auto_merged_count = len(hunks) - conflict_count
    return MergeAnalysisResultDTO(
        job_id=job_id,
        merge_session_id=merge_session_id,
        base_version=base_version,
        canonical_version=canonical_version,
        has_conflicts=has_conflicts,
        clean_text=clean_text,
        hunks=tuple(hunks),
        conflict_count=conflict_count,
        auto_merged_count=auto_merged_count,
        canonical_text=canonical_text,
    )


def test_t_merge_40_advance_when_dirty_submits_analysis_to_executor(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-40: When dirty and external advance arrives, increments _merge_session_id
    and submits background merge analysis task to executor.
    """
    called_event = threading.Event()

    def _fake_analyze(job_id, base_version, local_text, canonical_version, merge_session_id):
        called_event.set()
        return _make_analysis_result(
            [], job_id=job_id, merge_session_id=merge_session_id,
            base_version=base_version, canonical_version=canonical_version,
            has_conflicts=False, clean_text="Clean Merged",
        )

    mock_merge_service.analyze_three_way_merge.side_effect = _fake_analyze

    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Base Document\nLine 1 local edit\nLine 2")
        assert ctrl.isDirty is True
        assert ctrl._merge_session_id == 0

        ctrl.notifyCanonicalDocumentAdvance(2)

        assert ctrl._merge_session_id == 1
        assert called_event.wait(timeout=2.0) is True

        mock_merge_service.analyze_three_way_merge.assert_called_once_with(
            job_id=1,
            base_version=1,
            local_text="# Base Document\nLine 1 local edit\nLine 2",
            canonical_version=2,
            merge_session_id=1,
        )
    finally:
        ctrl.shutdown()


def test_t_merge_41_clean_auto_merge(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-41: Clean auto-merge sets _source_text = MERGED, _saved_source_text = canonical_text,
    _active_version = N+1, _is_dirty = True, _has_conflict = False, and emits autoMergeNotified.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Base Document\nLine 1 local edit")
        assert ctrl.isDirty is True

        ctrl.notifyCanonicalDocumentAdvance(2)
        session_id = ctrl._merge_session_id
        assert session_id == 1

        notifications = []
        ctrl.autoMergeNotified.connect(lambda msg: notifications.append(msg))

        clean_result = _make_analysis_result(
            hunks=[],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=False,
            clean_text="# Merged Document\nLine 1 local edit\nLine 2 remote",
            canonical_text="# Canonical v2 Remote Text",
        )

        ctrl._on_internal_merge_analyzed(1, clean_result)

        assert ctrl.sourceText == "# Merged Document\nLine 1 local edit\nLine 2 remote"
        assert ctrl._saved_source_text == "# Canonical v2 Remote Text"
        assert ctrl.activeVersion == 2
        assert ctrl.isDirty is True
        assert ctrl.hasConflict is False
        assert ctrl.mergeSessionActive is False
        assert ctrl.autoMergeNotification == "External updates merged seamlessly."
        assert notifications == ["External updates merged seamlessly."]
    finally:
        ctrl.shutdown()


def test_t_merge_42_overlapping_conflict(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-42: Overlapping conflict instantiates ConflictSession, sets _has_conflict = True,
    and sets _source_text to in-buffer tagged conflict markers.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Title\nLocal body edit")

        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Original body",
            local_text="Local body edit",
            remote_text="Remote body edit",
            ast_label="Paragraph",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )

        ctrl._on_internal_merge_analyzed(1, conflict_result)

        assert ctrl.hasConflict is True
        assert ctrl.mergeSessionActive is True
        assert ctrl.currentConflictIndex == 0
        assert ctrl.totalConflicts == 1
        assert ctrl.currentConflictLabel == "Conflict 1 of 1: Paragraph"
        assert ctrl.canSaveConflict is False
        assert "<<<<<<< [LOCAL:hunk_0]" in ctrl.sourceText
        assert "Local body edit" in ctrl.sourceText
        assert "=======" in ctrl.sourceText
        assert "Remote body edit" in ctrl.sourceText
        assert ">>>>>>> [CANONICAL:hunk_0]" in ctrl.sourceText
        assert "Conflict detected with canonical version 2." in ctrl.conflictMessage
    finally:
        ctrl.shutdown()


def test_t_merge_43_save_strictly_blocked_while_unresolved(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-43: Save is strictly blocked while _active_conflict_session has unresolved hunks.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Title\nLocal edit")
        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base",
            local_text="Local edit",
            remote_text="Remote edit",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )
        ctrl._on_internal_merge_analyzed(1, conflict_result)

        assert ctrl.canSaveConflict is False

        # Attempt synchronous save
        save_success = ctrl.save_sync()
        assert save_success is False
        assert ctrl.errorMessage == "Cannot save: unresolved conflicts exist."
        mock_editor_service.commit_source_text.assert_not_called()

        # Attempt asynchronous save
        ctrl.save()
        assert ctrl.errorMessage == "Cannot save: unresolved conflicts exist."
        mock_editor_service.commit_source_text.assert_not_called()
    finally:
        ctrl.shutdown()


def test_t_merge_44_all_hunks_resolved_allows_save_with_canonical_base(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-44: When all hunks resolved, canSave is True; save() passes base_version = canonical_version
    to commit_source_text().
    """
    mock_editor_service.commit_source_text.return_value = 3

    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Title\nLocal edit")
        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base text",
            local_text="Local resolved text",
            remote_text="Remote incoming text",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )
        ctrl._on_internal_merge_analyzed(1, conflict_result)

        assert ctrl.canSaveConflict is False

        # Resolve the hunk using acceptCurrentHunkLocal
        ctrl.acceptCurrentHunkLocal()

        assert ctrl.canSaveConflict is True
        assert "<<<<<<<" not in ctrl.sourceText
        assert "Local resolved text" in ctrl.sourceText

        # Save should succeed and pass canonical version (2) as base_version
        saved = ctrl.save_sync()
        assert saved is True

        mock_editor_service.commit_source_text.assert_called_once_with(
            job_id=1,
            raw_text="Local resolved text",
            base_version=2,
        )
        assert ctrl.hasConflict is False
        assert ctrl.mergeSessionActive is False
    finally:
        ctrl.shutdown()


def test_t_merge_45_second_canonical_advance_invalidates_session_and_extracts_clean_candidate(
    qapp, mock_editor_service, mock_merge_service
):
    """
    T-MERGE-45: Second canonical advance (N+1 -> N+2) invalidates active session,
    extracts clean candidate from S1, and triggers S2 against N+2 without passing markers.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("User local edit")
        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base v1",
            local_text="User local edit",
            remote_text="Remote v2 edit",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )
        ctrl._on_internal_merge_analyzed(1, conflict_result)

        active_s1 = ctrl._active_conflict_session
        assert active_s1 is not None
        assert "<<<<<<< [LOCAL:hunk_0]" in ctrl.sourceText

        # Now canonical advances again to version 3
        mock_merge_service.analyze_three_way_merge.reset_mock()
        second_advance_event = threading.Event()

        def _capture_s2(job_id, base_version, local_text, canonical_version, merge_session_id):
            second_advance_event.set()
            return _make_analysis_result(
                [], job_id=job_id, merge_session_id=merge_session_id,
                base_version=base_version, canonical_version=canonical_version,
                has_conflicts=False, clean_text="Merged v3",
            )

        mock_merge_service.analyze_three_way_merge.side_effect = _capture_s2

        ctrl.notifyCanonicalDocumentAdvance(3)

        assert active_s1.isInvalidated is True
        assert ctrl._merge_session_id == 2
        assert second_advance_event.wait(timeout=2.0) is True

        mock_merge_service.analyze_three_way_merge.assert_called_once()
        _, kwargs = mock_merge_service.analyze_three_way_merge.call_args

        assert kwargs["job_id"] == 1
        assert kwargs["base_version"] == 2
        assert kwargs["canonical_version"] == 3
        assert kwargs["merge_session_id"] == 2
        # Clean candidate extraction: local_text must contain user edits, NOT diff3 markers
        assert "<<<<<<<" not in kwargs["local_text"]
        assert ">>>>>>>" not in kwargs["local_text"]
        assert "User local edit" in kwargs["local_text"]
    finally:
        ctrl.shutdown()


def test_t_merge_46_discard_clears_conflict_session_and_reloads_canonical(
    qapp, mock_editor_service, mock_merge_service
):
    """
    T-MERGE-46: Discard clears active conflict session, resets _has_conflict, and reloads canonical.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Local draft")
        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base v1",
            local_text="Local draft",
            remote_text="Remote v2",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )
        ctrl._on_internal_merge_analyzed(1, conflict_result)

        assert ctrl.mergeSessionActive is True
        assert ctrl.hasConflict is True

        load_called = threading.Event()
        mock_editor_service.load_source_text.side_effect = lambda job_id: (load_called.set(), ("# Canonical v2", 2))[1]
        mock_editor_service.load_source_text.reset_mock()

        ctrl.discard()

        assert ctrl.mergeSessionActive is False
        assert ctrl._active_conflict_session is None
        assert ctrl.hasConflict is False
        assert ctrl.autoMergeNotification == ""
        assert load_called.wait(timeout=2.0) is True
        mock_editor_service.load_source_text.assert_called_once_with(1)
    finally:
        ctrl.shutdown()


def test_t_merge_47_stale_merge_result_dropped_cleanly(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-47: Stale merge analysis result (matching old session_id) is dropped cleanly.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Local text")
        ctrl.notifyCanonicalDocumentAdvance(2)  # _merge_session_id = 1
        ctrl.notifyCanonicalDocumentAdvance(3)  # _merge_session_id = 2

        assert ctrl._merge_session_id == 2

        stale_result = _make_analysis_result(
            hunks=[],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=False,
            clean_text="Stale Merged Text From Session 1",
        )

        # Deliver stale result from session 1
        ctrl._on_internal_merge_analyzed(1, stale_result)

        # Verify stale text was not applied
        assert ctrl.sourceText != "Stale Merged Text From Session 1"
        assert ctrl.activeVersion != 2
        assert ctrl.mergeSessionActive is False
    finally:
        ctrl.shutdown()


def test_resolution_slots_incoming_and_both(qapp, mock_editor_service, mock_merge_service):
    """
    Verifies acceptCurrentHunkIncoming and acceptCurrentHunkBoth slots update buffer text.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Local text")
        ctrl.notifyCanonicalDocumentAdvance(2)

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base text",
            local_text="Local text",
            remote_text="Remote text",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )
        ctrl._on_internal_merge_analyzed(1, conflict_result)

        # 1. Accept incoming
        ctrl.acceptCurrentHunkIncoming()
        assert "Remote text" in ctrl.sourceText
        assert "<<<<<<<" not in ctrl.sourceText
        assert ctrl.canSaveConflict is True

        # 2. Re-resolve as both
        ctrl.acceptCurrentHunkBoth()
        assert "Local text" in ctrl.sourceText
        assert "Remote text" in ctrl.sourceText
        assert "<<<<<<<" not in ctrl.sourceText
        assert ctrl.canSaveConflict is True
    finally:
        ctrl.shutdown()


def test_t_merge_72_preview_unpauses_when_all_conflicts_resolved_before_save(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-72 (F-01): Preview unpauses and hasUnresolvedConflict becomes False immediately
    when all conflicts are resolved, before save is invoked.
    """
    from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
    from interfaces.desktop.app import wire_review_workspace_sync

    viewer_service = MagicMock()
    viewer_service.render_document.return_value = MagicMock(nodes=[], raw_markdown="")
    viewer_service.render_text.return_value = MagicMock(nodes=[], raw_markdown="")

    doc_viewer_ctrl = MagicMock()
    viewer_ctrl = MarkdownViewerController(viewer_service=viewer_service)
    editor_ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)

    try:
        wire_review_workspace_sync(
            markdown_viewer_controller=viewer_ctrl,
            document_viewer_controller=doc_viewer_ctrl,
            markdown_editor_controller=editor_ctrl,
        )

        editor_ctrl.load_source_sync(job_id=1)
        editor_ctrl.setSourceText("# Base\nUser edit")

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base text\n",
            local_text="Local text\n",
            remote_text="Remote text\n",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )

        import time
        advance_done = threading.Event()
        mock_merge_service.analyze_three_way_merge.side_effect = lambda *a, **kw: (advance_done.set(), conflict_result)[1]

        editor_ctrl.notifyCanonicalDocumentAdvance(2)
        assert advance_done.wait(timeout=2.0) is True
        for _ in range(50):
            qapp.processEvents()
            if editor_ctrl.hasConflict:
                break
            time.sleep(0.01)

        # Conflict is active and unresolved -> preview must be paused
        assert editor_ctrl.hasConflict is True
        assert editor_ctrl.hasUnresolvedConflict is True
        assert viewer_ctrl.previewPaused is True

        # Resolve the hunk
        editor_ctrl.acceptCurrentHunkLocal()
        qapp.processEvents()

        # Before calling save():
        # hasUnresolvedConflict must be False, preview must be unpaused immediately!
        assert editor_ctrl.hasUnresolvedConflict is False
        assert viewer_ctrl.previewPaused is False
        assert editor_ctrl.canSaveConflict is True
    finally:
        editor_ctrl.shutdown()
        viewer_ctrl.shutdown()


def test_t_merge_73_in_buffer_manual_edits_preserved_on_toolbar_resolution(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-73 (F-02): User manual edits outside and inside markers are preserved
    when clicking toolbar resolution actions.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Base content")

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base text\n",
            local_text="Original local\n",
            remote_text="Original remote\n",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )

        advance_done = threading.Event()
        mock_merge_service.analyze_three_way_merge.side_effect = lambda *a, **kw: (advance_done.set(), conflict_result)[1]

        ctrl.notifyCanonicalDocumentAdvance(2)
        assert advance_done.wait(timeout=2.0) is True
        qapp.processEvents()

        # In-buffer text now has markers
        current_buf = ctrl.sourceText
        assert "Original local" in current_buf

        # User edits outside markers AND inside local text
        user_edited = (
            "# My Custom Header\n"
            + current_buf.replace("Original local", "Edited local content")
            + "\n# Footer Notes\n"
        )
        ctrl.setSourceText(user_edited)

        # User clicks Accept Local
        ctrl.acceptCurrentHunkLocal()

        # All manual edits outside and inside hunk must be preserved
        assert "# My Custom Header" in ctrl.sourceText
        assert "# Footer Notes" in ctrl.sourceText
        assert "Edited local content" in ctrl.sourceText
        assert "<<<<<<<" not in ctrl.sourceText
    finally:
        ctrl.shutdown()


def test_t_merge_74_d06_preserves_manual_edits_on_second_advance(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-74 (D06 / F-02): Second external advance preserves manual edits when extracting candidate for S2.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Base content")

        conflict_hunk = _make_hunk(
            hunk_index=0,
            hunk_type="CONFLICT",
            base_text="Base text\n",
            local_text="Local section\n",
            remote_text="Remote section\n",
        )
        conflict_result = _make_analysis_result(
            hunks=[conflict_hunk],
            job_id=1,
            merge_session_id=1,
            base_version=1,
            canonical_version=2,
            has_conflicts=True,
        )

        advance_1_done = threading.Event()
        mock_merge_service.analyze_three_way_merge.side_effect = lambda *a, **kw: (advance_1_done.set(), conflict_result)[1]

        ctrl.notifyCanonicalDocumentAdvance(2)
        assert advance_1_done.wait(timeout=2.0) is True
        qapp.processEvents()

        # User adds text outside markers
        current_buf = ctrl.sourceText
        user_edited = "# Custom Intro\n" + current_buf
        ctrl.setSourceText(user_edited)

        # Second external canonical advance arrives (version 3)
        mock_merge_service.analyze_three_way_merge.reset_mock()
        advance_2_done = threading.Event()

        def _capture_s2(job_id, base_version, local_text, canonical_version, merge_session_id):
            advance_2_done.set()
            return _make_analysis_result(
                [], job_id=job_id, merge_session_id=merge_session_id,
                base_version=base_version, canonical_version=canonical_version,
                has_conflicts=False, clean_text="Merged v3",
            )

        mock_merge_service.analyze_three_way_merge.side_effect = _capture_s2

        ctrl.notifyCanonicalDocumentAdvance(3)
        assert advance_2_done.wait(timeout=2.0) is True
        qapp.processEvents()

        # Merge service must have been called for S2 with candidate text containing # Custom Intro and ZERO markers
        mock_merge_service.analyze_three_way_merge.assert_called_once()
        _, kwargs = mock_merge_service.analyze_three_way_merge.call_args
        assert kwargs["canonical_version"] == 3
        candidate_passed = kwargs["local_text"]
        assert "# Custom Intro" in candidate_passed
        assert "<<<<<<<" not in candidate_passed
        assert ">>>>>>>" not in candidate_passed
        assert "=======" not in candidate_passed
    finally:
        ctrl.shutdown()


def test_t_merge_75_navigation_emits_request_navigate_to_position(qapp, mock_editor_service, mock_merge_service):
    """
    T-MERGE-75 (F-04): nextConflictHunk and prevConflictHunk emit requestNavigateToPosition
    pointing to the hunk's character offset in sourceText.
    """
    ctrl = MarkdownEditorController(editor_service=mock_editor_service, merge_service=mock_merge_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("Prefix text\n")
        ctrl.notifyCanonicalDocumentAdvance(2)

        hunk0 = _make_hunk(0, "CONFLICT", "b0\n", "l0\n", "r0\n", 0, 1)
        hunk1 = _make_hunk(1, "CONFLICT", "b1\n", "l1\n", "r1\n", 2, 3)
        analysis = _make_analysis_result([hunk0, hunk1])

        nav_positions = []
        ctrl.requestNavigateToPosition.connect(lambda pos: nav_positions.append(pos))

        ctrl._on_internal_merge_analyzed(1, analysis)

        # Initial conflict detection navigates to hunk 0
        expected_pos_0 = ctrl.sourceText.find("<<<<<<< [LOCAL:hunk_0]")
        assert expected_pos_0 >= 0
        assert nav_positions[-1] == expected_pos_0

        # Navigate to next hunk (hunk 1)
        ctrl.nextConflictHunk()
        expected_pos_1 = ctrl.sourceText.find("<<<<<<< [LOCAL:hunk_1]")
        assert expected_pos_1 > expected_pos_0
        assert nav_positions[-1] == expected_pos_1

        # Navigate back to previous hunk (hunk 0)
        ctrl.prevConflictHunk()
        assert nav_positions[-1] == expected_pos_0
    finally:
        ctrl.shutdown()
