# ============================================================
#  config.py  –  تنظیمات اصلی ربات
# ============================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# ─── ریشه پروژه و بارگذاری .env ─────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _get_env_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val is not None else default


def _get_env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or not str(val).strip():
        return default
    try:
        return int(str(val).strip())
    except ValueError:
        return default


def _get_env_int_list(key: str, default: list[int] = None) -> list[int]:
    if default is None:
        default = []
    val = os.getenv(key)
    if val is None or not str(val).strip():
        return default
    result = []
    for item in str(val).split(","):
        cleaned = item.strip()
        if cleaned:
            try:
                result.append(int(cleaned))
            except ValueError:
                pass
    return result


def _resolve_path(key: str, default_relative: str) -> str:
    raw_path = _get_env_str(key, default_relative)
    path_obj = Path(raw_path)
    if path_obj.is_absolute():
        return str(path_obj)
    return str(PROJECT_ROOT / path_obj)


# ─── تلگرام ───────────────────────────────────────────────
BOT_TOKEN                  = _get_env_str("BOT_TOKEN", "")
ADMIN_IDS                  = _get_env_int_list("ADMIN_IDS", [])
BACKUP_CHANNEL_ID          = _get_env_int("BACKUP_CHANNEL_ID", 0)
SOURCE_ARCHIVE_CHANNEL_ID  = _get_env_int("SOURCE_ARCHIVE_CHANNEL_ID", 0)

# ─── دیتابیس MySQL ────────────────────────────────────────
DB_HOST = _get_env_str("DB_HOST", "localhost")
DB_PORT = _get_env_int("DB_PORT", 3306)
DB_NAME = _get_env_str("DB_NAME", "")
DB_USER = _get_env_str("DB_USER", "")
DB_PASS = _get_env_str("DB_PASS", "")

# ─── محدودیت‌ها ────────────────────────────────────────────
MAX_PDF_SIZE_MB     = _get_env_int("MAX_PDF_SIZE_MB", 100)          # حداکثر حجم PDF ورودی
DAILY_PAGE_LIMIT    = _get_env_int("DAILY_PAGE_LIMIT", 100)         # محدودیت صفحه روزانه هر کاربر
MAX_QUEUE_PER_USER  = _get_env_int("MAX_QUEUE_PER_USER", 3)          # حداکثر جاب همزمان در صف برای یک کاربر

# ─── مسیرها (متصل به ریشه پروژه) ──────────────────────────
TEMP_DIR          = _resolve_path("TEMP_DIR", "temp_files")         # پوشه فایل‌های موقت
OUTPUT_DIR        = _resolve_path("OUTPUT_DIR", "output_files")     # پوشه فایل‌های خروجی
WORKER_LOCK_FILE  = _resolve_path("WORKER_LOCK_FILE", "worker.lock") # فایل قفل Worker
RATE_FILE         = _resolve_path("RATE_FILE", "rate_limits.json")  # فایل ریت لیمیتر

# ─── پردازش ───────────────────────────────────────────────
DPI = _get_env_int("DPI", 200)                                      # کیفیت رندر صفحات PDF
