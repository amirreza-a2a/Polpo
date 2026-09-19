# ============================================================
#  application/services/markdown_editor_service.py
#  Phase 10E.3a: Migrated to Publication Authority (Ticket 10E.3a-09)
# ============================================================

import os
from typing import Optional, Tuple

from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.document_publication_service import DocumentPublicationService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    DomainError,
    EntityNotFoundError,
)
from core.markdown import capture_canonical_markdown_snapshot


class MarkdownEditorService:
    """
    Application service for raw Markdown document loading and optimistic concurrency-controlled saving.
    Delegates document publication and OCC authority exclusively to DocumentPublicationService.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        document_publication_service: Optional[DocumentPublicationService] = None,
    ):
        self.uow_factory = uow_factory
        self.storage = storage

        if document_publication_service is not None:
            self.document_publication_service: Optional[DocumentPublicationService] = document_publication_service
        elif hasattr(storage, "base_dir"):
            self.document_publication_service = DocumentPublicationService(
                uow_factory=self.uow_factory,
                artifacts_dir=getattr(storage, "base_dir", "."),
            )
        else:
            self.document_publication_service = None

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
            latest_doc = None
            if hasattr(uow, "document_versions"):
                latest_doc = uow.document_versions.get_latest(job_id)

        if not output_path:
            return ("", 0)

        if latest_doc:
            active_version = latest_doc.version
        else:
            snapshot = capture_canonical_markdown_snapshot(output_path)
            active_version = snapshot.active_version

        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=output_path,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            job_id=job_id,
            filename=os.path.basename(output_path),
        )

        try:
            data = self.storage.retrieve(handle)
            return (data.decode("utf-8"), active_version)
        except (ArtifactNotFoundError, DomainError, OSError, IOError):
            return ("", 0)

    def commit_source_text(
        self,
        job_id: int,
        raw_text: Optional[str] = None,
        base_version: int = 0,
        new_text: Optional[str] = None,
    ) -> int:
        """
        Commits user-edited Markdown text by delegating to DocumentPublicationService.publish_version().
        Enforces optimistic concurrency control (OCC) against document_versions.
        Returns the newly published document version integer.

        Args:
            job_id: The job ID whose document is being saved.
            raw_text: The user-edited raw Markdown string.
            base_version: The base document version the editor started editing from.
            new_text: Optional alias for raw_text.

        Returns:
            int: The newly published canonical document version.

        Raises:
            StaleDocumentVersionError: If base_version does not match current document version.
            CanonicalDocumentIntegrityError: If document is quarantined.
            PublicationInProgressError: If publication intent is already in progress.
            EntityNotFoundError: If the job does not exist.
        """
        if self.document_publication_service is None:
            raise RuntimeError("DocumentPublicationService is required for document publication.")

        text_to_save = raw_text if raw_text is not None else (new_text if new_text is not None else "")

        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            latest_doc = None
            if hasattr(uow, "document_versions"):
                latest_doc = uow.document_versions.get_latest(job_id)

        if base_version == 0 and latest_doc is None:
            record = self.document_publication_service.publish_initial(
                job_id=job_id,
                markdown_text=text_to_save,
                published_by="MARKDOWN_EDITOR",
            )
            return record.version

        record = self.document_publication_service.publish_version(
            job_id=job_id,
            base_version=base_version,
            markdown_text=text_to_save,
            staged_crops=[],
            published_by="MARKDOWN_EDITOR",
        )
        return record.version
