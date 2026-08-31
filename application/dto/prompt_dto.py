# ============================================================
#  application/dto/prompt_dto.py
# ============================================================

from dataclasses import dataclass
from typing import Optional


@dataclass
class PromptDTO:
    id: int
    name: str
    text: str
    prompt_type: str
    is_default: bool


@dataclass
class CreatePromptCommand:
    name: str
    text: str
    prompt_type: str = "pipeline1"
    is_default: bool = False


@dataclass
class UpdatePromptCommand:
    prompt_id: int
    name: Optional[str] = None
    text: Optional[str] = None
    is_default: Optional[bool] = None
