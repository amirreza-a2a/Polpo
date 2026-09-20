# ============================================================
#  tests/unit/test_markdown_editor_publication.py
#  Phase 10E.3a: Markdown Editor Publication Migration (Ticket 10E.3a-09)
# ============================================================

import hashlib
from pathlib import Path

import pytest

from application.services.document_publication_service import DocumentPublicationService
from application.services.markdown_editor_service import MarkdownEditorService
from core.entities.artifact import ArtifactType, resolve_canonical_file_path
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    PublicationInProgressError,
    StaleDocumentVersionError,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter


@pytest.fixture
def pub_editor_env(tmp_path):
    db_path = tmp_path / "test_editor_pub.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorageAdapter(base_dir=str(artifacts_dir))

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        uow.commit()

    pub_service = DocumentPublicationService(uow_factory=uow_factory, artifacts_dir=artifacts_dir)
    editor_service = MarkdownEditorService(
        uow_factory=uow_factory,
        storage=storage,
        document_publication_service=pub_service,
    )

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "pub_service": pub_service,
        "editor_service": editor_service,
        "prompt_id": prompt_id,
        "artifacts_dir": artifacts_dir,
    }


def test_editor_commit_publishes_new_version(pub_editor_env):
    """
    Verifies that MarkdownEditorService.commit_source_text() advances version
    from base_version (1 -> 2) through DocumentPublicationService.
    """
    uow_factory = pub_editor_env["uow_factory"]
    pub_service = pub_editor_env["pub_service"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    # 1. Create a job and publish initial document v1
    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="paper.pdf",
                file_path="file:///paper.pdf",
                total_pages=2,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    initial_text = "# Paper Title\nInitial generated text."
    initial_rec = pub_service.publish_initial(
        job_id=job.id,
        markdown_text=initial_text,
        published_by="PIPELINE_1_EXECUTION",
    )
    assert initial_rec.version == 1

    # 2. Commit user-edited text from base_version=1
    edited_text = "# Paper Title\nUser edited content."
    new_version = editor_service.commit_source_text(
        job_id=job.id,
        raw_text=edited_text,
        base_version=1,
    )

    assert new_version == 2

    # 3. Verify active output_path updated on job
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job.id)
        assert f"output_{job.id}_v2.md" in updated_job.output_path
        # output_artifact_version_watermark must remain untouched
        assert updated_job.output_artifact_version_watermark == 0

    # 4. Verify disk file content
    disk_path = resolve_canonical_file_path(updated_job.output_path)
    assert disk_path.exists()
    assert disk_path.read_text(encoding="utf-8") == edited_text


def test_editor_commit_creates_document_versions_row(pub_editor_env):
    """
    Verifies that a valid commit_source_text() records a row in document_versions
    with VALID integrity status and real SHA-256.
    """
    uow_factory = pub_editor_env["uow_factory"]
    pub_service = pub_editor_env["pub_service"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="article.pdf",
                file_path="file:///article.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    pub_service.publish_initial(job_id=job.id, markdown_text="v1", published_by="PIPELINE_1")

    v2_content = "# Section 1\nHuman review edits"
    new_ver = editor_service.commit_source_text(job.id, v2_content, base_version=1)
    assert new_ver == 2

    expected_sha = hashlib.sha256(v2_content.encode("utf-8")).hexdigest()

    with uow_factory.create() as uow:
        v2_record = uow.document_versions.get_by_version(job.id, 2)
        assert v2_record is not None
        assert v2_record.version == 2
        assert v2_record.sha256 == expected_sha
        assert v2_record.integrity_status == "VALID"
        assert v2_record.published_by == "MARKDOWN_EDITOR"


def test_editor_commit_stale_version_rejected(pub_editor_env):
    """
    Verifies that StaleDocumentVersionError is raised when base_version does not
    match the current latest version in document_versions.
    """
    uow_factory = pub_editor_env["uow_factory"]
    pub_service = pub_editor_env["pub_service"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="stale.pdf",
                file_path="file:///stale.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    pub_service.publish_initial(job_id=job.id, markdown_text="v1", published_by="PIPELINE_1")
    # Advance to v2
    pub_service.publish_version(job_id=job.id, base_version=1, markdown_text="v2", published_by="OTHER_USER")

    # Attempting to save with base_version=1 must raise StaleDocumentVersionError (current is 2)
    with pytest.raises(StaleDocumentVersionError) as exc_info:
        editor_service.commit_source_text(job.id, "conflicting text", base_version=1)

    assert exc_info.value.base_version == 1
    assert exc_info.value.current_version == 2


def test_editor_commit_quarantined_rejected(pub_editor_env):
    """
    Verifies that CanonicalDocumentIntegrityError is raised when attempting to edit
    a job whose latest document version is QUARANTINED.
    """
    uow_factory = pub_editor_env["uow_factory"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="corrupt.pdf",
                file_path="file:///corrupt.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path="file:///missing/output_1_v1.md",
            )
        )
        # Manually register quarantined document version
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                job_id=job.id,
                version=1,
                output_path="file:///missing/output_1_v1.md",
                sha256=None,
                integrity_status="QUARANTINED",
                published_by="BACKFILL",
            )
        )
        uow.commit()

    with pytest.raises(CanonicalDocumentIntegrityError) as exc_info:
        editor_service.commit_source_text(job.id, "Attempted fix", base_version=1)

    assert "QUARANTINED" in str(exc_info.value)


