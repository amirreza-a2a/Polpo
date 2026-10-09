# ============================================================
#  tests/unit/test_document_viewer_controller.py
#  DocumentViewerController Visual Region Publication Wiring Tests (TICK-P02C, #29)
# ============================================================

import inspect
from unittest.mock import MagicMock
import pytest

from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from application.services.document_viewer_service import DocumentViewerService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_controller_dispatches_async_apply_to_publication_service(qapp):
    """
    Verifies that calling _trigger_async_apply dispatches background execution
    to VisualRegionPublicationService.publish_region_review off the GUI thread.
    """
    mock_service = MagicMock(spec=DocumentViewerService)
    mock_pub_service = MagicMock(spec=VisualRegionPublicationService)
    mock_pub_service.publish_region_review.return_value = RegionPublicationResultDTO(
        job_id=42,
        region_id="reg-abc",
        success=True,
        document_version=2,
        artifact_version=1,
        artifact_uri="crops/job_42/crop_reg-abc.jpg",
    )

    controller = DocumentViewerController(
        viewer_service=mock_service,
        region_publication_service=mock_pub_service,
    )

    try:
        controller._trigger_async_apply(job_id=42, region_id="reg-abc")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        mock_pub_service.publish_region_review.assert_called_once_with(
            job_id=42,
            region_id="reg-abc",
        )
    finally:
        controller.shutdown()


def test_controller_emits_applied_signal_on_success(qapp):
    """
    Verifies that upon successful review publication, both regionArtifactCommitted
    and regionApplied signals are emitted with the expected arguments.
    """
    mock_service = MagicMock(spec=DocumentViewerService)
    mock_pub_service = MagicMock(spec=VisualRegionPublicationService)
    mock_pub_service.publish_region_review.return_value = RegionPublicationResultDTO(
        job_id=10,
        region_id="reg-xyz",
        success=True,
        document_version=3,
        artifact_version=2,
        artifact_uri="crops/job_10/crop_reg-xyz.jpg",
    )

    controller = DocumentViewerController(
        viewer_service=mock_service,
        region_publication_service=mock_pub_service,
    )

    applied_events = []
    committed_events = []
    canonical_events = []

    controller.regionApplied.connect(lambda j, r, v, u: applied_events.append((j, r, v, u)))
    controller.regionArtifactCommitted.connect(lambda j, r, v, u: committed_events.append((j, r, v, u)))
    controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))

    try:
        controller._trigger_async_apply(job_id=10, region_id="reg-xyz")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        assert len(applied_events) == 1
        assert applied_events[0] == (10, "reg-xyz", 2, "crops/job_10/crop_reg-xyz.jpg")

        assert len(committed_events) == 1
        assert committed_events[0] == (10, "reg-xyz", 2, "crops/job_10/crop_reg-xyz.jpg")

        assert len(canonical_events) == 1
        assert canonical_events[0] == (10, 3)
    finally:
        controller.shutdown()


def test_controller_handles_publication_failure_gracefully(qapp):
    """
    Verifies that when publication fails or raises an exception, the applyFailed
    signal is emitted, error message is updated, and regionApplied is not emitted.
    """
    mock_service = MagicMock(spec=DocumentViewerService)
    mock_pub_service = MagicMock(spec=VisualRegionPublicationService)
    mock_pub_service.publish_region_review.return_value = RegionPublicationResultDTO(
        job_id=7,
        region_id="reg-fail",
        success=False,
        status_message="Document version conflict",
    )

    controller = DocumentViewerController(
        viewer_service=mock_service,
        region_publication_service=mock_pub_service,
    )

    applied_events = []
    failed_events = []
    canonical_events = []

    controller.regionApplied.connect(lambda j, r, v, u: applied_events.append((j, r, v, u)))
    controller.applyFailed.connect(lambda j, r, err: failed_events.append((j, r, err)))
    controller.canonicalDocumentPublished.connect(lambda j, v: canonical_events.append((j, v)))

    try:
        # Failure case 1: DTO returns success=False
        controller._trigger_async_apply(job_id=7, region_id="reg-fail")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        assert len(applied_events) == 0
        assert len(failed_events) == 1
        assert len(canonical_events) == 0
        assert failed_events[0] == (7, "reg-fail", "Document version conflict")
        assert "Document version conflict" in controller.errorMessage

        # Failure case 2: Service raises an exception
        failed_events.clear()
        mock_pub_service.publish_region_review.side_effect = RuntimeError("Disk IO failure")

        controller._trigger_async_apply(job_id=7, region_id="reg-fail")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        assert len(applied_events) == 0
        assert len(failed_events) == 1
        assert len(canonical_events) == 0
        assert failed_events[0] == (7, "reg-fail", "Disk IO failure")
        assert "Disk IO failure" in controller.errorMessage
    finally:
        controller.shutdown()


