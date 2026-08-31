# ============================================================
#  utils/rate_limiter.py  –  کنترل نرخ درخواست (RPM)
# ============================================================

import time
import json
import os
import logging
from config import RATE_FILE

logger = logging.getLogger("rate_limiter")

# RPM پیش‌فرض به تفکیک provider
DEFAULT_RPM = {
    "google":     5,     # Gemini Flash Free Tier
    "openai":     60,
    "openrouter": 20,
}


def _load() -> dict:
    if os.path.exists(RATE_FILE):
        try:
            with open(RATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error("Corrupted JSON in rate limits file (%s): %s", RATE_FILE, e)
            return {}
        except OSError as e:
            logger.error("Filesystem error reading rate limits file (%s): %s", RATE_FILE, e)
            return {}
    return {}


def _save(data: dict):
    try:
        with open(RATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        logger.error("Failed to write rate limits file (%s): %s", RATE_FILE, e)
    except Exception as e:
        logger.error("Unexpected error saving rate limits (%s): %s", RATE_FILE, e)


def _make_key(api_id: int | str, provider: str = None, api_type: str = None) -> str:
    """
    تولید کلید یکتا برای هر اسلات API جهت جلوگیری از تداخل کلید بین APIهای مختلف.
    """
    parts = []
    if provider:
        parts.append(str(provider).lower())
    if api_type:
        parts.append(str(api_type).lower())
    parts.append(str(api_id))
    return "_".join(parts)


def wait_if_needed(api_id: int | str | dict, provider: str = None, api_type: str = None):
    """
    اگر از آخرین درخواست به این API زمان کافی نگذشته، صبر می‌کند.
    بر اساس RPM provider محاسبه می‌شود.
    پشتیبانی از دریافت dict (ورودی کامل اسلات API) یا شناسه‌های مجزا.
    """
    if isinstance(api_id, dict):
        provider = api_id.get("provider", provider)
        api_type = api_id.get("type", api_type)
        api_id = api_id.get("id", 0)

    rpm = DEFAULT_RPM.get(provider, 30)
    min_interval = 60.0 / rpm          # ثانیه بین هر درخواست

    data    = _load()
    key     = _make_key(api_id, provider, api_type)
    now     = time.time()
    last_ts = data.get(key, 0)

    elapsed  = now - last_ts
    wait_for = min_interval - elapsed

    if wait_for > 0:
        time.sleep(wait_for)

    # ثبت زمان این درخواست
    data[key] = time.time()
    _save(data)


def mark_request_sent(api_id: int | str | dict, provider: str = None, api_type: str = None):
    """زمان آخرین درخواست را ثبت می‌کند (بدون صبر کردن)."""
    if isinstance(api_id, dict):
        provider = api_id.get("provider", provider)
        api_type = api_id.get("type", api_type)
        api_id = api_id.get("id", 0)

    key = _make_key(api_id, provider, api_type)
    data = _load()
    data[key] = time.time()
    _save(data)
