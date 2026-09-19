# ============================================================
#  core/entities/document_version.py
#  Domain entities for publication intents and document version history
# ============================================================

from dataclasses import dataclass
from typing import Optional


@dataclass
class PublishIntentRecord:
    """Represents an active or flushed intent to publish a new canonical document version."""
    intent_id: str
    job_id: int
    base_version: int
    target_version: int
    output_filename: str
    output_sha256: str
    staged_artifacts_manifest: str = "[]"
    status: str = "PENDING"
    created_at: Optional[str] = None


@dataclass
class DocumentVersionRecord:
    """Represents an immutable record in the canonical document version history."""
    job_id: int
    version: int
    output_path: str
    sha256: Optional[str]
    integrity_status: str = "VALID"
    published_by: str = "SYSTEM"
    created_at: Optional[str] = None
    id: Optional[int] = None
