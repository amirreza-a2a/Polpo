# ============================================================
#  application/services/document_viewer_service.py
#  Document Page Raster Rendering & Read-Only Region Geometry Service
# ============================================================

from typing import List, Optional

from application.dto.document_viewer_dto import PageRasterDTO, VisualRegionOverlayItemDTO
from application.dto.visual_region_dto import VisualRegionDTO
from application.ports.document_processor import IDocumentProcessor
from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.visual_region import VisualRegion
from core.geometry.coordinates import (
    CoordinateTransformer,
    DisplayedImageMetrics,
    FitMode,
)
from core.exceptions.domain_exceptions import ArtifactNotFoundError, DomainError, EntityNotFoundError


class DocumentViewerService:
    """
    Application Service responsible for PDF document page raster retrieval,
    caching displayable page raster representations, querying active page visual regions,
    and calculating presentation-level coordinate mappings.

    Guarantees:
      - Independent from Qt / PySide6 / QML presentation frameworks.
      - Page isolation: queries strictly partition visual regions by page_number.
      - Read-only semantic projection: rejected regions are excluded from active document visual overlays.
      - Deterministic coordinate translation using the core CoordinateTransformer.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
        default_dpi: int = 150,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor
        self.default_dpi = default_dpi

    def get_page_raster(
        self,
        job_id: int,
        page_number: int,
        dpi: Optional[int] = None,
    ) -> PageRasterDTO:
        """
        Renders or retrieves the displayable JPEG raster for the specified PDF page.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

        target_dpi = dpi or self.default_dpi

        source_handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=job.file_path,
            artifact_type=ArtifactType.SOURCE_PDF,
            job_id=job.id,
            filename=job.file_name,
        )

        try:
            pdf_bytes = self.storage.retrieve(source_handle)
        except (ArtifactNotFoundError, IOError, OSError) as e:
            raise DomainError(f"Cannot render page for job {job_id}: Source PDF missing ({e})") from e

        total_pages = self.doc_processor.get_page_count(pdf_bytes)
        if page_number < 1 or page_number > total_pages:
            raise DomainError(
                f"Requested page {page_number} is out of document bounds (1..{total_pages}) for job {job_id}."
            )

        render_filename = f"page_render_{page_number}_dpi{target_dpi}.jpg"
        render_handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri="",
            artifact_type=ArtifactType.PAGE_IMAGE,
            job_id=job.id,
            filename=render_filename,
        )

        # Use cached render if it exists
        if self.storage.exists(render_handle):
            jpeg_bytes = self.storage.retrieve(render_handle)
        else:
            try:
                jpeg_bytes = self.doc_processor.render_page_to_jpeg(
                    pdf_bytes=pdf_bytes,
                    page_number=page_number,
                    dpi=target_dpi,
                )
            except Exception as e:
                raise DomainError(f"Failed rendering page {page_number} for job {job_id}: {e}") from e

            render_handle = self.storage.store(
                job_id=job.id,
                artifact_type=ArtifactType.PAGE_IMAGE,
                filename=render_filename,
                data=jpeg_bytes,
                mime_type="image/jpeg",
            )

        raster_w, raster_h = self.doc_processor.get_image_dimensions(jpeg_bytes)

        return PageRasterDTO(
            job_id=job_id,
            page_number=page_number,
            total_pages=total_pages,
            raster_width=raster_w,
            raster_height=raster_h,
            image_uri=render_handle.uri,
            dpi=target_dpi,
        )

    def get_active_page_regions(
        self,
        job_id: int,
        page_number: int,
    ) -> List[VisualRegionDTO]:
        """
        Retrieves active (non-rejected) visual regions for the specified job and page.
        Enforces strict page isolation and filters out deleted/rejected regions.
        """
        with self.uow_factory.create() as uow:
            regions = uow.visual_regions.get_by_job_and_page(job_id, page_number)

        # Exclude rejected regions
        active = [r for r in regions if not r.is_deleted]
        active.sort(key=lambda r: r.display_order)

        return [self._to_dto(r) for r in active]

    def calculate_page_overlay_rects(
        self,
        job_id: int,
        page_number: int,
        item_width: float,
        item_height: float,
        raster_width: float,
        raster_height: float,
        fit_mode: FitMode = FitMode.PRESERVE_ASPECT_FIT,
    ) -> List[VisualRegionOverlayItemDTO]:
        """
        Transforms all active visual regions on a page into item coordinate rectangles (S_item).
        """
        if item_width <= 0 or item_height <= 0 or raster_width <= 0 or raster_height <= 0:
            return []

        active_regions = self.get_active_page_regions(job_id, page_number)
        metrics = DisplayedImageMetrics(
            raster_width=raster_width,
            raster_height=raster_height,
            item_width=item_width,
            item_height=item_height,
            fit_mode=fit_mode,
        )

        overlay_items: List[VisualRegionOverlayItemDTO] = []
        for r in active_regions:
            item_rect = CoordinateTransformer.normalized_to_item_rect(r.effective_bbox, metrics)
            overlay_items.append(
                VisualRegionOverlayItemDTO(
                    region_id=r.region_id,
                    job_id=r.job_id,
                    page_number=r.page_number,
                    display_order=r.display_order,
                    origin=r.origin,
                    review_status=r.review_status,
                    effective_bbox=r.effective_bbox,
                    x=item_rect.x,
                    y=item_rect.y,
                    width=item_rect.width,
                    height=item_rect.height,
                )
            )

        return overlay_items

    @staticmethod
    def _to_dto(region: VisualRegion) -> VisualRegionDTO:
        return VisualRegionDTO(
            id=region.id,
            region_id=region.region_id,
            job_id=region.job_id,
            page_number=region.page_number,
            display_order=region.display_order,
            origin=region.origin.value,
            review_status=region.review_status.value,
            sync_status=region.sync_status.value,
            effective_bbox=region.effective_bbox,
            detected_bbox=region.detected_bbox,
            reviewed_bbox=region.reviewed_bbox,
            active_artifact_version=region.active_artifact_version,
            active_artifact_uri=region.active_artifact_uri,
            is_modified=region.is_modified,
            is_deleted=region.is_deleted,
            created_at=region.created_at.isoformat() if region.created_at else None,
            updated_at=region.updated_at.isoformat() if region.updated_at else None,
        )
