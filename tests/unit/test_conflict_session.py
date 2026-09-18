# ============================================================
#  tests/unit/test_conflict_session.py
#  Unit tests for ConflictSession presentation model (Phase 10F.5 Task 3)
# ============================================================

from typing import Optional, Sequence
import pytest

from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO
from interfaces.desktop.models.conflict_session import ConflictSession


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
) -> MergeAnalysisResultDTO:
    conflict_count = sum(1 for h in hunks if h.hunk_type == "CONFLICT")
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
    )


def test_t_merge_30_initializes_with_hunks_and_index_zero():
    """
    T-MERGE-30: ConflictSession initializes with hunks, current hunk index set to 0.
    """
    hunks = [
        _make_hunk(0, "CLEAN_UNCHANGED", "clean 0", "clean 0", "clean 0", 0, 1),
        _make_hunk(1, "CONFLICT", "base 1", "local 1", "remote 1", 1, 2, "Heading 1: Intro"),
        _make_hunk(2, "CONFLICT", "base 2", "local 2", "remote 2", 3, 4, "Paragraph"),
    ]
    analysis = _make_analysis_result(hunks, job_id=10, merge_session_id=42, base_version=3, canonical_version=4)
    session = ConflictSession(
        job_id=10,
        merge_session_id=42,
        base_version=3,
        canonical_version=4,
        analysis_result=analysis,
    )

    assert session.currentHunkIndex == 0
    assert session.totalConflicts == 2
    assert session.canSave is False
    assert session.isInvalidated is False
    assert session.canonicalVersion == 4
    assert session.baseVersion == 3
    assert session.mergeSessionId == 42
    assert session.currentConflictLabel == "Conflict 1 of 2: Heading 1: Intro"
    current_hunk = session.get_current_hunk()
    assert current_hunk is not None
    assert current_hunk.hunk_index == 1

    # Empty conflict list edge-case
    empty_analysis = _make_analysis_result([_make_hunk(0, "CLEAN_UNCHANGED", "all clean", "all clean", "all clean", 0, 1)], has_conflicts=False)
    empty_session = ConflictSession(1, 1, 1, 2, empty_analysis)
    assert empty_session.currentHunkIndex == -1
    assert empty_session.totalConflicts == 0
    assert empty_session.canSave is True
    assert empty_session.currentConflictLabel == ""
    assert empty_session.get_current_hunk() is None


