# ============================================================
#  infrastructure/ai/executor_service.py
# ============================================================

import logging
from typing import Callable, List, Optional, Tuple
from application.ports.ai_executor import IAIExecutionService
from application.ports.ai_provider import AIProviderPort
from application.ports.rate_limiter import IRateLimiter
from core.entities.api_slot import ApiSlot
from core.policies.fallback_policy import FallbackChainPolicy
from core.ai.types import VisionPromptRequest, TextPromptRequest
from core.ai.exceptions import (
    AIError,
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
    AIAuthenticationError,
    AIModelNotFoundError,
    AIContentFilterError,
)

logger = logging.getLogger("polpot.ai.executor")


class RateLimitedAIExecutor(IAIExecutionService):
    """
    Infrastructure implementation of the AI execution orchestrator and fallback chain.
    Catches only standardized domain exceptions (AIError) to handle fallback switching,
    allowing system or programming errors to bubble up directly.
    """

    def __init__(
        self,
        adapter_factory: Callable[[ApiSlot], AIProviderPort],
        rate_limiter: IRateLimiter,
    ):
        self.adapter_factory = adapter_factory
        self.rate_limiter = rate_limiter

    def execute_vision_with_fallback(
        self,
        chain: List[ApiSlot],
        image_bytes: bytes,
        prompt: str,
        at_page: int = 1,
        mime_type: str = "image/jpeg",
        on_switch: Optional[Callable[[str, str, str, int], None]] = None,
    ) -> Tuple[Optional[str], Optional[ApiSlot]]:
        if not chain:
            return None, None

        current_idx = 0
        while current_idx < len(chain):
            slot = chain[current_idx]
            try:
                self.rate_limiter.wait_if_needed(slot)
                adapter = self.adapter_factory(slot)
                req = VisionPromptRequest(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    model=slot.selected_model,
                )
                resp = adapter.generate_vision(req)
                self.rate_limiter.mark_request_sent(slot)
                return resp.content, slot

            except AIError as err:
                reason = self._classify_ai_error(err)
                logger.warning(
                    f"AI provider slot '{slot.label}' failed at page {at_page} (reason: {reason}): {err}. Advancing chain..."
                )
                next_slot, next_idx, event = FallbackChainPolicy.advance_chain(
                    chain=chain,
                    current_index=current_idx,
                    reason=reason,
                    at_page=at_page,
                )
                if on_switch and next_slot:
                    on_switch(slot.label, next_slot.label, reason, at_page)

                if next_slot is None:
                    break

                current_idx = next_idx

        return None, None

    def execute_text_with_fallback(
        self,
        chain: List[ApiSlot],
        prompt: str,
        input_text: Optional[str] = None,
        at_page: int = 0,
        on_switch: Optional[Callable[[str, str, str, int], None]] = None,
    ) -> Tuple[Optional[str], Optional[ApiSlot]]:
        if not chain:
            return None, None

        full_prompt = f"{prompt}\n\n---\n\n{input_text}" if input_text else prompt

        current_idx = 0
        while current_idx < len(chain):
            slot = chain[current_idx]
            try:
                self.rate_limiter.wait_if_needed(slot)
                adapter = self.adapter_factory(slot)
                req = TextPromptRequest(
                    prompt=full_prompt,
                    model=slot.selected_model,
                )
                resp = adapter.generate_text(req)
                self.rate_limiter.mark_request_sent(slot)
                return resp.content, slot


            except AIError as err:
                reason = self._classify_ai_error(err)
                logger.warning(
                    f"AI provider slot '{slot.label}' failed in text request (reason: {reason}): {err}. Advancing chain..."
                )
                next_slot, next_idx, event = FallbackChainPolicy.advance_chain(
                    chain=chain,
                    current_index=current_idx,
                    reason=reason,
                    at_page=at_page,
                )
                if on_switch and next_slot:
                    on_switch(slot.label, next_slot.label, reason, at_page)

                if next_slot is None:
                    break

                current_idx = next_idx

        return None, None

    def _classify_ai_error(self, err: AIError) -> str:
        if isinstance(err, AIRateLimitError):
            return "rate_limit"
        if isinstance(err, AITimeoutError):
            return "timeout"
        if isinstance(err, AIProviderUnavailableError):
            return "provider_unavailable"
        if isinstance(err, AIAuthenticationError):
            return "auth_error"
        if isinstance(err, AIModelNotFoundError):
            return "model_not_found"
        if isinstance(err, AIContentFilterError):
            return "content_filter"
        return "api_error"
