# ============================================================
#  infrastructure/persistence/connection.py
# ============================================================

import os
import queue
import logging
import threading
from contextlib import contextmanager
from typing import Generator, Any

import pymysql
import pymysql.cursors
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

logger = logging.getLogger("database.connection")


class PooledConnectionWrapper:
    """
    پروکسی روی اتصال خام PyMySQL جهت رهگیری متد close() و بازگرداندن اتصال به Pool.
    تمام متدها و پراپرتی‌های اتصال به اتصال خام زیرین Delegate می‌شوند.
    """

    def __init__(self, raw_conn, pool_manager: "DatabaseManager"):
        self._raw_conn = raw_conn
        self._pool_manager = pool_manager
        self._is_closed = False

    def close(self):
        """بازگرداندن اتصال به استخر اتصالات با پاکسازی وضعیت تراکنش."""
        if not self._is_closed:
            self._is_closed = True
            if self._pool_manager and self._raw_conn:
                self._pool_manager.release_connection(self._raw_conn)
                self._raw_conn = None
                self._pool_manager = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        if self._is_closed or self._raw_conn is None:
            raise RuntimeError("Cannot access attributes on a closed database connection.")
        return getattr(self._raw_conn, name)


class DatabaseManager:
    """
    مدیریت اتصالات و تراکنش‌های پایگاه داده با پشتیبانی از Connection Pooling و Transaction Context.
    این کلاس جزئیات درایور دیتابیس (PyMySQL) را پشت اینترفیس‌های context manager پنهان می‌کند.
    """

    def __init__(
        self,
        host: str = DB_HOST,
        port: int = DB_PORT,
        user: str = DB_USER,
        password: str = DB_PASS,
        database: str = DB_NAME,
        max_connections: int = 10,
        connection_timeout: float = 30.0,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout

        self._pool: queue.Queue = queue.Queue(maxsize=max_connections)
        self._created_connections = 0
        self._lock = threading.Lock()
        self._closed = False

    def _create_raw_connection(self):
        """ایجاد یک اتصال خام جدید با درایور PyMySQL."""
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def get_connection(self) -> PooledConnectionWrapper:
        """
        دریافت یک اتصال زنده از Pool یا ساخت اتصال جدید در صورت وجود ظرفیت.
        اتصال برگشتی در قالب PooledConnectionWrapper است تا close() اتصال را به pool بازگرداند.
        """
        if self._closed:
            raise RuntimeError("DatabaseManager has been closed.")

        raw_conn = None

        while raw_conn is None:
            try:
                raw_conn = self._pool.get_nowait()
            except queue.Empty:
                with self._lock:
                    if self._created_connections < self.max_connections:
                        raw_conn = self._create_raw_connection()
                        self._created_connections += 1

                # اگر ظرفیت پر بود، منتظر بازگشت یک اتصال می‌مانیم
                if raw_conn is None:
                    try:
                        raw_conn = self._pool.get(timeout=self.connection_timeout)
                    except queue.Empty:
                        raise TimeoutError("Database connection pool exhausted; timeout waiting for connection.")

            # سنجش سلامت اتصال
            try:
                raw_conn.ping(reconnect=True)
            except Exception as e:
                logger.warning("Pooled connection ping failed, discarding and recreating: %s", e)
                with self._lock:
                    self._created_connections -= 1
                try:
                    raw_conn.close()
                except Exception:
                    pass
                raw_conn = None

        return PooledConnectionWrapper(raw_conn, self)

    def release_connection(self, raw_conn):
        """
        بازگرداندن اتصال خام به Pool همراه با Rollback تراکنش‌های معلق و ریست autocommit.
        """
        if raw_conn is None:
            return

        if self._closed:
            with self._lock:
                self._created_connections -= 1
            try:
                raw_conn.close()
            except Exception:
                pass
            return

        try:
            # 1. Rollback هرگونه تراکنش معلق در صورت غیرفعال بودن autocommit
            is_autocommit = True
            if hasattr(raw_conn, "get_autocommit"):
                is_autocommit = raw_conn.get_autocommit()
            elif hasattr(raw_conn, "_autocommit"):
                is_autocommit = raw_conn._autocommit

            if not is_autocommit:
                try:
                    raw_conn.rollback()
                except Exception:
                    pass

            # 2. ریست autocommit به مقدار پیش‌فرض True
            if hasattr(raw_conn, "autocommit"):
                raw_conn.autocommit(True)

            # 3. قرار دادن اتصال سالم در صف Pool
            self._pool.put_nowait(raw_conn)
        except queue.Full:
            # در صورتی که صف پر باشد، اتصال اضافه بسته می‌شود
            with self._lock:
                self._created_connections -= 1
            try:
                raw_conn.close()
            except Exception:
                pass
        except Exception as e:
            logger.warning("Error resetting and releasing connection to pool: %s", e)
            with self._lock:
                self._created_connections -= 1
            try:
                raw_conn.close()
            except Exception:
                pass

    @contextmanager
    def connection(self) -> Generator[PooledConnectionWrapper, None, None]:
        """
        Context Manager جهت دریافت اتصال و بازگرداندن تضمینی به Pool پس از خروج از بلوک.
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def cursor(self) -> Generator[Any, None, None]:
        """
        Context Manager جهت اجرای کوئری با Cursor و آزادسازی خودکار منابع.
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                yield cur

    @contextmanager
    def transaction(self) -> Generator[Any, None, None]:
        """
        Context Manager تراکنش صریح (Explicit Transaction).
        در صورت اتمام موفقیت‌آمیز بلوک commit شده و در صورت بروز خطا rollback می‌شود.
        """
        with self.connection() as conn:
            conn.autocommit(False)
            try:
                with conn.cursor() as cur:
                    yield cur
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                    logger.debug("Transaction rolled back due to error: %s", e)
                except Exception as rollback_err:
                    logger.error("Failed to rollback transaction: %s", rollback_err)
                raise
            finally:
                conn.autocommit(True)

    def close(self):
        """بستن تمام اتصالات موجود در Pool."""
        with self._lock:
            self._closed = True
            while not self._pool.empty():
                try:
                    conn = self._pool.get_nowait()
                    conn.close()
                except Exception:
                    pass
            self._created_connections = 0


# ─── Singleton DatabaseManager Instance ──────────────────────
_GLOBAL_DB_MANAGER: DatabaseManager | None = None
_GLOBAL_LOCK = threading.Lock()


def get_db_manager() -> DatabaseManager:
    """دریافت یا ساخت Instance سراسری DatabaseManager."""
    global _GLOBAL_DB_MANAGER
    if _GLOBAL_DB_MANAGER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_DB_MANAGER is None:
                _GLOBAL_DB_MANAGER = DatabaseManager()
    return _GLOBAL_DB_MANAGER


def get_connection() -> PooledConnectionWrapper:
    """
    تابع کمکی سازگار با کدهای قدیمی جهت دریافت اتصال مستقیم.
    اتصال حاصل پس از فراخوانی .close() به Pool بازمی‌گردد.
    """
    return get_db_manager().get_connection()