def test_t_merge_31_generate_in_buffer_markdown_outputs_tagged_markers():
    """
    T-MERGE-31: generate_in_buffer_markdown() outputs tagged conflict markers.
    """
    hunks = [
        _make_hunk(0, "CLEAN_UNCHANGED", "Header\n", "Header\n", "Header\n", 0, 1),
        _make_hunk(1, "CONFLICT", "base body", "local body", "remote body", 1, 2),
        _make_hunk(2, "CLEAN_UNCHANGED", "Footer", "Footer", "Footer", 2, 3),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    in_buffer = session.generate_in_buffer_markdown()
    assert "<<<<<<< [LOCAL:hunk_1]" in in_buffer
    assert "local body" in in_buffer
    assert "=======" in in_buffer
    assert "remote body" in in_buffer
    assert ">>>>>>> [CANONICAL:hunk_1]" in in_buffer
    assert "Header" in in_buffer
    assert "Footer" in in_buffer


def test_t_merge_32_resolve_hunk_local_records_choice():
    """
    T-MERGE-32: resolve_hunk(idx, 'local') records choice, marks hunk resolved.
    """
    hunk = _make_hunk(0, "CONFLICT", "base text", "user local text", "canonical incoming", 0, 1)
    analysis = _make_analysis_result([hunk])
    session = ConflictSession(1, 1, 1, 2, analysis)

    signals_emitted = {"session": 0, "can_save": []}
    session.sessionChanged.connect(lambda: signals_emitted.__setitem__("session", signals_emitted["session"] + 1))
    session.canSaveChanged.connect(lambda val: signals_emitted["can_save"].append(val))

    session.resolve_hunk(0, "local")

    assert session._resolutions[0] == "user local text"
    assert session.is_fully_resolved() is True
    assert session.canSave is True
    assert signals_emitted["session"] == 1
    assert signals_emitted["can_save"] == [True]

    in_buffer = session.generate_in_buffer_markdown()
    assert "<<<<<<< [LOCAL:hunk_0]" not in in_buffer
    assert "user local text" in in_buffer


def test_t_merge_33_resolve_hunk_both_concatenates_replace_replace():
    """
    T-MERGE-33: resolve_hunk(idx, 'both') concatenates local and remote for replace/replace.
    """
    hunk = _make_hunk(0, "CONFLICT", "base", "local edit", "remote edit", 0, 1)
    analysis = _make_analysis_result([hunk])
    session = ConflictSession(1, 1, 1, 2, analysis)

    session.resolve_hunk(0, "both")
    assert session._resolutions[0] == "local edit\nremote edit"

    # Multiline or newline-terminated hunks
    hunk_nl = _make_hunk(1, "CONFLICT", "base", "local line\n", "remote line\n", 0, 1)
    analysis_nl = _make_analysis_result([hunk_nl])
    session_nl = ConflictSession(1, 1, 1, 2, analysis_nl)
    session_nl.resolve_hunk(1, "both")
    assert session_nl._resolutions[1] == "local line\nremote line\n"


def test_t_merge_34_resolve_hunk_both_delete_modify_emits_remote():
    """
    T-MERGE-34: resolve_hunk(idx, 'both') on delete/modify emits remote modifications.
    """
    hunk = _make_hunk(0, "CONFLICT", "original section", "", "modified remote section", 0, 1)
    analysis = _make_analysis_result([hunk])
    session = ConflictSession(1, 1, 1, 2, analysis)

    session.resolve_hunk(0, "both")
    assert session._resolutions[0] == "modified remote section"


def test_t_merge_35_is_fully_resolved_lifecycle():
    """
    T-MERGE-35: is_fully_resolved() returns False until all hunks resolved, then True.
    """
    hunks = [
        _make_hunk(0, "CONFLICT", "b0", "l0", "r0", 0, 1),
        _make_hunk(1, "CONFLICT", "b1", "l1", "r1", 1, 2),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    assert session.is_fully_resolved() is False
    assert session.canSave is False

    session.resolve_hunk(0, "local")
    assert session.is_fully_resolved() is False
    assert session.canSave is False

    session.resolve_hunk(1, "remote")
    assert session.is_fully_resolved() is True
    assert session.canSave is True


def test_t_merge_36_generate_candidate_markdown_zero_markers():
    """
    T-MERGE-36: generate_candidate_markdown() produces clean Markdown with zero markers.
    """
    hunks = [
        _make_hunk(0, "CLEAN_UNCHANGED", "Preamble\n", "Preamble\n", "Preamble\n", 0, 1),
        _make_hunk(1, "CONFLICT", "b1", "l1", "r1", 1, 2),
        _make_hunk(2, "CONFLICT", "b2", "l2", "r2", 2, 3),
        _make_hunk(3, "CLEAN_UNCHANGED", "Epilogue", "Epilogue", "Epilogue", 3, 4),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    session.resolve_hunk(1, "local")
    session.resolve_hunk(2, "custom", "custom user content")

    candidate = session.generate_candidate_markdown()
    assert "<<<<<<<" not in candidate
    assert ">>>>>>>" not in candidate
    assert "=======" not in candidate
    assert "Preamble" in candidate
    assert "l1" in candidate
    assert "custom user content" in candidate
    assert "Epilogue" in candidate


def test_t_merge_37_generate_candidate_markdown_partial_resolution_falls_back_to_local():
    """
    T-MERGE-37: generate_candidate_markdown() with partial resolutions falls back to Local for unresolved hunks.
    """
    hunks = [
        _make_hunk(0, "CONFLICT", "b0", "local 0 text", "remote 0 text", 0, 1),
        _make_hunk(1, "CONFLICT", "b1", "local 1 text", "remote 1 text", 1, 2),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    session.resolve_hunk(0, "incoming")
    # Hunk 1 remains unresolved!

    candidate = session.generate_candidate_markdown()
    assert "<<<<<<<" not in candidate
    assert ">>>>>>>" not in candidate
    assert "=======" not in candidate
    assert "remote 0 text" in candidate
    assert "local 1 text" in candidate
    assert "remote 1 text" not in candidate


def test_t_merge_38_tagged_delimiter_prevents_collision():
    """
    T-MERGE-38: Tagged delimiter prevents collision with legitimate markdown '<<<<<<<'.
    """
    legit_line = "<<<<<<< legitimate comparison marker"
    hunks = [
        _make_hunk(0, "CLEAN_UNCHANGED", legit_line, legit_line, legit_line, 0, 1),
        _make_hunk(1, "CONFLICT", "base", "local edit", "remote edit", 1, 2),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    in_buffer = session.generate_in_buffer_markdown()
    # In-buffer has both legitimate marker and system tagged marker
    assert legit_line in in_buffer
    assert "<<<<<<< [LOCAL:hunk_1]" in in_buffer

    # Candidate text preserves the legitimate line without corruption
    session.resolve_hunk(1, "local")
    candidate = session.generate_candidate_markdown()
    assert legit_line in candidate
    assert "[LOCAL:hunk_1]" not in candidate
    assert "[CANONICAL:hunk_1]" not in candidate


def test_t_merge_39_invalidate_flags_session():
    """
    T-MERGE-39: invalidate() flags session as invalidated and emits signals.
    """
    hunk = _make_hunk(0, "CONFLICT", "b", "l", "r", 0, 1)
    analysis = _make_analysis_result([hunk])
    session = ConflictSession(1, 1, 1, 2, analysis)

    assert session.isInvalidated is False

    signals_emitted = {"invalidated": 0, "session": 0}
    session.invalidated.connect(lambda: signals_emitted.__setitem__("invalidated", signals_emitted["invalidated"] + 1))
    session.sessionChanged.connect(lambda: signals_emitted.__setitem__("session", signals_emitted["session"] + 1))

    session.invalidate()

    assert session.isInvalidated is True
    assert signals_emitted["invalidated"] == 1
    assert signals_emitted["session"] == 1


def test_navigation_next_prev_and_labels():
    """
    Tests next_hunk(), prev_hunk(), and currentConflictLabel across boundaries.
    """
    hunks = [
        _make_hunk(0, "CONFLICT", "b0", "l0", "r0", 0, 1, "Heading 1: Intro"),
        _make_hunk(1, "CONFLICT", "b1", "l1", "r1", 2, 3, "Visual Region: crop_1"),
        _make_hunk(2, "CONFLICT", "b2", "l2", "r2", 4, 5),
    ]
    analysis = _make_analysis_result(hunks)
    session = ConflictSession(1, 1, 1, 2, analysis)

    nav_emissions = []
    session.currentHunkIndexChanged.connect(lambda idx: nav_emissions.append(idx))

    assert session.currentHunkIndex == 0
    assert session.currentConflictLabel == "Conflict 1 of 3: Heading 1: Intro"

    # Forward navigation
    idx = session.next_hunk()
    assert idx == 1
    assert session.currentHunkIndex == 1
    assert session.currentConflictLabel == "Conflict 2 of 3: Visual Region: crop_1"

    idx = session.next_hunk()
    assert idx == 2
    assert session.currentHunkIndex == 2
    assert session.currentConflictLabel == "Conflict 3 of 3"

    # Boundary at end
    idx = session.next_hunk()
    assert idx == 2
    assert session.currentHunkIndex == 2

    # Backward navigation
    idx = session.prev_hunk()
    assert idx == 1
    assert session.currentHunkIndex == 1

    idx = session.prev_hunk()
    assert idx == 0
    assert session.currentHunkIndex == 0

    # Boundary at start
    idx = session.prev_hunk()
    assert idx == 0
    assert session.currentHunkIndex == 0

    assert nav_emissions == [1, 2, 1, 0]
