"""End-to-end integration and concurrency tests for manual visual region publication (TICK-P02D, #30).

Validates the complete workflow connecting manual visual region creation in the PDF viewer
through asynchronous cropping, canonical Markdown token mutation, atomic document publication,
artifact promotion, dirty editor 3-way merge, worker staleness guards, bounded OCC retry,
and crash recovery reconciliation against real SQLite databases and real document processing.
"""

from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Dict, Optional
import uuid

import pytest
import pymupdf as fitz

from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.markdown_merge_service import MarkdownMergeService
from application.services.markdown_viewer_service import MarkdownViewerService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from core.entities.artifact import resolve_canonical_file_path
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import ReviewStatus, SyncStatus, VisualRegion
from core.markdown.visual_token_mutator import find_canonical_tokens
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.markdown.pandoc_parser import PandocParser
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter


def _generate_synthetic_pdf(target_path: Path, num_pages: int = 2) -> None:
    doc = fitz.open()
    for page_idx in range(num_pages):
        page = doc.new_page(width=600, height=800)
        page.draw_rect(fitz.Rect(50, 50, 550, 750), color=(0.2, 0.4, 0.8), fill=(0.9, 0.9, 0.9))
        page.insert_text(fitz.Point(100, 150), f"Synthetic PDF Page {page_idx + 1}", fontsize=18)
    doc.save(str(target_path))
    doc.close()


@pytest.fixture
def workflow_env(tmp_path: Path) -> Dict[str, Any]:
    db_file = tmp_path / "integration_app.db"
    db_manager = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager)
    runner.run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(db_manager)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = artifacts_dir / ".staging"
    staging_service = CropArtifactStagingService(base_dir=staging_dir)

    publication_service = DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=staging_service,
    )

    doc_processor = PyMuPDFDocumentProcessor()

    publication_app_service = VisualRegionPublicationService(
        uow_factory=uow_factory,
        doc_processor=doc_processor,
        staging_service=staging_service,
        publication_service=publication_service,
    )

    storage = LocalStorageAdapter(base_dir=artifacts_dir)
    parser = PandocParser()
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

    pdf_path = tmp_path / "test_document.pdf"
    _generate_synthetic_pdf(pdf_path, num_pages=2)

    with uow_factory.create() as uow:
        p = uow.prompts.save(
            Prompt(
                id=None,
                name="Default",
                text="Default prompt",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        )
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="test_document.pdf",
                file_path=pdf_path.as_uri(),
                total_pages=2,
                prompt_id=p.id,
                status=JobStatus.DONE,
                output_path="",
                output_artifact_version_watermark=1,
            )
        )
        uow.commit()
        job_id = job.id

    initial_markdown = (
        "<!-- Page 1 -->\n\n"
        "# Section 1\n\n"
        "This is the first section of the document.\n\n"
        "<!-- Page 2 -->\n\n"
        "# Section 2\n\n"
        "This is the second section of the document.\n"
    )
    publication_service.publish_initial(
        job_id=job_id,
        markdown_text=initial_markdown,
        published_by="SYSTEM",
    )

    return {
        "job_id": job_id,
        "pdf_path": pdf_path,
        "artifacts_dir": artifacts_dir,
        "staging_service": staging_service,
        "publication_service": publication_service,
        "doc_processor": doc_processor,
        "publication_app_service": publication_app_service,
        "uow_factory": uow_factory,
        "merge_service": merge_service,
        "storage": storage,
    }


def _create_user_manual_region(
    uow_factory: SQLiteUnitOfWorkFactory,
    job_id: int,
    page_number: int = 1,
    display_order: int = 1,
    bbox: Optional[BoundingBox] = None,
    region_id: Optional[str] = None,
) -> VisualRegion:
    box = bbox or BoundingBox(ymin=100, xmin=100, ymax=300, xmax=300)
    region = VisualRegion.create_user_manual(
        job_id=job_id,
        page_number=page_number,
        display_order=display_order,
        reviewed_bbox=box,
        region_id=region_id,
    )
    with uow_factory.create() as uow:
        uow.begin_immediate()
        saved = uow.visual_regions.save(region)
        uow.commit()
    return saved


