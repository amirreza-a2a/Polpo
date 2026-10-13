# ============================================================
#  tests/unit/test_atomic_publisher.py
#  Unit tests for Atomic PDF Publisher & Durability State Machine
#  Phase 13 — Ticket #102
# ============================================================

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from application.dto.pdf_publish_dto import PublishResultDTO
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    DestinationDirectoryNotFoundError,
)
from core.exceptions.domain_exceptions import (
    ArtifactNotFoundError,
    ExportPermissionError,
    UnsupportedFilesystemError,
)
from infrastructure.pdf.atomic_publisher import AtomicPdfPublisher


def test_publish_no_clobber_success(tmp_path: Path):
    """Publishes new PDF via atomic hard link when overwrite is False."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    content = b"%PDF-1.7 publication payload"
    source_pdf.write_bytes(content)

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"

    publisher = AtomicPdfPublisher()
    result = publisher.publish(source_pdf, destination, overwrite=False)

    assert isinstance(result, PublishResultDTO)
    assert result.destination_path == destination
    assert result.published_bytes == len(content)
    assert result.cleanup_warning is None
    assert destination.exists()
    assert destination.read_bytes() == content
    # Staging source was unlinked
    assert not source_pdf.exists()


def test_publish_no_clobber_destination_exists(tmp_path: Path):
    """Asserts DestinationAlreadyExistsError is raised and destination is untouched."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"new compiled content")

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"
    initial_content = b"original user report"
    destination.write_bytes(initial_content)

    publisher = AtomicPdfPublisher()

    with pytest.raises(DestinationAlreadyExistsError) as exc_info:
        publisher.publish(source_pdf, destination, overwrite=False)

    assert "already exists" in str(exc_info.value).lower()
    # Ensure existing destination is completely untouched
    assert destination.read_bytes() == initial_content
    # Staging source should not be deleted on failure
    assert source_pdf.exists()


def test_publish_overwrite_atomic_replacement(tmp_path: Path):
    """Atomically replaces existing file when overwrite is True."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    new_content = b"%PDF-1.7 updated content"
    source_pdf.write_bytes(new_content)

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"
    destination.write_bytes(b"stale content")

    publisher = AtomicPdfPublisher()
    result = publisher.publish(source_pdf, destination, overwrite=True)

    assert result.destination_path == destination
    assert result.published_bytes == len(new_content)
    assert result.cleanup_warning is None
    assert destination.read_bytes() == new_content
    assert not source_pdf.exists()


def test_publish_missing_destination_directory(tmp_path: Path):
    """Raises DestinationDirectoryNotFoundError when target parent does not exist."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"%PDF-1.7")

    nonexistent_dir = tmp_path / "missing_folder"
    destination = nonexistent_dir / "out.pdf"

    publisher = AtomicPdfPublisher()
    with pytest.raises(DestinationDirectoryNotFoundError):
        publisher.publish(source_pdf, destination, overwrite=False)


def test_publish_missing_source_pdf(tmp_path: Path):
    """Raises ArtifactNotFoundError when source compiled PDF does not exist."""
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "out.pdf"
    missing_source = tmp_path / "nonexistent.pdf"

    publisher = AtomicPdfPublisher()
    with pytest.raises(ArtifactNotFoundError):
        publisher.publish(missing_source, destination, overwrite=False)


def test_publish_cleanup_failure_surfaces_warning(tmp_path: Path):
    """If unlinking staging file fails, publication still succeeds with non-fatal cleanup warning."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    content = b"%PDF-1.7 valid pdf"
    source_pdf.write_bytes(content)

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"

    publisher = AtomicPdfPublisher()

    with patch.object(Path, "unlink", side_effect=PermissionError("Locked file")):
        result = publisher.publish(source_pdf, destination, overwrite=False)

    assert destination.exists()
    assert destination.read_bytes() == content
    assert result.cleanup_warning is not None
    assert "Failed to delete temporary staging artifact" in result.cleanup_warning


def test_publish_cross_device_unsupported_filesystem(tmp_path: Path):
    """Raises UnsupportedFilesystemError when os.link fails across filesystems (EXDEV)."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"%PDF-1.7")

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"

    publisher = AtomicPdfPublisher()

    exdev_err = OSError()
    exdev_err.errno = errno.EXDEV
    exdev_err.strerror = "Invalid cross-device link"

    with patch("os.link", side_effect=exdev_err):
        with pytest.raises(UnsupportedFilesystemError) as exc_info:
            publisher.publish(source_pdf, destination, overwrite=False)

    assert "does not support atomic hard links" in str(exc_info.value).lower()
    assert not destination.exists()


def test_publish_permission_denial(tmp_path: Path):
    """Raises ExportPermissionError when user lacks write permission to destination."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"%PDF-1.7")

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "report.pdf"

    publisher = AtomicPdfPublisher()

    with patch("os.access", return_value=False):
        with pytest.raises(ExportPermissionError):
            publisher.publish(source_pdf, destination, overwrite=False)

    assert not destination.exists()


def test_publish_empty_source_pdf_rejected(tmp_path: Path):
    """Raises ArtifactNotFoundError when source compiled PDF is empty (0 bytes)."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"")  # 0 bytes

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    destination = export_dir / "out.pdf"

    publisher = AtomicPdfPublisher()
    with pytest.raises(ArtifactNotFoundError) as exc_info:
        publisher.publish(source_pdf, destination, overwrite=False)

    assert "empty" in str(exc_info.value).lower()
    assert not destination.exists()


def test_publish_destination_is_directory_rejected(tmp_path: Path):
    """Raises DestinationAlreadyExistsError when target destination path is an existing directory."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    source_pdf = staging_dir / "compiled.pdf"
    source_pdf.write_bytes(b"%PDF-1.7 valid")

    target_dir = tmp_path / "existing_folder"
    target_dir.mkdir()

    publisher = AtomicPdfPublisher()
    with pytest.raises(DestinationAlreadyExistsError) as exc_info:
        publisher.publish(source_pdf, target_dir, overwrite=True)

    assert "existing directory" in str(exc_info.value).lower()
