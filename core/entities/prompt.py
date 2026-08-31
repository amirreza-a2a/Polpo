# ============================================================
#  core/entities/prompt.py
# ============================================================

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class PromptType(str, Enum):
    PIPELINE_1 = "pipeline1"
    PIPELINE_2 = "pipeline2"
    QUICK_CONVERT = "quick_convert"


@dataclass
class Prompt:
    """موجودیت پرامپت استخراج یا تبدیل در سیستم."""
    id: Optional[int]
    name: str
    text: str
    prompt_type: PromptType = PromptType.PIPELINE_1
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
