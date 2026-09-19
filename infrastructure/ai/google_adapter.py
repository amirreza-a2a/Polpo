# ============================================================
#  infrastructure/ai/google_adapter.py
# ============================================================

import io
import logging
from typing import Optional
from PIL import Image

from application.ports.ai_provider import AIProviderPort
from core.ai.types import VisionPromptRequest, TextPromptRequest, AIResponse
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
)

logger = logging.getLogger("ai.google_adapter")


class GoogleAdapter(AIProviderPort):
    """
    Adapter communicating with Google Gemini API via official google-genai SDK.
    Implements the application layer AIProviderPort interface.
    """

    PROVIDER_NAME = "google"
    FALLBACK_MODEL = "gemini-3.5-flash"

    def __init__(
        self,
        api_key: str,
        default_model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url
        self.timeout = timeout

    def _normalize_error(self, err: Exception, model: str) -> AIError:
        """Normalizes Google SDK-specific exceptions into standard domain AIError exceptions."""
        err_msg = str(err).lower()
        status_code = getattr(err, "code", None) or getattr(err, "status_code", None)
        err_type = type(err).__name__.lower()

        if status_code == 429 or "429" in err_msg or "resourceexhausted" in err_type or "resource_exhausted" in err_msg or "quota" in err_msg or "rate limit" in err_msg:
            return AIRateLimitError(
                message=f"Google API rate limit / quota exceeded: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
                status_code=429,
            )

        if status_code in (401, 403) or "401" in err_msg or "403" in err_msg or "permissiondenied" in err_type or "api_key_invalid" in err_msg or "permission_denied" in err_msg or "unauthenticated" in err_msg:
            return AIAuthenticationError(
                message=f"Google API authentication failed: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
                status_code=status_code or 401,
            )

        if status_code == 404 or "404" in err_msg or "notfound" in err_type or "not_found" in err_msg:
            return AIModelNotFoundError(
                message=f"Google model '{model}' not found: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
                status_code=404,
            )

        if "safety" in err_msg or "blocked" in err_msg or "filter" in err_msg:
            return AIContentFilterError(
                message=f"Google content safety filter triggered: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
            )

        if "timeout" in err_msg or "deadline" in err_msg or "timed out" in err_msg:
            return AITimeoutError(
                message=f"Google API request timed out: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
            )

        if status_code in (500, 502, 503, 504) or "unavailable" in err_msg or "overloaded" in err_msg or "internal" in err_msg:
            return AIProviderUnavailableError(
                message=f"Google service unavailable: {err}",
                provider=self.PROVIDER_NAME,
                model=model,
                status_code=status_code or 503,
            )

        return AIRetryableError(
            message=f"Google API invocation failed: {err}",
            provider=self.PROVIDER_NAME,
            model=model,
            status_code=status_code,
        )

    def generate_vision(self, request: VisionPromptRequest) -> AIResponse:
        """Sends image and prompt to Google Gemini for vision-based content extraction."""
        from google import genai
        from infrastructure.ai.proxy import normalized_proxy_env

        model = request.model or self.default_model or self.FALLBACK_MODEL

        try:
            try:
                img_payload = Image.open(io.BytesIO(request.image_bytes))
            except Exception:
                img_payload = request.image_bytes

            with normalized_proxy_env():
                client = genai.Client(api_key=self.api_key)
                response = client.models.generate_content(
                    model=model,
                    contents=[img_payload, request.prompt],
                )
            content = getattr(response, "text", "") or ""
            return AIResponse(content=content, model=model)
        except Exception as e:
            norm_err = self._normalize_error(e, model)
            logger.warning("Google vision request failed (model=%s): %s", model, norm_err)
            raise norm_err from e

    def generate_text(self, request: TextPromptRequest) -> AIResponse:
        """Sends text prompt to Google Gemini for text-based generation."""
        from google import genai
        from infrastructure.ai.proxy import normalized_proxy_env

        model = request.model or self.default_model or self.FALLBACK_MODEL

        try:
            with normalized_proxy_env():
                client = genai.Client(api_key=self.api_key)
                response = client.models.generate_content(
                    model=model,
                    contents=[request.prompt],
                )
            content = getattr(response, "text", "") or ""
            return AIResponse(content=content, model=model)
        except Exception as e:
            norm_err = self._normalize_error(e, model)
            logger.warning("Google text request failed (model=%s): %s", model, norm_err)
            raise norm_err from e