def test_controller_has_no_legacy_apply_service():
    """
    Verifies that DocumentViewerController constructor parameter and instance
    attribute is region_publication_service, and completely omits legacy apply service.
    """
    sig = inspect.signature(DocumentViewerController.__init__)
    assert "region_publication_service" in sig.parameters
    assert "apply" not in "".join(sig.parameters.keys()).replace("region_publication_service", "")

    mock_service = MagicMock(spec=DocumentViewerService)
    controller = DocumentViewerController(viewer_service=mock_service)
    try:
        assert hasattr(controller, "region_publication_service")
        assert not hasattr(controller, "".join(["apply_", "review_", "service"]))
    finally:
        controller.shutdown()


def _setup_controller_with_regions():
    from application.dto.visual_region_dto import VisualRegionDTO
    from core.entities.bounding_box import BoundingBox

    mock_service = MagicMock(spec=DocumentViewerService)
    mock_pub = MagicMock(spec=VisualRegionPublicationService)

    regions_store = {
        "01234567-89ab-4cde-8f01-23456789abcd": {
            "region_id": "01234567-89ab-4cde-8f01-23456789abcd",
            "job_id": 10,
            "page_number": 1,
            "display_order": 0,
            "origin": "ai_detected",
            "review_status": "unreviewed",
            "sync_status": "synced",
            "active_artifact_uri": "artifacts/crop_a.png",
            "is_modified": False,
            "effective_ymin": 100,
            "effective_xmin": 150,
            "effective_ymax": 300,
            "effective_xmax": 350,
        },
        "11111111-2222-4333-8444-555555555555": {
            "region_id": "11111111-2222-4333-8444-555555555555",
            "job_id": 10,
            "page_number": 1,
            "display_order": 1,
            "origin": "ai_detected",
            "review_status": "accepted",
            "sync_status": "synced",
            "active_artifact_uri": "artifacts/crop_b.png",
            "is_modified": True,
            "effective_ymin": 400,
            "effective_xmin": 450,
            "effective_ymax": 600,
            "effective_xmax": 650,
        },
        "99999999-9999-4999-8999-999999999999": {
            "region_id": "99999999-9999-4999-8999-999999999999",
            "job_id": 99,  # Mismatched job
            "page_number": 1,
            "display_order": 2,
            "origin": "ai_detected",
            "review_status": "unreviewed",
            "sync_status": "synced",
            "active_artifact_uri": "artifacts/crop_other.png",
            "is_modified": False,
            "effective_ymin": 10,
            "effective_xmin": 20,
            "effective_ymax": 30,
            "effective_xmax": 40,
        },
    }

    def _get_active(job_id, page_number):
        return [
            VisualRegionDTO(
                id=i,
                region_id=r["region_id"],
                job_id=r["job_id"],
                page_number=r["page_number"],
                display_order=r["display_order"],
                origin=r["origin"],
                review_status=r["review_status"],
                sync_status=r["sync_status"],
                effective_bbox=BoundingBox(
                    ymin=r["effective_ymin"],
                    xmin=r["effective_xmin"],
                    ymax=r["effective_ymax"],
                    xmax=r["effective_xmax"],
                ),
                detected_bbox=None,
                reviewed_bbox=None,
                active_artifact_version=1,
                active_artifact_uri=r["active_artifact_uri"],
                is_modified=r["is_modified"],
                is_deleted=False,
            )
            for i, r in enumerate(regions_store.values())
            if r["job_id"] == job_id and r["page_number"] == page_number
        ]

    def _reject(region_id):
        if region_id in regions_store:
            del regions_store[region_id]

    def _accept(region_id):
        if region_id in regions_store:
            regions_store[region_id]["review_status"] = "accepted"

    def _reset(region_id):
        if region_id in regions_store:
            regions_store[region_id]["is_modified"] = False

    mock_service.get_active_page_regions.side_effect = _get_active
    mock_service.reject_region.side_effect = _reject
    mock_service.accept_region.side_effect = _accept
    mock_service.reset_region_to_ai.side_effect = _reset

    ctrl = DocumentViewerController(viewer_service=mock_service, region_publication_service=mock_pub)
    ctrl._current_job_id = 10
    ctrl._current_page = 1
    ctrl._reload_page_regions()
    # Inject mismatched region specifically for cross-job rejection tests
    ctrl._active_regions.append(dict(regions_store["99999999-9999-4999-8999-999999999999"]))
    return ctrl, mock_service, mock_pub


