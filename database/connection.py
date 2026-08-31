# ============================================================
#  database/connection.py
# ============================================================

from infrastructure.persistence.connection import (
    DatabaseManager,
    get_db_manager,
    get_connection,
)
from infrastructure.persistence.migration_runner import (
    run_migrations,
    MigrationRunner,
    MigrationError,
)


def init_db():
    """
    راه‌اندازی پایگاه داده با استفاده از Migration Runner نسخه‌بندی‌شده.
    """
    applied = run_migrations()
    print(f"✅ دیتابیس با موفقیت راه‌اندازی شد. (مایگریشن‌های اعمال‌شده: {len(applied)})")


if __name__ == "__main__":
    init_db()