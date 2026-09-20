# ============================================================
#  tests/unit/test_job_execution_publication.py
#  Phase 10E.3a: Pipeline 1 Initial Publication Migration (Ticket 10E.3a-08)
# ============================================================

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import (
    ArtifactType,
    StorageBackendType,
    ArtifactHandle,
    resolve_canonical_file_path,
)
from application.dto.job_dto import SubmitJobCommand
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.events.event_bus import InMemoryEventBus


class DummyDocProcessor:
    def get_page_count(self, file_bytes: bytes) -> int:
        return 2

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_num: int) -> bytes:
        return b"dummy_jpeg_bytes"

    def extract_and_crop_images(self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1):
        return markdown_text, []


class DummyAIExecutor:
    def __init__(self, responses=None):
        self.responses = responses or ["# Page 1 Header\nPage 1 body", "# Page 2 Header\nPage 2 body"]
        self.call_count = 0

    def execute_vision_with_fallback(self, chain, image_bytes, prompt, at_page=1, mime_type="image/jpeg", on_switch=None):
        resp = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return resp, chain[0] if chain else None

    def execute_text_with_fallback(self, chain, prompt, input_text=None, at_page=0, on_switch=None):
        return "# Refined Output\n" + (input_text or ""), chain[0] if chain else None


@pytest.fixture
def test_setup(tmp_path: Path):
    db_path = tmp_path / "test.db"
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    db_manager = SQLiteDatabaseManager(db_path)
    migration_runner = SQLiteMigrationRunner(db_manager)
    migration_runner.run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(db_manager)

    storage = LocalStorageAdapter(base_dir=str(artifacts_dir))
    doc_processor = DummyDocProcessor()
    ai_executor = DummyAIExecutor()
    event_bus = InMemoryEventBus()

    crop_staging = CropArtifactStagingService(base_dir=artifacts_dir)
    pub_service = DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=crop_staging,
    )

    exec_service = JobExecutionService(
        uow_factory=uow_factory,
        storage=storage,
        doc_processor=doc_processor,
        ai_executor=ai_executor,
        event_publisher=event_bus,
        document_publication_service=pub_service,
    )

    submission_service = JobSubmissionService(
        uow_factory=uow_factory,
        storage=storage,
        doc_processor=doc_processor,
        event_publisher=event_bus,
    )

    # Seed default prompt and api slot
    with uow_factory.create() as uow:
        p = Prompt(id=None, name="Default", text="Convert", prompt_type=PromptType.PIPELINE_1, is_default=True)
        saved_prompt = uow.prompts.save(p)
        slot = ApiSlot(
            id=None,
            label="Test Slot",
            provider="google",
            selected_model="gemini-1.5-flash",
            credential_ref=CredentialRef(identifier="test-cred", provider="google"),
        )
        saved_slot = uow.apis.save(slot)
        uow.commit()

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "pub_service": pub_service,
        "exec_service": exec_service,
        "submission_service": submission_service,
        "artifacts_dir": artifacts_dir,
        "slot": saved_slot,
        "prompt": saved_prompt,
    }


def test_pipeline1_publishes_initial_document(test_setup):
    """
    Verifies that Pipeline 1 completion delegates canonical document creation to
    DocumentPublicationService.publish_initial(), creating a version 1 record in
    document_versions and linking jobs.output_path to the published canonical file.
    """
    sub_service = test_setup["submission_service"]
    exec_service = test_setup["exec_service"]
    uow_factory = test_setup["uow_factory"]

    # Submit job
    job_dto = sub_service.submit_job(
        SubmitJobCommand(user_id=1, filename="test_doc.pdf", file_bytes=b"%PDF-1.4 dummy")
    )

    # Claim and execute
    with uow_factory.create() as uow:
        claimed_job = uow.jobs.claim_job(job_dto.id)
        uow.commit()

    completed_job = exec_service.execute_claimed_job(claimed_job.id)

    assert completed_job is not None
    assert completed_job.status == JobStatus.DONE
    assert completed_job.output_path is not None
    assert completed_job.output_path.startswith("file://")
    assert f"output_{completed_job.id}_v1.md" in completed_job.output_path

    # Verify document_versions entry
    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest(completed_job.id)
        assert latest is not None
        assert latest.version == 1
        assert latest.integrity_status == "VALID"
        assert latest.published_by == "PIPELINE_1_EXECUTION"
        assert latest.output_path == completed_job.output_path

        # Verify on-disk file checksum matches
        canonical_path = resolve_canonical_file_path(latest.output_path)
        assert canonical_path.is_file()
        file_sha = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
        assert latest.sha256 == file_sha

        # Verify watermark is decoupled from document version authority
        db_job = uow.jobs.get_by_id(completed_job.id)
        assert db_job.output_artifact_version_watermark == 0


