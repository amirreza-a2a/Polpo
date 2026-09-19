# ============================================================
#  application/ports/ai_executor.py
# ============================================================

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple
from core.entities.api_slot import ApiSlot


class IAIExecutionService(ABC):
    """
    Port for orchestrating AI execution, rate limiting, and fallback chain progression.
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
        Executes a vision request with centralized rate limiting and fallback progression.
        Returns: (generated_content, successful_api_slot)
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
        Executes a text request with centralized rate limiting and fallback progression.
        Returns: (generated_content, successful_api_slot)
        """
        pass
