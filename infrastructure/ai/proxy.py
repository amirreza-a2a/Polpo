# ============================================================
#  infrastructure/ai/proxy.py
#  Proxy environment normalization for AI provider SDK clients
# ============================================================

import os
import logging
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger("infrastructure.ai.proxy")

# httpx (used by google-genai SDK) supports http://, https://, socks5://
# but rejects the generic socks:// scheme that some system proxy configs use.
# The socks:// scheme is functionally equivalent to socks5:// for all
# practical proxy operations; normalizing it prevents SDK-level ValueError.
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
)


def _normalize_proxy_url(url: str) -> Optional[str]:
    """Normalizes a proxy URL for httpx compatibility.

    Converts socks:// to socks5:// since they are functionally equivalent
    and httpx only supports socks5:// as the SOCKS scheme identifier.
    Returns None if the URL should be suppressed entirely.
    """
    if not url:
        return url
    lower = url.lower()
    if lower.startswith("socks://"):
        normalized = "socks5://" + url[len("socks://"):]
        logger.debug(
            "Normalized proxy scheme: %s -> %s", url, normalized
        )
        return normalized
    return url


@contextmanager
def normalized_proxy_env():
    """Context manager that temporarily normalizes proxy environment variables
    for httpx/SDK compatibility.

    The google-genai SDK creates httpx.Client() without explicit proxy config,
    causing httpx to auto-detect from environment variables. If the shell sets
    socks:// (common on systems using V2Ray, Clash, etc.), httpx rejects it.

    This context manager:
    - Normalizes socks:// -> socks5:// for the duration of the call
    - Restores original values on exit
    - Does NOT suppress valid http:// or https:// proxy configurations
    """
    saved: Dict[str, Optional[str]] = {}
    try:
        for key in _PROXY_ENV_KEYS:
            original = os.environ.get(key)
            if original is not None:
                normalized = _normalize_proxy_url(original)
                if normalized != original:
                    saved[key] = original
                    os.environ[key] = normalized
        yield
    finally:
        for key, original_value in saved.items():
            os.environ[key] = original_value
