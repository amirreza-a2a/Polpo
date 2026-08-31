# ============================================================
#  tests/unit/test_persistence_infrastructure.py
# ============================================================

import unittest
import tempfile
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import tests.characterization.conftest_base
from infrastructure.persistence.connection import DatabaseManager, PooledConnectionWrapper
from infrastructure.persistence.migration_runner import MigrationRunner, MigrationError
import database.models as db_models


class TestDatabaseManager(unittest.TestCase):
    """
    Tests for DatabaseManager lifecycle, pooling, connection wrapper, and transaction semantics.
    """

    def setUp(self):
        self.db = DatabaseManager(max_connections=3, connection_timeout=0.5)

    def tearDown(self):
        self.db.close()

    def test_legacy_conn_close_returns_connection_to_pool(self):
        """Verify that legacy conn = db.get_connection(); conn.close() returns connection to pool."""
        mock_raw = MagicMock()
        mock_raw.ping.return_value = None

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw):
            conn = self.db.get_connection()
            self.assertIsInstance(conn, PooledConnectionWrapper)
            self.assertEqual(self.db._created_connections, 1)
            self.assertEqual(self.db._pool.qsize(), 0)

            # Legacy caller closes connection
            conn.close()

            # Physical connection must NOT be closed, but returned to pool queue
            mock_raw.close.assert_not_called()
            self.assertEqual(self.db._pool.qsize(), 1)
            self.assertEqual(self.db._created_connections, 1)

    def test_repeated_legacy_calls_do_not_exhaust_pool(self):
        """
        Verify that making many consecutive legacy calls with a small pool (max_connections=3)
        reuses pooled connections without pool exhaustion.
        """
        mock_raw = MagicMock()
        mock_raw.ping.return_value = None

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw):
            for _ in range(50):
                conn = self.db.get_connection()
                self.assertIsNotNone(conn)
                conn.close()

            # Only 1 raw connection was ever created and reused 50 times
            self.assertEqual(self.db._created_connections, 1)
            self.assertEqual(self.db._pool.qsize(), 1)

    def test_broken_pooled_connections_are_replaced_correctly(self):
        """Verify that when a pooled connection fails ping, it is closed and replaced cleanly."""
        mock_raw_dead = MagicMock()
        mock_raw_dead.ping.side_effect = Exception("Lost connection to MySQL server")

        mock_raw_healthy = MagicMock()
        mock_raw_healthy.ping.return_value = None

        # Pool starts with the dead connection
        self.db._pool.put_nowait(mock_raw_dead)
        self.db._created_connections = 1

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw_healthy):
            conn = self.db.get_connection()
            self.assertIsNotNone(conn)
            # Dead connection was physically closed
            mock_raw_dead.close.assert_called_once()
            # Created count remains accurate
            self.assertEqual(self.db._created_connections, 1)

            conn.close()
            self.assertEqual(self.db._pool.qsize(), 1)

    def test_transaction_commits_on_success(self):
        """Verify transaction context manager commits on clean exit."""
        mock_raw = MagicMock()
        mock_cursor = MagicMock()
        mock_raw.cursor.return_value.__enter__.return_value = mock_cursor
        mock_raw.ping.return_value = None

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw):
            with self.db.transaction() as cur:
                cur.execute("INSERT INTO test VALUES (1)")

            mock_raw.autocommit.assert_any_call(False)
            mock_raw.commit.assert_called_once()
            mock_raw.rollback.assert_not_called()
            mock_raw.autocommit.assert_called_with(True)

    def test_transaction_rolls_back_on_exception(self):
        """Verify transaction context manager rolls back on exception and propagates error."""
        mock_raw = MagicMock()
        mock_cursor = MagicMock()
        mock_raw.cursor.return_value.__enter__.return_value = mock_cursor
        mock_raw.ping.return_value = None

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw):
            with self.assertRaises(ValueError):
                with self.db.transaction() as cur:
                    cur.execute("INSERT INTO test VALUES (1)")
                    raise ValueError("Simulated business error")

            mock_raw.commit.assert_not_called()
            mock_raw.rollback.assert_called_once()
            mock_raw.autocommit.assert_called_with(True)

    def test_failed_transaction_leaves_connection_clean_for_reuse(self):
        """Verify that a connection used in an uncommitted state is rolled back before returning to pool."""
        mock_raw = MagicMock()
        mock_raw.ping.return_value = None
        # Simulate dirty uncommitted session state (autocommit is False)
        mock_raw.get_autocommit.return_value = False

        with patch.object(self.db, "_create_raw_connection", return_value=mock_raw):
            conn = self.db.get_connection()
            # Close wrapper while dirty
            conn.close()

            # release_connection must have executed rollback and autocommit(True)
            mock_raw.rollback.assert_called()
            mock_raw.autocommit.assert_called_with(True)


