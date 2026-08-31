# ============================================================
#  infrastructure/persistence/unit_of_work.py
# ============================================================

from typing import Optional
from database.connection import DatabaseManager
from application.ports.unit_of_work import IUnitOfWork, IUnitOfWorkFactory
from infrastructure.persistence.repositories import (
    MySQLJobRepository,
    MySQLPipeline2JobRepository,
    MySQLUserRepository,
    MySQLPromptRepository,
    MySQLApiRepository,
    MySQLDonationRepository,
)


class MySQLUnitOfWork(IUnitOfWork):
    """
    پیاده‌سازی MySQL درگاه Unit of Work با اتصال اشتراکی استخراج‌شده از Pool.
    تمام عملیات‌های چند-مخزنی در محدوده یک تراکنش واحد انجام می‌شوند.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self._conn = None
        self._tx_active = False

    def __enter__(self) -> "IUnitOfWork":
        self._conn = self.db_manager.get_connection()
        self._conn.begin()
        self._tx_active = True

        # اتصال مخازن به اتصال تراکنشی فعال
        self.jobs = MySQLJobRepository(self._conn)
        self.pipeline2_jobs = MySQLPipeline2JobRepository(self._conn)
        self.users = MySQLUserRepository(self._conn)
        self.prompts = MySQLPromptRepository(self._conn)
        self.apis = MySQLApiRepository(self._conn)
        self.donations = MySQLDonationRepository(self._conn)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if self._tx_active:
                if exc_type:
                    self.rollback()
                else:
                    self.commit()
        finally:
            if self._conn:
                self._conn.close()  # بازگرداندن اتصال به pool
                self._conn = None

    def commit(self) -> None:
        if self._conn and self._tx_active:
            self._conn.commit()
            self._tx_active = False

    def rollback(self) -> None:
        if self._conn and self._tx_active:
            self._conn.rollback()
            self._tx_active = False


class MySQLUnitOfWorkFactory(IUnitOfWorkFactory):
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db_manager = db_manager or DatabaseManager()

    def create(self) -> IUnitOfWork:
        return MySQLUnitOfWork(self.db_manager)
