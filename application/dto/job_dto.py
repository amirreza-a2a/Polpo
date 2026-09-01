# ============================================================
#  application/dto/job_dto.py
# ============================================================

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SubmitJobCommand:
    user_id: int
    filename: str
    file_bytes: bytes
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    api_chain_ids: Optional[List[int]] = None
    auto_pipeline2: bool = False
    pipeline2_prompt_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None


@dataclass
class JobResponseDTO:
    id: int
    user_id: int
    file_name: str
    status: str
    total_pages: int
    processed_pages: int
    auto_pipeline2: bool
    created_at: Optional[str] = None


@dataclass
class JobDetailDTO:
    id: int
    user_id: int
    file_name: str
    status: str
    total_pages: int
    processed_pages: int
    prompt_id: Optional[int]
    prompt_text: Optional[str]
    active_api_label: Optional[str]
    api_switch_log: List[Dict[str, Any]]
    output_path: Optional[str]
    error_message: Optional[str]
    auto_pipeline2: bool
    created_at: Optional[str]
    updated_at: Optional[str]


@dataclass
class ArtifactDownloadDTO:
    data: bytes
    filename: str
    mime_type: str = "application/octet-stream"
