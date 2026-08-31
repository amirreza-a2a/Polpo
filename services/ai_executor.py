# ============================================================
#  services/ai_executor.py – ارکستراسیون و اجرای درخواست‌های AI
# ============================================================

import logging
from typing import Callable, Optional, Union

from core.ai.types import ApiSlot, VisionPromptRequest, TextPromptRequest, AIResponse
from core.ai.exceptions import (
    AIError,
    AIRetryableError,
    AIRateLimitError,
    AITimeoutError,
    AIProviderUnavailableError,
    AINonRetryableError,
    AIAuthenticationError,
    AIModelNotFoundError,
    AIContentFilterError,
    AIChainExhaustedError,
)
from infrastructure.ai.factory import create_ai_adapter
from services.api_manager import switch_to_next_api
from utils.rate_limiter import wait_if_needed, mark_request_sent

logger = logging.getLogger("services.ai_executor")


def _ensure_api_slot(slot: Union[ApiSlot, dict]) -> ApiSlot:
    """تبدیل دیکشنری اسلات به موجودیت تایپ‌شده ApiSlot در صورت نیاز."""
    if isinstance(slot, ApiSlot):
        return slot
    return ApiSlot.from_dict(slot)


def execute_single_vision_request(
    request: VisionPromptRequest,
    slot: Union[ApiSlot, dict],
) -> AIResponse:
    """
    اجرای یک درخواست منفرد ویژن با استفاده از Adapter متناظر.
    استثناهای نرمال‌شده AIError به لایه بالاتر منتقل می‌شوند و بی‌صدا بلعیده نمی‌شوند.
    """
    api_slot = _ensure_api_slot(slot)
    model = request.model or api_slot.selected_model

    adapter = create_ai_adapter(
        provider=api_slot.provider,
        api_key=api_slot.api_key,
        model=model,
        base_url=api_slot.base_url,
        timeout=request.timeout,
    )
    return adapter.generate_vision(request)


def execute_single_text_request(
    request: TextPromptRequest,
    slot: Union[ApiSlot, dict],
) -> AIResponse:
    """
    اجرای یک درخواست منفرد متنی با استفاده از Adapter متناظر.
    استثناهای نرمال‌شده AIError به لایه بالاتر منتقل می‌شوند و بی‌صدا بلعیده نمی‌شوند.
    """
    api_slot = _ensure_api_slot(slot)
    model = request.model or api_slot.selected_model

    adapter = create_ai_adapter(
        provider=api_slot.provider,
        api_key=api_slot.api_key,
        model=model,
        base_url=api_slot.base_url,
        timeout=request.timeout,
    )
    return adapter.generate_text(request)


def _classify_error_reason(err: AIError) -> str:
    """تشخیص دلیل سوئیچ بر اساس ساختار استثناهای هوش مصنوعی."""
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


def execute_vision_with_fallback(
    job: dict,
    image_bytes: bytes,
    prompt: str,
    at_page: int,
    mime_type: str = "image/jpeg",
    notify_switch_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    اجرای درخواست ویژن با اعمال سیاست Fallback زنجیره API و کنترل Rate Limit.
    تنها خطاهای دامنه هوش مصنوعی (AIError) مشمول سوئیچ و Fallback می‌شوند.
    خطاهای برنامه‌نویسی/سیستمی دیگر (مانند TypeError, KeyError, etc.) منتشر می‌شوند.
    خروجی: tuple(result_text, current_active_api_entry_dict)
    در صورت اتمام تمام APIها، tuple(None, None) برگردانده می‌شود.
    """
    chain = job["api_chain"]
    current_idx = job["current_api_index"]

    if not chain or current_idx >= len(chain):
        return None, None

    current_api_dict = chain[current_idx]

    while current_api_dict is not None:
        slot = _ensure_api_slot(current_api_dict)
        slot_dict = slot.to_dict()
        wait_if_needed(slot_dict)

        req = VisionPromptRequest(
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            model=slot.selected_model,
        )

        try:
            response = execute_single_vision_request(req, slot)
            mark_request_sent(slot_dict)
            return response.content, current_api_dict

        except AIError as e:
            mark_request_sent(slot_dict)
            reason = _classify_error_reason(e)
            old_label = slot.label or f"{slot.provider}_{slot.id}"

            logger.warning(
                "AI provider slot '%s' failed at page %d (reason: %s): %s. Advancing fallback chain...",
                old_label, at_page, reason, e
            )

            new_api_dict = switch_to_next_api(job, reason=reason, at_page=at_page)

            if new_api_dict is None:
                logger.warning("All APIs in chain exhausted at page %d.", at_page)
                return None, None

            current_api_dict = new_api_dict
            new_slot = _ensure_api_slot(current_api_dict)
            new_label = new_slot.label or f"{new_slot.provider}_{new_slot.id}"

            if notify_switch_callback and job.get("user_id"):
                try:
                    notify_switch_callback(job["user_id"], old_label, new_label)
                except Exception as notify_err:
                    logger.error("Error in switch notification callback: %s", notify_err)

    return None, None


def execute_text_with_fallback(
    job: dict,
    prompt: str,
    at_page: int = 0,
    notify_switch_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    اجرای درخواست متنی با اعمال سیاست Fallback زنجیره API و کنترل Rate Limit.
    تنها خطاهای دامنه هوش مصنوعی (AIError) مشمول سوئیچ و Fallback می‌شوند.
    خطاهای برنامه‌نویسی/سیستمی دیگر (مانند TypeError, KeyError, etc.) منتشر می‌شوند.
    خروجی: tuple(result_text, current_active_api_entry_dict)
    در صورت اتمام تمام APIها، tuple(None, None) برگردانده می‌شود.
    """
    chain = job["api_chain"]
    current_idx = job["current_api_index"]

    if not chain or current_idx >= len(chain):
        return None, None

    current_api_dict = chain[current_idx]

    while current_api_dict is not None:
        slot = _ensure_api_slot(current_api_dict)
        slot_dict = slot.to_dict()
        wait_if_needed(slot_dict)

        req = TextPromptRequest(
            prompt=prompt,
            model=slot.selected_model,
        )

        try:
            response = execute_single_text_request(req, slot)
            mark_request_sent(slot_dict)
            return response.content, current_api_dict

        except AIError as e:
            mark_request_sent(slot_dict)
            reason = _classify_error_reason(e)
            old_label = slot.label or f"{slot.provider}_{slot.id}"

            logger.warning(
                "AI provider slot '%s' failed for text job (reason: %s): %s. Advancing fallback chain...",
                old_label, reason, e
            )

            new_api_dict = switch_to_next_api(job, reason=reason, at_page=at_page)

            if new_api_dict is None:
                logger.warning("All APIs in chain exhausted for text job.")
                return None, None

            current_api_dict = new_api_dict
            new_slot = _ensure_api_slot(current_api_dict)
            new_label = new_slot.label or f"{new_slot.provider}_{new_slot.id}"

            if notify_switch_callback and job.get("user_id"):
                try:
                    notify_switch_callback(job["user_id"], old_label, new_label)
                except Exception as notify_err:
                    logger.error("Error in switch notification callback: %s", notify_err)

    return None, None