def test_get_region_context_actions_targets_and_rejection(qapp):
    """
    Verifies that getRegionContextActions resolves target region, rejects stale/wrong-job
    regions without fallback, and supports selected region fallback only when region_id is empty.
    """
    ctrl, _, _ = _setup_controller_with_regions()
    try:
        reg_a = "01234567-89ab-4cde-8f01-23456789abcd"
        reg_b = "11111111-2222-4333-8444-555555555555"
        ctrl._selected_region_id = reg_b

        # 1. Explicit target A (different from selection B)
        actions_a = ctrl.getRegionContextActions(reg_a)
        assert len(actions_a) == 9
        acts_dict_a = {a["action_id"]: a for a in actions_a}
        assert acts_dict_a["accept_region"]["is_enabled"] is True

        # 2. Empty region_id falls back to selected B
        actions_empty = ctrl.getRegionContextActions("")
        assert len(actions_empty) == 9
        acts_dict_b = {a["action_id"]: a for a in actions_empty}
        assert acts_dict_b["accept_region"]["is_enabled"] is False  # B is already accepted

        # 3. Nonexistent target MUST NOT fall back to selected B
        actions_nonexistent = ctrl.getRegionContextActions("nonexistent-region-id")
        assert actions_nonexistent == []

        # 4. Target from another job rejected
        actions_other_job = ctrl.getRegionContextActions("99999999-9999-4999-8999-999999999999")
        assert actions_other_job == []

        # 5. Invalid / uninitialized active job (<= 0)
        ctrl._current_job_id = 0
        assert ctrl.getRegionContextActions(reg_a) == []
    finally:
        ctrl.shutdown()


def test_execute_region_action_exact_target_and_safety(qapp):
    """
    Verifies that executeRegionAction operates strictly on the targeted region,
    does not alter an unrelated selection, and rejects invalid/disabled actions.
    """
    ctrl, mock_service, _ = _setup_controller_with_regions()
    try:
        reg_a = "01234567-89ab-4cde-8f01-23456789abcd"
        reg_b = "11111111-2222-4333-8444-555555555555"
        ctrl._selected_region_id = reg_b

        selection_changed_events = []
        ctrl.selectionChanged.connect(lambda: selection_changed_events.append(1))

        # Execute accept on target A (selection is B)
        res = ctrl.executeRegionAction("accept_region", reg_a)
        assert res is True
        mock_service.accept_region.assert_called_once_with(reg_a)

        # Selection B must NOT be changed or signal emitted
        assert ctrl.selectedRegionId == reg_b
        assert len(selection_changed_events) == 0

        # Attempt to execute disabled action (accept on B, which is already accepted)
        mock_service.reset_mock()
        res_disabled = ctrl.executeRegionAction("accept_region", reg_b)
        assert res_disabled is False
        mock_service.accept_region.assert_not_called()

        # Nonexistent target rejected
        assert ctrl.executeRegionAction("accept_region", "nonexistent") is False

        # Wrong job rejected
        assert ctrl.executeRegionAction("accept_region", "99999999-9999-4999-8999-999999999999") is False

        # Job <= 0 rejected
        ctrl._current_job_id = -1
        assert ctrl.executeRegionAction("accept_region", reg_a) is False
    finally:
        ctrl.shutdown()


