# ============================================================
#  infrastructure/persistence/__init__.py
# ============================================================

from typing import Any

# Public exports preserved for backward compatibility with legacy MySQL consumers.
# Legacy MySQL modules (and their PyMySQL dependency) are loaded lazily via PEP 562
# to avoid contaminating sys.modules during Desktop SQLite initialization.
__all__ = [
    "DatabaseManager",
    "get_db_manager",
    "get_connection",
    "MigrationRunner",
    "MigrationError",
    "run_migrations",
]


def __getattr__(name: str) -> Any:
    if name in ("DatabaseManager", "get_db_manager", "get_connection"):
        from infrastructure.persistence import connection
        val = getattr(connection, name)
        globals()[name] = val
        return val
    if name in ("MigrationRunner", "MigrationError", "run_migrations"):
        from infrastructure.persistence import migration_runner
        val = getattr(migration_runner, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))
