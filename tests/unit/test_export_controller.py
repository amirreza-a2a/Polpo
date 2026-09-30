# ============================================================
#  tests/unit/test_export_controller.py
#  Unit tests for Desktop ExportController
# ============================================================

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application.dto.job_dto import JobDetailDTO
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    ExportSourceNotFoundError,
    MarkdownExportResult,
    PackageExportResult,
)
from interfaces.desktop.controllers.export_controller import ExportController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_export_service():
    service = MagicMock()
    service.export_markdown.return_value = MarkdownExportResult(
        destination_path=Path("/tmp/exported.md"),
        canonical_version=1,
        asset_count=0,
        has_local_asset_references=False,
        canonical_sha256="abc123canonical",
        exported_sha256="abc123exported",
        size_bytes=100,
    )
    service.export_package.return_value = PackageExportResult(
        destination_path=Path("/tmp/exported.zip"),
        root_directory="doc_v1",
        canonical_version=1,
        asset_count=2,
        manifest={},
        canonical_sha256="abc123canonical",
        exported_sha256="abc123exported",
        size_bytes=500,
    )
    return service


@pytest.fixture
def mock_query_service():
    service = MagicMock()
    dto = JobDetailDTO(
        id=1,
        user_id=1,
        file_name="research_paper.pdf",
        status="done",
        total_pages=5,
        processed_pages=5,
        prompt_id=None,
        prompt_text=None,
        active_api_label=None,
        api_switch_log=[],
        output_path="/tmp/artifacts/job_1/output_1_v1.md",
        error_message=None,
        auto_pipeline2=False,
        created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:01:00Z",
    )
    service.get_job_detail.return_value = dto
    return service


@pytest.fixture
def mock_editor_controller():
    ctrl = MagicMock()
    ctrl.activeJobId = 1
    ctrl.activeVersion = 1
    ctrl.isDirty = False
    ctrl.hasConflict = False
    # Set up signal mocks
    ctrl.saved = MagicMock()
    ctrl.errorChanged = MagicMock()
    ctrl.conflictDetected = MagicMock()
    return ctrl


