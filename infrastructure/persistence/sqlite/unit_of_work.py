# ============================================================
#  infrastructure/persistence/sqlite/unit_of_work.py
# ============================================================

import sqlite3
from typing import Optional

from application.ports.unit_of_work import IUnitOfWork, IUnitOfWorkFactory
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.repositories import (
    SQLiteSettingsRepository,
    SQLitePromptRepository,
    SQLiteApiSlotRepository,
    SQLiteJobRepository,
    SQLitePipeline2JobRepository,
)


class SQLiteUnitOfWork(IUnitOfWork):
    """
    Unit of Work managing SQLite transaction lifecycle for desktop operations.
    Uses deferred transaction semantics to allow concurrent readers in WAL mode.
    """

    def __init__(self, db_manager: SQLiteDatabaseManager):
        self.db_manager = db_manager
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "SQLiteUnitOfWork":
        self._conn = self.db_manager.create_connection()

        self.settings = SQLiteSettingsRepository(self._conn)
        self.jobs = SQLiteJobRepository(self._conn)
        self.pipeline2_jobs = SQLitePipeline2JobRepository(self._conn)
        self.prompts = SQLitePromptRepository(self._conn)
        self.apis = SQLiteApiSlotRepository(self._conn)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if self._conn and self._conn.in_transaction:
                if exc_type is not None:
                    self.rollback()
                else:
                    self.commit()
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

    def commit(self) -> None:
        if self._conn and self._conn.in_transaction:
            self._conn.execute("COMMIT")

    def rollback(self) -> None:
        if self._conn and self._conn.in_transaction:
            self._conn.execute("ROLLBACK")


class SQLiteUnitOfWorkFactory(IUnitOfWorkFactory):
    def __init__(self, db_manager: SQLiteDatabaseManager):
        if db_manager is None:
            raise ValueError("SQLiteUnitOfWorkFactory requires an explicit SQLiteDatabaseManager instance.")
        self.db_manager = db_manager

    def create(self) -> IUnitOfWork:
        return SQLiteUnitOfWork(self.db_manager)
