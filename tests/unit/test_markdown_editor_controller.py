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
    finally:
        ctrl.shutdown()
