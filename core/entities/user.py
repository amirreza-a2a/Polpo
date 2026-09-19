# ============================================================
#  core/entities/user.py
# ============================================================

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class UserPreferences:
    """User behavioral settings and preferences."""
    use_public_fallback: bool = True
    auto_retry: bool = False
    auto_pipeline2: bool = False
    default_prompt_id: Optional[int] = None
    default_pipeline2_prompt_id: Optional[int] = None


@dataclass
class QuotaAllocation:
    """User daily quota allocation and usage tracking."""
    daily_limit: int = 50
    daily_pages_used: int = 0
    last_active_date: Optional[date] = None

    @property
    def remaining_pages(self) -> int:
        return max(0, self.daily_limit - self.daily_pages_used)

    @property
    def is_quota_exceeded(self) -> bool:
        return self.daily_pages_used >= self.daily_limit


@dataclass
class User:
    """User domain entity."""
    id: Optional[int]
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    is_admin: bool = False
    quota: QuotaAllocation = field(default_factory=QuotaAllocation)
    preferences: UserPreferences = field(default_factory=UserPreferences)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
