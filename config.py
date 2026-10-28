# ============================================================
#  config.py  –  Main bot configuration (legacy)
# ============================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# ─── Project root and .env loading ─────────────────────────────
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


# ─── Telegram ───────────────────────────────────────────────
BOT_TOKEN                  = _get_env_str("BOT_TOKEN", "")
ADMIN_IDS                  = _get_env_int_list("ADMIN_IDS", [])
BACKUP_CHANNEL_ID          = _get_env_int("BACKUP_CHANNEL_ID", 0)
SOURCE_ARCHIVE_CHANNEL_ID  = _get_env_int("SOURCE_ARCHIVE_CHANNEL_ID", 0)

# ─── MySQL Database ────────────────────────────────────────
DB_HOST = _get_env_str("DB_HOST", "localhost")
DB_PORT = _get_env_int("DB_PORT", 3306)
DB_NAME = _get_env_str("DB_NAME", "")
DB_USER = _get_env_str("DB_USER", "")
DB_PASS = _get_env_str("DB_PASS", "")

# ─── Limits ────────────────────────────────────────────────
MAX_PDF_SIZE_MB     = _get_env_int("MAX_PDF_SIZE_MB", 100)          # Maximum input PDF size (MB)
DAILY_PAGE_LIMIT    = _get_env_int("DAILY_PAGE_LIMIT", 100)         # Daily page limit per user
MAX_QUEUE_PER_USER  = _get_env_int("MAX_QUEUE_PER_USER", 3)          # Maximum concurrent jobs in queue per user

# ─── Paths (relative to project root) ──────────────────────
TEMP_DIR          = _resolve_path("TEMP_DIR", "temp_files")         # Temporary files directory
OUTPUT_DIR        = _resolve_path("OUTPUT_DIR", "output_files")     # Output files directory
WORKER_LOCK_FILE  = _resolve_path("WORKER_LOCK_FILE", "worker.lock") # Worker lock file
RATE_FILE         = _resolve_path("RATE_FILE", "rate_limits.json")  # Rate limiter tracking file

# ─── Processing ────────────────────────────────────────────
DPI = _get_env_int("DPI", 200)                                      # PDF page rendering DPI
