# ============================================================
#  infrastructure/persistence/__init__.py
# ============================================================

try:
    from infrastructure.persistence.connection import DatabaseManager, get_db_manager, get_connection
    from infrastructure.persistence.migration_runner import MigrationRunner, MigrationError, run_migrations
    __all__ = [
        "DatabaseManager",
        "get_db_manager",
        "get_connection",
        "MigrationRunner",
        "MigrationError",
        "run_migrations",
    ]
except ImportError:
    # Legacy MySQL dependencies (pymysql) are optional for Desktop SQLite execution.
    __all__ = []
