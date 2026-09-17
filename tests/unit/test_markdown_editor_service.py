# ============================================================
#  tests/unit/test_markdown_editor_service.py
#  Unit tests for MarkdownEditorService
# ============================================================

import pytest
from unittest.mock import MagicMock

from application.services.markdown_editor_service import MarkdownEditorService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
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
        assert updated_job.output_artifact_version_watermark == 1
        assert "output_1_v1.md" in updated_job.output_path

    handle = ArtifactHandle(StorageBackendType.LOCAL_FS, updated_job.output_path, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v1.md")
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
                output_artifact_version_watermark=2,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)
    v3_content = "# Version 3 Content by Human"
    new_ver = service.commit_source_text(job.id, v3_content, base_version=2)

    assert new_ver == 3
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job.id)
        assert updated_job.output_artifact_version_watermark == 3
        assert "output_1_v3.md" in updated_job.output_path

    # Invariant: v2 file is untouched and still exists on disk
    assert storage.exists(out_v2)
    assert storage.retrieve(out_v2).decode("utf-8") == v2_content

    # Invariant: v3 file exists with new content
    v3_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, updated_job.output_path, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v3.md")
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
        assert j.output_artifact_version_watermark == 3


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
                output_artifact_version_watermark=3,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Intercept storage.store to simulate a concurrent writer advancing the document before step 4
    orig_store = storage.store

    def side_effect_store(*args, **kwargs):
        res = orig_store(*args, **kwargs)
        with uow_factory.create() as uow:
            j = uow.jobs.get_by_id(job.id)
            j.output_path = "file:///artifacts/output_1_v5.md"
            j.output_artifact_version_watermark = 5
            uow.jobs.save(j)
            uow.commit()
        return res

    storage.store = MagicMock(side_effect=side_effect_store)

    with pytest.raises(StaleDocumentVersionError) as exc_info:
        service.commit_source_text(job.id, "# Conflict At Step 4", base_version=3)

    assert exc_info.value.base_version == 3
    assert exc_info.value.current_version == 5

    # Verify that V5 remains the active pointer in SQLite
    with uow_factory.create() as uow:
        j_final = uow.jobs.get_by_id(job.id)
        assert j_final.output_path == "file:///artifacts/output_1_v5.md"


def test_commit_source_text_monotonic_watermark_burned_on_failure(editor_env):
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
                output_artifact_version_watermark=1,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Simulate storage failure during staging
    def failing_store(*args, **kwargs):
        raise OSError("Disk write failed during staging")

    storage.store = MagicMock(side_effect=failing_store)

    with pytest.raises(OSError):
        service.commit_source_text(job.id, "# Attempt V2", base_version=1)

    # Watermark was burned to 2
    with uow_factory.create() as uow:
        j = uow.jobs.get_by_id(job.id)
        assert j.output_artifact_version_watermark == 2
        # Output path is still V1
        assert j.output_path == out_v1.uri

    # Restore storage adapter
    storage.store = LocalStorageAdapter(base_dir=storage.base_dir).store

    # Subsequent retry from base_version=1 must be rejected if base_version advanced, or allocate V3
    # In this case base_version is still 1 (current is still 1), so next allocated version must be watermark + 1 = 3!
    new_ver = service.commit_source_text(job.id, "# Successful Retry", base_version=1)
    assert new_ver == 3

    with uow_factory.create() as uow:
        j_after = uow.jobs.get_by_id(job.id)
        assert j_after.output_artifact_version_watermark == 3
        assert "output_1_v3.md" in j_after.output_path
