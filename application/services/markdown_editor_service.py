# ============================================================
#  application/services/markdown_editor_service.py
#  Phase 10F.1 — Markdown Editor Service
# ============================================================

import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    DomainError,
    EntityNotFoundError,
    StaleDocumentVersionError,
)
from core.markdown import capture_canonical_markdown_snapshot, parse_canonical_markdown_version


class MarkdownEditorService:
    """
    Application service for raw Markdown document loading and optimistic concurrency-controlled saving.
    Interacts exclusively with application ports (IUnitOfWorkFactory, IArtifactStorage) and core domain entities.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory, storage: IArtifactStorage):
        self.uow_factory = uow_factory
        self.storage = storage

    def load_source_text(self, job_id: int) -> Tuple[str, int]:
        """
        Loads the raw canonical Markdown source text and its active version number.
        Returns:
            Tuple[str, int]: (markdown_text, active_version)
            If the job has no output markdown yet or the file is missing, returns ("", 0).
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            output_path = job.output_path

        if not output_path:
            return ("", 0)

        snapshot = capture_canonical_markdown_snapshot(output_path)
        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=output_path,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            job_id=job_id,
            filename=os.path.basename(output_path),
        )

        try:
            data = self.storage.retrieve(handle)
            return (data.decode("utf-8"), snapshot.active_version)
        except (ArtifactNotFoundError, DomainError, OSError, IOError):
            return ("", 0)

    def commit_source_text(self, job_id: int, raw_text: str, base_version: int) -> int:
        """
        Persists raw Markdown text as an immutable new versioned artifact under strict OCC.
        Watermark allocation and final pointer commit are executed under atomic transactions.

        Args:
            job_id: The job ID whose document is being saved.
            raw_text: The user-edited raw Markdown string.
            base_version: The version number the editor started editing from.

        Returns:
            int: The new canonical document version committed.

        Raises:
            StaleDocumentVersionError: If concurrent edits modified the active document version.
            EntityNotFoundError: If the job does not exist.
            DomainError: If pre-commit file validation fails.
        """
        # 1. Durable Watermark Reservation & OCC Check 1 (BEGIN IMMEDIATE)
        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            current_snapshot = capture_canonical_markdown_snapshot(job.output_path)
            if current_snapshot.active_version != base_version:
                raise StaleDocumentVersionError(
                    job_id=job_id,
                    base_version=base_version,
                    current_version=current_snapshot.active_version,
                    message=(
                        f"Cannot save markdown: document was modified concurrently before watermark reservation "
                        f"(base v{base_version} vs current v{current_snapshot.active_version})"
                    ),
                )

            target_version = max(job.output_artifact_version_watermark, current_snapshot.active_version) + 1
            job.output_artifact_version_watermark = target_version
            uow.jobs.save(job)
            uow.commit()

        # 2. Stage immutable new artifact to storage
        filename = f"output_{job_id}_v{target_version}.md"
        data_bytes = raw_text.encode("utf-8")
        output_handle = self.storage.store(
            job_id=job_id,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename=filename,
            data=data_bytes,
            mime_type="text/markdown",
        )

        # 3. Pre-Commit Validation: Assert newly staged artifact genuinely exists on disk
        if not self.storage.exists(output_handle):
            raise DomainError(f"Pre-commit invariant failed: Staged markdown '{filename}' missing from disk.")

        # 4. Final OCC Check 2 & SQLite Pointer Commit (BEGIN IMMEDIATE)
        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            job_record = uow.jobs.get_by_id(job_id)
            if not job_record:
                raise EntityNotFoundError("Job", job_id)

            final_snapshot = capture_canonical_markdown_snapshot(job_record.output_path)
            if final_snapshot.active_version != base_version:
                raise StaleDocumentVersionError(
                    job_id=job_id,
                    base_version=base_version,
                    current_version=final_snapshot.active_version,
                    message=(
                        f"Cannot save markdown: document was modified concurrently before final pointer commit "
                        f"(base v{base_version} vs current v{final_snapshot.active_version})"
                    ),
                )

            job_record.output_path = output_handle.uri
            job_record.updated_at = datetime.now(timezone.utc)
            uow.jobs.save(job_record)
            uow.commit()

        return target_version
