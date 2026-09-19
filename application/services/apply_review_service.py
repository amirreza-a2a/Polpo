# ============================================================
#  application/services/apply_review_service.py
#  Phase 10B — Apply & Re-crop Transaction Engine
# ============================================================

import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from application.dto.visual_region_dto import ApplyReviewResultDTO
from application.ports.document_processor import IDocumentProcessor
from application.ports.notifier import IApplicationEventPublisher
from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox, CropPolicy
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    DomainError,
    EntityNotFoundError,
    StaleDocumentVersionError,
)
from core.markdown import capture_canonical_markdown_snapshot, parse_canonical_markdown_version


class ApplyReviewService:
    """
    [QUARANTINED LEGACY SERVICE EXCEPTION]
    This service contains legacy direct canonical Markdown writing logic and is quarantined
    from runtime execution in Phase 10E.3a. It is disconnected at the desktop composition level.
    Formal replacement/removal scheduled for Phase 10E.3b.

    Application Service implementing the Recoverable Immutable Staging and Re-crop Engine.
    Coordinates on-demand page raster rendering, bounding box coordinate cropping,
    immutable versioned artifact staging, canonical Markdown document regeneration,
    and SQLite-authoritative active state commitment.

    Guarantees:
      - Durable Watermark Reservation: Allocation watermarks are persisted to SQLite in a dedicated
        transaction BEFORE any artifact staging begins. If staging, rendering, or active-state commit fails,
        the reserved version number is permanently burned and NEVER reused on subsequent retries.
      - Active SQLite state references ONLY complete, verified immutable artifacts.
      - Previous active artifacts (both crops and markdown) are NEVER overwritten during preparation.
      - Staged uncommitted artifacts are treated as harmless orphan candidates.
      - Monotonic artifact version allocation: once version N is allocated for a region or document,
        version N is NEVER reused across retry, rollback, rejection, restore, or garbage collection.
      - Clean first-version convention: first generated markdown is v1, subsequent generations are v2, v3...
      - Rejected regions have active_artifact_uri = None and are omitted from active Markdown.
      - Pre-commit artifact validation asserts all staged files genuinely exist on disk.
      - Deterministic, idempotent cleanup for superseded historical artifacts.
      - Per-job serialized execution preventing concurrent race conditions on the same job.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        doc_processor: IDocumentProcessor,
        crop_policy: Optional[CropPolicy] = None,
        event_publisher: Optional[IApplicationEventPublisher] = None,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.doc_processor = doc_processor
        self.crop_policy = crop_policy or CropPolicy()
        self.event_publisher = event_publisher
        self._manager_lock = threading.Lock()
        self._job_locks: Dict[int, threading.Lock] = {}

    def _get_job_lock(self, job_id: int) -> threading.Lock:
        """Retrieves or creates a dedicated logical lock for serializing Apply operations on a single job."""
        with self._manager_lock:
            if job_id not in self._job_locks:
                self._job_locks[job_id] = threading.Lock()
            return self._job_locks[job_id]

    def apply_reviews(
        self,
        job_id: int,
        region_ids: Optional[List[str]] = None,
    ) -> ApplyReviewResultDTO:
        """
        Main entry point for applying visual region reviews for a job.
        Serializes execution per job and guarantees recoverable immutable versioning.
        """
        with self._get_job_lock(job_id):
            return self._execute_apply(job_id=job_id, region_ids=region_ids)

    def _parse_job_md_version(self, output_path: Optional[str]) -> int:
        """Extracts the active version number from output markdown URI or returns 0 if none exists."""
        return parse_canonical_markdown_version(output_path)

    def _execute_apply(
        self,
        job_id: int,
        region_ids: Optional[List[str]] = None,
    ) -> ApplyReviewResultDTO:
        # 1. Load current active state (Job and VisualRegions) from SQLite
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            all_regions = uow.visual_regions.get_by_job_id(job_id)

        base_snapshot = capture_canonical_markdown_snapshot(job.output_path)

        target_set: Optional[Set[str]] = set(region_ids) if region_ids is not None else None

        # Determine which regions require new crops vs sync reconciliation
        dirty_regions: List[VisualRegion] = []
        rejected_regions: List[VisualRegion] = []

        for r in all_regions:
            if target_set is not None and r.region_id not in target_set:
                continue

            if r.is_deleted:
                if r.sync_status != SyncStatus.SYNCED:
                    rejected_regions.append(r)
            else:
                if r.sync_status in (
                    SyncStatus.DIRTY_RECROP_REQUIRED,
                    SyncStatus.PENDING_INITIAL_CROP,
                    SyncStatus.SYNC_FAILED,
                ):
                    dirty_regions.append(r)

        # Idempotency check: zero dirty regions and zero rejected regions needing sync
        if not dirty_regions and not rejected_regions:
            return ApplyReviewResultDTO(
                job_id=job_id,
                applied_count=0,
                updated_region_ids=[],
                output_markdown_uri=job.output_path,
                success=True,
            )

        # 2. DURABLE WATERMARK RESERVATION (BEGIN IMMEDIATE committed to SQLite BEFORE staging!)
        reserved_crop_versions: Dict[str, int] = {}
        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            job_record = uow.jobs.get_by_id(job_id)
            if not job_record:
                raise EntityNotFoundError("Job", job_id)

            # OCC Check 1: verify job.output_path matches base_snapshot
            curr_snap = capture_canonical_markdown_snapshot(job_record.output_path)
            if (curr_snap.version != base_snapshot.version) or (curr_snap.output_path != base_snapshot.output_path):
                raise StaleDocumentVersionError(
                    job_id=job_id,
                    base_version=base_snapshot.version,
                    current_version=curr_snap.version,
                    message=(
                        f"Canonical markdown document was modified concurrently before watermark reservation "
                        f"(base v{base_snapshot.version} vs current v{curr_snap.version})"
                    ),
                )

            curr_active_md_ver = base_snapshot.version
            watermark_md = max(job_record.output_artifact_version_watermark, curr_active_md_ver)
            reserved_md_version = watermark_md + 1
            job_record.output_artifact_version_watermark = reserved_md_version
            uow.jobs.save(job_record)

            for region in dirty_regions:
                watermark_region = max(region.artifact_version_watermark, region.active_artifact_version)
                reserved_v = watermark_region + 1
                region.artifact_version_watermark = reserved_v
                reserved_crop_versions[region.region_id] = reserved_v
                uow.visual_regions.save(region)

            uow.commit()  # Watermark reservations are permanently committed to SQLite!

        # 3. Retrieve source PDF bytes from local artifact storage
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
            raise DomainError(f"Cannot apply reviews for job {job_id}: Source PDF missing ({e})") from e

        # 4. Stage new immutable crops using the RESERVED versions
        staged_crops: Dict[str, Tuple[ArtifactHandle, int]] = {}  # region_id -> (staged_handle, target_version)
        page_jpeg_cache: Dict[int, bytes] = {}

        for region in dirty_regions:
            if region.page_number not in page_jpeg_cache:
                try:
                    page_jpeg = self.doc_processor.render_page_to_jpeg(
                        pdf_bytes,
                        region.page_number,
                    )
                    page_jpeg_cache[region.page_number] = page_jpeg
                except Exception as e:
                    raise DomainError(
                        f"Failed rendering page {region.page_number} for region {region.region_id}: {e}"
                    ) from e

            page_jpeg = page_jpeg_cache[region.page_number]
            crop_bytes = self.doc_processor.crop_region_image(
                page_jpeg_bytes=page_jpeg,
                box=region.effective_bbox,
                policy=self.crop_policy,
            )

            if not crop_bytes:
                raise DomainError(
                    f"Cannot crop visual region '{region.region_id}': mapped coordinates are invalid or below minimum size."
                )

            target_version = reserved_crop_versions[region.region_id]
            crop_filename = f"crop_{job_id}_{region.region_id}_v{target_version}.jpg"

            # Write to distinct immutable versioned location
            staged_handle = self.storage.store(
                job_id=job_id,
                artifact_type=ArtifactType.CROPPED_IMAGE,
                filename=crop_filename,
                data=crop_bytes,
                mime_type="image/jpeg",
            )
            staged_crops[region.region_id] = (staged_handle, target_version)

        # 5. Regenerate and Stage Immutable Versioned Markdown using reserved_md_version
        target_md_version = reserved_md_version
        canonical_text: Optional[str] = None

        if base_snapshot.output_path:
            try:
                base_handle = ArtifactHandle(
                    storage_backend=StorageBackendType.LOCAL_FS,
                    uri=base_snapshot.output_path,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    job_id=job.id,
                    filename=os.path.basename(base_snapshot.output_path),
                )
                if self.storage.exists(base_handle):
                    canonical_text = self.storage.retrieve(base_handle).decode("utf-8")
            except Exception:
                canonical_text = None

        if canonical_text is not None:
            updated_md = canonical_text

            for r in rejected_regions:
                tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]\s*", re.MULTILINE)
                tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{r.page_number}_{r.display_order}\.jpg[^\]]*\]\]\s*", re.MULTILINE)
                updated_md = tag_pattern_1.sub("", updated_md)
                updated_md = tag_pattern_2.sub("", updated_md)

            for r in all_regions:
                if r.is_deleted:
                    continue

                if r.region_id in staged_crops:
                    _, v = staged_crops[r.region_id]
                    active_filename = f"crop_{job_id}_{r.region_id}_v{v}.jpg"
                elif r.active_artifact_uri:
                    active_filename = os.path.basename(r.active_artifact_uri)
                else:
                    active_filename = f"crop_{job_id}_{r.region_id}_v{r.active_artifact_version}.jpg"

                new_token = f"![[{active_filename}|region_id={r.region_id}]]"

                tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]")
                tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{r.page_number}_{r.display_order}\.jpg[^\]]*\]\]")

                if tag_pattern_1.search(updated_md):
                    updated_md = tag_pattern_1.sub(new_token, updated_md, count=1)
                elif tag_pattern_2.search(updated_md):
                    updated_md = tag_pattern_2.sub(new_token, updated_md, count=1)
                else:
                    page_marker = f"<!-- Page {r.page_number} -->"
                    if page_marker in updated_md:
                        p_idx = updated_md.find(page_marker)
                        after_p = updated_md[p_idx + len(page_marker):]
                        next_marker_match = re.search(r"\n<!-- Page \d+ -->", after_p)
                        if next_marker_match:
                            insert_pos = p_idx + len(page_marker) + next_marker_match.start()
                            updated_md = updated_md[:insert_pos].rstrip() + f"\n\n{new_token}\n\n" + updated_md[insert_pos:].lstrip()
                        else:
                            updated_md = updated_md.rstrip() + f"\n\n{new_token}\n"
                    else:
                        updated_md = updated_md.rstrip() + f"\n\n{new_token}\n"

            output_content = updated_md

            # If page-level artifacts exist, update page_{p}_v{target_md_version}.md for consistency
            total_pages = max(job.total_pages or 1, max((r.page_number for r in all_regions), default=1))
            for p in range(1, total_pages + 1):
                candidate_handles = []
                if base_snapshot.version > 0:
                    candidate_handles.append(ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, job.id, f"page_{p}_v{base_snapshot.version}.md"))
                candidate_handles.append(ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, job.id, f"page_{p}.md"))

                p_handle = None
                for ch in candidate_handles:
                    if self.storage.exists(ch):
                        p_handle = ch
                        break

                if p_handle:
                    try:
                        p_text = self.storage.retrieve(p_handle).decode("utf-8")
                        for r in rejected_regions:
                            if r.page_number == p:
                                tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]\s*", re.MULTILINE)
                                tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{p}_{r.display_order}\.jpg[^\]]*\]\]\s*", re.MULTILINE)
                                p_text = tag_pattern_1.sub("", p_text)
                                p_text = tag_pattern_2.sub("", p_text)

                        for r in all_regions:
                            if r.is_deleted or r.page_number != p:
                                continue
                            if r.region_id in staged_crops:
                                _, v = staged_crops[r.region_id]
                                active_filename = f"crop_{job_id}_{r.region_id}_v{v}.jpg"
                            elif r.active_artifact_uri:
                                active_filename = os.path.basename(r.active_artifact_uri)
                            else:
                                active_filename = f"crop_{job_id}_{r.region_id}_v{r.active_artifact_version}.jpg"

                            new_token = f"![[{active_filename}|region_id={r.region_id}]]"
                            tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]")
                            tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{p}_{r.display_order}\.jpg[^\]]*\]\]")

                            if tag_pattern_1.search(p_text):
                                p_text = tag_pattern_1.sub(new_token, p_text, count=1)
                            elif tag_pattern_2.search(p_text):
                                p_text = tag_pattern_2.sub(new_token, p_text, count=1)
                            else:
                                p_text = p_text.rstrip() + f"\n\n{new_token}\n"

                        self.storage.store(
                            job_id=job_id,
                            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                            filename=f"page_{p}_v{target_md_version}.md",
                            data=p_text.encode("utf-8"),
                            mime_type="text/markdown",
                        )
                    except Exception:
                        pass
        else:
            assembled_pages: List[str] = []
            total_pages = max(job.total_pages or 1, max((r.page_number for r in all_regions), default=1))

            for p in range(1, total_pages + 1):
                page_text = f"<!-- Page {p} -->\n"
                candidate_handles = []
                if base_snapshot.version > 0:
                    candidate_handles.append(ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, job.id, f"page_{p}_v{base_snapshot.version}.md"))
                candidate_handles.append(ArtifactHandle(StorageBackendType.LOCAL_FS, "", ArtifactType.OUTPUT_MARKDOWN, job.id, f"page_{p}.md"))

                for ch in candidate_handles:
                    try:
                        if self.storage.exists(ch):
                            page_text = self.storage.retrieve(ch).decode("utf-8")
                            break
                    except Exception:
                        pass

                page_regions = sorted(
                    [r for r in all_regions if r.page_number == p],
                    key=lambda x: x.display_order,
                )

                for r in page_regions:
                    if r.is_deleted:
                        tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]\s*", re.MULTILINE)
                        tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{p}_{r.display_order}\.jpg[^\]]*\]\]\s*", re.MULTILINE)
                        page_text = tag_pattern_1.sub("", page_text)
                        page_text = tag_pattern_2.sub("", page_text)
                    else:
                        if r.region_id in staged_crops:
                            _, v = staged_crops[r.region_id]
                            active_filename = f"crop_{job_id}_{r.region_id}_v{v}.jpg"
                        elif r.active_artifact_uri:
                            active_filename = os.path.basename(r.active_artifact_uri)
                        else:
                            active_filename = f"crop_{job_id}_{r.region_id}_v{r.active_artifact_version}.jpg"

                        new_token = f"![[{active_filename}|region_id={r.region_id}]]"

                        tag_pattern_1 = re.compile(rf"!\[\[[^\]]*{re.escape(r.region_id)}[^\]]*\]\]")
                        tag_pattern_2 = re.compile(rf"!\[\[crop_{job_id}_p{p}_{r.display_order}\.jpg[^\]]*\]\]")

                        if tag_pattern_1.search(page_text):
                            page_text = tag_pattern_1.sub(new_token, page_text, count=1)
                        elif tag_pattern_2.search(page_text):
                            page_text = tag_pattern_2.sub(new_token, page_text, count=1)
                        else:
                            page_text = page_text.rstrip() + f"\n\n{new_token}\n"

                self.storage.store(
                    job_id=job_id,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    filename=f"page_{p}_v{target_md_version}.md",
                    data=page_text.encode("utf-8"),
                    mime_type="text/markdown",
                )
                assembled_pages.append(page_text)

            output_content = self.doc_processor.unify_markdown("\n".join(assembled_pages))

        # Stage updated versioned output markdown (never overwriting old output version)
        output_handle = self.storage.store(
            job_id=job_id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=f"output_{job_id}_v{target_md_version}.md",
            data=output_content.encode("utf-8"),
            mime_type="text/markdown",
        )

        # 6. Pre-Commit Validation: Assert all newly staged files genuinely exist on disk
        for handle, _ in staged_crops.values():
            if not self.storage.exists(handle):
                raise DomainError(f"Pre-commit invariant failed: Staged crop '{handle.filename}' missing from disk.")
        if not self.storage.exists(output_handle):
            raise DomainError(f"Pre-commit invariant failed: Staged markdown '{output_handle.filename}' missing from disk.")

        # 7. SQLite-Authoritative Active State Commit (BEGIN IMMEDIATE)
        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            job_record = uow.jobs.get_by_id(job_id)
            if not job_record:
                raise EntityNotFoundError("Job", job_id)

            # OCC Check 2: verify current canonical document version and output_path still match base_snapshot
            final_snap = capture_canonical_markdown_snapshot(job_record.output_path)
            if (final_snap.version != base_snapshot.version) or (final_snap.output_path != base_snapshot.output_path):
                raise StaleDocumentVersionError(
                    job_id=job_id,
                    base_version=base_snapshot.version,
                    current_version=final_snap.version,
                    message=(
                        f"Canonical markdown document was modified concurrently before final commit "
                        f"(base v{base_snapshot.version} vs current v{final_snap.version})"
                    ),
                )

            for region in all_regions:
                if region.region_id in staged_crops:
                    handle, new_ver = staged_crops[region.region_id]
                    region.active_artifact_uri = handle.uri
                    region.active_artifact_version = new_ver
                    region.sync_status = SyncStatus.SYNCED
                    if region.review_status in (ReviewStatus.MODIFIED, ReviewStatus.MANUAL):
                        region.review_status = ReviewStatus.ACCEPTED
                    region.updated_at = datetime.now(timezone.utc)
                    uow.visual_regions.save(region)
                elif region.is_deleted and region.sync_status != SyncStatus.SYNCED:
                    # Rejected region: clear active artifact pointer since it is no longer in the active document
                    region.active_artifact_uri = None
                    region.sync_status = SyncStatus.SYNCED
                    region.updated_at = datetime.now(timezone.utc)
                    uow.visual_regions.save(region)

            job_record.output_path = output_handle.uri
            job_record.updated_at = datetime.now(timezone.utc)
            uow.jobs.save(job_record)
            uow.commit()

        # 8. Return Result
        updated_ids = list(staged_crops.keys()) + [r.region_id for r in rejected_regions]
        return ApplyReviewResultDTO(
            job_id=job_id,
            applied_count=len(updated_ids),
            updated_region_ids=updated_ids,
            output_markdown_uri=output_handle.uri,
            success=True,
        )

    def cleanup_superseded_artifacts(self, job_id: int) -> int:
        """
        Idempotent garbage collection: locates and prunes superseded older version
        crop and markdown files from disk while preserving the authoritative active versions in SQLite.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            regions = uow.visual_regions.get_by_job_id(job_id)

        active_uris = {r.active_artifact_uri for r in regions if r.active_artifact_uri}
        if job and job.output_path:
            active_uris.add(job.output_path)
        if job and job.file_path:
            active_uris.add(job.file_path)

        pruned_count = 0

        # 1. Prune superseded crops (all versions up to watermark that are not active)
        for r in regions:
            watermark = max(r.artifact_version_watermark, r.active_artifact_version)
            for v in range(1, watermark + 1):
                old_filename = f"crop_{job_id}_{r.region_id}_v{v}.jpg"
                old_handle = ArtifactHandle(
                    storage_backend=StorageBackendType.LOCAL_FS,
                    uri="",
                    artifact_type=ArtifactType.CROPPED_IMAGE,
                    job_id=job_id,
                    filename=old_filename,
                )
                if self.storage.exists(old_handle):
                    resolved_uri = None
                    if hasattr(self.storage, "_resolve_path"):
                        try:
                            resolved_uri = f"file://{self.storage._resolve_path(old_handle)}"
                        except Exception:
                            pass
                    if resolved_uri not in active_uris and self.storage.delete(old_handle):
                        pruned_count += 1

        # 2. Prune superseded markdown versions (all versions up to watermark that are not active)
        if job:
            watermark_md = max(job.output_artifact_version_watermark, self._parse_job_md_version(job.output_path))
            total_pages = max(job.total_pages or 1, max((r.page_number for r in regions), default=1))
            for v in range(1, watermark_md + 1):
                old_md_names = [f"output_{job_id}_v{v}.md"]
                for p in range(1, total_pages + 1):
                    old_md_names.append(f"page_{p}_v{v}.md")

                for name in old_md_names:
                    old_handle = ArtifactHandle(
                        storage_backend=StorageBackendType.LOCAL_FS,
                        uri="",
                        artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                        job_id=job_id,
                        filename=name,
                    )
                    if self.storage.exists(old_handle):
                        resolved_uri = None
                        if hasattr(self.storage, "_resolve_path"):
                            try:
                                resolved_uri = f"file://{self.storage._resolve_path(old_handle)}"
                            except Exception:
                                pass
                        if resolved_uri not in active_uris and self.storage.delete(old_handle):
                            pruned_count += 1

        return pruned_count

    def reconcile_orphaned_artifacts(self, job_id: int) -> List[str]:
        """
        Discovers unreferenced orphan candidate files on disk for a job.
        Returns list of unreferenced filenames.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            regions = uow.visual_regions.get_by_job_id(job_id)

        active_uris = {r.active_artifact_uri for r in regions if r.active_artifact_uri}
        if job and job.output_path:
            active_uris.add(job.output_path)
        if job and job.file_path:
            active_uris.add(job.file_path)

        orphans = []
        if hasattr(self.storage, "base_dir"):
            job_dir = self.storage.base_dir / f"job_{job_id}"
            if job_dir.exists() and job_dir.is_dir():
                for item in job_dir.iterdir():
                    if item.is_file():
                        item_uri = f"file://{item.resolve()}"
                        if item_uri not in active_uris:
                            orphans.append(item.name)
        return orphans
