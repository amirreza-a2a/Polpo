# ============================================================
#  tests/unit/test_visual_region_publication_service.py
#  Unit Tests for VisualRegionPublicationService (TICK-P02B, #28)
# ============================================================

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock
import uuid
import pytest

from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IApplicationEventPublisher
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.visual_region_publication_service import VisualRegionPublicationService
from core.entities.artifact import resolve_canonical_file_path
from core.entities.bounding_box import BoundingBox, CropPolicy
from core.entities.job import Job, JobStatus
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    EntityNotFoundError,
    RegionPublicationError,
    StaleDocumentVersionError,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork, SQLiteUnitOfWorkFactory


class FakeDocumentProcessor(IDocumentProcessor):
    """Stub document processor recording interactions and supporting fault injection."""

    def __init__(self) -> None:
        self.render_calls: List[Tuple[bytes, int, int]] = []
        self.crop_calls: List[Tuple[bytes, BoundingBox, Optional[CropPolicy]]] = []
        self.render_error: Optional[Exception] = None
        self.crop_error: Optional[Exception] = None
        self.on_render_callback = None
        self.on_crop_callback = None

    def get_page_count(self, pdf_bytes: bytes) -> int:
        return 5

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_number: int, dpi: int = 150) -> bytes:
        if self.on_render_callback:
            self.on_render_callback()
        if self.render_error:
            raise self.render_error
        self.render_calls.append((pdf_bytes, page_number, dpi))
        return b"\xff\xd8\xff\xe0\x00\x10JFIFfake-page-jpeg"

    def crop_region_image(
        self,
        page_jpeg_bytes: bytes,
        box: BoundingBox,
        policy: Optional[CropPolicy] = None,
    ) -> Optional[bytes]:
        if self.on_crop_callback:
            self.on_crop_callback()
        if self.crop_error:
            raise self.crop_error
        self.crop_calls.append((page_jpeg_bytes, box, policy))
        return b"\xff\xd8\xff\xe0\x00\x10JFIFfake-crop-bytes"

    def extract_and_crop_images(
        self, markdown_text: str, page_jpeg_bytes: bytes, job_id: int, page_number: int = 1
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        return (markdown_text, [])

    def unify_markdown(self, raw_text: str) -> str:
        return raw_text

    def get_image_dimensions(self, image_bytes: bytes) -> Tuple[int, int]:
        return (800, 600)


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_pub_service.db"
    mgr = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager=mgr)
    runner.run_migrations()
    return mgr


@pytest.fixture
def uow_factory(db_manager: SQLiteDatabaseManager) -> SQLiteUnitOfWorkFactory:
    return SQLiteUnitOfWorkFactory(db_manager)


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def staging_service(artifacts_dir: Path) -> CropArtifactStagingService:
    return CropArtifactStagingService(base_dir=artifacts_dir / ".staging")


@pytest.fixture
def publication_service(
    uow_factory: SQLiteUnitOfWorkFactory,
    artifacts_dir: Path,
    staging_service: CropArtifactStagingService,
) -> DocumentPublicationService:
    return DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=staging_service,
    )


@pytest.fixture
def doc_processor() -> FakeDocumentProcessor:
    return FakeDocumentProcessor()


@pytest.fixture
def service(
    uow_factory: SQLiteUnitOfWorkFactory,
    doc_processor: FakeDocumentProcessor,
    staging_service: CropArtifactStagingService,
    publication_service: DocumentPublicationService,
) -> VisualRegionPublicationService:
    return VisualRegionPublicationService(
        uow_factory=uow_factory,
        doc_processor=doc_processor,
        staging_service=staging_service,
        publication_service=publication_service,
    )


def _setup_job_and_doc(
    uow_factory: SQLiteUnitOfWorkFactory,
    pub_service: DocumentPublicationService,
    tmp_path: Path,
    initial_text: str = "<!-- Page 1 -->\nInitial text.\n",
) -> Tuple[int, Path]:
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy pdf bytes")
    file_uri = pdf_path.resolve().as_uri()

    with uow_factory.create() as uow:
        cur = uow._conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs (file_name, file_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("doc.pdf", file_uri, JobStatus.DONE.value, "2026-09-24T00:00:00+00:00", "2026-09-24T00:00:00+00:00"),
        )
        job_id = cur.lastrowid
        uow.commit()

    pub_service.publish_initial(job_id=job_id, markdown_text=initial_text)
    return job_id, pdf_path