def pump_until(app, condition, timeout=3.0, step=0.01):
    """Pumps the Qt event loop until condition evaluates to True or timeout expires."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        app.processEvents()
        if condition():
            return True
        time.sleep(step)
    app.processEvents()
    return bool(condition())


# ------------------------------------------------------------
# 1. Initial State
# ------------------------------------------------------------

def test_export_controller_initial_state(qapp, mock_export_service, mock_query_service):
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        assert ctrl.isExporting is False
        assert ctrl.errorMessage == ""
        assert ctrl.hasPendingExport is False
        assert ctrl.hasPendingOverwrite is False
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 2. Markdown Export
# ------------------------------------------------------------

def test_export_markdown_async_success(qapp, mock_export_service, mock_query_service):
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        completed_spy = MagicMock()
        ctrl.exportCompleted.connect(completed_spy)

        dest_uri = "file:///tmp/exported.md"
        ctrl.exportMarkdown(job_id=1, destination_uri=dest_uri, version=1, overwrite=False)

        # Should be exporting initially
        assert ctrl.isExporting is True

        # Wait for worker completion
        assert pump_until(qapp, lambda: not ctrl.isExporting)

        assert ctrl.isExporting is False
        assert ctrl.errorMessage == ""
        mock_export_service.export_markdown.assert_called_once_with(
            job_id=1,
            destination_path=Path("/tmp/exported.md"),
            version=1,
            overwrite=False,
        )
        completed_spy.assert_called_once_with(1, str(Path("/tmp/exported.md")))
    finally:
        ctrl.shutdown()


def test_export_markdown_failure_sanitized(qapp, mock_export_service, mock_query_service):
    mock_export_service.export_markdown.side_effect = ExportSourceNotFoundError("File missing: sk-secret1234567890")
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        failed_spy = MagicMock()
        ctrl.exportFailed.connect(failed_spy)

        ctrl.exportMarkdown(job_id=1, destination_uri="/tmp/exported.md", version=1)
        assert pump_until(qapp, lambda: not ctrl.isExporting)

        assert ctrl.isExporting is False
        assert "sk-secret" not in ctrl.errorMessage
        assert "[REDACTED" in ctrl.errorMessage or "File missing" in ctrl.errorMessage
        failed_spy.assert_called_once()
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 3. Package Export
# ------------------------------------------------------------

def test_export_package_async_success(qapp, mock_export_service, mock_query_service):
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        completed_spy = MagicMock()
        ctrl.exportCompleted.connect(completed_spy)

        dest_uri = "file:///tmp/exported.zip"
        ctrl.exportPackage(job_id=1, destination_uri=dest_uri, version=1, overwrite=False)

        assert ctrl.isExporting is True
        assert pump_until(qapp, lambda: not ctrl.isExporting)

        assert ctrl.isExporting is False
        mock_export_service.export_package.assert_called_once_with(
            job_id=1,
            destination_path=Path("/tmp/exported.zip"),
            version=1,
            overwrite=False,
        )
        completed_spy.assert_called_once_with(1, str(Path("/tmp/exported.zip")))
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 4. Overwrite Flow
# ------------------------------------------------------------

def test_export_overwrite_prompt_and_confirmation(qapp, mock_export_service, mock_query_service):
    # First attempt raises DestinationAlreadyExistsError
    target_path = Path("/tmp/existing.zip")
    mock_export_service.export_package.side_effect = [
        DestinationAlreadyExistsError(f"Destination file already exists: '{target_path}'"),
        PackageExportResult(
            destination_path=target_path,
            root_directory="doc_v1",
            canonical_version=1,
            asset_count=2,
            manifest={},
            canonical_sha256="abc",
            exported_sha256="def",
            size_bytes=500,
        ),
    ]

    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        overwrite_spy = MagicMock()
        completed_spy = MagicMock()
        ctrl.overwriteRequired.connect(overwrite_spy)
        ctrl.exportCompleted.connect(completed_spy)

        ctrl.exportPackage(job_id=1, destination_uri=str(target_path), version=1, overwrite=False)
        assert pump_until(qapp, lambda: ctrl.hasPendingOverwrite)

        # Overwrite signal must have fired
        assert ctrl.isExporting is False
        assert ctrl.hasPendingOverwrite is True
        overwrite_spy.assert_called_once_with(1, "package", str(target_path), 1)
        completed_spy.assert_not_called()

        # User confirms overwrite
        ctrl.confirmOverwrite()
        assert ctrl.isExporting is True
        assert pump_until(qapp, lambda: not ctrl.isExporting)

        assert ctrl.isExporting is False
        assert ctrl.hasPendingOverwrite is False
        completed_spy.assert_called_once_with(1, str(target_path))
        # Second call had overwrite=True
        assert mock_export_service.export_package.call_count == 2
        mock_export_service.export_package.assert_called_with(
            job_id=1,
            destination_path=target_path,
            version=1,
            overwrite=True,
        )
    finally:
        ctrl.shutdown()


def test_export_overwrite_cancellation(qapp, mock_export_service, mock_query_service):
    target_path = Path("/tmp/existing.zip")
    mock_export_service.export_package.side_effect = DestinationAlreadyExistsError("exists")

    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        overwrite_spy = MagicMock()
        ctrl.overwriteRequired.connect(overwrite_spy)

        ctrl.exportPackage(job_id=1, destination_uri=str(target_path), version=1, overwrite=False)
        assert pump_until(qapp, lambda: ctrl.hasPendingOverwrite)

        assert ctrl.hasPendingOverwrite is True
        ctrl.cancelOverwrite()
        assert ctrl.hasPendingOverwrite is False
        assert mock_export_service.export_package.call_count == 1
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 5. Dirty Editor Guard & Critical Version Rule
# ------------------------------------------------------------

def test_request_export_clean_editor_triggers_ready_for_destination(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = False
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        ready_spy = MagicMock()
        save_required_spy = MagicMock()
        ctrl.readyForDestination.connect(ready_spy)
        ctrl.saveBeforeExportRequired.connect(save_required_spy)

        ctrl.requestExport(job_id=1, export_type="package")

        save_required_spy.assert_not_called()
        ready_spy.assert_called_once_with(1, "package", 1)
    finally:
        ctrl.shutdown()


def test_request_export_dirty_editor_prompts_save_and_resumes_on_saved(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = True
    mock_editor_controller.activeJobId = 1
    mock_editor_controller.activeVersion = 1

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        save_required_spy = MagicMock()
        ready_spy = MagicMock()
        ctrl.saveBeforeExportRequired.connect(save_required_spy)
        ctrl.readyForDestination.connect(ready_spy)

        ctrl.requestExport(job_id=1, export_type="package")

        # Must request save before export
        save_required_spy.assert_called_once_with(1, "package", 1)
        ready_spy.assert_not_called()
        assert ctrl.hasPendingExport is True

        # User confirms "Save & Export"
        ctrl.confirmSaveAndExport()
        mock_editor_controller.save.assert_called_once()

        # Simulate editor saving successfully and producing version 2
        # In real controller, ctrl connects to editor_controller.saved
        # We invoke the slot handler directly through the connected signal
        save_slot = None
        for call_args in mock_editor_controller.saved.connect.call_args_list:
            save_slot = call_args[0][0]
            break
        assert save_slot is not None, "ExportController must connect to editor.saved"

        save_slot(2)  # New canonical version 2
        ready_spy.assert_called_once_with(1, "package", 2)
        assert ctrl.hasPendingExport is False
    finally:
        ctrl.shutdown()


def test_request_export_dirty_editor_save_failure_aborts_export(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = True
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        failed_spy = MagicMock()
        ready_spy = MagicMock()
        ctrl.exportFailed.connect(failed_spy)
        ctrl.readyForDestination.connect(ready_spy)

        ctrl.requestExport(job_id=1, export_type="package")
        ctrl.confirmSaveAndExport()

        # Simulate error in editor saving
        error_slot = None
        for call_args in mock_editor_controller.errorChanged.connect.call_args_list:
            error_slot = call_args[0][0]
            break
        assert error_slot is not None

        mock_editor_controller.errorMessage = "Failed to write file to disk"
        error_slot()

        assert ctrl.hasPendingExport is False
        ready_spy.assert_not_called()
        failed_spy.assert_called_once()
        assert "Failed to write file" in ctrl.errorMessage
    finally:
        ctrl.shutdown()


def test_request_export_dirty_editor_cancel_cleans_up(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = True
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        ctrl.requestExport(job_id=1, export_type="markdown")
        assert ctrl.hasPendingExport is True

        ctrl.cancelPendingExport()
        assert ctrl.hasPendingExport is False
    finally:
        ctrl.shutdown()


def test_confirm_save_and_export_when_conflict_without_dirty_fails_fast(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = False
    mock_editor_controller.hasConflict = True
    # Real MarkdownEditorController.save() does nothing when isDirty is False
    mock_editor_controller.save.side_effect = lambda: None

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        failed_spy = MagicMock()
        save_prompt_spy = MagicMock()
        ctrl.exportFailed.connect(failed_spy)
        ctrl.saveBeforeExportRequired.connect(save_prompt_spy)

        ctrl.requestExport(job_id=1, export_type="markdown")
        assert ctrl.hasPendingExport is True
        save_prompt_spy.assert_called_once_with(1, "markdown", 1)

        ctrl.confirmSaveAndExport()

        # Must not remain stuck in pending export
        assert ctrl.hasPendingExport is False
        failed_spy.assert_called_once()
        assert "conflict" in ctrl.errorMessage.lower()
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 6. Suggested File Name & Slugs
# ------------------------------------------------------------

def test_suggested_file_name(qapp, mock_export_service, mock_query_service):
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        md_name = ctrl.getSuggestedFileName(job_id=1, export_type="markdown", version=2)
        zip_name = ctrl.getSuggestedFileName(job_id=1, export_type="package", version=2)

        assert md_name == "research-paper_v2.md"
        assert zip_name == "research-paper_v2.zip"
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 7. State Safety & Concurrent Guard
# ------------------------------------------------------------

def test_duplicate_export_request_rejected(qapp, mock_export_service, mock_query_service):
    # Make export hang to verify duplicate request is blocked
    def slow_export(*args, **kwargs):
        time.sleep(0.2)
        return MarkdownExportResult(
            destination_path=Path("/tmp/slow.md"),
            canonical_version=1,
            asset_count=0,
            has_local_asset_references=False,
            canonical_sha256="abc",
            exported_sha256="def",
            size_bytes=10,
        )

    mock_export_service.export_markdown.side_effect = slow_export
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    try:
        ctrl.exportMarkdown(job_id=1, destination_uri="/tmp/slow.md", version=1)
        assert ctrl.isExporting is True

        # Second attempt while still exporting should be ignored
        ctrl.exportMarkdown(job_id=1, destination_uri="/tmp/another.md", version=1)
        assert mock_export_service.export_markdown.call_count == 1

        assert pump_until(qapp, lambda: not ctrl.isExporting)
        assert ctrl.isExporting is False
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------
# 8. Shutdown Lifecycle
# ------------------------------------------------------------

def test_shutdown_cancels_work_and_cleans_up(qapp, mock_export_service, mock_query_service):
    ctrl = ExportController(
        export_service=mock_export_service,
        query_service=mock_query_service,
    )
    ctrl.shutdown()
    assert ctrl.isShutdown is True
    # Subsequent calls should not do anything
    ctrl.exportMarkdown(job_id=1, destination_uri="/tmp/after_shutdown.md")
    assert ctrl.isExporting is False


def test_export_package_direct_call_while_dirty_resumes_to_destination(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = True
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        save_required_spy = MagicMock()
        completed_spy = MagicMock()
        ctrl.saveBeforeExportRequired.connect(save_required_spy)
        ctrl.exportCompleted.connect(completed_spy)

        dest_uri = "file:///tmp/auto_resume.zip"
        ctrl.exportPackage(job_id=1, destination_uri=dest_uri, version=1, overwrite=False)

        # Must intercept dirty state and ask for save
        save_required_spy.assert_called_once_with(1, "package", 1)
        assert ctrl.isExporting is False
        assert ctrl.hasPendingExport is True

        # Confirm save
        ctrl.confirmSaveAndExport()
        save_slot = mock_editor_controller.saved.connect.call_args[0][0]

        # Simulate save emitting new version 2
        save_slot(2)
        assert ctrl.isExporting is True

        assert pump_until(qapp, lambda: not ctrl.isExporting)
        assert ctrl.isExporting is False
        completed_spy.assert_called_once_with(1, str(mock_export_service.export_package.return_value.destination_path))
        mock_export_service.export_package.assert_called_once_with(
            job_id=1,
            destination_path=Path("/tmp/auto_resume.zip"),
            version=2,
            overwrite=False,
        )
    finally:
        ctrl.shutdown()


def test_cancelled_save_does_not_trigger_export_on_later_save(qapp, mock_export_service, mock_query_service, mock_editor_controller):
    mock_editor_controller.isDirty = True
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    try:
        ctrl.requestExport(job_id=1, export_type="package")
        ctrl.confirmSaveAndExport()

        # Grab connected save slot
        save_slot = mock_editor_controller.saved.connect.call_args[0][0]

        # User cancels pending export before save completes
        ctrl.cancelPendingExport()

        # Simulate save completing later
        save_slot(3)

        assert ctrl.isExporting is False
        mock_export_service.export_package.assert_not_called()
    finally:
        ctrl.shutdown()


def test_qml_review_workspace_export_integration(qapp, tmp_path):
    from interfaces.desktop.app import create_app
    from interfaces.desktop.qt_compat import QObject

    db_path = tmp_path / "test_export_ui.db"
    app, engine, container = create_app(
        argv=[],
        db_path=db_path,
        start_background_runtime=False,
    )
    try:
        qml_file = (
            Path(__file__).parent.parent.parent
            / "interfaces"
            / "desktop"
            / "qml"
            / "views"
            / "ReviewWorkspaceView.qml"
        )
        engine.load(str(qml_file))
        root_objs = engine.rootObjects()
        assert len(root_objs) > 0, "Failed to load ReviewWorkspaceView.qml"
        view = root_objs[-1]
        qapp.processEvents()

        md_btn = view.findChild(QObject, "exportMarkdownButton")
        assert md_btn is not None, "exportMarkdownButton must exist in ReviewWorkspaceView"

        pkg_btn = view.findChild(QObject, "exportPackageButton")
        assert pkg_btn is not None, "exportPackageButton must exist in ReviewWorkspaceView"

        save_modal = view.findChild(QObject, "saveBeforeExportModal")
        assert save_modal is not None, "saveBeforeExportModal must exist in ReviewWorkspaceView"

        overwrite_modal = view.findChild(QObject, "overwriteConfirmModal")
        assert overwrite_modal is not None, "overwriteConfirmModal must exist in ReviewWorkspaceView"

        export_md_dialog = view.findChild(QObject, "exportMarkdownFileDialog")
        assert export_md_dialog is not None, "exportMarkdownFileDialog must exist in ReviewWorkspaceView"

        export_pkg_dialog = view.findChild(QObject, "exportPackageFileDialog")
        assert export_pkg_dialog is not None, "exportPackageFileDialog must exist in ReviewWorkspaceView"
    finally:
        container.shutdown()
