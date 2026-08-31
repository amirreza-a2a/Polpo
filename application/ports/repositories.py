# ============================================================
#  application/ports/repositories.py
# ============================================================

from abc import ABC, abstractmethod
from typing import List, Optional
from core.entities.job import Job, Pipeline2Job, JobStatus
from core.entities.user import User
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot


class IJobRepository(ABC):
    @abstractmethod
    def get_by_id(self, job_id: int) -> Optional[Job]: ...

    @abstractmethod
    def get_next_pending(self) -> Optional[Job]: ...

    @abstractmethod
    def list_by_user(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Job]: ...

    @abstractmethod
    def save(self, job: Job) -> Job: ...

    @abstractmethod
    def update_progress(self, job_id: int, processed_pages: int, switch_log: List[dict], output_path: Optional[str] = None) -> None: ...

    @abstractmethod
    def count_by_user(self, user_id: int, status: Optional[JobStatus] = None) -> int: ...

    @abstractmethod
    def get_queue_position(self, job_id: int) -> int: ...

    @abstractmethod
    def update_status(self, job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None: ...


class IPipeline2JobRepository(ABC):
    @abstractmethod
    def get_by_id(self, p2_job_id: int) -> Optional[Pipeline2Job]: ...

    @abstractmethod
    def get_next_pending(self) -> Optional[Pipeline2Job]: ...

    @abstractmethod
    def save(self, job: Pipeline2Job) -> Pipeline2Job: ...

    @abstractmethod
    def update_status(self, p2_job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None: ...

    @abstractmethod
    def update_paths(self, p2_job_id: int, input_path: Optional[str] = None, output_path: Optional[str] = None) -> None: ...

    @abstractmethod
    def update_output_path(self, p2_job_id: int, output_path: str) -> None: ...



class IUserRepository(ABC):
    @abstractmethod
    def get_by_id(self, user_id: int) -> Optional[User]: ...

    @abstractmethod
    def get_by_telegram_id(self, telegram_id: int) -> Optional[User]: ...

    @abstractmethod
    def get_by_username(self, username: str) -> Optional[User]: ...

    @abstractmethod
    def save(self, user: User) -> User: ...

    @abstractmethod
    def increment_daily_pages(self, user_id: int, amount: int = 1) -> int: ...

    @abstractmethod
    def reset_daily_quota(self, user_id: int) -> None: ...


    @abstractmethod
    def get_today_stats(self) -> dict: ...


class IPromptRepository(ABC):
    @abstractmethod
    def get_by_id(self, prompt_id: int) -> Optional[Prompt]: ...

    @abstractmethod
    def get_default(self, prompt_type: PromptType) -> Optional[Prompt]: ...

    @abstractmethod
    def list_all(self, prompt_type: Optional[PromptType] = None) -> List[Prompt]: ...

    @abstractmethod
    def save(self, prompt: Prompt) -> Prompt: ...

    @abstractmethod
    def delete(self, prompt_id: int) -> bool: ...

    @abstractmethod
    def set_default(self, prompt_id: int, prompt_type: PromptType) -> None: ...

    @abstractmethod
    def toggle_active(self, prompt_id: int, is_active: bool) -> bool: ...

    @abstractmethod
    def get_quick_convert_prompt(self) -> Optional[str]: ...

    @abstractmethod
    def set_quick_convert_prompt(self, text: str) -> None: ...


class IApiRepository(ABC):
    @abstractmethod
    def get_by_id(self, api_id: int, slot_type: str = "private") -> Optional[ApiSlot]: ...

    @abstractmethod
    def list_by_user(self, user_id: int, include_public: bool = False) -> List[ApiSlot]: ...

    @abstractmethod
    def list_public(self) -> List[ApiSlot]: ...

    @abstractmethod
    def save_private(
        self,
        user_id: int,
        provider: str,
        api_key: str,
        label: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> ApiSlot: ...

    @abstractmethod
    def save_public(
        self,
        provider: str,
        api_key: str,
        label: str,
        models: List[str],
        daily_limit: int,
        selected_model: Optional[str] = None,
        base_url: Optional[str] = None,
        donated_by: Optional[int] = None,
    ) -> ApiSlot: ...

    @abstractmethod
    def toggle_public(self, api_id: int, is_active: bool) -> bool: ...

    @abstractmethod
    def update_public_model_url(
        self,
        api_id: int,
        selected_model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> bool: ...

    @abstractmethod
    def delete_private(self, api_id: int, user_id: int) -> bool: ...

    @abstractmethod
    def delete_public(self, api_id: int) -> bool: ...

    @abstractmethod
    def report_pages_used(self, api_id: int, slot_type: str, pages: int = 1) -> None: ...


class IDonationRepository(ABC):
    @abstractmethod
    def save_donation(self, user_id: int, provider: str, api_key: str, label: str, models: List[str]) -> int: ...

    @abstractmethod
    def get_by_id(self, donation_id: int) -> Optional[dict]: ...

    @abstractmethod
    def update_status(self, donation_id: int, status: str) -> bool: ...

    @abstractmethod
    def list_all(self) -> List[dict]: ...
