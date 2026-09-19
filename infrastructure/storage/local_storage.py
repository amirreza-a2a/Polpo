# ============================================================
#  infrastructure/storage/local_storage.py
# ============================================================

import os
import shutil
import time
from pathlib import Path
from typing import BinaryIO, Optional
from application.ports.storage import IArtifactStorage
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.exceptions.domain_exceptions import ArtifactNotFoundError, DomainError
from config import OUTPUT_DIR


class LocalStorageAdapter(IArtifactStorage):
    """
    Secure local filesystem artifact storage adapter enforcing strict path containment validation.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or OUTPUT_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, filename: str) -> str:
        base = os.path.basename(filename).strip()
        # Strip dangerous characters and directory traversal elements
        clean = base.replace("..", "").replace("/", "").replace("\\", "")
        return clean or "unnamed_artifact"

    def store(
        self,
        job_id: int,
        artifact_type: ArtifactType,
        filename: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> ArtifactHandle:
        clean_name = self._sanitize_filename(filename)
        job_dir = (self.base_dir / f"job_{job_id}").resolve()
        job_dir.mkdir(parents=True, exist_ok=True)

        file_path = (job_dir / clean_name).resolve()

        # Path traversal protection check
        if not file_path.is_relative_to(job_dir):
            raise DomainError(f"Security error: Artifact path '{filename}' escapes job directory.")

        temp_file = job_dir / f".{clean_name}.{os.getpid()}.{time.time_ns()}.tmp"
        try:
            with open(temp_file, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, file_path)
        except Exception:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise

        uri = f"file://{file_path}"
        return ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=uri,
            artifact_type=artifact_type,
            job_id=job_id,
            filename=clean_name,
            size_bytes=len(data),
            mime_type=mime_type,
            metadata={"path": str(file_path)},
        )

    def retrieve(self, handle: ArtifactHandle) -> bytes:
        file_path = self._resolve_path(handle)
        if not file_path.exists():
            raise ArtifactNotFoundError(handle.uri)
        with open(file_path, "rb") as f:
            return f.read()

    def open_stream(self, handle: ArtifactHandle) -> BinaryIO:
        file_path = self._resolve_path(handle)
        if not file_path.exists():
            raise ArtifactNotFoundError(handle.uri)
        return open(file_path, "rb")

    def exists(self, handle: ArtifactHandle) -> bool:
        try:
            return self._resolve_path(handle).exists()
        except Exception:
            return False

    def resolve_uri(self, handle: ArtifactHandle) -> str:
        """
        Returns the canonical file URI for an existing or addressed artifact handle.
        """
        file_path = self._resolve_path(handle)
        return f"file://{file_path}"

    def delete(self, handle: ArtifactHandle) -> bool:
        try:
            path = self._resolve_path(handle)
            if path.exists():
                path.unlink()
                return True
        except Exception:
            pass
        return False

    def cleanup_job_artifacts(self, job_id: int) -> None:
        job_dir = (self.base_dir / f"job_{job_id}").resolve()
        if job_dir.is_relative_to(self.base_dir) and job_dir.exists() and job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)

    def prune_expired(self, max_age_hours: int = 48) -> int:
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        pruned_count = 0

        for item in self.base_dir.iterdir():
            if item.is_dir() and item.name.startswith("job_"):
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        shutil.rmtree(item, ignore_errors=True)
                        pruned_count += 1
                except Exception:
                    pass
        return pruned_count

    def _resolve_path(self, handle: ArtifactHandle) -> Path:
        if "path" in handle.metadata:
            resolved = Path(handle.metadata["path"]).resolve()
        elif handle.uri.startswith("file://"):
            resolved = Path(handle.uri[7:]).resolve()
        else:
            resolved = (self.base_dir / f"job_{handle.job_id}" / self._sanitize_filename(handle.filename)).resolve()

        if not resolved.is_relative_to(self.base_dir):
            raise DomainError(f"Security error: Artifact path '{resolved}' escapes base storage directory.")

        return resolved
