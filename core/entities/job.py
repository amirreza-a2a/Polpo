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


class JobType(str, Enum):
    PIPELINE_1 = "pipeline1"
    PIPELINE_2 = "pipeline2"
    QUICK_CONVERT = "quick_convert"


@dataclass(frozen=True)
class SwitchEvent:
    """رویداد تغییر اسلات API در طول پردازش."""
    page: int
    from_api: str
    to_api: str
    reason: str
    timestamp: str


@dataclass
class Job:
    """موجودیت دامنه جهت نمایش یک کار پردازش سند PDF."""
    id: Optional[int]
    user_id: int
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
    error_message: Optional[str] = None
    retry_count: int = 0
    auto_pipeline2: bool = False
    pipeline2_prompt_id: Optional[int] = None
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
        return self.status in (JobStatus.DONE, JobStatus.FAILED)


@dataclass
class Pipeline2Job:
    """موجودیت دامنه جهت نمایش مرحله دوم یکپارچه‌سازی و بازنویسی سند."""
    id: Optional[int]
    source_job_id: int
    user_id: int
    prompt_id: Optional[int] = None
    prompt_text: Optional[str] = None
    status: JobStatus = JobStatus.PENDING
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    api_chain: List[ApiSlot] = field(default_factory=list)
    current_api_index: int = 0
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
