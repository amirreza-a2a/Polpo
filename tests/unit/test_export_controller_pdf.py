# ============================================================
#  tests/unit/test_export_controller_pdf.py
#  Unit tests for PDF export flows in ExportController
# ============================================================

from pathlib import Path
import time
from unittest.mock import MagicMock

import pytest

from application.dto.job_dto import JobDetailDTO
from application.dto.package_recovery_dto import PackageRecoveryPlanDTO
from application.dto.pdf_export_dto import PdfExportResultDTO
from application.services.export_package_service import DestinationAlreadyExistsError
from core.exceptions.domain_exceptions import (
    MissingLatexPackageError,
    PdfCompilationError,
)
from interfaces.desktop.controllers.export_controller import ExportController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


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


@pytest.fixture
def mock_export_service():
    return MagicMock()


@pytest.fixture
def mock_export_pdf_service():
    service = MagicMock()
    service.export_pdf.return_value = PdfExportResultDTO(
        destination_path=Path("/tmp/exported.pdf"),
        canonical_version=1,
        page_count=5,
        file_size_bytes=1024,
        duration_seconds=1.2,
        cleanup_warning=None,
    )
    return service


@pytest.fixture
def mock_query_service():
    service = MagicMock()
    dto = JobDetailDTO(
        id=1,
        user_id=1,
        file_name="Deep_Learning_Paper.pdf",
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
    ctrl.saved = MagicMock()
    ctrl.errorChanged = MagicMock()
    ctrl.conflictDetected = MagicMock()
    return ctrl


@pytest.fixture
def mock_recovery_service():
    service = MagicMock()
    service.create_recovery_plan.return_value = PackageRecoveryPlanDTO(
        missing_packages=("algorithm2e", "amsmath"),
        can_auto_recover=True,
        manual_command="",
        diagnostic_summary="TinyTeX auto-recovery available.",
    )
    return service


def test_export_controller_pdf_suggested_file_name(
    qapp, mock_export_service, mock_query_service, mock_editor_controller
):
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
    )
    name = ctrl.getSuggestedFileName(1, "pdf", version=2)
    assert name.endswith(".pdf")
    assert "deep-learning-paper_v2.pdf" in name.lower() or "deep_learning_paper_v2.pdf" in name.lower()
    ctrl.shutdown()


def test_export_controller_pdf_happy_path(
    qapp, mock_export_service, mock_export_pdf_service, mock_query_service, mock_editor_controller
):
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
    )

    completed_events = []
    ctrl.exportCompleted.connect(lambda jid, path: completed_events.append((jid, path)))

    ctrl.exportPdf(job_id=1, destination_uri="/tmp/output.pdf", version=1)

    assert pump_until(qapp, lambda: len(completed_events) > 0, timeout=2.0)
    jid, path = completed_events[0]
    assert jid == 1
    assert path == "/tmp/exported.pdf"
    assert not ctrl.isExporting

    mock_export_pdf_service.export_pdf.assert_called_once()
    ctrl.shutdown()


def test_export_controller_pdf_with_warnings(
    qapp, mock_export_service, mock_export_pdf_service, mock_query_service, mock_editor_controller
):
    mock_export_pdf_service.export_pdf.return_value = PdfExportResultDTO(
        destination_path=Path("/tmp/exported.pdf"),
        canonical_version=1,
        page_count=3,
        file_size_bytes=1024,
        duration_seconds=0.8,
        cleanup_warning="Ephemeral staging directory cleanup failed: Permission denied",
    )

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
    )

    warnings = []
    completed = []
    ctrl.exportWarning.connect(lambda jid, w: warnings.append((jid, w)))
    ctrl.exportCompleted.connect(lambda jid, p: completed.append((jid, p)))

    ctrl.start_export(job_id=1, export_type="pdf", destination_path="/tmp/output.pdf", version=1)

    assert pump_until(qapp, lambda: len(completed) > 0, timeout=2.0)
    assert len(warnings) == 1
    assert "Permission denied" in warnings[0][1]
    assert completed[0][1] == "/tmp/exported.pdf"

    ctrl.shutdown()


