# ============================================================
#  tests/unit/test_phase10f_apply_review_source.py
#  Unit tests for ApplyReviewService Canonical Source & Snapshot OCC
# ============================================================

import uuid
import pytest
from unittest.mock import MagicMock

from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from application.services.apply_review_service import ApplyReviewService
from application.services.markdown_editor_service import MarkdownEditorService


def _create_sample_pdf_bytes():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 0), fill=(1, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def apply_env(tmp_path):
    db_path = tmp_path / "test.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))
    doc_proc = PyMuPDFDocumentProcessor()

    pdf_bytes = _create_sample_pdf_bytes()
    pdf_handle = storage.store(job_id=1, artifact_type=ArtifactType.SOURCE_PDF, filename="source.pdf", data=pdf_bytes)

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        uow.commit()

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "doc_proc": doc_proc,
        "pdf_handle": pdf_handle,
        "prompt_id": prompt_id,
    }


def test_apply_reviews_preserves_user_authored_markdown(apply_env):
    uow_factory = apply_env["uow_factory"]
    storage = apply_env["storage"]
    doc_proc = apply_env["doc_proc"]
    pdf_handle = apply_env["pdf_handle"]
    prompt_id = apply_env["prompt_id"]

    rid = uuid.uuid4().hex
    user_text = (
        "# Custom Title\n\n"
        "Human authored narrative line 1.\n"
        "    Indented code or spacing preserved.\n\n"
        f"![[crop_1_{rid}_v1.jpg|region_id={rid}]]\n\n"
        "Human authored narrative line 2."
    )
    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", user_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_h.uri,
                output_artifact_version_watermark=1,
            )
        )
        uow.visual_regions.save(
            VisualRegion(
                id=None,
                region_id=rid,
                job_id=job.id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                review_status=ReviewStatus.UNREVIEWED,
                sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
                detected_bbox=BoundingBox(100, 100, 500, 500),
                active_artifact_version=1,
                artifact_version_watermark=1,
            )
        )
        uow.commit()

    service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    result = service.apply_reviews(job_id=job.id)

    assert result.success is True
    assert "output_1_v2.md" in result.output_markdown_uri

    v2_h = ArtifactHandle(StorageBackendType.LOCAL_FS, result.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v2.md")
    v2_text = storage.retrieve(v2_h).decode("utf-8")

    assert "# Custom Title" in v2_text
    assert "Human authored narrative line 1." in v2_text
    assert "    Indented code or spacing preserved." in v2_text
    assert "Human authored narrative line 2." in v2_text
    assert f"crop_1_{rid}_v2.jpg" in v2_text


def test_apply_reviews_inserts_new_manual_region_token(apply_env):
    uow_factory = apply_env["uow_factory"]
    storage = apply_env["storage"]
    doc_proc = apply_env["doc_proc"]
    pdf_handle = apply_env["pdf_handle"]
    prompt_id = apply_env["prompt_id"]

    rid = uuid.uuid4().hex
    initial_text = "<!-- Page 1 -->\n# Page 1 Text\nSome content here.\n"
    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", initial_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_h.uri,
                output_artifact_version_watermark=1,
            )
        )
        uow.visual_regions.save(
            VisualRegion(
                id=None,
                region_id=rid,
                job_id=job.id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.USER_MANUAL,
                review_status=ReviewStatus.MANUAL,
                sync_status=SyncStatus.PENDING_INITIAL_CROP,
                reviewed_bbox=BoundingBox(200, 200, 600, 600),
                active_artifact_version=0,
                artifact_version_watermark=0,
            )
        )
        uow.commit()

    service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    result = service.apply_reviews(job_id=job.id)

    assert result.success is True
    v2_h = ArtifactHandle(StorageBackendType.LOCAL_FS, result.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v2.md")
    v2_text = storage.retrieve(v2_h).decode("utf-8")

    assert "<!-- Page 1 -->" in v2_text
    assert "# Page 1 Text" in v2_text
    assert f"![[crop_1_{rid}_v1.jpg|region_id={rid}]]" in v2_text


def test_race_a_editor_commits_before_apply_reservation_rejects_apply(apply_env):
    """
    Race A: Apply captures V3 snapshot at Step 1.
    Before Apply executes Step 2 watermark reservation, an Editor commits V4.
    Apply MUST detect conflict at OCC Check 1, reject with StaleDocumentVersionError,
    and leave V4 active without executing any recrops.
    """
    uow_factory = apply_env["uow_factory"]
    storage = apply_env["storage"]
    doc_proc = apply_env["doc_proc"]
    pdf_handle = apply_env["pdf_handle"]
    prompt_id = apply_env["prompt_id"]

    rid = uuid.uuid4().hex
    v3_text = "# V3 Content"
    out_v3 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v3.md", v3_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v3.uri,
                output_artifact_version_watermark=3,
            )
        )
        uow.visual_regions.save(
            VisualRegion(
                id=None,
                region_id=rid,
                job_id=job.id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                review_status=ReviewStatus.UNREVIEWED,
                sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
                detected_bbox=BoundingBox(100, 100, 500, 500),
                active_artifact_version=1,
                artifact_version_watermark=1,
            )
        )
        uow.commit()

    apply_service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    editor_service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # Trigger editor commit right before Apply's Step 2 (watermark reservation)
    original_create = uow_factory.create
    uow_call_count = 0

    def intercept_create(*args, **kwargs):
        nonlocal uow_call_count
        uow_call_count += 1
        if uow_call_count == 2:
            # Step 2: Before Apply begins its immediate reservation transaction,
            # Editor commits V4 from base V3
            editor_service.commit_source_text(
                job_id=job.id,
                raw_text="# V4 Editor Authoritative",
                base_version=3,
            )
        return original_create(*args, **kwargs)

    uow_factory.create = intercept_create

    with pytest.raises(StaleDocumentVersionError) as exc_info:
        apply_service.apply_reviews(job_id=job.id)

    assert exc_info.value.base_version == 3
    assert exc_info.value.current_version == 4

    # Verify V4 remains the active output path in SQLite
    with original_create() as uow:
        j_final = uow.jobs.get_by_id(job.id)
        assert "output_1_v4.md" in j_final.output_path
        latest = uow.document_versions.get_latest(job.id)
        assert latest is not None and latest.version == 4


