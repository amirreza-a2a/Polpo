# ============================================================
#  tests/integration/test_pipeline2_publication.py
#  P2-03: Pipeline 2 Artifact Lineage and Canonical Publication Tests
# ============================================================

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Optional, List

from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import (
    ArtifactType,
    StorageBackendType,
    ArtifactHandle,
)
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    DomainError,
    EntityNotFoundError,
    StaleDocumentVersionError,
)
from application.dto.job_dto import SubmitJobCommand
from application.events import (
    JobCompletedEvent,
    JobStateChangedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    Pipeline2CompletedEvent,
    Pipeline2StateChangedEvent,
    Pipeline2FailedEvent,
    Pipeline2CancelledEvent,
)
from interfaces.desktop.bridge import QtSignalEventBridge
from interfaces.desktop.qt_compat import QCoreApplication
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.markdown_editor_service import MarkdownEditorService
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


class FakeDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 1

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"fake_jpeg"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []

    def unify_markdown(self, markdown_text: str) -> str:
        return markdown_text.strip()


class FakeAIExecutor:
    def __init__(self, refinement_text: str = "# Refined Markdown Content\nPolished text."):
        self.refinement_text = refinement_text
        self.call_count = 0

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        return "# P1 Initial Markdown\nRaw transcription.", chain[0] if chain else None

    def execute_text_with_fallback(self, chain, prompt, input_text):
        self.call_count += 1
        return self.refinement_text, chain[0] if chain else None


