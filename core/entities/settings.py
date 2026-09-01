# ============================================================
#  core/entities/settings.py
# ============================================================

from dataclasses import dataclass
from typing import Optional


@dataclass
class AppSettings:
    """
    Domain entity representing desktop application settings and runtime preferences.
    Completely independent of UI frameworks, database drivers, and OS platform.
    """
    theme: str = "system"
    max_concurrent_jobs: int = 2
    auto_retry: bool = True
    auto_pipeline2: bool = False
    default_prompt_id: Optional[int] = None
    default_pipeline2_prompt_id: Optional[int] = None
    artifact_retention_days: int = 30
    missed_schedule_policy: str = "prompt"

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        """Validates fields and value boundaries for application settings."""
        valid_themes = ("system", "dark", "light")
        if self.theme not in valid_themes:
            raise ValueError(f"Invalid theme '{self.theme}'. Must be one of {valid_themes}")

        if not isinstance(self.max_concurrent_jobs, int) or not (1 <= self.max_concurrent_jobs <= 8):
            raise ValueError(f"max_concurrent_jobs must be an integer between 1 and 8, got {self.max_concurrent_jobs}")

        if not isinstance(self.artifact_retention_days, int) or self.artifact_retention_days < 1:
            raise ValueError(f"artifact_retention_days must be a positive integer >= 1, got {self.artifact_retention_days}")

        valid_policies = ("run_immediately", "prompt", "mark_paused")
        if self.missed_schedule_policy not in valid_policies:
            raise ValueError(f"Invalid missed_schedule_policy '{self.missed_schedule_policy}'. Must be one of {valid_policies}")
