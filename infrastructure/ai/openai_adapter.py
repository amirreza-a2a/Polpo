# ============================================================
#  infrastructure/ai/openai_adapter.py
# ============================================================

import base64
import logging
from typing import Optional

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

logger = logging.getLogger("ai.openai_adapter")


class OpenAIAdapter(AIProviderPort):
    """
    Adapter communicating with OpenAI-compatible endpoints (OpenAI, OpenRouter, and custom URLs).
    Implements the application layer AIProviderPort interface.
    """

    DEFAULT_OPENAI_MODEL = "gpt-4o"
    DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o"

    def __init__(
        self,
        api_key: str,
        default_model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: str = "openai",
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url
        self.provider_name = provider_name
        self.timeout = timeout

    def _normalize_error(self, err: Exception, model: str) -> AIError:
        """Normalizes OpenAI SDK exceptions into standard domain AIError exceptions."""
        err_msg = str(err).lower()
        status_code = getattr(err, "status_code", None) or getattr(err, "code", None)
        err_type = type(err).__name__

        if "ratelimit" in err_type.lower() or status_code == 429 or "rate limit" in err_msg or "ratelimit" in err_msg or "quota" in err_msg or "429" in err_msg:
            return AIRateLimitError(
                message=f"{self.provider_name} rate limit / quota exceeded: {err}",
                provider=self.provider_name,
                model=model,
                status_code=429,
            )

        if "authentication" in err_type.lower() or status_code in (401, 403) or "401" in err_msg or "403" in err_msg or "unauthorized" in err_msg or "invalid api key" in err_msg or "invalid_api_key" in err_msg:
            return AIAuthenticationError(
                message=f"{self.provider_name} authentication failed: {err}",
                provider=self.provider_name,
                model=model,
                status_code=status_code or 401,
            )

        if "notfound" in err_type.lower() or status_code == 404 or "404" in err_msg or "model not found" in err_msg or "does not exist" in err_msg:
            return AIModelNotFoundError(
                message=f"{self.provider_name} model '{model}' not found: {err}",
                provider=self.provider_name,
                model=model,
                status_code=404,
            )

        if "timeout" in err_type.lower() or "timeout" in err_msg or "timed out" in err_msg:
            return AITimeoutError(
                message=f"{self.provider_name} request timed out: {err}",
                provider=self.provider_name,
                model=model,
            )

        if "connection" in err_type.lower() or "internalserver" in err_type.lower() or status_code in (500, 502, 503, 504) or "connection error" in err_msg or "service unavailable" in err_msg:
            return AIProviderUnavailableError(
                message=f"{self.provider_name} service unavailable / connection error: {err}",
                provider=self.provider_name,
                model=model,
                status_code=status_code or 503,
            )

        if "badrequest" in err_type.lower() or status_code == 400:
            if "safety" in err_msg or "content_policy" in err_msg or "moderation" in err_msg:
                return AIContentFilterError(
                    message=f"{self.provider_name} content filter triggered: {err}",
                    provider=self.provider_name,
                    model=model,
                    status_code=400,
                )
            return AINonRetryableError(
                message=f"{self.provider_name} bad request: {err}",
                provider=self.provider_name,
                model=model,
                status_code=400,
            )

        return AIRetryableError(
            message=f"{self.provider_name} invocation failed: {err}",
            provider=self.provider_name,
            model=model,
            status_code=status_code,
        )

    def _get_client(self):
        """Instantiates the OpenAI SDK client with active configuration."""
        import openai
        kwargs = {
            "api_key": self.api_key,
            "timeout": self.timeout,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return openai.OpenAI(**kwargs)

    def generate_vision(self, request: VisionPromptRequest) -> AIResponse:
        """Sends image and prompt to OpenAI / OpenRouter endpoint."""
        fallback_model = self.DEFAULT_OPENROUTER_MODEL if self.provider_name == "openrouter" else self.DEFAULT_OPENAI_MODEL
        model = request.model or self.default_model or fallback_model
        img_b64 = base64.b64encode(request.image_bytes).decode("utf-8")

        try:
            client = self._get_client()
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{request.mime_type};base64,{img_b64}"},
                    },
                    {"type": "text", "text": request.prompt},
                ],
            }]

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=request.timeout or self.timeout,
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            usage_dict = usage.model_dump() if usage and hasattr(usage, "model_dump") else None

            return AIResponse(content=content, model=model, usage=usage_dict)
        except Exception as e:
            norm_err = self._normalize_error(e, model)
            logger.warning("%s vision request failed (model=%s): %s", self.provider_name, model, norm_err)
            raise norm_err from e

    def generate_text(self, request: TextPromptRequest) -> AIResponse:
        """Sends text prompt to OpenAI / OpenRouter endpoint."""
        fallback_model = self.DEFAULT_OPENROUTER_MODEL if self.provider_name == "openrouter" else self.DEFAULT_OPENAI_MODEL
        model = request.model or self.default_model or fallback_model

        try:
            client = self._get_client()
            messages = [{"role": "user", "content": request.prompt}]

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=request.timeout or self.timeout,
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            usage_dict = usage.model_dump() if usage and hasattr(usage, "model_dump") else None

            return AIResponse(content=content, model=model, usage=usage_dict)
        except Exception as e:
            norm_err = self._normalize_error(e, model)
            logger.warning("%s text request failed (model=%s): %s", self.provider_name, model, norm_err)
            raise norm_err from e