def _create_region(
    uow_factory: SQLiteUnitOfWorkFactory,
    job_id: int,
    region_id: Optional[str] = None,
    page_number: int = 1,
    display_order: int = 1,
    review_status: ReviewStatus = ReviewStatus.MANUAL,
    sync_status: SyncStatus = SyncStatus.PENDING_INITIAL_CROP,
    active_artifact_version: int = 0,
    active_artifact_uri: Optional[str] = None,
) -> VisualRegion:
    r_id = region_id or uuid.uuid4().hex
    region = VisualRegion(
        id=None,
        region_id=r_id,
        job_id=job_id,
        page_number=page_number,
        display_order=display_order,
        origin=RegionOrigin.USER_MANUAL,
        detected_bbox=None,
        reviewed_bbox=BoundingBox(10, 10, 100, 100),
        review_status=review_status,
        sync_status=sync_status,
        active_artifact_version=active_artifact_version,
        artifact_version_watermark=active_artifact_version,
        active_artifact_uri=active_artifact_uri,
    )
    with uow_factory.create() as uow:
        saved = uow.visual_regions.save(region)
        uow.commit()
    return saved


def test_publish_region_review_happy_path(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    doc_processor: FakeDocumentProcessor,
    artifacts_dir: Path,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    result = service.publish_region_review(job_id, region.region_id)

    assert isinstance(result, RegionPublicationResultDTO)
    assert result.success is True
    assert result.job_id == job_id
    assert result.region_id == region.region_id
    assert result.document_version == 2
    assert result.artifact_version == 1
    assert result.artifact_uri is not None
    assert f"crop_{region.region_id}_v1.jpg" in result.artifact_uri

    # Verify IDocumentProcessor was called with correct page and bbox
    assert len(doc_processor.render_calls) == 1
    assert doc_processor.render_calls[0][1] == region.page_number
    assert len(doc_processor.crop_calls) == 1
    assert doc_processor.crop_calls[0][1] == region.effective_bbox

    # Verify VisualRegion in SQLite
    with uow_factory.create() as uow:
        updated_region = uow.visual_regions.get_by_region_id(region.region_id)
        assert updated_region is not None
        assert updated_region.active_artifact_version == 1
        assert updated_region.active_artifact_uri == result.artifact_uri
        assert updated_region.sync_status == SyncStatus.SYNCED

    # Verify promoted crop exists on disk in job directory
    crop_path = resolve_canonical_file_path(result.artifact_uri)
    assert crop_path.is_file()

    # Verify canonical markdown text was mutated and published
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 2
        published_md = resolve_canonical_file_path(latest_doc.output_path).read_text(encoding="utf-8")
        assert f'polpo:region={uuid.UUID(hex=region.region_id)}' in published_md
        assert result.artifact_uri in published_md

    # Verify isolated staging directory was cleaned up
    staging_base = artifacts_dir / ".staging"
    if staging_base.exists():
        assert list(staging_base.iterdir()) == []


def test_publish_region_review_passes_resolved_legacy_target(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path, initial_text="")
    initial_text = f"<!-- Page 1 -->\nParagraph.\n\n![[crop_{job_id}_p1_1.jpg]]\n\nFooter."
    publication_service.publish_version(job_id=job_id, base_version=1, markdown_text=initial_text)
    # Match the legacy target: crop_{job_id}_p{page_number}_{display_order}.jpg
    region = _create_region(uow_factory, job_id, page_number=1, display_order=1)

    result = service.publish_region_review(job_id, region.region_id)
    assert result.success is True

    # Verify legacy wiki link was replaced in place
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        published_md = resolve_canonical_file_path(latest_doc.output_path).read_text(encoding="utf-8")
        assert f"![[crop_{job_id}_p1_1.jpg]]" not in published_md
        assert f'polpo:region={uuid.UUID(hex=region.region_id)}' in published_md


def test_publish_region_review_stale_snapshot_aborts(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    doc_processor: FakeDocumentProcessor,
    artifacts_dir: Path,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    # When crop is invoked, simulate concurrent user update of bbox in SQLite
    def concurrent_edit():
        with uow_factory.create() as uow:
            uow.begin_immediate()
            r = uow.visual_regions.get_by_region_id(region.region_id)
            r.reviewed_bbox = BoundingBox(200, 200, 400, 400)
            r.updated_at = datetime.now(timezone.utc)
            uow.visual_regions.save(r)
            uow.commit()

    doc_processor.on_crop_callback = concurrent_edit

    result = service.publish_region_review(job_id, region.region_id)

    assert result.success is False
    assert "stale" in result.status_message.lower()

    # Document was NOT published to v2
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 1
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r.sync_status != SyncStatus.SYNCED

    # Staging directory was cleaned up
    staging_base = artifacts_dir / ".staging"
    if staging_base.exists():
        assert list(staging_base.iterdir()) == []


def test_publish_region_review_deleted_region_aborts(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    doc_processor: FakeDocumentProcessor,
    artifacts_dir: Path,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    # When crop is invoked, simulate region deletion in SQLite
    def concurrent_delete():
        with uow_factory.create() as uow:
            uow.begin_immediate()
            uow.visual_regions.delete_by_region_id(region.region_id)
            uow.commit()

    doc_processor.on_crop_callback = concurrent_delete

    result = service.publish_region_review(job_id, region.region_id)

    assert result.success is False
    assert "stale" in result.status_message.lower() or "deleted" in result.status_message.lower()

    # Document version remains 1
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 1

    # Staging cleaned
    staging_base = artifacts_dir / ".staging"
    if staging_base.exists():
        assert list(staging_base.iterdir()) == []


def test_publish_region_review_occ_retry_success(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    original_publish_version = publication_service.publish_version
    calls = []

    def mock_publish_version(*args, **kwargs):
        calls.append(kwargs.get("base_version", args[1] if len(args) > 1 else None))
        if len(calls) == 1:
            # Simulate concurrent publication advancing version to 2
            original_publish_version(
                job_id=job_id,
                base_version=1,
                markdown_text="<!-- Page 1 -->\nConcurrent human edit.\n",
                published_by="CONCURRENT_USER",
            )
            raise StaleDocumentVersionError(job_id=job_id, base_version=1, current_version=2)
        return original_publish_version(*args, **kwargs)

    monkeypatch.setattr(publication_service, "publish_version", mock_publish_version)

    result = service.publish_region_review(job_id, region.region_id)

    assert result.success is True
    assert len(calls) == 2
    assert calls[0] == 1  # First attempt base_version was 1
    assert calls[1] == 2  # Second attempt reloaded base_version 2
    assert result.document_version == 3

    # Verify latest document incorporated both the concurrent edit and the token
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 3
        text = resolve_canonical_file_path(latest_doc.output_path).read_text(encoding="utf-8")
        assert "Concurrent human edit." in text
        assert f'polpo:region={uuid.UUID(hex=region.region_id)}' in text


def test_publish_region_review_occ_retry_exhaustion(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    artifacts_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    call_count = 0

    def mock_always_stale(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise StaleDocumentVersionError(job_id=job_id, base_version=1, current_version=2)

    monkeypatch.setattr(publication_service, "publish_version", mock_always_stale)

    with pytest.raises(RegionPublicationError) as exc_info:
        service.publish_region_review(job_id, region.region_id)

    assert "exhausted" in str(exc_info.value).lower()
    assert call_count == 3  # Exactly 3 bounded attempts

    # Region must be marked SYNC_FAILED
    with uow_factory.create() as uow:
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r.sync_status == SyncStatus.SYNC_FAILED

    # Staging must be cleaned up
    staging_base = artifacts_dir / ".staging"
    if staging_base.exists():
        assert list(staging_base.iterdir()) == []


def test_publish_region_review_render_failure_does_not_publish(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    doc_processor: FakeDocumentProcessor,
    artifacts_dir: Path,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    region = _create_region(uow_factory, job_id)

    doc_processor.render_error = RuntimeError("PDF render hardware acceleration crashed")

    with pytest.raises(RegionPublicationError):
        service.publish_region_review(job_id, region.region_id)

    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 1

    staging_base = artifacts_dir / ".staging"
    if staging_base.exists():
        assert list(staging_base.iterdir()) == []


def test_reconcile_region_sync_provable_facts(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    artifacts_dir: Path,
    tmp_path: Path,
):
    reg_id = uuid.uuid4().hex
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path, initial_text="")

    # Create artifact file in permanent job directory
    job_dir = artifacts_dir / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    crop_filename = f"crop_{reg_id}_v1.jpg"
    crop_path = job_dir / crop_filename
    crop_path.write_bytes(b"reconciled-crop-bytes")
    crop_uri = crop_path.resolve().as_uri()

    # Create document version with matching canonical visual token
    reg_uuid = uuid.UUID(hex=reg_id)
    occ_uuid = uuid.uuid4()
    valid_token = f'![Figure]({crop_uri} "polpo:region={reg_uuid};occ={occ_uuid}")'
    doc_text = f"# Reconciled Doc\n\n{valid_token}\n"
    publication_service.publish_version(job_id=job_id, base_version=1, markdown_text=doc_text)

    # Create region with active_artifact_version=1 and active_artifact_uri=crop_uri, but sync_status=PENDING_INITIAL_CROP
    _create_region(
        uow_factory,
        job_id=job_id,
        region_id=reg_id,
        sync_status=SyncStatus.PENDING_INITIAL_CROP,
        active_artifact_version=1,
        active_artifact_uri=crop_uri,
    )

    result = service.reconcile_region_sync(job_id, reg_id)

    assert result.success is True
    assert result.artifact_version == 1
    assert result.artifact_uri == crop_uri

    # Verify region in SQLite transitioned to SYNCED
    with uow_factory.create() as uow:
        r = uow.visual_regions.get_by_region_id(reg_id)
        assert r.sync_status == SyncStatus.SYNCED
        latest_doc = uow.document_versions.get_latest(job_id)
        # Verify NO duplicate document version was published
        assert latest_doc.version == 2


def test_reconcile_region_sync_missing_file_does_not_sync(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    artifacts_dir: Path,
    tmp_path: Path,
):
    reg_id = uuid.uuid4().hex
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path, initial_text="")

    job_dir = artifacts_dir / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    crop_filename = f"crop_{reg_id}_v1.jpg"
    crop_path = job_dir / crop_filename
    # Do NOT write file to disk (missing file)
    crop_uri = crop_path.resolve().as_uri()

    reg_uuid = uuid.UUID(hex=reg_id)
    valid_token = f'![Figure]({crop_uri} "polpo:region={reg_uuid};occ={uuid.uuid4()}")'
    doc_text = f"# Reconciled Doc\n\n{valid_token}\n"
    publication_service.publish_version(job_id=job_id, base_version=1, markdown_text=doc_text)

    _create_region(
        uow_factory,
        job_id=job_id,
        region_id=reg_id,
        sync_status=SyncStatus.PENDING_INITIAL_CROP,
        active_artifact_version=1,
        active_artifact_uri=crop_uri,
    )

    result = service.reconcile_region_sync(job_id, reg_id)

    assert result.success is False
    assert "not found on disk" in result.status_message.lower()

    # Region remained unsynced
    with uow_factory.create() as uow:
        r = uow.visual_regions.get_by_region_id(reg_id)
        assert r.sync_status == SyncStatus.PENDING_INITIAL_CROP


def test_publish_region_review_missing_job_raises_entity_not_found(
    service: VisualRegionPublicationService,
):
    with pytest.raises(EntityNotFoundError) as exc_info:
        service.publish_region_review(9999, uuid.uuid4().hex)
    assert "job" in str(exc_info.value).lower()


def test_publish_region_review_missing_region_raises_entity_not_found(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    tmp_path: Path,
):
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path)
    with pytest.raises(EntityNotFoundError) as exc_info:
        service.publish_region_review(job_id, uuid.uuid4().hex)
    assert "visualregion" in str(exc_info.value).lower()


def test_publish_region_review_rejected_region_removes_token(
    service: VisualRegionPublicationService,
    uow_factory: SQLiteUnitOfWorkFactory,
    publication_service: DocumentPublicationService,
    doc_processor: FakeDocumentProcessor,
    tmp_path: Path,
):
    reg_id = uuid.uuid4().hex
    reg_uuid = uuid.UUID(hex=reg_id)
    initial_text = f"<!-- Page 1 -->\nKeep text.\n\n![Figure](crop.jpg \"polpo:region={reg_uuid};occ={uuid.uuid4()}\")\n\nEnd."
    job_id, _ = _setup_job_and_doc(uow_factory, publication_service, tmp_path, initial_text=initial_text)

    region = _create_region(
        uow_factory,
        job_id=job_id,
        region_id=reg_id,
        review_status=ReviewStatus.REJECTED,
        sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
        active_artifact_version=1,
        active_artifact_uri="file:///dummy/crop.jpg",
    )

    result = service.publish_region_review(job_id, region.region_id)

    assert result.success is True
    # Rejected region should NOT trigger rendering or cropping
    assert len(doc_processor.render_calls) == 0
    assert len(doc_processor.crop_calls) == 0

    # Token removed in published document
    with uow_factory.create() as uow:
        latest_doc = uow.document_versions.get_latest(job_id)
        assert latest_doc.version == 2
        text = resolve_canonical_file_path(latest_doc.output_path).read_text(encoding="utf-8")
        assert f"polpo:region={reg_uuid}" not in text
        assert "Keep text." in text

        # Region active_artifact_uri cleared and marked SYNCED
        r = uow.visual_regions.get_by_region_id(region.region_id)
        assert r.active_artifact_uri is None
        assert r.sync_status == SyncStatus.SYNCED
