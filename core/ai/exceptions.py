# ============================================================
#  core/ai/exceptions.py
# ============================================================

from typing import Optional


class AIError(Exception):
    """
    Base exception for all AI layer errors.
    Preserves infrastructure-neutral metadata (message, provider, model, status_code)
    without retaining raw SDK objects within the domain layer.
    """
    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code


class AIRetryableError(AIError):
    """Transient, retryable error (rate limit, timeout, temporary server outage)."""
    pass


class AIRateLimitError(AIRetryableError):
    """Rate limit or quota exhaustion error (429 Too Many Requests / Quota Exceeded)."""
    pass


class AITimeoutError(AIRetryableError):
    """Request timeout error."""
    pass


class AIProviderUnavailableError(AIRetryableError):
    """Provider service unavailability or internal server error (500, 502, 503, 504)."""
    pass


class AINonRetryableError(AIError):
    """Non-retryable error under the same key or parameters (invalid key, model not found, content filter)."""
    pass


class AIAuthenticationError(AINonRetryableError):
    """API key authentication or authorization error (401 / 403 Invalid API Key)."""
    pass


class AIModelNotFoundError(AINonRetryableError):
    """Requested model not found error (404 Model Not Found)."""
    pass


class AIContentFilterError(AINonRetryableError):
    """Provider safety or content filter violation error."""
    pass


class AIChainExhaustedError(AIError):
    """Raised when all slots in the API fallback chain have been exhausted without success."""
    pass
