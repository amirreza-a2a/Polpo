# ============================================================
#  core/entities/artifact.py
# ============================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class StorageBackendType(str, Enum):
    LOCAL_FS = "local_fs"
    TELEGRAM_CHANNEL = "telegram_channel"
    S3_COMPATIBLE = "s3"


class ArtifactType(str, Enum):
    SOURCE_PDF = "source_pdf"
    PAGE_IMAGE = "page_image"
    CROPPED_IMAGE = "cropped_image"
    OUTPUT_MARKDOWN = "output_markdown"
    UNIFIED_MARKDOWN = "unified_markdown"
    ATTACHMENTS_ZIP = "attachments_zip"
    PIPELINE2_MARKDOWN = "pipeline2_markdown"



@dataclass(frozen=True)
class ArtifactHandle:
    """
    Access handle for stored artifact files.
    Independent of physical storage backend (local filesystem, Telegram channel, or cloud storage).
    """
    storage_backend: StorageBackendType
    uri: str
    artifact_type: ArtifactType
    job_id: int
    filename: str
    size_bytes: int = 0
    mime_type: str = "application/octet-stream"
    metadata: Mapping[str, Any] = field(default_factory=dict)
