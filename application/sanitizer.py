# ============================================================
#  application/sanitizer.py
# ============================================================

import re
from typing import Optional

# Regular expressions for identifying and redacting sensitive credentials and tokens
_RE_GOOGLE_KEY = re.compile(r"AIzaSy[A-Za-z0-9_-]{10,}")
_RE_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
_RE_GENERIC_SECRET = re.compile(r"(?:secret|token|password|api_key|apikey)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{6,})['\"]?", re.IGNORECASE)
_RE_SYNTHETIC_SECRET = re.compile(r"secret_[A-Za-z0-9_-]{4,}", re.IGNORECASE)


def sanitize_error_message(message: Optional[str]) -> str:
    """
    Sanitizes diagnostic messages, logs, and error strings by redacting any raw API keys,
    passwords, tokens, or credential references.
    """
    if not message:
        return ""

    sanitized = str(message)
    sanitized = _RE_GOOGLE_KEY.sub("[REDACTED_API_KEY]", sanitized)
    sanitized = _RE_OPENAI_KEY.sub("[REDACTED_API_KEY]", sanitized)
    sanitized = _RE_SYNTHETIC_SECRET.sub("[REDACTED_SECRET]", sanitized)

    # Redact captured groups in generic secret patterns
    def _redact_generic(match: re.Match) -> str:
        full = match.group(0)
        secret_part = match.group(1)
        return full.replace(secret_part, "[REDACTED_SECRET]")

    sanitized = _RE_GENERIC_SECRET.sub(_redact_generic, sanitized)
    return sanitized
