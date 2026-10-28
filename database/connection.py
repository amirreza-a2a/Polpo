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
    Initialize the database using the versioned Migration Runner.
    """
    applied = run_migrations()
    print(f"✅ دیتابیس با موفقیت راه‌اندازی شد. (مایگریشن‌های اعمال‌شده: {len(applied)})")


if __name__ == "__main__":
    init_db()