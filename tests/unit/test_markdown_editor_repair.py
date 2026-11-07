# ============================================================
#  tests/unit/test_markdown_editor_repair.py
#  Unit tests for AI LaTeX Error Repair in MarkdownEditorController
# ============================================================

import time
from unittest.mock import MagicMock
import pytest

from application.dto.latex_repair_dto import LaTeXRepairProposal, LaTeXRepairRequest
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.qt_compat import QGuiApplication, QTextCursor


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_editor_service():
    service = MagicMock()
    service.load_source_text.return_value = (
        "# Document\n\nHere is a failing formula: $\\frac{1}{0$.\nEnd of file.",
        1,
    )
    service.commit_source_text.return_value = 2
    return service


@pytest.fixture
def mock_repair_service():
    service = MagicMock()
    proposal = LaTeXRepairProposal(
        request_id="rep-123",
        job_id=10,
        canonical_version=1,
        draft_revision=0,
        node_id="math-1",
        original_source="\\frac{1}{0",
        original_hash="orig-hash",
        repaired_source="\\frac{1}{0}",
        repaired_hash="rep-hash",
        change_summary="Added missing closing brace",
        preserved_invariants=("syntax_valid",),
        validation_passed=True,
        validation_error=None,
        rendered_preview_xml="<svg></svg>",
        rendered_preview_width=50.0,
        rendered_preview_height=20.0,
        retry_count=0,
        provider_label="Gemini",
        model_name="gemini-2.5-flash",
    )
    service.repair_latex.return_value = proposal
    return service


