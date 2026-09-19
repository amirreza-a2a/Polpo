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

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.lower().replace("_", "").strip()
            if normalized in ("pipeline1", "p1"):
                return cls.PIPELINE_1
            if normalized in ("pipeline2", "p2"):
                return cls.PIPELINE_2
            if normalized in ("quickconvert", "quick"):
                return cls.QUICK_CONVERT
        return super()._missing_(value)



@dataclass
class Prompt:
    """Extraction or conversion prompt domain entity."""
    id: Optional[int]
    name: str
    text: str
    prompt_type: PromptType = PromptType.PIPELINE_1
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