def test_e2e_manual_region_creation_publishes_canonical_markdown(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    publication_app_service = workflow_env["publication_app_service"]
    artifacts_dir = workflow_env["artifacts_dir"]

    region = _create_user_manual_region(uow_factory, job_id, page_number=1)

    result = publication_app_service.publish_region_review(job_id, region.region_id)

    assert isinstance(result, RegionPublicationResultDTO)
    assert result.success is True
    assert result.document_version == 2
    assert result.artifact_version == 1
    assert result.artifact_uri is not None
    assert f"crop_{region.region_id}_v1.jpg" in result.artifact_uri

    # Verify promoted crop on disk
    expected_crop_path = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v1.jpg"
    assert expected_crop_path.is_file()
    crop_bytes = expected_crop_path.read_bytes()
    assert len(crop_bytes) > 0
    assert crop_bytes.startswith(b"\xff\xd8")

    # Verify canonical markdown on disk and in database
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        assert latest_doc.version == 2
        assert latest_doc.integrity_status == "VALID"
        assert latest_doc.published_by == "VISUAL_REGION_REVIEW"

        job = uow.jobs.get_by_id(job_id)
        assert job is not None
        assert job.output_path == latest_doc.output_path

        synced_reg = uow.visual_regions.get_by_region_id(region.region_id)
        assert synced_reg is not None
        assert synced_reg.sync_status == SyncStatus.SYNCED
        assert synced_reg.active_artifact_version == 1
        assert synced_reg.artifact_version_watermark >= 1
        assert synced_reg.active_artifact_uri == result.artifact_uri

    doc_file = resolve_canonical_file_path(latest_doc.output_path)
    markdown_text = doc_file.read_text(encoding="utf-8")
    tokens = find_canonical_tokens(markdown_text, region.region_id)
    assert len(tokens) == 1
    assert tokens[0].uri == result.artifact_uri
    assert "# Section 1" in markdown_text
    assert "# Section 2" in markdown_text


def test_e2e_manual_region_resize_increments_artifact_version(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    publication_app_service = workflow_env["publication_app_service"]
    artifacts_dir = workflow_env["artifacts_dir"]

    region = _create_user_manual_region(uow_factory, job_id, page_number=1)
    res_v1 = publication_app_service.publish_region_review(job_id, region.region_id)
    assert res_v1.success is True
    assert res_v1.artifact_version == 1

    # User resizes bounding box in presentation layer
    with uow_factory.create() as uow:
        uow.begin_immediate()
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r is not None
        r.reviewed_bbox = BoundingBox(ymin=150, xmin=150, ymax=450, xmax=450)
        r.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        r.updated_at = datetime.now(timezone.utc)
        uow.visual_regions.save(r)
        uow.commit()

    res_v2 = publication_app_service.publish_region_review(job_id, region.region_id)

    assert res_v2.success is True
    assert res_v2.document_version == 3
    assert res_v2.artifact_version == 2
    assert res_v2.artifact_uri is not None
    assert f"crop_{region.region_id}_v2.jpg" in res_v2.artifact_uri

    crop_v2_path = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v2.jpg"
    crop_v1_path = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v1.jpg"
    assert crop_v2_path.is_file()
    assert crop_v1_path.is_file()

    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        assert latest_doc.version == 3
        synced_reg = uow.visual_regions.get_by_region_id(region.region_id)
        assert synced_reg is not None
        assert synced_reg.sync_status == SyncStatus.SYNCED
        assert synced_reg.active_artifact_version == 2
        assert synced_reg.artifact_version_watermark >= 2

    doc_file = resolve_canonical_file_path(latest_doc.output_path)
    markdown_text = doc_file.read_text(encoding="utf-8")
    tokens = find_canonical_tokens(markdown_text, region.region_id)
    assert len(tokens) == 1
    assert tokens[0].uri == res_v2.artifact_uri
    assert f"crop_{region.region_id}_v1.jpg" not in markdown_text


def test_e2e_manual_region_deletion_removes_token(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    publication_app_service = workflow_env["publication_app_service"]
    artifacts_dir = workflow_env["artifacts_dir"]

    region = _create_user_manual_region(uow_factory, job_id, page_number=1)
    res_pub = publication_app_service.publish_region_review(job_id, region.region_id)
    assert res_pub.success is True

    # User rejects the region in presentation layer
    with uow_factory.create() as uow:
        uow.begin_immediate()
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r is not None
        r.review_status = ReviewStatus.REJECTED
        r.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        r.updated_at = datetime.now(timezone.utc)
        uow.visual_regions.save(r)
        uow.commit()

    res_del = publication_app_service.publish_region_review(job_id, region.region_id)

    assert res_del.success is True
    assert res_del.document_version == 3
    assert res_del.artifact_version is None
    assert res_del.artifact_uri is None

    # Verify token is excised from canonical Markdown
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        assert latest_doc.version == 3
        r_db = uow.visual_regions.get_by_region_id(region.region_id)
        assert r_db is not None
        assert r_db.sync_status == SyncStatus.SYNCED
        assert r_db.active_artifact_version == 0
        assert r_db.active_artifact_uri is None

    doc_file = resolve_canonical_file_path(latest_doc.output_path)
    markdown_text = doc_file.read_text(encoding="utf-8")
    tokens = find_canonical_tokens(markdown_text, region.region_id)
    assert len(tokens) == 0

    # Historical crop file remains preserved on disk
    crop_v1_path = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v1.jpg"
    assert crop_v1_path.is_file()


def test_e2e_concurrent_dirty_editor_draft_three_way_merge(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    publication_app_service = workflow_env["publication_app_service"]
    merge_service = workflow_env["merge_service"]

    # Local user types an uncommitted note into the editor draft based on version 1
    dirty_editor_draft = (
        "<!-- Page 1 -->\n\n"
        "# Section 1\n\n"
        "This is the first section of the document.\n\n"
        "<!-- Page 2 -->\n\n"
        "# Section 2\n\n"
        "This is the second section of the document.\n\n"
        "## User Notes\n"
        "Uncommitted reviewer notes added in editor buffer.\n"
    )

    region = _create_user_manual_region(uow_factory, job_id, page_number=1)
    res_pub = publication_app_service.publish_region_review(job_id, region.region_id)
    assert res_pub.success is True
    assert res_pub.document_version == 2

    # Presentation layer performs three-way merge analysis against the published canonical version
    merge_result = merge_service.analyze_three_way_merge(
        job_id=job_id,
        base_version=1,
        local_text=dirty_editor_draft,
        canonical_version=2,
        merge_session_id=101,
    )

    assert merge_result.has_conflicts is False
    assert merge_result.clean_text is not None
    assert f"crop_{region.region_id}_v1.jpg" in merge_result.clean_text
    assert "Uncommitted reviewer notes added in editor buffer." in merge_result.clean_text


def test_e2e_stale_worker_snapshot_aborts_cleanly(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    staging_service = workflow_env["staging_service"]
    publication_service = workflow_env["publication_service"]
    artifacts_dir = workflow_env["artifacts_dir"]

    region = _create_user_manual_region(
        uow_factory,
        job_id,
        page_number=1,
        bbox=BoundingBox(ymin=100, xmin=100, ymax=200, xmax=200),
    )

    worker_crop_done = threading.Event()
    main_modified_region = threading.Event()

    class InterceptingProcessor(PyMuPDFDocumentProcessor):
        def crop_region_image(self, page_jpeg_bytes, box, policy=None):
            cropped = super().crop_region_image(page_jpeg_bytes, box, policy)
            worker_crop_done.set()
            main_modified_region.wait(timeout=10.0)
            return cropped

    synced_proc = InterceptingProcessor()
    test_pub_service = VisualRegionPublicationService(
        uow_factory=uow_factory,
        doc_processor=synced_proc,
        staging_service=staging_service,
        publication_service=publication_service,
    )

    worker_result: Optional[RegionPublicationResultDTO] = None

    def worker_entry():
        nonlocal worker_result
        worker_result = test_pub_service.publish_region_review(job_id, region.region_id)

    worker_thread = threading.Thread(target=worker_entry)
    worker_thread.start()

    assert worker_crop_done.wait(timeout=10.0)

    # Modify region geometry concurrently while worker holds rendered crop
    with uow_factory.create() as uow:
        uow.begin_immediate()
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r is not None
        r.reviewed_bbox = BoundingBox(ymin=300, xmin=300, ymax=500, xmax=500)
        r.updated_at = datetime.now(timezone.utc)
        uow.visual_regions.save(r)
        uow.commit()

    main_modified_region.set()
    worker_thread.join(timeout=10.0)

    assert worker_result is not None
    assert worker_result.success is False
    assert "stale" in worker_result.status_message.lower()

    # Document version remains unchanged; staging artifacts are discarded
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        assert latest_doc.version == 1

        r_check = uow.visual_regions.get_by_region_id(region.region_id)
        assert r_check is not None
        assert r_check.reviewed_bbox == BoundingBox(ymin=300, xmin=300, ymax=500, xmax=500)

    staged_files = [f for f in (artifacts_dir / ".staging").glob("**/*") if f.is_file()]
    assert len(staged_files) == 0

    crop_promoted = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v1.jpg"
    assert not crop_promoted.exists()


def test_e2e_concurrent_publication_occ_retry_preserves_both_regions(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    staging_service = workflow_env["staging_service"]
    publication_service = workflow_env["publication_service"]
    doc_processor = workflow_env["doc_processor"]
    artifacts_dir = workflow_env["artifacts_dir"]

    reg_a = _create_user_manual_region(
        uow_factory,
        job_id,
        page_number=1,
        display_order=1,
        bbox=BoundingBox(ymin=100, xmin=100, ymax=200, xmax=200),
    )
    reg_b = _create_user_manual_region(
        uow_factory,
        job_id,
        page_number=1,
        display_order=2,
        bbox=BoundingBox(ymin=300, xmin=300, ymax=400, xmax=400),
    )

    worker_b_ready = threading.Event()
    worker_a_done = threading.Event()
    b_publish_attempts = 0

    class InterceptingPublicationGateway:
        def __init__(self, target: DocumentPublicationService):
            self._target = target

        def __getattr__(self, name: str) -> Any:
            return getattr(self._target, name)

        def publish_version(self, **kwargs: Any) -> Any:
            nonlocal b_publish_attempts
            b_publish_attempts += 1
            if b_publish_attempts == 1:
                worker_b_ready.set()
                worker_a_done.wait(timeout=10.0)
            return self._target.publish_version(**kwargs)

    proxy_pub_b = InterceptingPublicationGateway(publication_service)
    service_a = VisualRegionPublicationService(uow_factory, doc_processor, staging_service, publication_service)
    service_b = VisualRegionPublicationService(uow_factory, doc_processor, staging_service, proxy_pub_b)  # type: ignore

    res_a: Optional[RegionPublicationResultDTO] = None
    res_b: Optional[RegionPublicationResultDTO] = None

    def worker_b_target():
        nonlocal res_b
        res_b = service_b.publish_region_review(job_id, reg_b.region_id)

    thread_b = threading.Thread(target=worker_b_target)
    thread_b.start()

    assert worker_b_ready.wait(timeout=10.0)
    res_a = service_a.publish_region_review(job_id, reg_a.region_id)
    worker_a_done.set()
    thread_b.join(timeout=10.0)

    assert res_a is not None
    assert res_a.success is True
    assert res_a.document_version == 2

    assert res_b is not None
    assert res_b.success is True
    assert res_b.document_version == 3

    # Canonical markdown for version 3 must preserve both region tokens
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        assert latest_doc.version == 3

        r_a = uow.visual_regions.get_by_region_id(reg_a.region_id)
        r_b = uow.visual_regions.get_by_region_id(reg_b.region_id)
        assert r_a is not None and r_a.sync_status == SyncStatus.SYNCED
        assert r_b is not None and r_b.sync_status == SyncStatus.SYNCED

    doc_file = resolve_canonical_file_path(latest_doc.output_path)
    markdown_text = doc_file.read_text(encoding="utf-8")
    assert len(find_canonical_tokens(markdown_text, reg_a.region_id)) == 1
    assert len(find_canonical_tokens(markdown_text, reg_b.region_id)) == 1

    crop_a_path = artifacts_dir / f"job_{job_id}" / f"crop_{reg_a.region_id}_v1.jpg"
    crop_b_path = artifacts_dir / f"job_{job_id}" / f"crop_{reg_b.region_id}_v1.jpg"
    assert crop_a_path.is_file()
    assert crop_b_path.is_file()


def test_e2e_crash_recovery_reconciliation(workflow_env: Dict[str, Any]):
    job_id = workflow_env["job_id"]
    uow_factory = workflow_env["uow_factory"]
    publication_app_service = workflow_env["publication_app_service"]
    artifacts_dir = workflow_env["artifacts_dir"]

    region = _create_user_manual_region(uow_factory, job_id, page_number=1)
    res_pub = publication_app_service.publish_region_review(job_id, region.region_id)
    assert res_pub.success is True
    assert res_pub.document_version == 2

    # Simulate desynchronization following a crash or interrupted subsequent review
    with uow_factory.create() as uow:
        uow.begin_immediate()
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r is not None
        r.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        r.updated_at = datetime.now(timezone.utc)
        uow.visual_regions.save(r)
        uow.commit()

    # Reconcile based strictly on verifiable repository facts
    recon_res = publication_app_service.reconcile_region_sync(job_id, region.region_id)
    assert recon_res.success is True
    assert "reconciliation verified all provable facts" in recon_res.status_message.lower()

    with uow_factory.create() as uow:
        synced_reg = uow.visual_regions.get_by_region_id(region.region_id)
        assert synced_reg is not None
        assert synced_reg.sync_status == SyncStatus.SYNCED
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc is not None
        # Ensures no redundant duplicate document version was created during reconciliation
        assert latest_doc.version == 2

    # Negative test: missing disk artifact file prevents reconciliation to SYNCED
    crop_file = artifacts_dir / f"job_{job_id}" / f"crop_{region.region_id}_v1.jpg"
    assert crop_file.is_file()
    crop_file.unlink()

    with uow_factory.create() as uow:
        uow.begin_immediate()
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r is not None
        r.sync_status = SyncStatus.DIRTY_RECROP_REQUIRED
        uow.visual_regions.save(r)
        uow.commit()

    recon_neg = publication_app_service.reconcile_region_sync(job_id, region.region_id)
    assert recon_neg.success is False
    assert "not found on disk" in recon_neg.status_message.lower()

    with uow_factory.create() as uow:
        unsynced_reg = uow.visual_regions.get_by_region_id(region.region_id)
        assert unsynced_reg is not None
        assert unsynced_reg.sync_status == SyncStatus.DIRTY_RECROP_REQUIRED
