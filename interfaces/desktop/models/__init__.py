# ============================================================
#  interfaces/desktop/models/__init__.py
# ============================================================

from interfaces.desktop.models.job_queue_model import JobQueueModel
from interfaces.desktop.models.job_history_model import JobHistoryModel
from interfaces.desktop.models.api_slot_model import ApiSlotModel
from interfaces.desktop.models.prompt_list_model import PromptListModel

__all__ = [
    "JobQueueModel",
    "JobHistoryModel",
    "ApiSlotModel",
    "PromptListModel",
]
