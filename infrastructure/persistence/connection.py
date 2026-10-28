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
    Proxy wrapper over a raw PyMySQL connection to intercept close() and return it to the pool.
    All methods and attributes are delegated to the underlying raw connection.
    """

    def __init__(self, raw_conn, pool_manager: "DatabaseManager"):
        self._raw_conn = raw_conn
        self._pool_manager = pool_manager
        self._is_closed = False

    def close(self):
        """Return the connection to the pool while cleaning up transaction state."""
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
    Database connection and transaction manager supporting connection pooling and transaction contexts.
    Hides driver details (PyMySQL) behind context manager interfaces.
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
        """Create a new raw connection using the PyMySQL driver."""
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
        Acquire an active connection from the pool or create a new one if within capacity.
        Returns a PooledConnectionWrapper so that close() returns the connection to the pool.
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

                # If at max capacity, wait for an available connection from the pool
                if raw_conn is None:
                    try:
                        raw_conn = self._pool.get(timeout=self.connection_timeout)
                    except queue.Empty:
                        raise TimeoutError("Database connection pool exhausted; timeout waiting for connection.")

            # Ping connection to verify liveness
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
        Return a raw connection to the pool, rolling back pending transactions and resetting autocommit.
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
            # 1. Roll back pending transaction if autocommit is disabled
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

            # 2. Reset autocommit to default True
            if hasattr(raw_conn, "autocommit"):
                raw_conn.autocommit(True)

            # 3. Place healthy connection back into pool queue
            self._pool.put_nowait(raw_conn)
        except queue.Full:
            # If queue is full, close excess connection
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
        Context manager to acquire a connection and reliably return it to the pool upon exit.
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def cursor(self) -> Generator[Any, None, None]:
        """
        Context manager to execute queries via a cursor and automatically release resources.
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                yield cur

    @contextmanager
    def transaction(self) -> Generator[Any, None, None]:
        """
        Context manager for explicit transactions.
        Commits on successful completion and rolls back if an error occurs.
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
        """Close all connections in the pool."""
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
    """Get or create the global DatabaseManager singleton instance."""
    global _GLOBAL_DB_MANAGER
    if _GLOBAL_DB_MANAGER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_DB_MANAGER is None:
                _GLOBAL_DB_MANAGER = DatabaseManager()
    return _GLOBAL_DB_MANAGER


def get_connection() -> PooledConnectionWrapper:
    """
    Compatibility helper for legacy code to acquire a pooled database connection.
    The resulting connection returns to the pool when .close() is called.
    """
    return get_db_manager().get_connection()
