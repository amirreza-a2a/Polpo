# ============================================================
#  application/services/artifact_service.py
# ============================================================

from typing import BinaryIO, Tuple
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.storage import IArtifactStorage
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.exceptions.domain_exceptions import EntityNotFoundError, ArtifactNotFoundError


class ArtifactService:
    """
    سرویس دسترسی و بازیابی آرتیفکت‌های خروجی کارها جهت دانلود در REST و کلاینت دسکتاپ.
    """

    def __init__(self, storage: IArtifactStorage, uow_factory: IUnitOfWorkFactory):
        self.storage = storage
        self.uow_factory = uow_factory

    def get_job_artifact_stream(
        self,
        job_id: int,
        user_id: int,
        artifact_type: str = "output_markdown",
    ) -> Tuple[BinaryIO, str, str]:
        """
        واکشی جریان دودویی (Stream) یک آرتیفکت خروجی با اعتبارسنجی مالکیت کاربر.
        خروجی: (stream, filename, mime_type)
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)
            if job.user_id != user_id:
                raise EntityNotFoundError("Job", job_id)

        at = ArtifactType(artifact_type)
        filename = f"output_{job_id}.md" if at == ArtifactType.OUTPUT_MARKDOWN else f"artifact_{job_id}.bin"
        mime_type = "text/markdown" if at == ArtifactType.OUTPUT_MARKDOWN else "application/octet-stream"

        handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=job.output_path or f"file:///output_{job_id}.md",
            artifact_type=at,
            job_id=job_id,
            filename=filename,
            mime_type=mime_type,
        )

        stream = self.storage.open_stream(handle)
        return stream, filename, mime_type

    def get_artifact(
        self,
        user_id: int,
        job_id: int,
        artifact_type: str = "output_markdown",
    ):
        from application.dto.job_dto import ArtifactDownloadDTO
        stream, filename, mime_type = self.get_job_artifact_stream(
            job_id=job_id,
            user_id=user_id,
            artifact_type=artifact_type,
        )
        try:
            data = stream.read()
        finally:
            stream.close()
        return ArtifactDownloadDTO(data=data, filename=filename, mime_type=mime_type)


    def prune_expired_artifacts(self, max_age_hours: int = 48) -> int:
        return self.storage.prune_expired(max_age_hours)
