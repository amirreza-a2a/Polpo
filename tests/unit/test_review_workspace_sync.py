# ============================================================
#  tests/unit/test_review_workspace_sync.py
#  Targeted Unit Tests for Review Workspace Synchronization Wiring (TICK-P13A)
# ============================================================

import unittest
from unittest.mock import MagicMock

from interfaces.desktop.app import wire_review_workspace_sync
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from application.services.document_viewer_service import DocumentViewerService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from interfaces.desktop.qt_compat import QGuiApplication


class TestReviewWorkspaceSyncWiring(unittest.TestCase):
    """
    Verifies presentation-level direct Qt signal wiring between DocumentViewerController
    and MarkdownEditorController via wire_review_workspace_sync().
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.mock_viewer_service = MagicMock(spec=DocumentViewerService)
        self.mock_pub_service = MagicMock(spec=VisualRegionPublicationService)

        self.doc_viewer = DocumentViewerController(
            viewer_service=self.mock_viewer_service,
            region_publication_service=self.mock_pub_service,
        )
        self.md_viewer = MagicMock(spec=MarkdownViewerController)
        self.md_editor = MagicMock(spec=MarkdownEditorController)

        # Wire the workspace sync
        self.coordinator = wire_review_workspace_sync(
            document_viewer_controller=self.doc_viewer,
            markdown_viewer_controller=self.md_viewer,
            markdown_editor_controller=self.md_editor,
        )

    def tearDown(self):
        self.doc_viewer.shutdown()

    def test_canonical_advance_notifies_matching_active_editor(self):
        """
        When canonicalDocumentPublished(job_id, document_version) is emitted,
        markdown_editor_controller.notifyCanonicalDocumentAdvance is invoked
        if activeJobId matches job_id.
        """
        self.md_editor.activeJobId = 42

        # Emit canonical document published signal for job 42 with version 3
        self.doc_viewer.canonicalDocumentPublished.emit(42, 3)

        self.md_editor.notifyCanonicalDocumentAdvance.assert_called_once_with(3)

    def test_canonical_advance_ignores_mismatched_active_job(self):
        """
        When canonicalDocumentPublished(job_id, document_version) is emitted,
        markdown_editor_controller is NOT notified if activeJobId belongs to a different job.
        """
        self.md_editor.activeJobId = 99  # Different job

        self.doc_viewer.canonicalDocumentPublished.emit(42, 3)

        self.md_editor.notifyCanonicalDocumentAdvance.assert_not_called()

    def test_preview_inactivity_does_not_suppress_notification(self):
        """
        Canonical document advancement notification reaches editor regardless of
        preview state (e.g. preview unloaded, inactive, or activeVersion unchanged).
        """
        self.md_editor.activeJobId = 15
        self.md_viewer.activeVersion = 0
        self.md_viewer.activeJobId = 0

        self.doc_viewer.canonicalDocumentPublished.emit(15, 7)

        self.md_editor.notifyCanonicalDocumentAdvance.assert_called_once_with(7)

    def test_region_artifact_committed_does_not_trigger_canonical_advance(self):
        """
        Strict separation: regionArtifactCommitted only updates viewer artifact,
        and never triggers notifyCanonicalDocumentAdvance.
        """
        self.md_editor.activeJobId = 10
        self.md_viewer.activeJobId = 10

        self.doc_viewer.regionArtifactCommitted.emit(10, "reg-1", 2, "crops/reg-1.png")

        self.md_editor.notifyCanonicalDocumentAdvance.assert_not_called()
        self.md_viewer.updateRegionArtifact.assert_called_once_with("reg-1", "crops/reg-1.png", 2)

    def test_wire_without_editor_controller_does_not_fail(self):
        """
        wire_review_workspace_sync handles markdown_editor_controller=None cleanly without error.
        """
        coord = wire_review_workspace_sync(
            document_viewer_controller=self.doc_viewer,
            markdown_viewer_controller=self.md_viewer,
            markdown_editor_controller=None,
        )
        self.assertIsNone(coord)
        # Emitting signal should not raise
        self.doc_viewer.canonicalDocumentPublished.emit(42, 3)

    def test_canonical_advance_ignores_unloaded_editor_when_active_job_id_is_zero(self):
        """
        When editor activeJobId is 0 (no job loaded), canonicalDocumentPublished
        for job 0 or any other job must NEVER trigger notifyCanonicalDocumentAdvance.
        """
        self.md_editor.activeJobId = 0
        self.doc_viewer.canonicalDocumentPublished.emit(0, 5)
        self.md_editor.notifyCanonicalDocumentAdvance.assert_not_called()
