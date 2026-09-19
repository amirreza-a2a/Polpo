# ============================================================
#  tests/unit/test_markdown_editor_service.py
#  Unit tests for MarkdownEditorService
# ============================================================

import hashlib
import pytest

from application.services.markdown_editor_service import MarkdownEditorService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import EntityNotFoundError, StaleDocumentVersionError
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter


@pytest.fixture
def editor_env(tmp_path):
    db_path = tmp_path / "test_editor.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        uow.commit()

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "prompt_id": prompt_id,
    }


def test_load_source_text_success(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    content = "# Chapter 1\nSome paragraph text."
    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v2.md", content.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_h.uri,
                output_artifact_version_watermark=2,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    text, version = service.load_source_text(job.id)

    assert text == content
    assert version == 2


def test_load_source_text_empty_when_no_output_path(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=None,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    text, version = service.load_source_text(job.id)

    assert text == ""
    assert version == 0


def test_load_source_text_empty_when_file_missing(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path="file:///nonexistent/output_1_v3.md",
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    text, version = service.load_source_text(job.id)

    assert text == ""
    assert version == 0


def test_load_source_text_job_not_found(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    with pytest.raises(EntityNotFoundError):
        service.load_source_text(9999)


def test_commit_source_text_success_first_version(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=None,
                output_artifact_version_watermark=0,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    new_ver = service.commit_source_text(job.id, "# Hello Initial", base_version=0)

    assert new_ver == 1
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job.id)
        latest_doc = uow.document_versions.get_latest(job.id)
        assert latest_doc is not None and latest_doc.version == 1
        assert f"output_{job.id}_v1.md" in updated_job.output_path

    handle = ArtifactHandle(StorageBackendType.LOCAL_FS, updated_job.output_path, ArtifactType.OUTPUT_MARKDOWN, job.id, f"output_{job.id}_v1.md")
    assert storage.exists(handle)
    assert storage.retrieve(handle).decode("utf-8") == "# Hello Initial"


def test_commit_source_text_success_advances_version_immutably(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    v2_content = "# Version 2 Content"
    out_v2 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v2.md", v2_content.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v2.uri,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    v3_content = "# Version 3 Content by Human"
    new_ver = service.commit_source_text(job.id, v3_content, base_version=2)

    assert new_ver == 3
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job.id)
        latest_doc = uow.document_versions.get_latest(job.id)
        assert latest_doc is not None and latest_doc.version == 3
        assert f"output_{job.id}_v3.md" in updated_job.output_path

    # Invariant: v2 file is untouched and still exists on disk
    assert storage.exists(out_v2)
    assert storage.retrieve(out_v2).decode("utf-8") == v2_content

    # Invariant: v3 file exists with new content
    v3_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, updated_job.output_path, ArtifactType.OUTPUT_MARKDOWN, job.id, f"output_{job.id}_v3.md")
    assert storage.exists(v3_handle)
    assert storage.retrieve(v3_handle).decode("utf-8") == v3_content


def test_commit_source_text_occ1_rejection(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    out_v3 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v3.md", b"# V3")

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v3.uri,
                output_artifact_version_watermark=3,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Base version is stale (2 vs current 3)
    with pytest.raises(StaleDocumentVersionError) as exc_info:
        service.commit_source_text(job.id, "# Conflict", base_version=2)

    assert exc_info.value.base_version == 2
    assert exc_info.value.current_version == 3

    # Database pointer unchanged
    with uow_factory.create() as uow:
        j = uow.jobs.get_by_id(job.id)
        assert j.output_path == out_v3.uri


def test_commit_source_text_occ2_rejection(editor_env):
    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    out_v3 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v3.md", b"# V3")

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v3.uri,
            )
        )
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                job_id=job.id,
                version=3,
                output_path=out_v3.uri,
                sha256=hashlib.sha256(b"# V3").hexdigest(),
                integrity_status="VALID",
                published_by="TEST",
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Simulate a concurrent writer advancing the document to version 5 in document_versions
    v5_path = f"file:///artifacts/job_{job.id}/output_{job.id}_v5.md"
    with uow_factory.create() as uow:
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                job_id=job.id,
                version=5,
                output_path=v5_path,
                sha256="fake_sha_v5",
                integrity_status="VALID",
                published_by="CONCURRENT_WRITER",
            )
        )
        uow.jobs.update_progress(job.id, 1, [], output_path=v5_path)
        uow.commit()

    with pytest.raises(StaleDocumentVersionError) as exc_info:
        service.commit_source_text(job.id, "# Conflict At Step 4", base_version=3)

    assert exc_info.value.base_version == 3
    assert exc_info.value.current_version == 5

    # Verify that V5 remains the active pointer in SQLite
    with uow_factory.create() as uow:
        j_final = uow.jobs.get_by_id(job.id)
        assert j_final.output_path == v5_path


def test_commit_source_text_failure_cleans_up_and_allows_retry(editor_env):
    import unittest.mock as mock

    uow_factory = editor_env["uow_factory"]
    storage = editor_env["storage"]
    prompt_id = editor_env["prompt_id"]

    out_v1 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# V1")

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v1.uri,
            )
        )
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                job_id=job.id,
                version=1,
                output_path=out_v1.uri,
                sha256=hashlib.sha256(b"# V1").hexdigest(),
                integrity_status="VALID",
                published_by="TEST",
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Simulate filesystem failure during staging in publication service
    with mock.patch("builtins.open", side_effect=OSError("Disk write failed")):
        with pytest.raises(OSError):
            service.commit_source_text(job.id, "# Attempt V2", base_version=1)

    # Document versions still at 1; output_path still at V1
    with uow_factory.create() as uow:
        j = uow.jobs.get_by_id(job.id)
        assert j.output_path == out_v1.uri
        latest = uow.document_versions.get_latest(job.id)
        assert latest.version == 1

    # Subsequent retry from base_version=1 successfully publishes V2!
    new_ver = service.commit_source_text(job.id, "# Successful Retry", base_version=1)
    assert new_ver == 2

    with uow_factory.create() as uow:
        j_after = uow.jobs.get_by_id(job.id)
        latest_after = uow.document_versions.get_latest(job.id)
        assert latest_after.version == 2
        assert f"output_{job.id}_v2.md" in j_after.output_path
