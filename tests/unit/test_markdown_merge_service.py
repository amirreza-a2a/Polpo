# ============================================================
#  tests/unit/test_markdown_merge_service.py
#  Unit tests for MarkdownMergeService (Phase 10F.5 Task 2)
# ============================================================

import os
from unittest.mock import MagicMock
import pytest

from application.dtos.merge_dto import MergeAnalysisResultDTO
from application.services.markdown_merge_service import MarkdownMergeService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.artifact import ArtifactType
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter


@pytest.fixture
def merge_env(tmp_path):
    db_path = tmp_path / "test_merge.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))
    parser = MarkdownItParser()
    viewer_service = MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=storage,
    )
    merge_service = MarkdownMergeService(
        uow_factory=uow_factory,
        storage=storage,
        viewer_service=viewer_service,
    )

    with uow_factory.create() as uow:
        p = uow.prompts.save(
            Prompt(
                id=None,
                name="P",
                text="T",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        )
        prompt_id = p.id
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path="",
                output_artifact_version_watermark=1,
            )
        )
        job_id = job.id
        uow.commit()

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "viewer_service": viewer_service,
        "merge_service": merge_service,
        "job_id": job_id,
    }


def test_t_merge_20_artifact_retrieval_loads_base_and_canonical(merge_env):
    """
    T-MERGE-20: Artifact retrieval loads base vN and canonical vM from storage.
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]

    base_content = "Line 1\nLine 2\nLine 3"
    canonical_content = "Line 1\nLine 2 Modified\nLine 3"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    local_text = "Line 1\nLine 2\nLine 3"
    result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=42,
    )

    assert isinstance(result, MergeAnalysisResultDTO)
    assert result.job_id == job_id
    assert result.merge_session_id == 42
    assert result.base_version == 1
    assert result.canonical_version == 2
    assert result.has_conflicts is False
    assert result.clean_text == canonical_content


def test_t_merge_21_missing_base_artifact_falls_back_cleanly(merge_env):
    """
    T-MERGE-21: Missing base artifact falls back to empty base string cleanly without crashing.
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]

    canonical_content = "Canonical line 1\nCanonical line 2"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    # Case A: base_version <= 0
    result_zero = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=0,
        local_text="Local text",
        canonical_version=2,
        merge_session_id=1,
    )
    assert isinstance(result_zero, MergeAnalysisResultDTO)
    assert result_zero.base_version == 0

    # Case B: base_version does not exist in storage
    result_missing = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=999,
        local_text="Local text",
        canonical_version=2,
        merge_session_id=2,
    )
    assert isinstance(result_missing, MergeAnalysisResultDTO)
    assert result_missing.base_version == 999


def test_t_merge_22_clean_auto_merge_returns_has_conflicts_false(merge_env):
    """
    T-MERGE-22: Clean auto-merge returns MergeAnalysisResultDTO with has_conflicts=False.
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]

    base_content = "Section 1\nSection 2\nSection 3"
    canonical_content = "Section 1\nSection 2\nSection 3 Canonical"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    local_text = "Section 1 Local\nSection 2\nSection 3"
    result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=10,
    )

    assert result.has_conflicts is False
    assert result.conflict_count == 0
    assert result.auto_merged_count > 0
    assert result.clean_text == "Section 1 Local\nSection 2\nSection 3 Canonical"


def test_t_merge_23_overlapping_edits_return_has_conflicts_true(merge_env):
    """
    T-MERGE-23: Overlapping edits return MergeAnalysisResultDTO with has_conflicts=True.
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]

    base_content = "Common Header\nOriginal Body\nCommon Footer"
    canonical_content = "Common Header\nRemote Body Edit\nCommon Footer"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    local_text = "Common Header\nLocal Body Edit\nCommon Footer"
    result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=11,
    )

    assert result.has_conflicts is True
    assert result.conflict_count == 1
    assert result.clean_text is None
    conflict_hunks = [h for h in result.hunks if h.hunk_type == "CONFLICT"]
    assert len(conflict_hunks) == 1
    hunk = conflict_hunks[0]
    assert hunk.base_text == "Original Body"
    assert hunk.local_text == "Local Body Edit"
    assert hunk.remote_text == "Remote Body Edit"


