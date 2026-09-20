# ============================================================
#  core/entities/artifact.py
# ============================================================

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import url2pathname


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


def resolve_canonical_file_path(path_or_uri: str) -> Path:
    """
    Resolves an artifact URI or filesystem path string into a canonical Path object.
    Supports standard RFC 8089 file:/// URIs, legacy two-slash Windows file:// URIs,
    as well as direct POSIX and Windows filesystem paths.

    Preserves percent-encoded characters during drive-letter and leading-slash structural
    checks so url2pathname() performs single, canonical percent-decoding.
    """
    cleaned = str(path_or_uri).strip()
    if cleaned.startswith("file://"):
        parsed = urlparse(cleaned)
        netloc = parsed.netloc
        raw_path = parsed.path

        # Legacy Windows two-slash compatibility:
        # e.g. file://C:/artifacts/output_1_v1.md parses as netloc='C:', path='/artifacts/output_1_v1.md'.
        # Detecting a drive-letter netloc and re-prepending preserves the drive letter.
        if len(netloc) == 2 and netloc[0].isalpha() and netloc[1] == ":":
            raw_path = f"{netloc}{raw_path}"

        # RFC 8089 Windows file:/// URI cross-platform compatibility:
        # e.g. file:///C:/artifacts/output_1_v1.md produces raw_path='/C:/artifacts/output_1_v1.md'.
        # On POSIX platforms, url2pathname does not strip the leading slash preceding drive letters.
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[1].isalpha() and raw_path[2] == ":":
            raw_path = raw_path[1:]

        path_str = url2pathname(raw_path)
        return Path(path_str)
    return Path(cleaned)
