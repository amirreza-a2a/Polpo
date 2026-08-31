# ============================================================
#  application/ports/storage.py
# ============================================================

from abc import ABC, abstractmethod
from typing import BinaryIO, Optional
from core.entities.artifact import ArtifactHandle, ArtifactType


class IArtifactStorage(ABC):
    """
    درگاه ذخیره‌سازی و بازیابی فایل‌ها و آرتیفکت‌های سیستم.
    """

    @abstractmethod
    def store(
        self,
        job_id: int,
        artifact_type: ArtifactType,
        filename: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> ArtifactHandle:
        pass

    @abstractmethod
    def retrieve(self, handle: ArtifactHandle) -> bytes:
        pass

    @abstractmethod
    def open_stream(self, handle: ArtifactHandle) -> BinaryIO:
        pass

    @abstractmethod
    def exists(self, handle: ArtifactHandle) -> bool:
        pass

    @abstractmethod
    def delete(self, handle: ArtifactHandle) -> bool:
        pass

    @abstractmethod
    def cleanup_job_artifacts(self, job_id: int) -> None:
        pass

    @abstractmethod
    def prune_expired(self, max_age_hours: int = 48) -> int:
        pass
