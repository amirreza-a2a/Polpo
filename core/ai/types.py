# ============================================================
#  core/ai/types.py
# ============================================================

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List



@dataclass(frozen=True)
class VisionPromptRequest:
    """
    Vision processing prompt request contract.
    Contains raw bytes and MIME type with zero external library dependencies.
    """
    prompt: str
    image_bytes: bytes
    mime_type: str = "image/jpeg"
    model: Optional[str] = None
    system_instruction: Optional[str] = None
    timeout: float = 120.0


@dataclass(frozen=True)
class TextPromptRequest:
    """Text processing prompt request contract."""
    prompt: str
    model: Optional[str] = None
    system_instruction: Optional[str] = None
    timeout: float = 120.0


@dataclass(frozen=True)
class AIResponse:
    """
    Standardized provider-neutral AI response contract.
    No vendor-specific SDK objects penetrate into this boundary.
    """
    content: str
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    @property
    def text(self) -> str:
        """Compatibility property matching response.text callers."""
        return self.content