def test_editor_commit_in_flight_intent_rejected(pub_editor_env):
    """
    Verifies that PublicationInProgressError is raised when another publish intent
    is active for the job.
    """
    uow_factory = pub_editor_env["uow_factory"]
    pub_service = pub_editor_env["pub_service"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="busy.pdf",
                file_path="file:///busy.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    pub_service.publish_initial(job_id=job.id, markdown_text="v1", published_by="PIPELINE_1")

    # Insert an active intent
    with uow_factory.create() as uow:
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="active-intent-123",
                job_id=job.id,
                base_version=1,
                target_version=2,
                output_filename=f"output_{job.id}_v2.md",
                output_sha256="abc",
                staged_artifacts_manifest="[]",
                status="PENDING",
            )
        )
        uow.commit()

    with pytest.raises(PublicationInProgressError):
        editor_service.commit_source_text(job.id, "Concurrent text", base_version=1)


def test_editor_load_source_text_backfills_legacy_job(pub_editor_env):
    """
    Verifies that a legacy job with an output_path but no document_versions row
    can be read without mutating document_versions, and is backfilled during
    startup bootstrap before editing.
    """
    uow_factory = pub_editor_env["uow_factory"]
    storage = pub_editor_env["storage"]
    editor_service = pub_editor_env["editor_service"]
    pub_service = pub_editor_env["pub_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="legacy.pdf",
                file_path="file:///legacy.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    content = "# Legacy Markdown\nExisting content."
    out_h = storage.store(job.id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job.id}_v1.md", content.encode("utf-8"))

    with uow_factory.create() as uow:
        job.output_path = out_h.uri
        uow.jobs.save(job)
        uow.commit()

    # 1. Load source text before bootstrap backfill: reads version without mutating document_versions
    text, version = editor_service.load_source_text(job.id)
    assert text == content
    assert version == 1

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest(job.id)
        assert latest is None, "load_source_text must not mutate database or run inline backfill"

    # 2. Application bootstrap backfill runs (as in DesktopAppContainer.initialize())
    pub_service.backfill_legacy_document_versions()

    # Verify document_versions now has a valid v1 record
    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest(job.id)
        assert latest is not None
        assert latest.version == 1
        assert latest.integrity_status == "VALID"

    # 3. Subsequent edit to v2 succeeds cleanly
    new_v = editor_service.commit_source_text(job.id, "# Legacy Markdown V2\nUpdated.", base_version=1)
    assert new_v == 2


def test_editor_service_does_not_trigger_global_backfill(pub_editor_env):
    """
    Verifies that MarkdownEditorService.load_source_text() and commit_source_text()
    never trigger backfill_legacy_document_versions() or full-table database scans.
    """
    from unittest.mock import patch

    uow_factory = pub_editor_env["uow_factory"]
    pub_service = pub_editor_env["pub_service"]
    editor_service = pub_editor_env["editor_service"]
    prompt_id = pub_editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="nobackfill.pdf",
                file_path="file:///nobackfill.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    pub_service.publish_initial(job.id, "# Title\nInitial text.", published_by="PIPELINE_1")

    with patch(
        "application.services.legacy_document_backfill.backfill_legacy_document_versions"
    ) as mock_backfill:
        text, version = editor_service.load_source_text(job.id)
        assert text == "# Title\nInitial text."
        assert version == 1
        assert mock_backfill.call_count == 0

        new_v = editor_service.commit_source_text(job.id, "# Title\nUpdated text.", base_version=1)
        assert new_v == 2
        assert mock_backfill.call_count == 0
