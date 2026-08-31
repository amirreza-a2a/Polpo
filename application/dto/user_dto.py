# ============================================================
#  application/dto/user_dto.py
# ============================================================

from dataclasses import dataclass
from typing import Optional


@dataclass
class UserDTO:
    id: int
    telegram_id: Optional[int]
    username: Optional[str]
    is_admin: bool
    daily_pages_used: int
    daily_limit: int
    remaining_pages: int
    use_public_fallback: bool
    auto_retry: bool
    auto_pipeline2: bool
    default_prompt_id: Optional[int]
    default_pipeline2_prompt_id: Optional[int]


@dataclass
class UpdatePreferencesCommand:
    user_id: int
    use_public_fallback: Optional[bool] = None
    auto_retry: Optional[bool] = None
    auto_pipeline2: Optional[bool] = None
    default_prompt_id: Optional[int] = None
    default_pipeline2_prompt_id: Optional[int] = None