def test_t_merge_24_conflict_hunks_enriched_with_ast_context(merge_env):
    """
    T-MERGE-24: Conflict hunks enriched with AST context (node type, heading label, region ID).
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]

    base_content = "# Overview\n\nIntroductory paragraph.\n\n![[crop_1.jpg|region_id=reg_01]]"
    canonical_content = "# Overview Remote\n\nIntroductory paragraph remote.\n\n![[crop_1.jpg|region_id=reg_remote]]"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    local_text = "# Overview Local\n\nIntroductory paragraph local.\n\n![[crop_1.jpg|region_id=reg_local]]"
    result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=12,
    )

    assert result.has_conflicts is True
    assert result.conflict_count == 3
    conflict_hunks = [h for h in result.hunks if h.hunk_type == "CONFLICT"]
    assert len(conflict_hunks) == 3

    # Heading conflict label
    assert "Heading 1" in conflict_hunks[0].ast_label

    # Paragraph conflict label
    assert "Paragraph" in conflict_hunks[1].ast_label

    # Visual region conflict label
    assert "Visual Region" in conflict_hunks[2].ast_label or "reg_local" in conflict_hunks[2].ast_label


def test_t_merge_25_unparseable_ast_falls_back_to_line_based_label(merge_env):
    """
    T-MERGE-25: Missing/unparseable AST falls back to line-based label without failing merge.
    """
    storage = merge_env["storage"]
    job_id = merge_env["job_id"]
    uow_factory = merge_env["uow_factory"]

    # Mock viewer service whose render_text raises an unexpected parsing exception
    broken_viewer_service = MagicMock()
    broken_viewer_service.render_text.side_effect = RuntimeError("Parser crashed unexpectedly")

    failing_merge_service = MarkdownMergeService(
        uow_factory=uow_factory,
        storage=storage,
        viewer_service=broken_viewer_service,
    )

    base_content = "Line 1\nBase Content\nLine 3"
    canonical_content = "Line 1\nCanonical Content\nLine 3"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    local_text = "Line 1\nLocal Content\nLine 3"
    result = failing_merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=13,
    )

    assert result.has_conflicts is True
    conflict_hunks = [h for h in result.hunks if h.hunk_type == "CONFLICT"]
    assert len(conflict_hunks) == 1
    # Should fall back to "Line X" without failing the merge
    assert conflict_hunks[0].ast_label.startswith("Line ")


def test_t_merge_26_zero_write_invariant(merge_env):
    """
    T-MERGE-26: Synchronous execution does not mutate SQLite or storage (zero-write invariant).
    """
    storage = merge_env["storage"]
    merge_service = merge_env["merge_service"]
    job_id = merge_env["job_id"]
    uow_factory = merge_env["uow_factory"]

    base_content = "Base Header\nBase Body"
    canonical_content = "Base Header\nCanonical Body"
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v1.md", base_content.encode("utf-8"))
    storage.store(job_id, ArtifactType.OUTPUT_MARKDOWN, f"output_{job_id}_v2.md", canonical_content.encode("utf-8"))

    # Snapshot SQLite job state before analysis
    with uow_factory.create() as uow:
        job_before = uow.jobs.get_by_id(job_id)
        watermark_before = job_before.output_artifact_version_watermark
        updated_at_before = job_before.updated_at
        output_path_before = job_before.output_path

    # Count storage files before analysis
    job_dir = storage.base_dir / f"job_{job_id}"
    files_before = set(os.listdir(job_dir))

    # Spy on storage.store to assert it is never invoked
    storage.store = MagicMock(side_effect=AssertionError("storage.store() must not be called!"))

    local_text = "Base Header\nLocal Body"
    result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=local_text,
        canonical_version=2,
        merge_session_id=14,
    )

    assert isinstance(result, MergeAnalysisResultDTO)

    # Verify SQLite state is completely unchanged
    with uow_factory.create() as uow:
        job_after = uow.jobs.get_by_id(job_id)
        assert job_after.output_artifact_version_watermark == watermark_before
        assert job_after.updated_at == updated_at_before
        assert job_after.output_path == output_path_before

    # Verify storage files are completely unchanged
    files_after = set(os.listdir(job_dir))
    assert files_before == files_after
