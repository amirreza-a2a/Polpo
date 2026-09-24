"""Visual Region Publication Application Service (TICK-P02B, #28).

Coordinates the visual-region review publication lifecycle, uncommitted crop staging,
worker freshness validation, canonical Markdown token mutation, atomic document
publication, and sync reconciliation.
"""

from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from application.dto.staged_crop import StagedCropHandle
from application.dto.visual_region_publication_dto import RegionPublicationResultDTO
from application.ports.document_processor import IDocumentProcessor
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from core.entities.artifact import resolve_canonical_file_path
from core.entities.visual_region import SyncStatus
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    EntityNotFoundError,
    RegionPublicationError,
    StaleDocumentVersionError,
)
from core.markdown.visual_token_mutator import (
    find_canonical_tokens,
    remove_visual_token,
    upsert_visual_token,
)

logger = logging.getLogger("application.services.visual_region_publication_service")


class VisualRegionPublicationService:
    """
    Application-layer service coordinating the complete visual-region review publication lifecycle.
    Orchestrates PDF page rasterization, bounding-box cropping, uncommitted crop staging,
    worker snapshot freshness validation, canonical Markdown token mutation, atomic document
    publication via DocumentPublicationService, and optimistic concurrency control (OCC).
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        doc_processor: IDocumentProcessor,
        staging_service: CropArtifactStagingService,
        publication_service: DocumentPublicationService,
    ) -> None:
        self.uow_factory = uow_factory
        self.doc_processor = doc_processor
        self.staging_service = staging_service
        self.publication_service = publication_service

    def publish_region_review(
        self,
        job_id: int,
        region_id: str,
    ) -> RegionPublicationResultDTO:
        """
        Coordinates the complete visual region review publication lifecycle:
        1. Validates job and visual region existence.
        2. Snapshots region state for worker freshness verification.
        3. If active, renders page JPEG and crops the reviewed bounding box.
        4. Stages candidate crop in isolated .staging/ directory.
        5. Re-reads region immediately before publication; aborts if worker snapshot is stale.
        6. Loads active canonical Markdown and document version.
        7. Mutates canonical Markdown with VisualTokenMutator.
        8. Publishes new document version and promotes crop via DocumentPublicationService (with OCC retry).
        9. Updates region persistence state to SYNCED.
        10. Returns structured RegionPublicationResultDTO.
        """
        if not isinstance(job_id, int) or job_id <= 0:
            raise ValueError(f"job_id must be a positive integer, got {job_id}")
        if not region_id or not isinstance(region_id, str):
            raise ValueError("region_id must be a non-empty string")

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            region = uow.visual_regions.get_by_region_id(region_id)
            if not region or region.job_id != job_id:
                raise EntityNotFoundError("VisualRegion", region_id)

        # Worker snapshot taken prior to expensive rendering/cropping operations
        snapshot_reviewed_bbox = region.reviewed_bbox
        snapshot_updated_at = region.updated_at
        snapshot_is_deleted = region.is_deleted
        snapshot_page_number = region.page_number
        snapshot_display_order = region.display_order
        snapshot_effective_bbox = region.effective_bbox

        staging_id: Optional[str] = None
        staged_handle: Optional[StagedCropHandle] = None
        next_artifact_ver: Optional[int] = None

        try:
            if not snapshot_is_deleted:
                watermark = max(region.artifact_version_watermark, region.active_artifact_version)
                next_artifact_ver = watermark + 1

                if not job.file_path:
                    raise ArtifactNotFoundError(f"Job {job_id} has no source file_path recorded.")

                source_pdf_path = resolve_canonical_file_path(job.file_path)
                if not source_pdf_path.is_file():
                    raise ArtifactNotFoundError(job.file_path)

                pdf_bytes = source_pdf_path.read_bytes()

                try:
                    page_jpeg = self.doc_processor.render_page_to_jpeg(
                        pdf_bytes=pdf_bytes,
                        page_number=snapshot_page_number,
                    )
                except Exception as e:
                    raise RegionPublicationError(
                        f"Failed rendering page {snapshot_page_number} for job {job_id}: {e}"
                    ) from e

                try:
                    crop_bytes = self.doc_processor.crop_region_image(
                        page_jpeg_bytes=page_jpeg,
                        box=snapshot_effective_bbox,
                    )
                except Exception as e:
                    raise RegionPublicationError(
                        f"Failed cropping region {region_id} for job {job_id}: {e}"
                    ) from e

                if not crop_bytes:
                    raise RegionPublicationError(
                        f"Cannot crop visual region '{region_id}': crop returned empty bytes."
                    )

                staging_id = str(uuid.uuid4())
                staged_handle = self.staging_service.stage_crop(
                    job_id=job_id,
                    staging_id=staging_id,
                    region_id=region.region_id,
                    version=next_artifact_ver,
                    image_bytes=crop_bytes,
                )

            # Worker Staleness Guard: re-read region immediately before publication
            with self.uow_factory.create() as uow:
                current_region = uow.visual_regions.get_by_region_id(region_id)

            if (
                current_region is None
                or current_region.job_id != job_id
                or current_region.reviewed_bbox != snapshot_reviewed_bbox
                or current_region.updated_at != snapshot_updated_at
                or current_region.is_deleted != snapshot_is_deleted
            ):
                return RegionPublicationResultDTO(
                    job_id=job_id,
                    region_id=region_id,
                    success=False,
                    status_message="Stale worker snapshot: region was modified or deleted concurrently.",
                )

            # Assemble destination artifact URI and staged crops list
            if staged_handle is not None:
                dest_file = (
                    self.publication_service.artifacts_dir / f"job_{job_id}" / staged_handle.dest_filename
                ).resolve()
                dest_artifact_uri = dest_file.as_uri()
                staged_crops = [staged_handle]
            else:
                dest_artifact_uri = None
                staged_crops = None

            legacy_target = f"crop_{job_id}_p{snapshot_page_number}_{snapshot_display_order}.jpg"
            max_attempts = 3
            published_record = None

            # Bounded Optimistic Concurrency Control (OCC) retry loop
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    with self.uow_factory.create() as uow:
                        current_region = uow.visual_regions.get_by_region_id(region_id)
                    if (
                        current_region is None
                        or current_region.job_id != job_id
                        or current_region.reviewed_bbox != snapshot_reviewed_bbox
                        or current_region.updated_at != snapshot_updated_at
                        or current_region.is_deleted != snapshot_is_deleted
                    ):
                        return RegionPublicationResultDTO(
                            job_id=job_id,
                            region_id=region_id,
                            success=False,
                            status_message="Stale worker snapshot: region was modified or deleted concurrently.",
                        )

                with self.uow_factory.create() as uow:
                    latest_doc = uow.document_versions.get_latest(job_id)
                    if not latest_doc:
                        raise RegionPublicationError(
                            f"No active canonical document version found for job {job_id}."
                        )
                    base_version = latest_doc.version

                doc_path = resolve_canonical_file_path(latest_doc.output_path)
                if not doc_path.is_file():
                    raise ArtifactNotFoundError(latest_doc.output_path)

                current_markdown = doc_path.read_text(encoding="utf-8")

                if snapshot_is_deleted:
                    mutated_text = remove_visual_token(
                        text=current_markdown,
                        region_id=region_id,
                        legacy_target=legacy_target,
                    )
                else:
                    assert dest_artifact_uri is not None
                    mutated_text = upsert_visual_token(
                        text=current_markdown,
                        region_id=region_id,
                        occurrence_id=str(uuid.uuid4()),
                        artifact_uri=dest_artifact_uri,
                        page_number=snapshot_page_number,
                        alt_text=None,
                        legacy_target=legacy_target,
                    )

                try:
                    published_record = self.publication_service.publish_version(
                        job_id=job_id,
                        base_version=base_version,
                        markdown_text=mutated_text,
                        staged_crops=staged_crops,
                        published_by="VISUAL_REGION_REVIEW",
                    )
                    break
                except StaleDocumentVersionError:
                    if attempt == max_attempts:
                        with self.uow_factory.create() as uow:
                            uow.begin_immediate()
                            r = uow.visual_regions.get_by_region_id(region_id)
                            if r and r.job_id == job_id:
                                r.sync_status = SyncStatus.SYNC_FAILED
                                r.updated_at = datetime.now(timezone.utc)
                                uow.visual_regions.save(r)
                                uow.commit()
                        raise RegionPublicationError(
                            f"OCC retry exhausted ({max_attempts} attempts) publishing visual region {region_id} for job {job_id}."
                        )

            assert published_record is not None

            # Concurrency guard: update VisualRegion persistence state in SQLite
            region_synced = False
            with self.uow_factory.create() as uow:
                uow.begin_immediate()
                target_region = uow.visual_regions.get_by_region_id(region_id)
                if (
                    target_region is not None
                    and target_region.job_id == job_id
                    and target_region.reviewed_bbox == snapshot_reviewed_bbox
                    and target_region.updated_at == snapshot_updated_at
                    and target_region.is_deleted == snapshot_is_deleted
                ):
                    if snapshot_is_deleted:
                        target_region.active_artifact_uri = None
                        target_region.sync_status = SyncStatus.SYNCED
                    else:
                        assert next_artifact_ver is not None
                        target_region.active_artifact_version = next_artifact_ver
                        target_region.artifact_version_watermark = max(
                            target_region.artifact_version_watermark, next_artifact_ver
                        )
                        target_region.active_artifact_uri = dest_artifact_uri
                        target_region.sync_status = SyncStatus.SYNCED
                    target_region.updated_at = datetime.now(timezone.utc)
                    uow.visual_regions.save(target_region)
                    uow.commit()
                    region_synced = True
                elif target_region is not None and target_region.job_id == job_id:
                    # Region was modified concurrently during publication; advance watermark to prevent
                    # filename collision, but preserve dirty sync_status, newer bbox, and active_artifact_uri.
                    if next_artifact_ver is not None and next_artifact_ver > target_region.artifact_version_watermark:
                        target_region.artifact_version_watermark = next_artifact_ver
                        uow.visual_regions.save(target_region)
                        uow.commit()

            if not region_synced:
                return RegionPublicationResultDTO(
                    job_id=job_id,
                    region_id=region_id,
                    success=False,
                    document_version=published_record.version,
                    artifact_version=None,
                    artifact_uri=None,
                    status_message="Region was modified concurrently during publication; document published but region remains unsynced.",
                )

            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=True,
                document_version=published_record.version,
                artifact_version=next_artifact_ver if not snapshot_is_deleted else None,
                artifact_uri=dest_artifact_uri if not snapshot_is_deleted else None,
                status_message="Successfully published visual region review.",
            )
        finally:
            if staging_id:
                self.staging_service.discard_staging(staging_id)

    def reconcile_region_sync(
        self,
        job_id: int,
        region_id: str,
    ) -> RegionPublicationResultDTO:
        """
        Reconciles synchronization status based strictly on verifiable repository facts:
        1. The region exists in persistence.
        2. The active canonical Markdown contains a valid canonical visual token for that exact region.
        3. The token URI exactly corresponds to VisualRegion.active_artifact_uri.
        4. VisualRegion.active_artifact_version > 0.
        5. The artifact referenced by the active URI exists in the expected final job artifact area.

        If all five facts hold:
          - Sets sync_status = SYNCED.
          - Does NOT publish a duplicate document version.
          - Returns a successful result.
        Otherwise:
          - Returns an unsuccessful result without modifying the document or claiming sync.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            region = uow.visual_regions.get_by_region_id(region_id)
            if not region or region.job_id != job_id:
                raise EntityNotFoundError("VisualRegion", region_id)
            latest_doc = uow.document_versions.get_latest(job_id)

        # Fact 4 check: active_artifact_version > 0 and non-empty active_artifact_uri
        if region.active_artifact_version <= 0 or not region.active_artifact_uri:
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version if latest_doc else None,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message="Reconciliation failed: region active_artifact_version <= 0 or active_artifact_uri is empty.",
            )

        if not latest_doc or not latest_doc.output_path:
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=None,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message="Reconciliation failed: no active document version found for job.",
            )

        doc_file = resolve_canonical_file_path(latest_doc.output_path)
        if not doc_file.is_file():
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message=f"Reconciliation failed: canonical document file does not exist: {latest_doc.output_path}",
            )

        markdown_text = doc_file.read_text(encoding="utf-8")

        # Fact 2 & 3: active canonical Markdown contains valid canonical token matching region and active_artifact_uri
        try:
            tokens = find_canonical_tokens(markdown_text, region_id)
        except (ValueError, TypeError):
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message="Reconciliation failed: region_id is not a valid UUIDv4.",
            )

        if len(tokens) != 1:
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message=f"Reconciliation failed: expected 1 matching canonical token, found {len(tokens)}.",
            )

        matched_token = tokens[0]
        if matched_token.uri != region.active_artifact_uri:
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message="Reconciliation failed: token URI does not match active_artifact_uri.",
            )

        # Fact 5: artifact file exists on disk in the expected final job artifact area
        artifact_path = resolve_canonical_file_path(region.active_artifact_uri)
        expected_job_dir = (self.publication_service.artifacts_dir / f"job_{job_id}").resolve()

        if not artifact_path.is_file():
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message=f"Reconciliation failed: artifact file not found on disk: {artifact_path}",
            )

        try:
            resolved_artifact = artifact_path.resolve()
            if not resolved_artifact.is_relative_to(expected_job_dir):
                return RegionPublicationResultDTO(
                    job_id=job_id,
                    region_id=region_id,
                    success=False,
                    document_version=latest_doc.version,
                    artifact_version=region.active_artifact_version,
                    artifact_uri=region.active_artifact_uri,
                    status_message="Reconciliation failed: artifact path is outside the job directory.",
                )
        except (ValueError, RuntimeError):
            return RegionPublicationResultDTO(
                job_id=job_id,
                region_id=region_id,
                success=False,
                document_version=latest_doc.version,
                artifact_version=region.active_artifact_version,
                artifact_uri=region.active_artifact_uri,
                status_message="Reconciliation failed: could not resolve artifact path.",
            )

        # All five facts hold: transition to SYNCED without creating a duplicate document version
        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            target_reg = uow.visual_regions.get_by_region_id(region_id)
            if target_reg and target_reg.job_id == job_id:
                target_reg.sync_status = SyncStatus.SYNCED
                target_reg.updated_at = datetime.now(timezone.utc)
                uow.visual_regions.save(target_reg)
                uow.commit()

        return RegionPublicationResultDTO(
            job_id=job_id,
            region_id=region_id,
            success=True,
            document_version=latest_doc.version,
            artifact_version=region.active_artifact_version,
            artifact_uri=region.active_artifact_uri,
            status_message="Reconciliation verified all provable facts. Region marked SYNCED.",
        )