def test_pipeline1_auto_pipeline2_receives_published_uri(test_setup):
    """
    Verifies that when auto_pipeline2 is enabled on the source job, the auto-scheduled
    Pipeline2Job receives the canonical published URI as its input_path.
    """
    sub_service = test_setup["submission_service"]
    exec_service = test_setup["exec_service"]
    uow_factory = test_setup["uow_factory"]

    # Submit job with auto_pipeline2 enabled
    job_dto = sub_service.submit_job(
        SubmitJobCommand(
            user_id=1,
            filename="auto_p2.pdf",
            file_bytes=b"%PDF-1.4 dummy",
            auto_pipeline2=True,
            pipeline2_prompt_id=1,
        )
    )

    with uow_factory.create() as uow:
        claimed_job = uow.jobs.claim_job(job_dto.id)
        uow.commit()

    completed_job = exec_service.execute_claimed_job(claimed_job.id)
    assert completed_job.status == JobStatus.DONE

    # Verify Pipeline 2 job was queued with the published canonical URI
    with uow_factory.create() as uow:
        p2_job = uow.pipeline2_jobs.get_next_pending()
        assert p2_job is not None
        assert p2_job.source_job_id == completed_job.id
        assert p2_job.input_path == completed_job.output_path
        assert f"output_{completed_job.id}_v1.md" in p2_job.input_path


def test_pipeline1_cancellation_aborts_before_publication(test_setup):
    """
    Verifies that if a job is cancelled before final publication,
    DocumentPublicationService.publish_initial() is never called and no
    canonical version is recorded.
    """
    sub_service = test_setup["submission_service"]
    exec_service = test_setup["exec_service"]
    uow_factory = test_setup["uow_factory"]
    pub_service = test_setup["pub_service"]

    job_dto = sub_service.submit_job(
        SubmitJobCommand(user_id=1, filename="cancel_doc.pdf", file_bytes=b"%PDF-1.4 dummy")
    )

    with uow_factory.create() as uow:
        claimed_job = uow.jobs.claim_job(job_dto.id)
        uow.jobs.request_cancellation(claimed_job.id)
        uow.commit()

    pub_spy = MagicMock(wraps=pub_service.publish_initial)
    exec_service.document_publication_service.publish_initial = pub_spy

    result_job = exec_service.execute_claimed_job(claimed_job.id)

    assert result_job.status == JobStatus.CANCELLED
    pub_spy.assert_not_called()

    # Verify no document_versions row created
    with uow_factory.create() as uow:
        assert uow.document_versions.get_latest(claimed_job.id) is None


def test_pipeline2_unmodified_direct_execution(test_setup):
    """
    Verifies that Pipeline 2 execution continues to execute independently via
    pipeline2_jobs and PIPELINE2_MARKDOWN artifacts without touching
    DocumentPublicationService or document_versions.
    """
    uow_factory = test_setup["uow_factory"]
    storage = test_setup["storage"]
    exec_service = test_setup["exec_service"]
    pub_service = test_setup["pub_service"]

    # Pre-create real source job and input artifact for Pipeline 2
    with uow_factory.create() as uow:
        src_job = Job(
            id=None,
            file_name="src.pdf",
            file_path="/in/src.pdf",
            status=JobStatus.DONE,
        )
        saved_src = uow.jobs.save(src_job)
        uow.commit()

    source_job_id = saved_src.id
    in_handle = storage.store(
        job_id=source_job_id,
        artifact_type=ArtifactType.OUTPUT_MARKDOWN,
        filename="p2_in.md",
        data=b"# Source Markdown Text",
        mime_type="text/markdown",
    )

    slot = test_setup["slot"]

    with uow_factory.create() as uow:
        p2_job = Pipeline2Job(
            id=None,
            source_job_id=source_job_id,
            prompt_id=1,
            status=JobStatus.PENDING,
            input_path=in_handle.uri,
            api_chain=[slot],
            current_api_index=0,
        )
        uow.pipeline2_jobs.save(p2_job)
        uow.commit()

    pub_spy = MagicMock(wraps=pub_service.publish_initial)
    exec_service.document_publication_service.publish_initial = pub_spy

    completed_p2 = exec_service.execute_next_pipeline2_job()

    assert completed_p2 is not None
    assert completed_p2.status == JobStatus.DONE
    assert completed_p2.output_path is not None
    pub_spy.assert_not_called()

    # Verify output artifact stored as PIPELINE2_MARKDOWN
    out_handle = ArtifactHandle(
        storage_backend=StorageBackendType.LOCAL_FS,
        uri=completed_p2.output_path,
        artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
        job_id=source_job_id,
        filename="pipeline2_final.md",
    )
    data = storage.retrieve(out_handle).decode("utf-8")
    assert "# Refined Output" in data

    # Verify document_versions has no entries for source_job_id
    with uow_factory.create() as uow:
        assert uow.document_versions.get_latest(source_job_id) is None
