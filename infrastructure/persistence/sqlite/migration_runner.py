# ============================================================
#  infrastructure/persistence/sqlite/migration_runner.py
# ============================================================

import re
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Set
import sqlite3

from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager

logger = logging.getLogger("database.sqlite.migrations")

MIGRATIONS_DIR_DEFAULT = Path(__file__).parent / "migrations"


class MigrationError(Exception):
    """Raised when an error occurs during SQLite database migration."""
    pass


class SQLiteMigrationRunner:
    """
    Executes versioned SQL migrations atomically on SQLite databases.
    Guarantees that schema modifications and schema_version tracking commit or roll back together.
    """

    SCHEMA_VERSION_TABLE = "schema_version"

    def __init__(
        self,
        db_manager: SQLiteDatabaseManager,
        migrations_dir: Optional[Path | str] = None,
    ):
        self.db_manager = db_manager
        self.migrations_dir = Path(migrations_dir or MIGRATIONS_DIR_DEFAULT)

    def ensure_version_table(self, conn: sqlite3.Connection) -> None:
        """Creates the schema_version table if it does not already exist."""
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.SCHEMA_VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
        """)

    def get_applied_versions(self, conn: sqlite3.Connection) -> Set[int]:
        """Returns the set of applied integer migration versions."""
        self.ensure_version_table(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT version FROM {self.SCHEMA_VERSION_TABLE}")
        return {int(row["version"]) for row in cur.fetchall()}

    def discover_migrations(self) -> List[Path]:
        """Discovers and returns migration files sorted by version number."""
        if not self.migrations_dir.exists():
            return []

        pattern = re.compile(r"^(\d+)_(.+)\.sql$")
        files = []
        for path in self.migrations_dir.glob("*.sql"):
            match = pattern.match(path.name)
            if match:
                files.append((int(match.group(1)), path))

        files.sort(key=lambda item: item[0])
        return [path for _, path in files]

    def _split_sql_statements(self, sql_content: str) -> List[str]:
        """
        Parses SQL script into individual executable statements.
        Accurately handles single quotes, double quotes, line comments, and block comments,
        ensuring semicolons inside literals or comments do not cause invalid statement splits.
        """
        statements: List[str] = []
        current: List[str] = []
        in_single_quote = False
        in_double_quote = False
        in_line_comment = False
        in_block_comment = False
        i = 0
        n = len(sql_content)

        while i < n:
            char = sql_content[i]
            next_char = sql_content[i + 1] if i + 1 < n else ""

            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                    current.append(char)
                i += 1
                continue

            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_single_quote and not in_double_quote:
                if char == "-" and next_char == "-":
                    in_line_comment = True
                    i += 2
                    continue
                if char == "/" and next_char == "*":
                    in_block_comment = True
                    i += 2
                    continue

            if char == "'" and not in_double_quote:
                if in_single_quote and next_char == "'":
                    current.append("''")
                    i += 2
                    continue
                in_single_quote = not in_single_quote
                current.append(char)
                i += 1
                continue

            if char == '"' and not in_single_quote:
                if in_double_quote and next_char == '"':
                    current.append('""')
                    i += 2
                    continue
                in_double_quote = not in_double_quote
                current.append(char)
                i += 1
                continue

            if char == ";" and not in_single_quote and not in_double_quote:
                stmt = "".join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
                i += 1
                continue

            current.append(char)
            i += 1

        trailing = "".join(current).strip()
        if trailing:
            statements.append(trailing)

        return statements

    def run_migrations(self) -> List[int]:
        """
        Applies all pending migrations in sequential order.
        Each migration executes inside an atomic BEGIN IMMEDIATE transaction.
        """
        conn = self.db_manager.create_connection()
        try:
            self.ensure_version_table(conn)
            applied_versions = self.get_applied_versions(conn)
            migration_files = self.discover_migrations()
            applied_this_run: List[int] = []

            for migration_file in migration_files:
                match = re.match(r"^(\d+)_(.+)\.sql$", migration_file.name)
                if not match:
                    continue

                version = int(match.group(1))
                name = match.group(2)

                if version in applied_versions:
                    continue

                logger.info("Applying SQLite migration %03d: %s...", version, name)

                with open(migration_file, "r", encoding="utf-8") as f:
                    content = f.read()

                statements = self._split_sql_statements(content)

                # Execute migration DDL and version record within an atomic BEGIN IMMEDIATE transaction
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in statements:
                        conn.execute(stmt)

                    now_utc = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        f"INSERT INTO {self.SCHEMA_VERSION_TABLE} (version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, now_utc),
                    )
                    conn.execute("COMMIT")
                    applied_versions.add(version)
                    applied_this_run.append(version)
                    logger.info("SQLite migration %03d applied successfully.", version)
                except Exception as e:
                    conn.execute("ROLLBACK")
                    logger.error("Migration %03d (%s) failed: %s. Rolled back.", version, migration_file.name, e)
                    raise MigrationError(f"Migration {migration_file.name} failed: {e}") from e

            return applied_this_run
        finally:
            conn.close()