def test_race_b_editor_commits_during_apply_staging_rejects_apply(apply_env):
    """
    Race B:
    1. Active canonical = V3.
    2. ApplyReviewService captures base_version = 3 at Step 1.
    3. Apply reserves V4 successfully at Step 2 (output_artifact_version_watermark = 4).
    4. While Apply is staging output_1_v4.md, MarkdownEditorService commits V5 (from base V3).
    5. Apply reaches final pointer commit (Step 7 OCC Check 2).
    6. Apply raises StaleDocumentVersionError.
    7. Editor V5 remains active in SQLite.
    8. The staged stale artifact (output_1_v4.md) never becomes active.
    """
    uow_factory = apply_env["uow_factory"]
    storage = apply_env["storage"]
    doc_proc = apply_env["doc_proc"]
    pdf_handle = apply_env["pdf_handle"]
    prompt_id = apply_env["prompt_id"]

    rid = uuid.uuid4().hex
    v3_text = "# V3 Base Content"
    out_v3 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v3.md", v3_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v3.uri,
                output_artifact_version_watermark=3,
            )
        )
        uow.visual_regions.save(
            VisualRegion(
                id=None,
                region_id=rid,
                job_id=job.id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                review_status=ReviewStatus.UNREVIEWED,
                sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
                detected_bbox=BoundingBox(100, 100, 500, 500),
                active_artifact_version=1,
                artifact_version_watermark=1,
            )
        )
        uow.commit()

    apply_service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    editor_service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    original_store = storage.store
    editor_committed_ver = 0
    is_intercepting = False

    def intercept_store(*args, **kwargs):
        nonlocal editor_committed_ver, is_intercepting
        result = original_store(*args, **kwargs)
        filename = kwargs.get("filename") or (args[2] if len(args) > 2 else "")
        # Trigger when Apply stages its new output markdown (output_1_v4.md)
        if filename.startswith("output_") and not is_intercepting and editor_committed_ver == 0:
            is_intercepting = True
            try:
                # While Apply has staged output_1_v4.md, execute real MarkdownEditorService commit!
                editor_committed_ver = editor_service.commit_source_text(
                    job_id=job.id,
                    raw_text="# Editor V5 Authoritative User Edit",
                    base_version=3,
                )
            finally:
                is_intercepting = False
        return result

    storage.store = MagicMock(side_effect=intercept_store)

    with pytest.raises(StaleDocumentVersionError) as exc_info:
        apply_service.apply_reviews(job_id=job.id)

    assert editor_committed_ver == 4
    assert exc_info.value.base_version == 3
    assert exc_info.value.current_version == 4

    # Verify Editor's V4 remains the active output path in SQLite
    with uow_factory.create() as uow:
        j_final = uow.jobs.get_by_id(job.id)
        assert "output_1_v4.md" in j_final.output_path
        latest = uow.document_versions.get_latest(job.id)
        assert latest is not None and latest.version == 4

    # Verify active text is the user's edits, NOT Apply's stale V4
    v4_handle = ArtifactHandle(
        StorageBackendType.LOCAL_FS,
        j_final.output_path,
        ArtifactType.OUTPUT_MARKDOWN,
        job.id,
        "output_1_v4.md",
    )
    v4_content = storage.retrieve(v4_handle).decode("utf-8")
    assert "# Editor V5 Authoritative User Edit" in v4_content


def test_race_c_editor_commits_v4_apply_subsequently_applies_v5(apply_env):
    """
    Race C: Editor commits V4 first. Apply starts from V4 snapshot and commits V5.
    V5 must preserve V4 edits + new crop token.
    """
    uow_factory = apply_env["uow_factory"]
    storage = apply_env["storage"]
    doc_proc = apply_env["doc_proc"]
    pdf_handle = apply_env["pdf_handle"]
    prompt_id = apply_env["prompt_id"]

    rid = uuid.uuid4().hex
    v4_text = f"# V4 User Text\n\n![[crop_1_{rid}_v1.jpg|region_id={rid}]]"
    out_v4 = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v4.md", v4_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_v4.uri,
                output_artifact_version_watermark=4,
            )
        )
        uow.visual_regions.save(
            VisualRegion(
                id=None,
                region_id=rid,
                job_id=job.id,
                page_number=1,
                display_order=1,
                origin=RegionOrigin.AI_DETECTED,
                review_status=ReviewStatus.UNREVIEWED,
                sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
                detected_bbox=BoundingBox(100, 100, 400, 400),
                active_artifact_version=1,
                artifact_version_watermark=1,
            )
        )
        uow.commit()

    service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    result = service.apply_reviews(job_id=job.id)

    assert result.success is True
    assert "output_1_v5.md" in result.output_markdown_uri

    v5_h = ArtifactHandle(StorageBackendType.LOCAL_FS, result.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v5.md")
    v5_text = storage.retrieve(v5_h).decode("utf-8")

    assert "# V4 User Text" in v5_text
    assert f"crop_1_{rid}_v2.jpg" in v5_text
