# ============================================================
#  application/dto/pdf_export_dto.py
#  Data Transfer Objects for PDF Export Subsystem
#  Phase 13 — Ticket #103
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class ExportPdfCommand:
    """
    Command DTO initiating compilation and atomic publication of a canonical document.

    Attributes:
        job_id: Numeric identifier of the document job.
        version: Canonical document version watermark to export.
        destination_path: Final target filesystem path for the published PDF.
        overwrite: When True, replaces existing destination file; when False, fails fast.
        is_cancelled: Optional cooperative cancellation callback.
    """
    job_id: int
    version: int
    destination_path: Path
    overwrite: bool = False
    is_cancelled: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class PdfExportResultDTO:
    """
    Structured outcome of PDF document export orchestration.

    Attributes:
        destination_path: Target filesystem path where the published PDF is located.
        canonical_version: Exported canonical document version.
        page_count: Total number of pages in the compiled PDF.
        file_size_bytes: Size of the published PDF in bytes.
        duration_seconds: End-to-end elapsed export duration in seconds.
        cleanup_warning: Optional non-fatal warning if staging cleanup had non-critical issues.
    """
    destination_path: Path
    canonical_version: int
    page_count: int
    file_size_bytes: int
    duration_seconds: float
    cleanup_warning: Optional[str] = None