class TestPipeline2PublicationAndLineage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_p2_pub.db"
        self.artifacts_dir = Path(self.temp_dir.name) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.storage = LocalStorageAdapter(base_dir=str(self.artifacts_dir))
        self.event_bus = InMemoryEventBus()
        self.events: List[object] = []
        self.event_bus.subscribe(object, lambda e: self.events.append(e))

        self.crop_staging = CropArtifactStagingService(base_dir=self.artifacts_dir)
        self.pub_service = DocumentPublicationService(
            uow_factory=self.uow_factory,
            artifacts_dir=self.artifacts_dir,
            staging_service=self.crop_staging,
        )

        self.doc_processor = FakeDocProcessor()
        self.ai_executor = FakeAIExecutor()

        self.exec_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
            document_publication_service=self.pub_service,
        )

        self.submission_service = JobSubmissionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            event_publisher=self.event_bus,
        )

        self.editor_service = MarkdownEditorService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            document_publication_service=self.pub_service,
        )

        # Seed prompt and API slot
        with self.uow_factory.create() as uow:
            uow.prompts.save(Prompt(id=None, name="P1", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True))
            uow.prompts.save(Prompt(id=None, name="P2", text="Refine", prompt_type=PromptType.PIPELINE_2, is_default=True))
            uow.apis.save(ApiSlot(id=None, provider="google", label="Slot 1", credential_ref=CredentialRef("cred_1", "google", "byok")))
            uow.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_completed_p1_job(self, auto_pipeline2: bool = True) -> int:
        dto = self.submission_service.submit_job(
            SubmitJobCommand(
                user_id=1,
                filename="doc.pdf",
                file_bytes=b"%PDF-1.4 dummy",
                auto_pipeline2=auto_pipeline2,
            )
        )
        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_job(dto.id)
            uow.commit()
        job = self.exec_service.execute_claimed_job(claimed.id)
        return job.id

    # -------------------------------------------------------------------------
    # 1. Derivative and Canonical Invariants
    # -------------------------------------------------------------------------

    def test_pipeline2_completion_stores_derivative_and_preserves_canonical(self):
        """
        Criteria 1 & 2: Successful Pipeline 2 completion stores PIPELINE2_MARKDOWN,
        records output_path in pipeline2_jobs, but DOES NOT modify canonical
        jobs.output_path or insert a new canonical document_version record.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            p1_job = uow.jobs.get_by_id(source_job_id)
            canonical_path_v1 = p1_job.output_path
            latest_v1 = uow.document_versions.get_latest(source_job_id)
            self.assertIsNotNone(latest_v1)
            self.assertEqual(latest_v1.version, 1)

            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            self.assertIsNotNone(claimed_p2)
            uow.commit()

        self.events.clear()
        finished_p2 = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        self.assertEqual(finished_p2.status, JobStatus.DONE)
        self.assertIsNotNone(finished_p2.output_path)
        self.assertIn("p2_output_", finished_p2.output_path)

        # Verify derivative artifact on disk
        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=finished_p2.output_path,
            artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
            job_id=source_job_id,
            filename="output.md",
        )
        data = self.storage.retrieve(handle).decode("utf-8")
        self.assertIn("Refined Markdown Content", data)

        # Invariant check: Parent canonical job unchanged
        with self.uow_factory.create() as uow:
            p1_after = uow.jobs.get_by_id(source_job_id)
            self.assertEqual(p1_after.output_path, canonical_path_v1)
            latest_doc_after = uow.document_versions.get_latest(source_job_id)
            self.assertEqual(latest_doc_after.version, 1)

    def test_pipeline2_retains_trustworthy_source_version_lineage(self):
        """
        Criterion 3: Pipeline 2 job persists explicit source_version lineage
        identifying the exact canonical document version it was derived from.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.assertIsNotNone(claimed_p2.source_version)
        self.assertEqual(claimed_p2.source_version, 1)

        finished_p2 = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)
        self.assertEqual(finished_p2.source_version, 1)

        with self.uow_factory.create() as uow:
            persisted_p2 = uow.pipeline2_jobs.get_by_id(finished_p2.id)
            self.assertEqual(persisted_p2.source_version, 1)

    def test_pipeline2_completion_events_do_not_clobber_canonical_events(self):
        """
        Criterion 4: Pipeline 2 completion emits Pipeline2CompletedEvent and
        NEVER emits JobCompletedEvent or contradictory JobStateChangedEvent
        for the source job.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.events.clear()
        self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        # Must NOT emit JobCompletedEvent (which would confuse consumers about canonical output)
        job_completed_events = [e for e in self.events if isinstance(e, JobCompletedEvent)]
        self.assertEqual(len(job_completed_events), 0, "Pipeline 2 must NEVER emit JobCompletedEvent!")

        # Must emit typed Pipeline2CompletedEvent
        p2_completed_events = [e for e in self.events if isinstance(e, Pipeline2CompletedEvent)]
        self.assertEqual(len(p2_completed_events), 1)
        self.assertEqual(p2_completed_events[0].source_job_id, source_job_id)
        self.assertEqual(p2_completed_events[0].source_version, 1)
        self.assertIn("p2_output_", p2_completed_events[0].output_artifact_uri)

    # -------------------------------------------------------------------------
    # 2. Successful Explicit Adoption
    # -------------------------------------------------------------------------

    def test_explicit_adoption_publishes_next_canonical_version_via_publication_service(self):
        """
        Criteria 5, 6, 7: Adopting a completed Pipeline 2 refinement explicitly
        publishes exactly one next canonical document version (v2) through
        DocumentPublicationService, updates jobs.output_path, preserves the
        derivative artifact, and maintains audit provenance.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)
        derivative_uri = p2_job.output_path

        # Explicit adoption action
        new_record = self.exec_service.adopt_pipeline2_refinement(p2_job.id)

        self.assertEqual(new_record.version, 2)
        self.assertEqual(new_record.job_id, source_job_id)
        self.assertEqual(new_record.integrity_status, "VALID")
        self.assertEqual(new_record.published_by, "PIPELINE_2_ADOPTION")

        # Canonical pointer on job is updated to v2
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(source_job_id)
            self.assertEqual(job.output_path, new_record.output_path)
            self.assertIn("output_", job.output_path)
            self.assertIn("_v2.md", job.output_path)

            latest_doc = uow.document_versions.get_latest(source_job_id)
            self.assertEqual(latest_doc.version, 2)

        # Derivative artifact remains intact and retrievable
        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=derivative_uri,
            artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
            job_id=source_job_id,
            filename="output.md",
        )
        der_bytes = self.storage.retrieve(handle)
        self.assertTrue(len(der_bytes) > 0)

    def test_repeated_adoption_rejected_by_occ(self):
        """
        Criterion 8: Repeated adoption attempts with a stale base version
        are rejected by OCC (StaleDocumentVersionError).
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        # First adoption succeeds (v1 -> v2)
        self.exec_service.adopt_pipeline2_refinement(p2_job.id)

        # Second adoption attempt must be rejected because base_version (1) is now stale
        with self.assertRaises(StaleDocumentVersionError):
            self.exec_service.adopt_pipeline2_refinement(p2_job.id)

    # -------------------------------------------------------------------------
    # 3. OCC and Conflict Preservation
    # -------------------------------------------------------------------------

    def test_concurrent_human_edit_blocks_refinement_adoption_under_occ(self):
        """
        Criterion 9: If a human edit creates a newer canonical version (v2) after
        Pipeline 2 began from v1, adopting the v1 refinement fails with
        StaleDocumentVersionError and leaves the newer canonical version untouched.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)
        self.assertEqual(p2_job.source_version, 1)

        # Human edits the document and commits v2
        human_text = "# Canonical v2\nHuman edit made in ReviewWorkspace."
        v2_record = self.pub_service.publish_version(
            job_id=source_job_id,
            base_version=1,
            markdown_text=human_text,
            published_by="MARKDOWN_EDITOR",
        )
        self.assertEqual(v2_record.version, 2)

        # Attempt to adopt the v1 refinement must be rejected
        with self.assertRaises(StaleDocumentVersionError) as ctx:
            self.exec_service.adopt_pipeline2_refinement(p2_job.id)

        self.assertEqual(ctx.exception.job_id, source_job_id)
        self.assertEqual(ctx.exception.base_version, 1)
        self.assertEqual(ctx.exception.current_version, 2)

        # Verify that canonical v2 content is preserved untouched
        with self.uow_factory.create() as uow:
            latest = uow.document_versions.get_latest(source_job_id)
            self.assertEqual(latest.version, 2)
            job = uow.jobs.get_by_id(source_job_id)
            self.assertEqual(job.output_path, v2_record.output_path)

    def test_missing_or_untrustworthy_lineage_rejects_adoption(self):
        """
        Criteria 11 & 16: Adoption is rejected safely when lineage is missing,
        invalid, or inconsistent with stored records. Legacy records without
        lineage do not acquire fabricated lineage.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=False)

        # Simulate legacy Pipeline 2 job without source_version
        with self.uow_factory.create() as uow:
            legacy_p2 = Pipeline2Job(
                id=None,
                source_job_id=source_job_id,
                status=JobStatus.DONE,
                input_path="file:///fake_input.md",
                output_path="file:///fake_output.md",
                source_version=None,  # Legacy: missing lineage
            )
            saved_legacy = uow.pipeline2_jobs.save(legacy_p2)
            uow.commit()

        with self.assertRaises(DomainError) as ctx:
            self.exec_service.adopt_pipeline2_refinement(saved_legacy.id)
        self.assertIn("lineage", str(ctx.exception).lower())

    def test_adoption_validates_input_path_consistency(self):
        """
        Criterion 11: If recorded source_version points to an input path that
        contradicts canonical version history, adoption is rejected.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=False)

        # Record with source_version=1 but input_path referencing nonexistent v99
        with self.uow_factory.create() as uow:
            corrupt_p2 = Pipeline2Job(
                id=None,
                source_job_id=source_job_id,
                status=JobStatus.DONE,
                input_path="file:///artifacts/job_1/output_1_v99.md",
                output_path="file:///artifacts/job_1/p2_output_99.md",
                source_version=1,
            )
            saved = uow.pipeline2_jobs.save(corrupt_p2)
            uow.commit()

        with self.assertRaises((CanonicalDocumentIntegrityError, DomainError)):
            self.exec_service.adopt_pipeline2_refinement(saved.id)

    # -------------------------------------------------------------------------
    # 4. Controller & Editor Protection
    # -------------------------------------------------------------------------

    def test_editor_controller_blocks_adoption_when_dirty(self):
        """
        Criterion 10: Unsaved editor changes block adoption to protect human work.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        controller = MarkdownEditorController(
            editor_service=self.editor_service,
        )
        controller.load_source_sync(source_job_id)

        # Editor is clean initially; refinement should be detectable
        controller.checkForPipeline2Refinement()
        self.assertTrue(controller.hasPipeline2Refinement)
        self.assertTrue(controller.canAdoptPipeline2)

        # User modifies the editor buffer (makes it dirty)
        controller.setSourceText("# Dirty unsaved user edits")
        self.assertTrue(controller.isDirty)
        self.assertFalse(controller.canAdoptPipeline2, "Dirty buffer MUST block adoption!")

        # Attempting adoption while dirty returns False and sets actionable error
        adopted = controller.adoptPipeline2Refinement()
        self.assertFalse(adopted)
        self.assertIn("unsaved", controller.errorMessage.lower())

        # Buffer must remain intact
        self.assertEqual(controller.sourceText, "# Dirty unsaved user edits")

    def test_editor_controller_successful_adoption_updates_metrics_and_state(self):
        """
        Criterion 5 & 10: Successful adoption updates activeVersion, sourceText,
        character/word metrics, clears dirty state, and emits saved signal.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        controller = MarkdownEditorController(editor_service=self.editor_service)
        controller.load_source_sync(source_job_id)
        controller.checkForPipeline2Refinement()

        self.assertTrue(controller.hasPipeline2Refinement)
        self.assertTrue(controller.canAdoptPipeline2)
        self.assertIn("Refined Markdown Content", controller.pipeline2RefinementText)

        saved_versions: List[int] = []
        controller.saved.connect(lambda v: saved_versions.append(v))

        # Adopt refinement
        adopted = controller.adoptPipeline2Refinement()
        self.assertTrue(adopted)
        self.assertEqual(controller.activeVersion, 2)
        self.assertFalse(controller.isDirty)
        self.assertIn("Refined Markdown Content", controller.sourceText)
        self.assertEqual(controller.characterCount, len(controller.sourceText))
        self.assertTrue(controller.wordCount > 0)
        self.assertFalse(controller.hasPipeline2Refinement)
        self.assertFalse(controller.canAdoptPipeline2)
        self.assertEqual(saved_versions, [2])
        self.assertIn("v2", controller.adoptionStatusMessage)

    def test_editor_controller_stale_lineage_explains_blocked_adoption(self):
        """
        Criterion 9 & 14: When refinement base_version (v1) does not match the active
        document version (v2), the UI status message clearly explains that adoption
        is blocked by stale lineage.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)
        self.assertEqual(p2_job.source_version, 1)

        # Human edit publishes canonical v2
        self.pub_service.publish_version(
            job_id=source_job_id,
            base_version=1,
            markdown_text="# Human Canonical v2",
            published_by="HUMAN_EDITOR",
        )

        controller = MarkdownEditorController(editor_service=self.editor_service)
        controller.load_source_sync(source_job_id)
        self.assertEqual(controller.activeVersion, 2)

        controller.checkForPipeline2Refinement()
        self.assertTrue(controller.hasPipeline2Refinement)
        self.assertFalse(controller.canAdoptPipeline2, "Stale lineage MUST block adoption!")
        self.assertIn("blocked", controller.adoptionStatusMessage.lower())
        self.assertIn("v1", controller.adoptionStatusMessage)
        self.assertIn("v2", controller.adoptionStatusMessage)

        # Attempting adoption fails with lineage conflict error
        adopted = controller.adoptPipeline2Refinement()
        self.assertFalse(adopted)
        self.assertIn("lineage conflict", controller.errorMessage.lower())

    def test_editor_controller_missing_lineage_explains_blocked_adoption(self):
        """
        Criterion 11 & 16: When refinement lacks source-version lineage (legacy record),
        the UI status message indicates adoption is unavailable.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=False)

        with self.uow_factory.create() as uow:
            legacy_p2 = Pipeline2Job(
                id=None,
                source_job_id=source_job_id,
                status=JobStatus.DONE,
                input_path="file:///in.md",
                output_path="file:///out.md",
                source_version=None,
            )
            saved = uow.pipeline2_jobs.save(legacy_p2)
            uow.commit()

        controller = MarkdownEditorController(editor_service=self.editor_service)
        controller.load_source_sync(source_job_id)
        controller.checkForPipeline2Refinement()

        self.assertTrue(controller.hasPipeline2Refinement)
        self.assertFalse(controller.canAdoptPipeline2)
        self.assertIn("lacks source-version lineage", controller.adoptionStatusMessage)

        adopted = controller.adoptPipeline2Refinement()
        self.assertFalse(adopted)
        self.assertIn("lineage", controller.errorMessage.lower())

    def test_event_isolation_pipeline2_events_not_dispatched_as_primary_job_events(self):
        """
        Criteria 4: Pipeline 2 completion and lifecycle events are isolated:
        QtSignalEventBridge emits pipeline2_completed_received and NEVER emits
        job_state_changed_received, job_failed_received, or job_cancelled_received
        for primary job queue models.
        """
        app = QCoreApplication.instance() or QCoreApplication([])
        bridge = QtSignalEventBridge(event_bus=self.event_bus)

        state_changed = []
        completed = []
        failed = []
        cancelled = []
        p2_completed = []

        bridge.job_state_changed_received.connect(lambda jid, o, n: state_changed.append((jid, o, n)))
        bridge.job_completed_received.connect(lambda jid, uri: completed.append((jid, uri)))
        bridge.job_failed_received.connect(lambda jid, msg, r: failed.append((jid, msg, r)))
        bridge.job_cancelled_received.connect(lambda jid: cancelled.append(jid))
        bridge.pipeline2_completed_received.connect(lambda pid, sid, uri, ver: p2_completed.append((pid, sid, uri, ver)))

        # Publish Pipeline 2 events
        self.event_bus.publish(
            Pipeline2StateChangedEvent(
                job_id=99,
                old_status=JobStatus.PENDING,
                new_status=JobStatus.PROCESSING,
                source_job_id=1,
            )
        )
        self.event_bus.publish(
            Pipeline2CompletedEvent(
                job_id=99,
                source_job_id=1,
                output_artifact_uri="file:///artifacts/p2_99.md",
                source_version=1,
            )
        )
        self.event_bus.publish(
            Pipeline2FailedEvent(
                job_id=99,
                error_message="AI failed",
                is_retryable=False,
                source_job_id=1,
            )
        )
        self.event_bus.publish(
            Pipeline2CancelledEvent(
                job_id=99,
                source_job_id=1,
            )
        )
        app.processEvents()

        # Invariant verification: primary job signals MUST be empty!
        self.assertEqual(state_changed, [], "Pipeline 2 must NOT trigger job_state_changed_received!")
        self.assertEqual(completed, [], "Pipeline 2 must NOT trigger job_completed_received!")
        self.assertEqual(failed, [], "Pipeline 2 must NOT trigger job_failed_received!")
        self.assertEqual(cancelled, [], "Pipeline 2 must NOT trigger job_cancelled_received!")

        # Dedicated Pipeline 2 signal was delivered
        self.assertEqual(len(p2_completed), 1)
        self.assertEqual(p2_completed[0], (99, 1, "file:///artifacts/p2_99.md", 1))

        bridge.detach()

    def test_adopt_refinement_synchronizes_controller_when_canonical_version_advanced_concurrently(self):
        """
        Criteria 9 & 14: When a user attempts to adopt a refinement while the canonical document
        has advanced in the database (e.g. from v1 to v2) without the editor having refreshed yet,
        OCC rejects publication, and the controller immediately synchronizes its active version
        to v2, disallowing further adoption and updating the UI status message.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        p2_job = self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)
        self.assertEqual(p2_job.source_version, 1)

        controller = MarkdownEditorController(editor_service=self.editor_service)
        controller.load_source_sync(source_job_id)
        controller.checkForPipeline2Refinement()

        self.assertEqual(controller.activeVersion, 1)
        self.assertEqual(controller.pipeline2SourceVersion, 1)
        self.assertTrue(controller.canAdoptPipeline2)

        # External write advances document to v2 directly
        self.pub_service.publish_version(
            job_id=source_job_id,
            base_version=1,
            markdown_text="# Canonical v2 published concurrently",
            published_by="CONCURRENT_AGENT",
        )

        # Controller still thinks it's v1. Clicking adopt triggers OCC rejection:
        adopted = controller.adoptPipeline2Refinement()
        self.assertFalse(adopted)
        self.assertIn("lineage conflict", controller.errorMessage.lower())

        # Wait briefly for notifyCanonicalDocumentAdvance async reload
        import time
        for _ in range(50):
            QCoreApplication.processEvents()
            if controller.activeVersion == 2:
                break
            time.sleep(0.01)

        self.assertEqual(controller.activeVersion, 2)
        self.assertFalse(controller.canAdoptPipeline2)
        self.assertIn("blocked", controller.adoptionStatusMessage.lower())
        self.assertIn("v1", controller.adoptionStatusMessage)
        self.assertIn("v2", controller.adoptionStatusMessage)

        controller.shutdown()

    def test_controller_clear_and_switch_job_resets_pipeline2_state(self):
        """
        Verifies that clear() and loading a new job immediately reset Pipeline 2
        refinement state, preventing state leakage across jobs.
        """
        source_job_id = self._create_completed_p1_job(auto_pipeline2=True)

        with self.uow_factory.create() as uow:
            claimed_p2 = uow.pipeline2_jobs.claim_next_pending()
            uow.commit()

        self.exec_service.execute_claimed_pipeline2_job(claimed_p2.id)

        controller = MarkdownEditorController(editor_service=self.editor_service)
        controller.load_source_sync(source_job_id)
        controller.checkForPipeline2Refinement()

        self.assertTrue(controller.hasPipeline2Refinement)
        self.assertTrue(controller.pipeline2JobId > 0)
        self.assertTrue(len(controller.pipeline2RefinementText) > 0)

        # Calling clear() must reset pipeline 2 properties
        controller.clear()
        self.assertFalse(controller.hasPipeline2Refinement)
        self.assertEqual(controller.pipeline2JobId, 0)
        self.assertEqual(controller.pipeline2SourceVersion, 0)
        self.assertEqual(controller.pipeline2RefinementText, "")
        self.assertEqual(controller.adoptionStatusMessage, "")

        # Reloading job restores state
        controller.load_source_sync(source_job_id)
        controller.checkForPipeline2Refinement()
        self.assertTrue(controller.hasPipeline2Refinement)

        # Calling loadSource for another job immediately clears refinement state
        controller.loadSource(9999)
        self.assertFalse(controller.hasPipeline2Refinement)
        self.assertEqual(controller.pipeline2JobId, 0)

        controller.shutdown()

    def test_pipeline2_completed_event_ignored_when_active_job_is_zero(self):
        """
        Verifies that Pipeline2Completed events with source_job_id=0 do not attach
        refinement state when no active job is loaded.
        """
        controller = MarkdownEditorController(editor_service=self.editor_service)
        self.assertEqual(controller.activeJobId, 0)

        controller.onPipeline2Completed(p2_job_id=1, source_job_id=0, output_uri="file:///dummy.md", source_version=1)
        self.assertFalse(controller.hasPipeline2Refinement)
        self.assertEqual(controller.pipeline2JobId, 0)

        controller.shutdown()


if __name__ == "__main__":
    unittest.main()
