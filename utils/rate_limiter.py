# ============================================================
#  utils/rate_limiter.py  –  کنترل نرخ درخواست (RPM)
# ============================================================

import time
import json
import os

# فایلی که آخرین زمان درخواست هر API را نگه می‌دارد
RATE_FILE = "rate_limits.json"

# RPM پیش‌فرض به تفکیک provider
DEFAULT_RPM = {
    "google":     5,     # Gemini 2.5 Flash Free Tier
    "openai":     60,
    "openrouter": 20,
}


def _load() -> dict:
    if os.path.exists(RATE_FILE):
        with open(RATE_FILE, "r") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(RATE_FILE, "w") as f:
        json.dump(data, f)


def wait_if_needed(api_id: int, provider: str):
    """
    اگر از آخرین درخواست به این API زمان کافی نگذشته، صبر می‌کند.
    بر اساس RPM provider محاسبه می‌شود.
    """
    rpm = DEFAULT_RPM.get(provider, 30)
    min_interval = 60.0 / rpm          # ثانیه بین هر درخواست

    data    = _load()
    key     = str(api_id)
    now     = time.time()
    last_ts = data.get(key, 0)

    elapsed  = now - last_ts
    wait_for = min_interval - elapsed

    if wait_for > 0:
        time.sleep(wait_for)

    # ثبت زمان این درخواست
    data[key] = time.time()
    _save(data)


def mark_request_sent(api_id: int):
    """زمان آخرین درخواست را ثبت می‌کند (بدون صبر کردن)."""
    data = _load()
    data[str(api_id)] = time.time()
    _save(data)
