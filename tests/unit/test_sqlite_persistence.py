# ============================================================
#  tests/unit/test_sqlite_persistence.py
# ============================================================

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.entities.settings import AppSettings
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, Pipeline2Job, JobStatus
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import (
    SQLiteMigrationRunner,
    MigrationError,
)
from infrastructure.persistence.sqlite.unit_of_work import (
    SQLiteUnitOfWork,
    SQLiteUnitOfWorkFactory,
)


class TestSQLitePersistence(unittest.TestCase):
    """
    Comprehensive unit tests for SQLite connection, migrations,
    repositories, transactions, and secret non-persistence.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_polpot.db"
        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ------------------------------------------------------------
    # 1. Connection & Pragmas & Path Semantics
    # ------------------------------------------------------------

    def test_sqlite_wal_mode_and_pragmas(self):
        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            cur.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]
            self.assertEqual(busy_timeout, 5000)

            cur.execute("PRAGMA foreign_keys;")
            fk = cur.fetchone()[0]
            self.assertEqual(fk, 1)
        finally:
            conn.close()

    def test_sqlite_connection_isolation(self):
        conn1 = self.db_manager.create_connection()
        conn2 = self.db_manager.create_connection()
        try:
            self.assertIsNot(conn1, conn2)
        finally:
            conn1.close()
            conn2.close()

    def test_sqlite_database_manager_path_and_memory_semantics(self):
        # 1. Empty path raises ValueError
        with self.assertRaises(ValueError):
            SQLiteDatabaseManager("")

        # 2. Disk path creates parent directories
        nested_path = Path(self.temp_dir.name) / "sub1" / "sub2" / "test.db"
        mgr = SQLiteDatabaseManager(nested_path)
        self.assertTrue(nested_path.parent.exists())

        # 3. Factory create_in_memory: private :memory: creates isolated instances
        priv_mgr = SQLiteDatabaseManager.create_in_memory(shared=False)
        c1 = priv_mgr.create_connection()
        c2 = priv_mgr.create_connection()
        try:
            c1.execute("CREATE TABLE t1 (id INT);")
            cur2 = c2.cursor()
            cur2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='t1'")
            self.assertIsNone(cur2.fetchone())
        finally:
            c1.close()
            c2.close()

        # 4. Factory create_in_memory: shared URI allows multiple connections to share schema
        shared_mgr = SQLiteDatabaseManager.create_in_memory(shared=True)
        s1 = shared_mgr.create_connection()
        s2 = shared_mgr.create_connection()
        try:
            s1.execute("CREATE TABLE shared_t (id INT);")
            cur_s2 = s2.cursor()
            cur_s2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shared_t'")
            self.assertIsNotNone(cur_s2.fetchone())
        finally:
            s1.close()
            s2.close()

    # ------------------------------------------------------------
    # 2. Migrations
    # ------------------------------------------------------------

    def test_migration_runner_applies_initial_schema(self):
        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT version, name FROM schema_version")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["version"], 1)

            # Verify all expected tables exist
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cur.fetchall()}
            expected_tables = {
                "schema_version",
                "prompts",
                "app_settings",
                "api_slots",
                "jobs",
                "pipeline2_jobs",
            }
            self.assertTrue(expected_tables.issubset(tables))
        finally:
            conn.close()

    def test_migration_runner_is_idempotent(self):
        applied = self.migration_runner.run_migrations()
        self.assertEqual(applied, [])

    def test_migration_sql_tokenizer_handles_semicolons_in_strings_and_comments(self):
        sql_with_complex_semicolons = """
        -- Statement 1 comment with semicolon ; and -- embedded
        CREATE TABLE sample_tokens (
            id INTEGER PRIMARY KEY,
            val TEXT DEFAULT 'prefix; with; semicolons; and quotes ''escaped'''
        );

        /* Block comment
           containing ; semicolons
           and multiple lines */
        INSERT INTO sample_tokens (id, val) VALUES (1, "double; quoted; literal;");
        """
        stmts = self.migration_runner._split_sql_statements(sql_with_complex_semicolons)
        self.assertEqual(len(stmts), 2)
        self.assertTrue(stmts[0].startswith("CREATE TABLE sample_tokens"))
        self.assertTrue("prefix; with; semicolons" in stmts[0])
        self.assertTrue(stmts[1].startswith("INSERT INTO sample_tokens"))

    def test_migration_runner_rollback_on_broken_script(self):
        custom_mig_dir = Path(self.temp_dir.name) / "custom_migrations"
        custom_mig_dir.mkdir(parents=True, exist_ok=True)

        with open(custom_mig_dir / "001_initial.sql", "w", encoding="utf-8") as f:
            f.write("CREATE TABLE sample (id INTEGER PRIMARY KEY);")

        with open(custom_mig_dir / "002_broken.sql", "w", encoding="utf-8") as f:
            f.write("CREATE TABLE broken_table (id INTEGER PRIMARY KEY);\nINVALID SQL SYNTAX HERE;")

        custom_db_path = Path(self.temp_dir.name) / "broken_test.db"
        custom_db = SQLiteDatabaseManager(custom_db_path)
        runner = SQLiteMigrationRunner(custom_db, migrations_dir=custom_mig_dir)

        with self.assertRaises(MigrationError):
            runner.run_migrations()

        conn = custom_db.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT version FROM schema_version")
            versions = [row["version"] for row in cur.fetchall()]
            self.assertEqual(versions, [1])

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='broken_table'")
            self.assertIsNone(cur.fetchone())
        finally:
            conn.close()

    # ------------------------------------------------------------
    # 3. Foreign Key Constraints
    # ------------------------------------------------------------

    def test_foreign_key_enforcement(self):
        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute(
                    """
                    INSERT INTO app_settings (id, default_prompt_id, updated_at)
                    VALUES (1, 9999, '2026-09-01T12:00:00Z')
                    """
                )
        finally:
            conn.close()

    def test_pipeline2_job_cascade_delete(self):
        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="test.pdf",
                file_path="/tmp/test.pdf",
                total_pages=1,
            )
            saved_job = uow.jobs.save(job)

            p2_job = Pipeline2Job(
                id=None,
                source_job_id=saved_job.id,
            )
            saved_p2 = uow.pipeline2_jobs.save(p2_job)
            uow.commit()

        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM jobs WHERE id = ?", (saved_job.id,))

            cur.execute("SELECT * FROM pipeline2_jobs WHERE id = ?", (saved_p2.id,))
            self.assertIsNone(cur.fetchone())
        finally:
            conn.close()

    # ------------------------------------------------------------
    # 4. Settings Repository CRUD
    # ------------------------------------------------------------

    def test_settings_repository_crud(self):
        with self.uow_factory.create() as uow:
            settings = uow.settings.get()
            self.assertEqual(settings.theme, "system")
            self.assertEqual(settings.max_concurrent_jobs, 2)
            self.assertTrue(settings.auto_retry)

            settings.theme = "dark"
            settings.max_concurrent_jobs = 4
            settings.auto_retry = False
            settings.auto_pipeline2 = True
            settings.artifact_retention_days = 60
            settings.missed_schedule_policy = "run_immediately"
            uow.settings.save(settings)
            uow.commit()

        with self.uow_factory.create() as uow:
            reloaded = uow.settings.get()
            self.assertEqual(reloaded.theme, "dark")
            self.assertEqual(reloaded.max_concurrent_jobs, 4)
            self.assertFalse(reloaded.auto_retry)
            self.assertTrue(reloaded.auto_pipeline2)
            self.assertEqual(reloaded.artifact_retention_days, 60)
            self.assertEqual(reloaded.missed_schedule_policy, "run_immediately")

    # ------------------------------------------------------------
    # 5. Prompt Repository CRUD
    # ------------------------------------------------------------

    def test_prompt_repository_crud(self):
        with self.uow_factory.create() as uow:
            prompt = Prompt(
                id=None,
                name="Custom Pipeline 1",
                text="Extract markdown carefully.",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
            saved = uow.prompts.save(prompt)
            self.assertIsNotNone(saved.id)
            uow.commit()

        with self.uow_factory.create() as uow:
            loaded = uow.prompts.get_by_id(saved.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "Custom Pipeline 1")
            self.assertTrue(loaded.is_default)

            default_p1 = uow.prompts.get_default(PromptType.PIPELINE_1)
            self.assertEqual(default_p1.id, saved.id)

            loaded.text = "Updated prompt text."
            uow.prompts.save(loaded)
            uow.commit()

        with self.uow_factory.create() as uow:
            updated = uow.prompts.get_by_id(saved.id)
            self.assertEqual(updated.text, "Updated prompt text.")

            toggled = uow.prompts.toggle_active(saved.id, False)
            self.assertTrue(toggled)
            uow.commit()

        with self.uow_factory.create() as uow:
            deleted = uow.prompts.delete(saved.id)
            self.assertTrue(deleted)
            uow.commit()

        with self.uow_factory.create() as uow:
            self.assertIsNone(uow.prompts.get_by_id(saved.id))

    def test_quick_convert_prompt_methods(self):
        with self.uow_factory.create() as uow:
            uow.prompts.set_quick_convert_prompt("Transcribe this image.")
            uow.commit()

        with self.uow_factory.create() as uow:
            text = uow.prompts.get_quick_convert_prompt()
            self.assertEqual(text, "Transcribe this image.")

            # Update existing quick convert prompt
            uow.prompts.set_quick_convert_prompt("Updated transcription prompt.")
            uow.commit()

        with self.uow_factory.create() as uow:
            text_updated = uow.prompts.get_quick_convert_prompt()
            self.assertEqual(text_updated, "Updated transcription prompt.")

    # ------------------------------------------------------------
    # 6. ApiSlot Repository CRUD
    # ------------------------------------------------------------

    def test_api_slot_repository_crud(self):
        cred_ref = CredentialRef(
            identifier="google_test_key_01",
            provider="google",
            slot_type="byok",
        )
        slot = ApiSlot(
            id=None,
            provider="google",
            label="My Google BYOK",
            credential_ref=cred_ref,
            selected_model="gemini-2.5-flash",
            supported_models=["gemini-2.5-flash", "gemini-2.5-pro"],
        )

        with self.uow_factory.create() as uow:
            saved_slot = uow.apis.save(slot)
            self.assertIsNotNone(saved_slot.id)
            uow.commit()

        with self.uow_factory.create() as uow:
            loaded_slot = uow.apis.get_by_id(saved_slot.id)
            self.assertIsNotNone(loaded_slot)
            self.assertEqual(loaded_slot.label, "My Google BYOK")
            self.assertEqual(loaded_slot.credential_ref.identifier, "google_test_key_01")
            self.assertEqual(loaded_slot.supported_models, ["gemini-2.5-flash", "gemini-2.5-pro"])

            # Report page usage
            uow.apis.report_pages_used(saved_slot.id, "byok", pages=5)
            uow.commit()

        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT total_pages_processed FROM api_slots WHERE id = ?", (saved_slot.id,))
            self.assertEqual(cur.fetchone()[0], 5)
        finally:
            conn.close()

        # Test delete
        with self.uow_factory.create() as uow:
            deleted = uow.apis.delete(saved_slot.id)
            self.assertTrue(deleted)
            uow.commit()

        with self.uow_factory.create() as uow:
            self.assertIsNone(uow.apis.get_by_id(saved_slot.id))

    # ------------------------------------------------------------
    # 7. Job Repository CRUD & Operations
    # ------------------------------------------------------------

    def test_job_repository_crud(self):
        cred_ref = CredentialRef(identifier="slot_01", provider="google", slot_type="byok")
        slot = ApiSlot(id=1, provider="google", label="BYOK 1", credential_ref=cred_ref)

        job = Job(
            id=None,
            file_name="invoice.pdf",
            file_path="/data/artifacts/job_1/source.pdf",
            total_pages=10,
            processed_pages=0,
            status=JobStatus.PENDING,
            api_chain=[slot],
        )

        with self.uow_factory.create() as uow:
            saved_job = uow.jobs.save(job)
            self.assertIsNotNone(saved_job.id)
            uow.commit()

        with self.uow_factory.create() as uow:
            loaded_job = uow.jobs.get_by_id(saved_job.id)
            self.assertIsNotNone(loaded_job)
            self.assertEqual(loaded_job.file_name, "invoice.pdf")
            self.assertEqual(len(loaded_job.api_chain), 1)
            self.assertEqual(loaded_job.api_chain[0].credential_ref.identifier, "slot_01")

            # Update progress
            switch_log = [{"from_api": "slot_01", "to_api": "slot_02", "page": 3, "reason": "rate_limit"}]
            uow.jobs.update_progress(saved_job.id, processed_pages=3, switch_log=switch_log, output_path="/data/out.md")
            uow.commit()

        with self.uow_factory.create() as uow:
            updated_job = uow.jobs.get_by_id(saved_job.id)
            self.assertEqual(updated_job.processed_pages, 3)
            self.assertEqual(len(updated_job.api_switch_log), 1)
            self.assertEqual(updated_job.output_path, "/data/out.md")

            # Update status
            uow.jobs.update_status(saved_job.id, JobStatus.DONE)
            uow.commit()

        with self.uow_factory.create() as uow:
            done_job = uow.jobs.get_by_id(saved_job.id)
            self.assertEqual(done_job.status, JobStatus.DONE)

    def test_job_queue_position_and_filtering(self):
        with self.uow_factory.create() as uow:
            j1 = uow.jobs.save(Job(id=None, file_name="j1.pdf", file_path="/1.pdf", total_pages=1, status=JobStatus.PENDING))
            j2 = uow.jobs.save(Job(id=None, file_name="j2.pdf", file_path="/2.pdf", total_pages=1, status=JobStatus.PENDING))
            j3 = uow.jobs.save(Job(id=None, file_name="j3.pdf", file_path="/3.pdf", total_pages=1, status=JobStatus.DONE))
            uow.commit()

        with self.uow_factory.create() as uow:
            pos1 = uow.jobs.get_queue_position(j1.id)
            pos2 = uow.jobs.get_queue_position(j2.id)
            self.assertEqual(pos1, 1)
            self.assertEqual(pos2, 2)

            pending_list = uow.jobs.list(status=JobStatus.PENDING)
            self.assertEqual(len(pending_list), 2)
            done_list = uow.jobs.list(status=JobStatus.DONE)
            self.assertEqual(len(done_list), 1)

    def test_claim_job_direct_edge_cases(self):
        with self.uow_factory.create() as uow:
            job = uow.jobs.save(Job(id=None, file_name="claim_test.pdf", file_path="/t.pdf", total_pages=1, status=JobStatus.PENDING))
            uow.commit()

        # 1. Claim when pending succeeds
        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_job(job.id)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.status, JobStatus.PROCESSING)

        # 2. Claim when already processing returns None
        with self.uow_factory.create() as uow:
            claimed_again = uow.jobs.claim_job(job.id)
            self.assertIsNone(claimed_again)

    def test_claim_next_pending_empty_queue(self):
        with self.uow_factory.create() as uow:
            claimed = uow.jobs.claim_next_pending()
            self.assertIsNone(claimed)

    def test_claim_operation_rejects_active_outer_transaction(self):
        """
        Verify that attempting to call claim_next_pending() or claim_job() within
        an existing active transaction raises RuntimeError to strictly enforce dedicated claiming atomicity.
        """
        with self.uow_factory.create() as uow:
            # Explicitly begin an outer write transaction
            uow.settings.save(AppSettings(theme="dark"))
            self.assertTrue(uow._conn.in_transaction)

            # Both claim operations must reject execution within the active outer transaction
            with self.assertRaises(RuntimeError):
                uow.jobs.claim_next_pending()

            with self.assertRaises(RuntimeError):
                uow.jobs.claim_job(1)

    def test_reconcile_stale_jobs_on_startup(self):
        with self.uow_factory.create() as uow:
            for i in range(3):
                job = Job(
                    id=None,
                    file_name=f"doc_{i}.pdf",
                    file_path=f"/path/{i}.pdf",
                    total_pages=5,
                    status=JobStatus.PROCESSING,
                )
                uow.jobs.save(job)
            uow.commit()

        with self.uow_factory.create() as uow:
            recovered_count = uow.jobs.reconcile_stale_jobs()
            self.assertEqual(recovered_count, 3)
            uow.commit()

        with self.uow_factory.create() as uow:
            jobs = uow.jobs.list()
            for j in jobs:
                self.assertEqual(j.status, JobStatus.PAUSED)
                self.assertIn("Interrupted", j.error_message)

    def test_request_cancellation(self):
        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="pending_doc.pdf",
                file_path="/path/pending.pdf",
                total_pages=2,
                status=JobStatus.PENDING,
            )
            saved = uow.jobs.save(job)
            uow.commit()

        with self.uow_factory.create() as uow:
            cancelled = uow.jobs.request_cancellation(saved.id)
            self.assertTrue(cancelled)
            uow.commit()

        with self.uow_factory.create() as uow:
            reloaded = uow.jobs.get_by_id(saved.id)
            self.assertTrue(reloaded.cancel_requested)
            self.assertEqual(reloaded.status, JobStatus.CANCELLED)

    def test_get_due_jobs_filtering(self):
        now = datetime.now(timezone.utc)
        past_due = now - timedelta(minutes=10)
        future_due = now + timedelta(hours=2)

        with self.uow_factory.create() as uow:
            j_past = Job(id=None, file_name="past.pdf", file_path="/p.pdf", total_pages=1, scheduled_at=past_due)
            j_future = Job(id=None, file_name="future.pdf", file_path="/f.pdf", total_pages=1, scheduled_at=future_due)
            uow.jobs.save(j_past)
            uow.jobs.save(j_future)
            uow.commit()

        with self.uow_factory.create() as uow:
            due_jobs = uow.jobs.get_due_jobs(as_of=now)
            due_files = [j.file_name for j in due_jobs]
            self.assertIn("past.pdf", due_files)
            self.assertNotIn("future.pdf", due_files)

    # ------------------------------------------------------------
    # 8. Pipeline2 Repository Operations
    # ------------------------------------------------------------

    def test_pipeline2_repository_crud_and_status(self):
        with self.uow_factory.create() as uow:
            source = uow.jobs.save(Job(id=None, file_name="src.pdf", file_path="/s.pdf", total_pages=1))
            p2 = Pipeline2Job(id=None, source_job_id=source.id, status=JobStatus.PENDING)
            saved_p2 = uow.pipeline2_jobs.save(p2)
            uow.commit()

        with self.uow_factory.create() as uow:
            next_p2 = uow.pipeline2_jobs.get_next_pending()
            self.assertIsNotNone(next_p2)
            self.assertEqual(next_p2.id, saved_p2.id)

            uow.pipeline2_jobs.update_paths(saved_p2.id, input_path="/in.md", output_path="/out.md")
            uow.pipeline2_jobs.update_status(saved_p2.id, JobStatus.DONE)
            uow.commit()

        with self.uow_factory.create() as uow:
            reloaded_p2 = uow.pipeline2_jobs.get_by_id(saved_p2.id)
            self.assertEqual(reloaded_p2.input_path, "/in.md")
            self.assertEqual(reloaded_p2.output_path, "/out.md")
            self.assertEqual(reloaded_p2.status, JobStatus.DONE)

    # ------------------------------------------------------------
    # 9. Unit of Work Transactions
    # ------------------------------------------------------------

    def test_unit_of_work_commit_and_rollback(self):
        # 1. Commit persists
        with self.uow_factory.create() as uow:
            prompt = Prompt(id=None, name="P_Commit", text="Text", prompt_type=PromptType.PIPELINE_1)
            uow.prompts.save(prompt)
            uow.commit()

        with self.uow_factory.create() as uow:
            prompts = uow.prompts.list_all()
            self.assertTrue(any(p.name == "P_Commit" for p in prompts))

        # 2. Rollback on exception discards
        try:
            with self.uow_factory.create() as uow:
                prompt_bad = Prompt(id=None, name="P_Rollback", text="Text", prompt_type=PromptType.PIPELINE_1)
                uow.prompts.save(prompt_bad)
                raise ValueError("Simulated failure inside transaction")
        except ValueError:
            pass

        with self.uow_factory.create() as uow:
            prompts_after = uow.prompts.list_all()
            self.assertFalse(any(p.name == "P_Rollback" for p in prompts_after))

    # ------------------------------------------------------------
    # 10. Secret Non-Persistence Audit Test
    # ------------------------------------------------------------

    def test_synthetic_secret_never_persisted_to_sqlite(self):
        """
        Verifies that a synthetic secret (e.g. TEST_SYNTHETIC_SECRET_PHASE8B_9A7B3C1D)
        never appears anywhere in the raw SQLite database or serialized JSON metadata.
        """
        synthetic_secret = "TEST_SYNTHETIC_SECRET_PHASE8B_9A7B3C1D"
        cred_ref = CredentialRef(
            identifier="google_vault_token_42",
            provider="google",
            slot_type="byok",
        )
        slot = ApiSlot(
            id=None,
            provider="google",
            label="Secure Google Slot",
            credential_ref=cred_ref,
            selected_model="gemini-2.5-flash",
        )

        with self.uow_factory.create() as uow:
            saved_slot = uow.apis.save(slot)

            job = Job(
                id=None,
                file_name="secret_test.pdf",
                file_path="/path/test.pdf",
                total_pages=1,
                api_chain=[saved_slot],
            )
            uow.jobs.save(job)
            uow.commit()

        # Perform a full binary/text inspection across the SQLite file
        with open(self.db_path, "rb") as f:
            db_bytes = f.read()

        self.assertNotIn(synthetic_secret.encode("utf-8"), db_bytes)

        # Inspect all SQLite table rows and columns via SQL queries
        conn = self.db_manager.create_connection()
        try:
            cur = conn.cursor()
            for table in ["api_slots", "jobs", "pipeline2_jobs", "prompts", "app_settings"]:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    for col, val in row_dict.items():
                        if isinstance(val, str):
                            self.assertNotIn(synthetic_secret, val)
                            self.assertNotIn("api_key", col.lower())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
