# ============================================================
#  application/ports/notifier.py
# ============================================================

from abc import ABC, abstractmethod


class IProgressNotifier(ABC):
    """
    درگاه اطلاع‌رسانی پیشرفت و تغییرات وضعیت کارها به محیط‌های شنونده (تلگرام، SSE، لاگ).
    """

    @abstractmethod
    def notify_progress(self, user_id: int, job_id: int, processed_pages: int, total_pages: int) -> None:
        pass

    @abstractmethod
    def notify_api_switch(
        self,
        user_id: int,
        job_id: int,
        old_label: str,
        new_label: str,
        reason: str,
        page: int,
    ) -> None:
        pass

    @abstractmethod
    def notify_job_completed(self, user_id: int, job_id: int, output_handle_uri: str) -> None:
        pass

    @abstractmethod
    def notify_job_failed(self, user_id: int, job_id: int, error_message: str) -> None:
        pass