def test_parameterized_accept_delete_reset_commands(qapp):
    """
    Verifies parameterized acceptRegion, deleteRegion, and resetRegionToAi
    with explicit targets vs selection fallback, ensuring selection preservation.
    """
    reg_a = "01234567-89ab-4cde-8f01-23456789abcd"
    reg_b = "11111111-2222-4333-8444-555555555555"

    # --- 1. Delete non-selected region A when B is selected ---
    ctrl, mock_service, _ = _setup_controller_with_regions()
    try:
        ctrl._selected_region_id = reg_b
        res = ctrl.deleteRegion(reg_a)
        assert res is True
        mock_service.reject_region.assert_called_once_with(reg_a)
        # B remains selected!
        assert ctrl.selectedRegionId == reg_b

        # --- 2. Delete selected region B ---
        mock_service.reset_mock()
        res_b = ctrl.deleteRegion(reg_b)
        assert res_b is True
        mock_service.reject_region.assert_called_once_with(reg_b)
        # Selection cleared because target was selected!
        assert ctrl.selectedRegionId == ""
    finally:
        ctrl.shutdown()

    # --- 3. Backward compatible toolbar deleteSelectedRegion ---
    ctrl2, mock_service2, _ = _setup_controller_with_regions()
    try:
        ctrl2._selected_region_id = reg_a
        ctrl2.deleteSelectedRegion()
        mock_service2.reject_region.assert_called_once_with(reg_a)
        assert ctrl2.selectedRegionId == ""
    finally:
        ctrl2.shutdown()

    # --- 4. Accept parameterized and toolbar ---
    ctrl3, mock_service3, _ = _setup_controller_with_regions()
    try:
        ctrl3._selected_region_id = reg_b
        res_acc = ctrl3.acceptRegion(reg_a)
        assert res_acc is True
        mock_service3.accept_region.assert_called_once_with(reg_a)
        assert ctrl3.selectedRegionId == reg_b

        mock_service3.reset_mock()
        ctrl3.acceptSelectedRegion()
        mock_service3.accept_region.assert_called_once_with(reg_b)
    finally:
        ctrl3.shutdown()

    # --- 5. Reset parameterized and toolbar ---
    ctrl4, mock_service4, _ = _setup_controller_with_regions()
    try:
        ctrl4._selected_region_id = reg_b
        res_reset = ctrl4.resetRegionToAi(reg_a)
        assert res_reset is True
        mock_service4.reset_region_to_ai.assert_called_once_with(reg_a)
        assert ctrl4.selectedRegionId == reg_b

        mock_service4.reset_mock()
        ctrl4.resetSelectedRegionToAi()
        mock_service4.reset_region_to_ai.assert_called_once_with(reg_b)
    finally:
        ctrl4.shutdown()


def test_clipboard_commands(qapp):
    """
    Verifies copyRegionId, copyRegionBoundingBox, copyRegionPath, and copyRegionToken
    set correct canonical content to QGuiApplication.clipboard().
    """
    from core.domain.visual_token import parse_fields
    from uuid import UUID

    ctrl, _, _ = _setup_controller_with_regions()
    try:
        reg_a = "01234567-89ab-4cde-8f01-23456789abcd"

        # 1. Copy Region ID
        res_id = ctrl.copyRegionId(reg_a)
        assert res_id is True
        assert QGuiApplication.clipboard().text() == reg_a

        # 2. Copy Bounding Box [ymin, xmin, ymax, xmax]
        res_bbox = ctrl.copyRegionBoundingBox(reg_a)
        assert res_bbox is True
        assert QGuiApplication.clipboard().text() == "[100, 150, 300, 350]"

        # 3. Copy Region Path
        from core.entities.artifact import resolve_canonical_file_path
        res_path = ctrl.copyRegionPath(reg_a)
        assert res_path is True
        assert QGuiApplication.clipboard().text() == str(resolve_canonical_file_path("artifacts/crop_a.png"))

        # 4. Copy Region Token
        res_token = ctrl.copyRegionToken(reg_a)
        assert res_token is True
        token_str = QGuiApplication.clipboard().text()
        parsed_token = parse_fields(token_str)
        assert parsed_token.region_id == UUID(reg_a)
        assert parsed_token.uri == "artifacts/crop_a.png"
        assert parsed_token.occurrence_id is not None
        assert parsed_token.occurrence_id.version == 4

        # 5. Copy operations reject unsynced / nonexistent targets
        ctrl._active_regions[0]["sync_status"] = "dirty"
        assert ctrl.copyRegionPath(reg_a) is False
        assert ctrl.copyRegionToken(reg_a) is False
        assert ctrl.copyRegionPath("nonexistent") is False
        assert ctrl.copyRegionToken("nonexistent") is False
    finally:
        ctrl.shutdown()


def test_markdown_token_inserter_wiring_and_delegation(qapp):
    """
    Verifies that set_markdown_token_inserter registers callback and executeRegionAction
    correctly delegates markdown insertion without duplicating token logic.
    """
    ctrl, _, _ = _setup_controller_with_regions()
    try:
        reg_a = "01234567-89ab-4cde-8f01-23456789abcd"
        mock_inserter = MagicMock(return_value=True)

        # Before wiring inserter, insert_markdown is disabled
        actions_before = ctrl.getRegionContextActions(reg_a)
        act_before = next(a for a in actions_before if a["action_id"] == "insert_markdown")
        assert act_before["is_enabled"] is False

        # Wire inserter
        ctrl.set_markdown_token_inserter(mock_inserter)
        actions_after = ctrl.getRegionContextActions(reg_a)
        act_after = next(a for a in actions_after if a["action_id"] == "insert_markdown")
        assert act_after["is_enabled"] is True

        # Execute insert_markdown
        res = ctrl.executeRegionAction("insert_markdown", reg_a)
        assert res is True
        mock_inserter.assert_called_once_with(10, reg_a, "")
    finally:
        ctrl.shutdown()


