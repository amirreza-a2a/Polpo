# ============================================================
#  application/ports/ai_executor.py
# ============================================================

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple
from core.entities.api_slot import ApiSlot


class IAIExecutionService(ABC):
    """
    درگاه ارکستراسیون اجرای هوش مصنوعی، کنترل نرخ درخواست‌ها و مدیریت چرخه Fallback.
    """

    @abstractmethod
    def execute_vision_with_fallback(
        self,
        chain: List[ApiSlot],
        image_bytes: bytes,
        prompt: str,
        at_page: int = 1,
        mime_type: str = "image/jpeg",
        on_switch: Optional[Callable[[str, str, str, int], None]] = None,
    ) -> Tuple[Optional[str], Optional[ApiSlot]]:
        """
        ارسال تصویر به هوش مصنوعی با اعمال متمرکز Fallback و Rate Limiting.
        خروجی: (محتوای تولیدشده, اسلات API موفق)
        """
        pass

    @abstractmethod
    def execute_text_with_fallback(
        self,
        chain: List[ApiSlot],
        prompt: str,
        input_text: Optional[str] = None,
        at_page: int = 0,
        on_switch: Optional[Callable[[str, str, str, int], None]] = None,
    ) -> Tuple[Optional[str], Optional[ApiSlot]]:
        """
        ارسال متن به هوش مصنوعی با اعمال متمرکز Fallback و Rate Limiting.
        خروجی: (محتوای تولیدشده, اسلات API موفق)
        """
        pass
