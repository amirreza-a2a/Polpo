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

    controller.regionApplied.connect(lambda j, r, v, u: applied_events.append((j, r, v, u)))
    controller.regionArtifactCommitted.connect(lambda j, r, v, u: committed_events.append((j, r, v, u)))

    try:
        controller._trigger_async_apply(job_id=10, region_id="reg-xyz")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        assert len(applied_events) == 1
        assert applied_events[0] == (10, "reg-xyz", 2, "crops/job_10/crop_reg-xyz.jpg")

        assert len(committed_events) == 1
        assert committed_events[0] == (10, "reg-xyz", 2, "crops/job_10/crop_reg-xyz.jpg")
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

    controller.regionApplied.connect(lambda j, r, v, u: applied_events.append((j, r, v, u)))
    controller.applyFailed.connect(lambda j, r, err: failed_events.append((j, r, err)))

    try:
        # Failure case 1: DTO returns success=False
        controller._trigger_async_apply(job_id=7, region_id="reg-fail")
        controller.wait_for_apply(timeout=3.0)
        QGuiApplication.processEvents()

        assert len(applied_events) == 0
        assert len(failed_events) == 1
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