def test_wire_review_workspace_sync_connects_markdown_inserter(qapp):
    """
    Verifies that app.py::wire_review_workspace_sync wires
    markdown_editor_controller.insertVisualRegionToken to document_viewer_controller.
    """
    from interfaces.desktop.app import wire_review_workspace_sync
    from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
    from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController

    mock_doc_service = MagicMock(spec=DocumentViewerService)
    doc_ctrl = DocumentViewerController(viewer_service=mock_doc_service)
    md_viewer = MagicMock(spec=MarkdownViewerController)
    md_editor = MagicMock(spec=MarkdownEditorController)

    try:
        wire_review_workspace_sync(
            document_viewer_controller=doc_ctrl,
            markdown_viewer_controller=md_viewer,
            markdown_editor_controller=md_editor,
        )
        assert doc_ctrl._markdown_token_inserter == md_editor.insertVisualRegionToken

        # Cleanly disconnect when editor is None
        wire_review_workspace_sync(
            document_viewer_controller=doc_ctrl,
            markdown_viewer_controller=md_viewer,
            markdown_editor_controller=None,
        )
        assert doc_ctrl._markdown_token_inserter is None
    finally:
        doc_ctrl.shutdown()


def test_wire_review_workspace_sync_enables_markdown_insertion_with_real_editor(qapp):
    """
    Verifies that wiring a real MarkdownEditorController correctly enables
    insert_markdown action when jobs match, and disables it when jobs differ.
    Protects against bound-method comparison bugs (editor.active_job_id vs int).
    """
    from interfaces.desktop.app import wire_review_workspace_sync
    from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController

    ctrl, _, _ = _setup_controller_with_regions()
    reg_a = "01234567-89ab-4cde-8f01-23456789abcd"

    real_editor = MarkdownEditorController(editor_service=MagicMock())
    real_editor._active_job_id = 10  # Matches ctrl._current_job_id = 10

    try:
        wire_review_workspace_sync(
            document_viewer_controller=ctrl,
            markdown_viewer_controller=MagicMock(),
            markdown_editor_controller=real_editor,
        )

        actions = ctrl.getRegionContextActions(reg_a)
        insert_act = next(a for a in actions if a["action_id"] == "insert_markdown")
        assert insert_act["is_enabled"] is True
        assert insert_act["disabled_reason"] == ""

        # Switch editor to another job
        real_editor._active_job_id = 99
        actions_mismatch = ctrl.getRegionContextActions(reg_a)
        insert_act_mismatch = next(a for a in actions_mismatch if a["action_id"] == "insert_markdown")
        assert insert_act_mismatch["is_enabled"] is False
        assert "Markdown editor is not available" in insert_act_mismatch["disabled_reason"]
    finally:
        ctrl.shutdown()


def test_target_resolution_case_insensitive_uuid(qapp):
    """
    Verifies that target region resolution accepts uppercase UUID string targets
    and executes without false rejection.
    """
    ctrl, mock_service, _ = _setup_controller_with_regions()
    reg_a = "01234567-89ab-4cde-8f01-23456789abcd"
    reg_a_upper = reg_a.upper()

    try:
        actions = ctrl.getRegionContextActions(reg_a_upper)
        assert len(actions) == 9

        res = ctrl.executeRegionAction("accept_region", reg_a_upper)
        assert res is True
        mock_service.accept_region.assert_called_once_with(reg_a)
    finally:
        ctrl.shutdown()


def test_copy_selected_token_slot(qapp):
    """Verifies that copySelectedToken slot copies token of currently selected region."""
    from core.domain.visual_token import parse_fields
    from uuid import UUID

    ctrl, _, _ = _setup_controller_with_regions()
    reg_a = "01234567-89ab-4cde-8f01-23456789abcd"

    try:
        ctrl._selected_region_id = reg_a
        res = ctrl.copySelectedToken()
        assert res is True
        parsed = parse_fields(QGuiApplication.clipboard().text())
        assert parsed.region_id == UUID(reg_a)
    finally:
        ctrl.shutdown()


def test_execute_region_action_inserter_exception_handled(qapp):
    """Verifies that an exception during markdown token insertion is handled gracefully."""
    ctrl, _, _ = _setup_controller_with_regions()
    reg_a = "01234567-89ab-4cde-8f01-23456789abcd"

    failing_inserter = MagicMock(side_effect=RuntimeError("Buffer corrupted"))
    ctrl.set_markdown_token_inserter(failing_inserter)

    try:
        res = ctrl.executeRegionAction("insert_markdown", reg_a)
        assert res is False
        assert "Buffer corrupted" in ctrl.errorMessage
    finally:
        ctrl.shutdown()
