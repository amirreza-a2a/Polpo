# ============================================================
#  infrastructure/pdf/atomic_publisher.py
#  Atomic PDF Artifact Publisher & Durability State Machine
#  Phase 13 — Ticket #102
# ============================================================

from __future__ import annotations

import errno
import os
from pathlib import Path
from shutil import SameFileError
from typing import Optional
import uuid

from application.dto.pdf_publish_dto import PublishResultDTO
from application.ports.pdf_export_port import IPdfArtifactPublisher
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    DestinationDirectoryNotFoundError,
)
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    ExportPermissionError,
    UnsupportedFilesystemError,
)


class AtomicPdfPublisher(IPdfArtifactPublisher):
    """
    Atomic PDF artifact publisher with no-clobber and durable replacement semantics.

    Guarantees:
    1. Zero 0-byte placeholders: Destination is never created empty or written to partially.
    2. Atomic no-clobber (overwrite=False): Uses atomic os.link; fails fast without modifying destination.
    3. Atomic durable replacement (overwrite=True): Uses sibling temporary file with os.fsync + os.replace.
    4. Resilient staging cleanup: Failure to delete source staging artifact does not fail the export.
    """

    def publish(
        self,
        source_compiled_pdf: Path,
        destination_path: Path,
        overwrite: bool = False,
    ) -> PublishResultDTO:
        """
        Atomically publishes compiled PDF to destination.

        Args:
            source_compiled_pdf: Path to compiled PDF artifact in staging workspace.
            destination_path: Target path requested by user for published PDF.
            overwrite: When True, atomically replaces existing destination;
                       when False, strictly enforces no-clobber semantics via atomic linking.

        Returns:
            PublishResultDTO with target path, persisted byte count, and optional cleanup warning.

        Raises:
            DestinationDirectoryNotFoundError: If destination's parent directory does not exist.
            DestinationAlreadyExistsError: If target exists and overwrite is False.
            ArtifactNotFoundError: If source compiled PDF is missing.
            ExportPermissionError: If user lacks permissions to write to destination.
            UnsupportedFilesystemError: If atomic hard linking is not supported by filesystem.
            SameFileError: If source and destination resolve to the same underlying file.
        """
        source_path = Path(source_compiled_pdf)
        dest_path = Path(destination_path)
        dest_dir = dest_path.parent

        # 1. Pre-flight Validation
        if not dest_dir.exists() or not dest_dir.is_dir():
            raise DestinationDirectoryNotFoundError(
                f"Destination directory does not exist: '{dest_dir}'"
            )

        if not os.access(dest_dir, os.W_OK):
            raise ExportPermissionError(
                f"Permission denied writing to destination directory '{dest_dir}'"
            )

        if not source_path.is_file():
            raise ArtifactNotFoundError(
                f"Source compiled PDF not found at '{source_path}'"
            )

        if source_path.stat().st_size == 0:
            raise ArtifactNotFoundError(
                f"Source compiled PDF is empty (0 bytes): '{source_path}'"
            )

        if dest_path.is_dir():
            raise DestinationAlreadyExistsError(
                f"Destination path is an existing directory: '{dest_path}'"
            )

        # Check for same-file invalid invocation
        if dest_path.exists() or dest_path.is_symlink():
            try:
                is_same = os.path.samefile(source_path, dest_path)
            except OSError:
                is_same = source_path.resolve() == dest_path.resolve()
            if is_same:
                raise SameFileError(
                    f"Source and destination resolve to the same file: '{dest_path}'"
                )

        if not overwrite and (dest_path.exists() or dest_path.is_symlink()):
            raise DestinationAlreadyExistsError(
                f"Destination file already exists: '{dest_path}'"
            )

        # 2. Publication Execution
        if not overwrite:
            # Atomic No-Clobber via Hard Link
            try:
                os.link(source_path, dest_path)
            except FileExistsError as e:
                raise DestinationAlreadyExistsError(
                    f"Destination file already exists: '{dest_path}'"
                ) from e
            except PermissionError as e:
                raise ExportPermissionError(
                    f"Permission denied creating hard link at '{dest_path}': {e}"
                ) from e
            except OSError as e:
                if e.errno == getattr(errno, "EEXIST", 17):
                    raise DestinationAlreadyExistsError(
                        f"Destination file already exists: '{dest_path}'"
                    ) from e
                if e.errno in {
                    getattr(errno, "EACCES", 13),
                    getattr(errno, "EPERM", 1),
                    getattr(errno, "EROFS", 30),
                }:
                    raise ExportPermissionError(
                        f"Permission denied creating hard link at '{dest_path}': {e}"
                    ) from e
                unsupported_errnos = {
                    getattr(errno, "EXDEV", 18),
                    getattr(errno, "ENOTSUP", 95),
                    getattr(errno, "EOPNOTSUPP", 95),
                }
                msg = str(e).lower()
                if (
                    e.errno in unsupported_errnos
                    or "cross-device" in msg
                    or "not supported" in msg
                    or "function not implemented" in msg
                    or "incorrect function" in msg
                ):
                    raise UnsupportedFilesystemError(
                        f"Filesystem does not support atomic hard links: {e}"
                    ) from e
                raise
        else:
            # Atomic Durable Replacement via sibling temp file + os.replace
            temp_path = dest_dir / f".tmp_{uuid.uuid4().hex[:12]}.pdf"
            try:
                with open(source_path, "rb") as src, open(temp_path, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())

                os.replace(temp_path, dest_path)
            except PermissionError as e:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                raise ExportPermissionError(
                    f"Permission denied writing to '{dest_path}': {e}"
                ) from e
            except Exception:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                raise

        # 3. Cleanup State Machine
        published_bytes = dest_path.stat().st_size
        cleanup_warning: Optional[str] = None
        try:
            source_path.unlink()
        except Exception as e:
            cleanup_warning = f"Failed to delete temporary staging artifact: {e}"

        return PublishResultDTO(
            destination_path=dest_path,
            published_bytes=published_bytes,
            cleanup_warning=cleanup_warning,
        )