def test_initial_repair_properties(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        assert ctrl.isRepairing is False
        assert ctrl.activeProposal is None
        assert ctrl.repairError == ""
    finally:
        ctrl.shutdown()


def test_request_repair_precondition_failures(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        failed_msgs = []
        ctrl.repairFailed.connect(failed_msgs.append)

        # 1. No active job
        assert ctrl.requestRepair("node1", "\\frac{1}{0", "math_inline", "Syntax error") is False
        assert "No active document job" in ctrl.repairError
        assert len(failed_msgs) == 1

        # Load document
        ctrl.load_source_sync(10)
        failed_msgs.clear()

        # 2. Empty original source
        assert ctrl.requestRepair("node1", "", "math_inline", "Syntax error") is False
        assert "LaTeX source code cannot be empty" in ctrl.repairError

        # 3. Formula not in buffer
        assert ctrl.requestRepair("node1", "\\nonexistent{code}", "math_inline", "Error") is False
        assert "not found in current editor buffer" in ctrl.repairError

        # 4. No repair service
        ctrl_no_service = MarkdownEditorController(editor_service=mock_editor_service)
        ctrl_no_service.load_source_sync(10)
        no_service_msgs = []
        ctrl_no_service.repairFailed.connect(no_service_msgs.append)
        assert ctrl_no_service.requestRepair("node1", "\\frac{1}{0", "math_inline", "Error") is False
        assert "repair service is not configured" in ctrl_no_service.repairError
        ctrl_no_service.shutdown()
    finally:
        ctrl.shutdown()


def test_request_repair_conflict_rejection(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)
        ctrl._has_conflict = True

        failed_msgs = []
        ctrl.repairFailed.connect(failed_msgs.append)

        assert ctrl.requestRepair("node1", "\\frac{1}{0", "math_inline", "Error") is False
        assert "merge conflicts are active" in ctrl.repairError
        assert len(failed_msgs) == 1
    finally:
        ctrl.shutdown()


def test_request_repair_async_success(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        proposals_ready = []
        ctrl.repairProposalReady.connect(proposals_ready.append)

        assert ctrl.requestRepair(
            node_id="math-1",
            original_source="\\frac{1}{0",
            renderer_type="math_inline",
            diagnostics="Missing } inserted",
            error_line=1,
            log_excerpt="! Missing } inserted.",
        ) is True

        # Wait for worker thread to complete and emit to Qt event loop
        deadline = time.time() + 2.0
        while not proposals_ready and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(proposals_ready) == 1
        proposal_dict = proposals_ready[0]
        assert proposal_dict["repairedSource"] == "\\frac{1}{0}"
        assert proposal_dict["changeSummary"] == "Added missing closing brace"
        assert ctrl.isRepairing is False
        assert ctrl.activeProposal is not None
        assert ctrl.activeProposal["repairedSource"] == "\\frac{1}{0}"
    finally:
        ctrl.shutdown()


def test_request_repair_validation_failed_proposal(qapp, mock_editor_service, mock_repair_service):
    # Configure repair service to return an invalid candidate
    failed_proposal = LaTeXRepairProposal(
        request_id="rep-fail",
        job_id=10,
        canonical_version=1,
        draft_revision=0,
        node_id="math-1",
        original_source="\\frac{1}{0",
        original_hash="orig-hash",
        repaired_source="\\frac{bad}",
        repaired_hash="bad-hash",
        change_summary="Attempted fix",
        preserved_invariants=(),
        validation_passed=False,
        validation_error="Compilation error: undefined sequence",
        retry_count=1,
    )
    mock_repair_service.repair_latex.return_value = failed_proposal

    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        failed_msgs = []
        ctrl.repairFailed.connect(failed_msgs.append)

        assert ctrl.requestRepair("math-1", "\\frac{1}{0", "math_inline", "Missing }") is True

        deadline = time.time() + 2.0
        while not failed_msgs and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(failed_msgs) == 1
        assert "Compilation error: undefined sequence" in failed_msgs[0]
        assert ctrl.isRepairing is False
        assert ctrl.activeProposal is None
    finally:
        ctrl.shutdown()


def test_request_repair_stale_version_rejection(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        # Simulate version changing while repair was processing
        ctrl._active_version = 2

        failed_msgs = []
        ctrl.repairFailed.connect(failed_msgs.append)

        # Deliver internal proposal for version 1
        proposal = mock_repair_service.repair_latex.return_value
        ctrl._repair_request_id = 5
        ctrl._on_internal_repair_completed(5, proposal)

        assert len(failed_msgs) == 1
        assert "Document version changed" in failed_msgs[0]
        assert ctrl.activeProposal is None
    finally:
        ctrl.shutdown()


def test_request_repair_buffer_modified_rejection(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        # User modifies buffer and deletes the failing formula
        ctrl.set_source_text("# Document\n\nNo formula here.\nEnd of file.")

        failed_msgs = []
        ctrl.repairFailed.connect(failed_msgs.append)

        proposal = mock_repair_service.repair_latex.return_value
        ctrl._repair_request_id = 5
        ctrl._on_internal_repair_completed(5, proposal)

        assert len(failed_msgs) == 1
        assert "Original formula was modified or removed" in failed_msgs[0]
        assert ctrl.activeProposal is None
    finally:
        ctrl.shutdown()


def test_apply_repair_and_undo_stack(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        proposals_ready = []
        ctrl.repairProposalReady.connect(proposals_ready.append)
        applied_positions = []
        ctrl.repairApplied.connect(applied_positions.append)

        assert ctrl.requestRepair("math-1", "\\frac{1}{0", "math_inline", "Missing }") is True

        deadline = time.time() + 2.0
        while not proposals_ready and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert ctrl.activeProposal is not None
        assert ctrl.isDirty is False
        assert ctrl.activeVersion == 1

        # Apply repair
        assert ctrl.applyRepair() is True

        # Verify replacement in buffer
        assert "$\\frac{1}{0}$" in ctrl.sourceText
        assert "$\\frac{1}{0$." not in ctrl.sourceText
        assert ctrl.isDirty is True
        assert ctrl.activeVersion == 1  # Canonical version watermark preserved until Save
        assert ctrl.activeProposal is None
        assert len(applied_positions) == 1

        # Test single undo on QTextDocument
        doc = ctrl._get_document()
        doc.undo()
        ctrl.set_source_text(doc.toPlainText())

        # Verify undo cleanly restores original failing text
        assert "$\\frac{1}{0$." in ctrl.sourceText
    finally:
        ctrl.shutdown()


def test_reject_repair(qapp, mock_editor_service, mock_repair_service):
    ctrl = MarkdownEditorController(
        editor_service=mock_editor_service,
        repair_service=mock_repair_service,
    )
    try:
        ctrl.load_source_sync(10)

        proposals_ready = []
        ctrl.repairProposalReady.connect(proposals_ready.append)

        ctrl.requestRepair("math-1", "\\frac{1}{0", "math_inline", "Missing }")
        deadline = time.time() + 2.0
        while not proposals_ready and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert ctrl.activeProposal is not None
        ctrl.rejectRepair()
        assert ctrl.activeProposal is None
        assert ctrl.repairError == ""
    finally:
        ctrl.shutdown()