class TestMigrationRunner(unittest.TestCase):
    """
    Tests for versioned MigrationRunner, full column baseline validation, and strict lock semantics.
    """

    def setUp(self):
        self.mock_db = MagicMock(spec=DatabaseManager)
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_db.connection.return_value.__enter__.return_value = self.mock_conn
        self.mock_db.cursor.return_value.__enter__.return_value = self.mock_cursor
        self.mock_conn.cursor.return_value.__enter__.return_value = self.mock_cursor
        # Default mock query returns success for advisory lock
        self.mock_cursor.fetchone.return_value = {"lock_acquired": 1}

    def test_ensure_migrations_table(self):
        """Verify runner creates schema_migrations tracking table."""
        runner = MigrationRunner(db=self.mock_db)
        runner.ensure_migrations_table()

        call_args = self.mock_cursor.execute.call_args[0][0]
        self.assertIn("CREATE TABLE IF NOT EXISTS schema_migrations", call_args)

    def test_full_baseline_detected_and_recorded_safely(self):
        """
        Verify that if all 8 expected tables and columns exist, baseline (000) is recorded
        without re-executing DDL statements.
        """
        full_schema = {tbl: set(cols) for tbl, cols in MigrationRunner.EXPECTED_BASELINE_SCHEMA.items()}

        with tempfile.TemporaryDirectory() as tmpdir:
            migration_dir = Path(tmpdir)
            baseline_file = migration_dir / "000_baseline_production.sql"
            baseline_file.write_text("CREATE TABLE users (id INT);", encoding="utf-8")

            runner = MigrationRunner(db=self.mock_db, migrations_dir=migration_dir)

            with patch.object(runner, "get_existing_schema", return_value=full_schema), \
                 patch.object(runner, "get_applied_migrations", return_value=set()), \
                 patch.object(runner, "record_migration") as mock_record:

                applied = runner.run_migrations()

                mock_record.assert_called_once_with("000", "baseline_production")
                self.assertEqual(applied, ["000"])

    def test_partial_schema_missing_tables_raises_error_and_halts(self):
        """
        Verify that a database missing required tables raises MigrationError and halts.
        """
        partial_schema = {
            "users": {"id", "telegram_id"},
            "jobs": {"id", "user_id"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            migration_dir = Path(tmpdir)
            baseline_file = migration_dir / "000_baseline_production.sql"
            baseline_file.write_text("CREATE TABLE users (id INT);", encoding="utf-8")

            runner = MigrationRunner(db=self.mock_db, migrations_dir=migration_dir)

            with patch.object(runner, "get_existing_schema", return_value=partial_schema), \
                 patch.object(runner, "get_applied_migrations", return_value=set()), \
                 patch.object(runner, "record_migration") as mock_record:

                with self.assertRaises(MigrationError) as ctx:
                    runner.run_migrations()

                self.assertIn("Inconsistent/partial database schema detected", str(ctx.exception))
                self.assertIn("Missing tables", str(ctx.exception))
                mock_record.assert_not_called()

    def test_partial_schema_missing_columns_raises_error_and_halts(self):
        """
        Verify that if all 8 table names exist but required columns are missing, MigrationError is raised.
        """
        schema_with_missing_col = {tbl: set(cols) for tbl, cols in MigrationRunner.EXPECTED_BASELINE_SCHEMA.items()}
        # Intentionally remove a required column from 'jobs'
        schema_with_missing_col["jobs"] = schema_with_missing_col["jobs"] - {"source_file_id", "retry_count"}

        with tempfile.TemporaryDirectory() as tmpdir:
            migration_dir = Path(tmpdir)
            baseline_file = migration_dir / "000_baseline_production.sql"
            baseline_file.write_text("CREATE TABLE users (id INT);", encoding="utf-8")

            runner = MigrationRunner(db=self.mock_db, migrations_dir=migration_dir)

            with patch.object(runner, "get_existing_schema", return_value=schema_with_missing_col), \
                 patch.object(runner, "get_applied_migrations", return_value=set()), \
                 patch.object(runner, "record_migration") as mock_record:

                with self.assertRaises(MigrationError) as ctx:
                    runner.run_migrations()

                self.assertIn("Inconsistent/partial database schema detected", str(ctx.exception))
                self.assertIn("Table 'jobs' missing columns", str(ctx.exception))
                mock_record.assert_not_called()

    def test_fresh_database_executes_baseline_sql(self):
        """Verify that on a completely empty DB, baseline SQL statements are executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            migration_dir = Path(tmpdir)
            baseline_file = migration_dir / "000_baseline_production.sql"
            baseline_file.write_text("CREATE TABLE users (id INT);\nCREATE TABLE jobs (id INT);", encoding="utf-8")

            runner = MigrationRunner(db=self.mock_db, migrations_dir=migration_dir)

            with patch.object(runner, "get_existing_schema", return_value={}), \
                 patch.object(runner, "get_applied_migrations", return_value=set()), \
                 patch.object(runner, "record_migration") as mock_record:

                applied = runner.run_migrations()

                self.assertGreaterEqual(self.mock_cursor.execute.call_count, 2)
                mock_record.assert_called_once_with("000", "baseline_production")
                self.assertEqual(applied, ["000"])

    def test_concurrency_lock_returns_0_halts_execution(self):
        """Verify that if GET_LOCK returns 0 (lock contention/timeout), MigrationError is raised."""
        runner = MigrationRunner(db=self.mock_db)
        self.mock_cursor.fetchone.return_value = {"lock_acquired": 0}

        with self.assertRaises(MigrationError) as ctx:
            runner.run_migrations()

        self.assertIn("Could not acquire migration lock", str(ctx.exception))

    def test_concurrency_lock_returns_null_halts_execution(self):
        """Verify that if GET_LOCK returns NULL/None, MigrationError is raised."""
        runner = MigrationRunner(db=self.mock_db)
        self.mock_cursor.fetchone.return_value = {"lock_acquired": None}

        with self.assertRaises(MigrationError) as ctx:
            runner.run_migrations()

        self.assertIn("Failed to acquire migration lock", str(ctx.exception))

    def test_concurrency_lock_exception_never_fails_open(self):
        """Verify that if GET_LOCK raises a database exception, execution halts and raises MigrationError."""
        runner = MigrationRunner(db=self.mock_db)
        self.mock_cursor.execute.side_effect = Exception("DB Connection Lost during GET_LOCK")

        with self.assertRaises(MigrationError) as ctx:
            runner.run_migrations()

        self.assertIn("Database error while acquiring migration lock", str(ctx.exception))

    def test_release_lock_called_after_successful_acquisition(self):
        """Verify RELEASE_LOCK is executed in finally after successful migration run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            migration_dir = Path(tmpdir)
            runner = MigrationRunner(db=self.mock_db, migrations_dir=migration_dir)

            with patch.object(runner, "get_existing_schema", return_value={}), \
                 patch.object(runner, "get_applied_migrations", return_value=set()):
                runner.run_migrations()

            # Verify RELEASE_LOCK was executed
            calls = [str(c) for c in self.mock_cursor.execute.call_args_list]
            self.assertTrue(any("RELEASE_LOCK" in c for c in calls))


class TestDatabaseDeduplication(unittest.TestCase):
    """
    Tests for deduplicated database definitions in models.py and worker.py.
    """

    def test_add_public_api_accepts_all_parameters(self):
        """Verify add_public_api signature supports all optional arguments."""
        sig = inspect.signature(db_models.add_public_api)
        params = list(sig.parameters.keys())
        expected_params = [
            "api_key", "label", "provider", "models", "daily_limit",
            "priority", "donated_by", "selected_model", "base_url"
        ]
        self.assertEqual(params, expected_params)

    def test_get_user_by_id_exists_in_models(self):
        """Verify get_user_by_id is defined in database/models.py."""
        self.assertTrue(hasattr(db_models, "get_user_by_id"))
        self.assertTrue(callable(db_models.get_user_by_id))


if __name__ == "__main__":
    unittest.main()
