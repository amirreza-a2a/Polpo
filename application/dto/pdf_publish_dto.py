# ============================================================
#  application/dto/pdf_publish_dto.py
#  Publication Result DTO for PDF Export Subsystem
#  Phase 13 — Ticket #102
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PublishResultDTO:
    """
    Structured outcome of atomic PDF artifact publication.

    Attributes:
        destination_path: Final target filesystem path of the published PDF file.
        published_bytes: Total number of bytes written and persisted to disk.
        cleanup_warning: Optional non-fatal warning if staging temporary file cleanup failed.
    """
    destination_path: Path
    published_bytes: int
    cleanup_warning: Optional[str] = None
