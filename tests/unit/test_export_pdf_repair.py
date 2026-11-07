# ============================================================
#  tests/unit/test_export_pdf_repair.py
#  Unit tests for PDF Compilation Failure Repair in ExportController
# ============================================================

import time
from unittest.mock import MagicMock
import pytest

from core.exceptions.domain_exceptions import PdfCompilationError
from interfaces.desktop.controllers.export_controller import ExportController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_pdf_compilation_error_emits_repair_signal(qapp):
    mock_export_service = MagicMock()
    mock_pdf_service = MagicMock()
    mock_pdf_service.export_pdf.side_effect = PdfCompilationError(
        sanitized_message="Undefined control sequence \\badcmd",
        line_number=15,
    )

    ctrl = ExportController(
        export_service=mock_export_service,
        export_pdf_service=mock_pdf_service,
    )
    try:
        pdf_failures = []
        ctrl.pdfCompilationFailed.connect(
            lambda jid, msg, lno: pdf_failures.append((jid, msg, lno))
        )
        export_failures = []
        ctrl.exportFailed.connect(
            lambda jid, msg: export_failures.append((jid, msg))
        )

        ctrl.exportPdf(
            job_id=7,
            destination_uri="/tmp/output.pdf",
            version=1,
            overwrite=True,
        )

        deadline = time.time() + 2.0
        while (not pdf_failures or not export_failures) and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(pdf_failures) == 1
        jid, msg, lno = pdf_failures[0]
        assert jid == 7
        assert "Undefined control sequence \\badcmd" in msg
        assert lno == 15
        assert len(export_failures) == 1
    finally:
        ctrl.shutdown()


def test_request_pdf_repair_navigation(qapp):
    mock_editor_service = MagicMock()
    # Provide multi-line document
    doc_lines = "\n".join([f"Line {i}" for i in range(1, 30)])
    mock_editor_service.load_source_text.return_value = (doc_lines, 1)

    editor_ctrl = MarkdownEditorController(editor_service=mock_editor_service)
    editor_ctrl.load_source_sync(12)

    export_ctrl = ExportController(
        export_service=MagicMock(),
        editor_controller=editor_ctrl,
    )
    try:
        nav_positions = []
        editor_ctrl.requestNavigateToPosition.connect(nav_positions.append)

        # Mismatched job id returns False
        assert export_ctrl.requestPdfRepair(job_id=99, sanitized_message="Error", line_number=5) is False
        assert len(nav_positions) == 0

        # Matching job id navigates to line 5
        assert export_ctrl.requestPdfRepair(job_id=12, sanitized_message="Error", line_number=5) is True
        assert len(nav_positions) == 1
        # Line 5 position should be > 0
        assert nav_positions[0] > 0

        # Token-based navigation: finds \badmacro in document and navigates to its position
        doc_with_macro = "# Title\n\nSome text.\n\nHere is \\badmacro in code."
        mock_editor_service.load_source_text.return_value = (doc_with_macro, 1)
        editor_ctrl.load_source_sync(12)
        nav_positions.clear()

        # TeX reported line 200 (way beyond document length), but diagnostic contains \badmacro
        assert export_ctrl.requestPdfRepair(
            job_id=12,
            sanitized_message="Undefined control sequence \\badmacro on input line 200",
            line_number=200,
        ) is True
        assert len(nav_positions) == 1
        # Position should match position of \badmacro
        expected_pos = doc_with_macro.find("\\badmacro")
        assert nav_positions[0] == expected_pos
    finally:
        export_ctrl.shutdown()
        editor_ctrl.shutdown()
