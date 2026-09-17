# ============================================================
#  core/entities/job.py
# ============================================================

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from core.entities.api_slot import ApiSlot


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    PIPELINE_1 = "pipeline1"
    PIPELINE_2 = "pipeline2"
    QUICK_CONVERT = "quick_convert"


@dataclass(frozen=True)
class SwitchEvent:
    """Represents an API switch event recorded during document execution."""
    page: int
    from_api: str
    to_api: str
    reason: str
    timestamp: str


@dataclass
class Job:
    """Domain entity representing a PDF document processing job."""
    id: Optional[int]
    file_name: str
    file_path: str
    total_pages: int = 0
    processed_pages: int = 0
    status: JobStatus = JobStatus.PENDING
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    api_chain: List[ApiSlot] = field(default_factory=list)
    current_api_index: int = 0
    api_switch_log: List[Dict[str, Any]] = field(default_factory=list)
    output_path: Optional[str] = None
    output_artifact_version_watermark: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0
    auto_pipeline2: bool = False
    pipeline2_prompt_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    cancel_requested: bool = False
    claimed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def current_api(self) -> Optional[ApiSlot]:
        if self.api_chain and 0 <= self.current_api_index < len(self.api_chain):
            return self.api_chain[self.current_api_index]
        return None

    @property
    def is_completed(self) -> bool:
        return self.status == JobStatus.DONE

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def active_markdown_version(self) -> int:
        from core.markdown.version import parse_canonical_markdown_version
        return parse_canonical_markdown_version(self.output_path)


@dataclass
class Pipeline2Job:
    """Domain entity representing a Pipeline 2 typography refinement job."""
    id: Optional[int]
    source_job_id: int
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    status: JobStatus = JobStatus.PENDING
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    api_chain: List[ApiSlot] = field(default_factory=list)
    current_api_index: int = 0
    error_message: Optional[str] = None
    cancel_requested: bool = False
    claimed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)
