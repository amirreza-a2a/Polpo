# ============================================================
#  tests/unit/test_markdown_editor_controller.py
#  Unit tests for MarkdownEditorController
# ============================================================

import pytest
from unittest.mock import MagicMock

from core.exceptions.domain_exceptions import StaleDocumentVersionError
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
    service.load_source_text.return_value = ("# Original Title\nInitial body text.", 2)
    service.commit_source_text.return_value = 3
    return service


def test_controller_initial_properties(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        assert ctrl.sourceText == ""
        assert ctrl.isDirty is False
        assert ctrl.isLoading is False
        assert ctrl.isSaving is False
        assert ctrl.errorMessage == ""
        assert ctrl.activeJobId == 0
        assert ctrl.activeVersion == 0
        assert ctrl.hasConflict is False
        assert ctrl.conflictMessage == ""
    finally:
        ctrl.shutdown()


def test_controller_load_source_sync(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)

        mock_editor_service.load_source_text.assert_called_once_with(42)
        assert ctrl.activeJobId == 42
        assert ctrl.sourceText == "# Original Title\nInitial body text."
        assert ctrl.activeVersion == 2
        assert ctrl.isDirty is False
        assert ctrl.hasConflict is False
    finally:
        ctrl.shutdown()


def test_controller_setsourcetext_metaobject_slot(qapp, mock_editor_service):
    """Verifies that setSourceText is registered as a QMetaObject slot and invokable from QML."""
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        meta = ctrl.metaObject()
        methods = [meta.method(i).name().data().decode("utf-8") for i in range(meta.methodCount())]
        assert "setSourceText" in methods
        assert "set_source_text" in methods

        # Verify invokable via slot
        ctrl.setSourceText("# New Meta Text")
        assert ctrl.sourceText == "# New Meta Text"
        assert ctrl.isDirty is True
    finally:
        ctrl.shutdown()


def test_controller_dirty_tracking(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        assert ctrl.isDirty is False

        # Edit text
        ctrl.setSourceText("# Modified Title\nInitial body text.")
        assert ctrl.isDirty is True

        # Revert to original
        ctrl.setSourceText("# Original Title\nInitial body text.")
        assert ctrl.isDirty is False
    finally:
        ctrl.shutdown()


def test_controller_discard(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Changed Something")
        assert ctrl.isDirty is True

        discard_spy = MagicMock()
        ctrl.discarded.connect(discard_spy)

        ctrl.discard()
        assert ctrl.isDirty is False
        assert ctrl.sourceText == "# Original Title\nInitial body text."
        discard_spy.assert_called_once()
    finally:
        ctrl.shutdown()


def test_controller_save_sync_success(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Changed Content")
        assert ctrl.isDirty is True

        saved_spy = MagicMock()
        ctrl.saved.connect(saved_spy)

        success = ctrl.save_sync()
        assert success is True
        mock_editor_service.commit_source_text.assert_called_once_with(
            job_id=1,
            raw_text="# Changed Content",
            base_version=2,
        )

        assert ctrl.isDirty is False
        assert ctrl.activeVersion == 3
        saved_spy.assert_called_once_with(3)
    finally:
        ctrl.shutdown()


def test_controller_save_sync_stale_occ_rejection(qapp, mock_editor_service):
    mock_editor_service.commit_source_text.side_effect = StaleDocumentVersionError(
        job_id=1,
        base_version=2,
        current_version=3,
        message="Stale document",
    )

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Conflicted Edit")

        conflict_spy = MagicMock()
        ctrl.conflictDetected.connect(conflict_spy)

        success = ctrl.save_sync()
        assert success is False
        assert ctrl.hasConflict is True
        assert "Stale document" in ctrl.conflictMessage
        assert ctrl.isDirty is True
        conflict_spy.assert_called_once()
    finally:
        ctrl.shutdown()


def test_controller_notify_canonical_advance_when_dirty_detects_conflict(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# User Is Typing Here...")
        assert ctrl.isDirty is True

        conflict_spy = MagicMock()
        ctrl.conflictDetected.connect(conflict_spy)

        # External change advances to version 3
        ctrl.notifyCanonicalDocumentAdvance(3)

        assert ctrl.hasConflict is True
        assert "version 3" in ctrl.conflictMessage
        conflict_spy.assert_called_once()
        assert ctrl.sourceText == "# User Is Typing Here..."
    finally:
        ctrl.shutdown()


def test_controller_notify_canonical_advance_when_clean_reloads_automatically(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        assert ctrl.isDirty is False

        # Configure service to return v3 on next load
        mock_editor_service.load_source_text.return_value = ("# Newer V3 Document", 3)

        # External change advances to version 3 while buffer is clean
        ctrl.notifyCanonicalDocumentAdvance(3)

        # Wait for async background worker to complete
        import time
        for _ in range(50):
            QGuiApplication.processEvents()
            if ctrl.sourceText == "# Newer V3 Document":
                break
            time.sleep(0.01)

        assert ctrl.hasConflict is False
        assert ctrl.activeVersion == 3
        assert ctrl.sourceText == "# Newer V3 Document"
    finally:
        ctrl.shutdown()


def test_controller_clear(qapp, mock_editor_service):
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=1)
        ctrl.setSourceText("# Some Edit")

        ctrl.clear()
        assert ctrl.sourceText == ""
        assert ctrl.isDirty is False
        assert ctrl.activeJobId == 0
        assert ctrl.activeVersion == 0
        assert ctrl.hasConflict is False
        assert ctrl.cursorPosition == 0
    finally:
        ctrl.shutdown()


def _make_region_dto(
    job_id=42,
    region_id=None,
    review_status="accepted",
    sync_status="synced",
    active_artifact_uri="artifacts/crop_1.png",
    is_deleted=False,
    is_modified=False,
    is_active=True,
):
    import uuid
    from application.dto.visual_region_dto import VisualRegionDTO
    from core.entities.bounding_box import BoundingBox

    rid = region_id or uuid.uuid4().hex
    dto = VisualRegionDTO(
        id=1,
        region_id=rid,
        job_id=job_id,
        page_number=1,
        display_order=1,
        origin="ai_detected",
        review_status=review_status,
        sync_status=sync_status,
        effective_bbox=BoundingBox(100, 100, 500, 500),
        detected_bbox=BoundingBox(100, 100, 500, 500),
        reviewed_bbox=None,
        active_artifact_version=1,
        active_artifact_uri=active_artifact_uri,
        is_modified=is_modified,
        is_deleted=is_deleted,
    )
    if not is_active:
        setattr(dto, "is_active", False)
    return dto


def test_insert_visual_region_token_success(qapp, mock_editor_service):
    """Verifies successful token insertion at cursor position, buffer dirtying, and metrics update."""
    import uuid
    from core.domain.visual_token import parse_fields

    reg_uuid = uuid.uuid4()
    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex, active_artifact_uri="artifacts/crop_42.png")
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        assert ctrl.sourceText == "# Original Title\nInitial body text."
        assert ctrl.isDirty is False

        # Set cursor after first line ("# Original Title\n") which is 17 chars
        ctrl.updateCursorPosition(17)
        assert ctrl.cursorPosition == 17

        source_text_spy = MagicMock()
        dirty_spy = MagicMock()
        nav_spy = MagicMock()
        ctrl.sourceTextChanged.connect(source_text_spy)
        ctrl.dirtyChanged.connect(dirty_spy)
        ctrl.requestNavigateToPosition.connect(nav_spy)

        success = ctrl.insertVisualRegionToken(42, reg_uuid.hex, "Figure Diagram")
        assert success is True
        assert ctrl.isDirty is True

        # Buffer was mutated at offset 17
        expected_prefix = "# Original Title\n![Figure Diagram](artifacts/crop_42.png \"polpo:region="
        assert ctrl.sourceText.startswith(expected_prefix)
        assert ctrl.sourceText.endswith("Initial body text.")

        # Signals emitted
        source_text_spy.assert_called_once()
        dirty_spy.assert_called_once()
        nav_spy.assert_called_once()

        # Cursor advanced past inserted token
        inserted_len = len(ctrl.sourceText) - len("# Original Title\nInitial body text.")
        assert ctrl.cursorPosition == 17 + inserted_len
        nav_spy.assert_called_once_with(17 + inserted_len)

        # Parse token fields to verify canonical validity
        token_str = ctrl.sourceText[17:17 + inserted_len]
        token_obj = parse_fields(token_str)
        assert token_obj.region_id == reg_uuid
        assert token_obj.uri == "artifacts/crop_42.png"
        assert token_obj.alt_text == "Figure Diagram"
        assert token_obj.occurrence_id != reg_uuid
        assert token_obj.occurrence_id.version == 4
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_occurrence_uuid_semantics(qapp, mock_editor_service):
    """Verifies that consecutive insertions generate distinct, fresh occurrence UUIDs differing from document version."""
    import uuid
    from core.domain.visual_token import parse_fields

    reg_uuid = uuid.uuid4()
    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex)
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        ctrl.insertVisualRegionToken(42, reg_uuid.hex, "First Insert")

        token1_str = ctrl.sourceText[:ctrl.sourceText.find("#")]
        token1 = parse_fields(token1_str)
        occ1 = token1.occurrence_id
        assert occ1.version == 4
        assert str(occ1) != str(ctrl.activeVersion)
        assert str(occ1) != str(reg_uuid)

        # Clear and insert again for another fresh token
        ctrl.clear()
        ctrl.load_source_sync(job_id=42)
        ctrl.insertVisualRegionToken(42, reg_uuid.hex, "Second Insert")
        token2_str = ctrl.sourceText[:ctrl.sourceText.find("#")]
        token2 = parse_fields(token2_str)
        occ2 = token2.occurrence_id
        assert occ2.version == 4
        assert occ1 != occ2
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_preconditions_rejections(qapp, mock_editor_service):
    """Verifies that insertion fails and preserves clean buffer for invalid preconditions."""
    import uuid
    from core.entities.visual_region import ReviewStatus, SyncStatus

    valid_uuid = uuid.uuid4()
    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        original_text = ctrl.sourceText

        # 1. Nonexistent region (service returns None)
        mock_editor_service.get_visual_region.return_value = None
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 2. Deleted region (is_deleted = True)
        mock_editor_service.get_visual_region.return_value = _make_region_dto(job_id=42, is_deleted=True)
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 3. Rejected region (review_status = REJECTED)
        mock_editor_service.get_visual_region.return_value = _make_region_dto(
            job_id=42, review_status=ReviewStatus.REJECTED
        )
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 4. Unsynced region (sync_status = DIRTY_RECROP_REQUIRED)
        mock_editor_service.get_visual_region.return_value = _make_region_dto(
            job_id=42, sync_status=SyncStatus.DIRTY_RECROP_REQUIRED
        )
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 5. Missing / whitespace artifact URI
        mock_editor_service.get_visual_region.return_value = _make_region_dto(
            job_id=42, active_artifact_uri=None
        )
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        mock_editor_service.get_visual_region.return_value = _make_region_dto(
            job_id=42, active_artifact_uri="   "
        )
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 6. Inactive region (is_active = False)
        mock_editor_service.get_visual_region.return_value = _make_region_dto(
            job_id=42, is_active=False
        )
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 7. Invalid region UUID string
        assert ctrl.insertVisualRegionToken(42, "not-a-valid-uuid", "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 8. Invalid job ID (string or non-positive integer)
        assert ctrl.insertVisualRegionToken("bad-job-id", valid_uuid.hex, "Alt") is False
        assert ctrl.insertVisualRegionToken(0, valid_uuid.hex, "Alt") is False
        assert ctrl.insertVisualRegionToken(-5, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 9. Mismatched job ID (controller active job is 42, requested job is 99)
        mock_editor_service.get_visual_region.return_value = _make_region_dto(job_id=99)
        assert ctrl.insertVisualRegionToken(99, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # 10. Region belonging to different job than requested
        mock_editor_service.get_visual_region.return_value = _make_region_dto(job_id=99)
        assert ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False
    finally:
        ctrl.shutdown()

    # 11. Controller with no active job loaded (active_job_id == 0) must reject insertion
    unloaded_ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        assert unloaded_ctrl.activeJobId == 0
        assert unloaded_ctrl.insertVisualRegionToken(42, valid_uuid.hex, "Alt") is False
        assert unloaded_ctrl.sourceText == ""
        assert unloaded_ctrl.isDirty is False
    finally:
        unloaded_ctrl.shutdown()


def test_insert_visual_region_token_duplicate_guard(qapp, mock_editor_service):
    """Verifies that duplicate token insertion is rejected, buffer is untouched, and caret moves to existing token."""
    import uuid

    reg_uuid = uuid.uuid4()
    occ_uuid = uuid.uuid4()
    initial_text = f"# Title\n\n![Existing Fig](artifacts/crop.png \"polpo:region={reg_uuid};occ={occ_uuid}\")\n\nTail."
    mock_editor_service.load_source_text.return_value = (initial_text, 1)

    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex, active_artifact_uri="artifacts/crop.png")
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        assert ctrl.sourceText == initial_text
        assert ctrl.isDirty is False

        # Cursor at end of doc
        ctrl.updateCursorPosition(len(initial_text))

        nav_spy = MagicMock()
        ctrl.requestNavigateToPosition.connect(nav_spy)
        dirty_spy = MagicMock()
        ctrl.dirtyChanged.connect(dirty_spy)

        # Attempt to re-insert the same region
        success = ctrl.insertVisualRegionToken(42, reg_uuid.hex, "New Alt")
        assert success is False
        assert ctrl.sourceText == initial_text
        assert ctrl.isDirty is False
        dirty_spy.assert_not_called()

        # Existing token starts at offset 9 ("# Title\n\n")
        existing_pos = initial_text.find("![Existing Fig]")
        assert existing_pos == 9
        nav_spy.assert_called_once_with(existing_pos)
        assert ctrl.cursorPosition == existing_pos
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_with_injected_region_service(qapp, mock_editor_service):
    """Verifies that an explicitly injected region_service is prioritized over editor_service."""
    import uuid

    reg_uuid = uuid.uuid4()
    mock_region_service = MagicMock()
    mock_region_service.get_visual_region.return_value = _make_region_dto(
        job_id=42, region_id=reg_uuid.hex, active_artifact_uri="artifacts/crop_injected.png"
    )

    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        region_service=mock_region_service,
    )
    try:
        ctrl.load_source_sync(job_id=42)
        success = ctrl.insertVisualRegionToken(42, reg_uuid.hex, "Injected Alt")
        assert success is True
        mock_region_service.get_visual_region.assert_called_once_with(42, str(reg_uuid))
        mock_editor_service.get_visual_region.assert_not_called()
        assert "artifacts/crop_injected.png" in ctrl.sourceText
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_buffer_only_no_disk_publication(qapp, mock_editor_service):
    """Verifies the buffer-only invariant: no premature publication to disk or DB on insertion."""
    import uuid

    reg_uuid = uuid.uuid4()
    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex)
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        assert ctrl.activeVersion == 2

        success = ctrl.insertVisualRegionToken(42, reg_uuid.hex, "Buffer Only")
        assert success is True

        # Commit/save was NOT called on service
        mock_editor_service.commit_source_text.assert_not_called()
        # Active version was NOT incremented prematurely
        assert ctrl.activeVersion == 2
        # Buffer is dirty and ready for explicit user save
        assert ctrl.isDirty is True
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_undo_redo_integration(qapp, mock_editor_service):
    """Verifies that token insertion preserves undo/redo integrity."""
    import uuid

    reg_uuid = uuid.uuid4()
    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex)
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        ctrl.load_source_sync(job_id=42)
        original_text = ctrl.sourceText

        success = ctrl.insertVisualRegionToken(42, reg_uuid.hex, "Undo Test")
        assert success is True
        assert ctrl.isDirty is True
        assert original_text != ctrl.sourceText

        # Undo insertion
        ctrl.undo()
        assert ctrl.sourceText == original_text
        assert ctrl.isDirty is False

        # Redo insertion
        ctrl.redo()
        assert ctrl.sourceText != original_text
        assert ctrl.isDirty is True
        assert "polpo:region=" in ctrl.sourceText
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_metaobject_slot_overloads(qapp, mock_editor_service):
    """Verifies that all slot signatures are registered in QMetaObject and invokable."""
    import uuid

    reg_uuid = uuid.uuid4()
    region_dto = _make_region_dto(job_id=42, region_id=reg_uuid.hex)
    mock_editor_service.get_visual_region.return_value = region_dto

    ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    try:
        meta = ctrl.metaObject()
        methods = [meta.method(i).name().data().decode("utf-8") for i in range(meta.methodCount())]
        assert "insertVisualRegionToken" in methods
        assert "undo" in methods
        assert "redo" in methods

        ctrl.load_source_sync(job_id=42)

        # Invokable with (str, str, str)
        success = ctrl.insertVisualRegionToken("42", reg_uuid.hex, "Alt")
        assert success is True
    finally:
        ctrl.shutdown()


def test_insert_visual_region_token_clean_ast_imports():
    """Verifies that markdown_editor_controller does not import private symbols from visual_token_mutator."""
    import ast
    from pathlib import Path

    ctrl_path = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "controllers" / "markdown_editor_controller.py"
    tree = ast.parse(ctrl_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.markdown.visual_token_mutator":
            imported_names = [alias.name for alias in node.names]
            assert "_MD_IMAGE_RE" not in imported_names
            assert "_normalize_uuid" not in imported_names
            assert "find_canonical_token_spans" in imported_names
