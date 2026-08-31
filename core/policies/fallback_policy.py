# ============================================================
#  core/policies/fallback_policy.py
# ============================================================

from typing import List, Optional, Tuple
from datetime import datetime
from core.entities.api_slot import ApiSlot
from core.entities.job import Job


class FallbackChainPolicy:
    """
    سیاست حل اسلات‌ها و پیشروی زنجیره Fallback در لایه دامنه.
    """

    @classmethod
    def advance_chain(
        cls,
        chain: List[ApiSlot],
        current_index: int,
        reason: str,
        at_page: int,
    ) -> Tuple[Optional[ApiSlot], int, dict]:
        """
        محاسبه اسلات بعدی در زنجیره و ساخت رویداد سوئیچ.
        خروجی: (next_slot, new_index, switch_event_dict)
        """
        if not chain:
            return None, 0, {}

        next_idx = current_index + 1
        if next_idx >= len(chain):
            return None, next_idx, {}

        old_slot = chain[current_index]
        next_slot = chain[next_idx]

        event = {
            "from_api": old_slot.label,
            "to_api": next_slot.label,
            "page": at_page,
            "reason": reason,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        return next_slot, next_idx, event
