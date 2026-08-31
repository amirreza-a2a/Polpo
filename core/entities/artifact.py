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


@dataclass(frozen=True)
class ArtifactHandle:
    """
    شناسه دسترسی به فایل‌های ذخیره شده.
    مستقل از محل فیزیکی فایل (دیسک محلی، کانال تلگرام یا فضای ابری).
    """
    storage_backend: StorageBackendType
    uri: str
    artifact_type: ArtifactType
    job_id: int
    filename: str
    size_bytes: int = 0
    mime_type: str = "application/octet-stream"
    metadata: Mapping[str, Any] = field(default_factory=dict)
