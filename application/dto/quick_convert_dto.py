# ============================================================
#  application/dto/quick_convert_dto.py
# ============================================================

from dataclasses import dataclass
from typing import Optional


@dataclass
class QuickConvertCommand:
    user_id: int
    image_bytes: bytes
    prompt_text: Optional[str] = None
    mime_type: str = "image/jpeg"


@dataclass
class QuickConvertResultDTO:
    markdown_content: str
    used_api_label: Optional[str]
    pages_consumed: int = 1
