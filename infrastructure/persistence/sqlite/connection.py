# ============================================================
#  infrastructure/persistence/sqlite/connection.py
# ============================================================

import os
import sqlite3
from pathlib import Path
from typing import Union


class SQLiteDatabaseManager:
    """
    Manages SQLite database path and produces thread-isolated connections
    configured with WAL mode, busy timeout, and foreign keys.
    """

    def __init__(self, db_path: Union[Path, str]):
        if not db_path:
            raise ValueError("db_path must be a non-empty Path or string.")

        self.db_path = (
            Path(db_path)
            if isinstance(db_path, str) and not db_path.startswith(":") and not db_path.startswith("file:")
            else db_path
        )
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create_in_memory(cls, shared: bool = False) -> "SQLiteDatabaseManager":
        """
        Factory helper for test configurations.
        If shared=True, uses a shared in-memory URI so multiple connections
        can share the same temporary in-memory database.
        """
        if shared:
            return cls("file:memdb_shared?mode=memory&cache=shared")
        return cls(":memory:")

    def create_connection(self) -> sqlite3.Connection:
        """
        Creates and configures a fresh, thread-isolated SQLite connection.
        Connections are never shared across worker threads.
        """
        path_str = str(self.db_path)
        is_uri = path_str.startswith("file:")

        # isolation_level=None puts sqlite3 in autocommit mode, enabling
        # explicit BEGIN, BEGIN IMMEDIATE, COMMIT, and ROLLBACK control.
        conn = sqlite3.connect(
            path_str,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=True,
            uri=is_uri,
        )
        conn.row_factory = sqlite3.Row

        # Apply mandatory desktop SQLite pragmas
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")

        return conn
