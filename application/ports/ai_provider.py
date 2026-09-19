# ============================================================
#  application/ports/ai_provider.py
# ============================================================

from abc import ABC, abstractmethod
from core.ai.types import VisionPromptRequest, TextPromptRequest, AIResponse


class AIProviderPort(ABC):
    """
    Application port for AI service provider adapters.
    The application layer depends on this interface, and the infrastructure layer implements it.
    """

    @abstractmethod
    def generate_vision(self, request: VisionPromptRequest) -> AIResponse:
        """Dispatches a vision request (image + prompt) and returns a standardized response."""
        pass

    @abstractmethod
    def generate_text(self, request: TextPromptRequest) -> AIResponse:
        """Dispatches a pure text prompt request and returns a standardized response."""
        pass
