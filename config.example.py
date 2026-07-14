# این فایل را کپی کرده و به config.py تغییر نام دهید،
# سپس مقادیر واقعی خودتان را جایگزین کنید.

# ─── Telegram Bot ──────────────────────────────
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_IDS = [123456789]

# ─── Database ───────────────────────────────────
DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "your_db_name"
DB_USER = "your_db_user"
DB_PASS = "your_db_password"

# ─── کانال‌های آرشیو ────────────────────────────
BACKUP_CHANNEL_ID          = -1000000000000
SOURCE_ARCHIVE_CHANNEL_ID  = -1000000000000

# ─── محدودیت‌ها ──────────────────────────────────
DAILY_PAGE_LIMIT   = 50
MAX_PDF_SIZE_MB     = 50
MAX_QUEUE_PER_USER  = 3
DPI                 = 200

# ─── ابزارها ─────────────────────────────────────
PANDOC_PATH = "/home/USERNAME/pandoc-3.10/bin/pandoc"