def test_export_controller_pdf_save_gate(
    qapp, mock_export_service, mock_export_pdf_service, mock_query_service, mock_editor_controller
):
    mock_editor_controller.isDirty = True
    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
    )

    save_required = []
    ctrl.saveBeforeExportRequired.connect(lambda jid, t, v: save_required.append((jid, t, v)))

    ctrl.exportPdf(job_id=1, destination_uri="/tmp/output.pdf", version=1)

    # Must intercept and emit saveBeforeExportRequired
    assert len(save_required) == 1
    assert save_required[0] == (1, "pdf", 1)
    mock_export_pdf_service.export_pdf.assert_not_called()

    # User confirms Save & Export
    saved_callbacks = []
    mock_editor_controller.saved.connect.side_effect = lambda cb: saved_callbacks.append(cb)

    ctrl.confirmSaveAndExport()
    mock_editor_controller.save.assert_called_once()

    # Simulate editor successfully saving version 2
    mock_editor_controller.isDirty = False
    mock_editor_controller.activeVersion = 2
    for cb in saved_callbacks:
        cb(2)

    # Now export should dispatch and complete
    completed = []
    ctrl.exportCompleted.connect(lambda jid, path: completed.append((jid, path)))
    assert pump_until(qapp, lambda: len(completed) > 0, timeout=2.0)
    mock_export_pdf_service.export_pdf.assert_called_once()
    assert mock_export_pdf_service.export_pdf.call_args[0][0].version == 2

    ctrl.shutdown()


def test_export_controller_pdf_overwrite_flow(
    qapp, mock_export_service, mock_export_pdf_service, mock_query_service, mock_editor_controller
):
    mock_export_pdf_service.export_pdf.side_effect = [
        DestinationAlreadyExistsError("Destination file already exists: /tmp/output.pdf"),
        PdfExportResultDTO(
            destination_path=Path("/tmp/output.pdf"),
            canonical_version=1,
            page_count=2,
            file_size_bytes=1024,
            duration_seconds=0.5,
            cleanup_warning=None,
        ),
    ]

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
    )

    overwrite_events = []
    ctrl.overwriteRequired.connect(
        lambda jid, exp_type, path, ver: overwrite_events.append((jid, exp_type, path, ver))
    )

    ctrl.exportPdf(job_id=1, destination_uri="/tmp/output.pdf", version=1)

    assert pump_until(qapp, lambda: len(overwrite_events) > 0, timeout=2.0)
    assert overwrite_events[0] == (1, "pdf", "/tmp/output.pdf", 1)

    # Confirm overwrite
    completed = []
    ctrl.exportCompleted.connect(lambda jid, p: completed.append((jid, p)))
    ctrl.confirmOverwrite()

    assert pump_until(qapp, lambda: len(completed) > 0, timeout=2.0)
    assert mock_export_pdf_service.export_pdf.call_count == 2
    assert mock_export_pdf_service.export_pdf.call_args[0][0].overwrite is True

    ctrl.shutdown()


def test_export_controller_missing_package_signal(
    qapp,
    mock_export_service,
    mock_export_pdf_service,
    mock_query_service,
    mock_editor_controller,
    mock_recovery_service,
):
    mock_export_pdf_service.export_pdf.side_effect = MissingLatexPackageError(
        package_names=("algorithm2e", "amsmath"),
        sanitized_message="Missing LaTeX package 'algorithm2e'.",
    )

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
        package_recovery_service=mock_recovery_service,
    )

    recovery_events = []
    ctrl.packageRecoveryRequired.connect(
        lambda jid, pkgs, can_auto, inst: recovery_events.append((jid, pkgs, can_auto, inst))
    )

    ctrl.exportPdf(job_id=1, destination_uri="/tmp/output.pdf", version=1)

    assert pump_until(qapp, lambda: len(recovery_events) > 0, timeout=2.0)
    jid, pkgs, can_auto, inst = recovery_events[0]
    assert jid == 1
    assert "algorithm2e" in pkgs
    assert can_auto is True
    assert "TinyTeX" in inst
    assert not ctrl.isExporting

    ctrl.shutdown()


def test_export_controller_pdf_compilation_error_sanitization(
    qapp, mock_export_service, mock_export_pdf_service, mock_query_service, mock_editor_controller
):
    mock_export_pdf_service.export_pdf.side_effect = PdfCompilationError(
        sanitized_message="XeLaTeX compilation failed with syntax error.",
    )

    ctrl = ExportController(
        export_service=mock_export_service,
        editor_controller=mock_editor_controller,
        query_service=mock_query_service,
        export_pdf_service=mock_export_pdf_service,
    )

    failed_events = []
    ctrl.exportFailed.connect(lambda jid, msg: failed_events.append((jid, msg)))

    ctrl.exportPdf(job_id=1, destination_uri="/tmp/output.pdf", version=1)

    assert pump_until(qapp, lambda: len(failed_events) > 0, timeout=2.0)
    jid, msg = failed_events[0]
    assert jid == 1
    assert "XeLaTeX compilation failed" in msg
    assert not ctrl.isExporting

    ctrl.shutdown()
