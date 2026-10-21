# ============================================================
#  tests/unit/test_pipeline2_concurrency.py
# ============================================================

import threading
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

from core.entities.job import Job, JobStatus, Pipeline2Job
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory


class TestPipeline2Concurrency(unittest.TestCase):
    """
    Tests atomic claiming, race prevention, and rollback safety for Pipeline 2
    across multiple concurrent worker threads using SQLite WAL mode.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "p2_concurrency_test.db"
        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multi_worker_atomic_claiming_no_duplicates(self):
        """
        Creates 30 pending Pipeline 2 jobs and launches 6 concurrent worker threads.
        Verifies that every job is claimed exactly once with zero duplicates
        and zero lock collision errors.
        """
        num_jobs = 30
        num_workers = 6

        with self.uow_factory.create() as uow:
            for i in range(num_jobs):
                src = Job(id=None, file_name=f"p2_src_{i}.pdf", file_path=f"/p2_{i}.pdf", total_pages=1, status=JobStatus.DONE)
                saved_src = uow.jobs.save(src)
                p2 = Pipeline2Job(
                    id=None,
                    source_job_id=saved_src.id,
                    prompt_id=None,
                    status=JobStatus.PENDING,
                    input_path=f"file:///art/p2_{i}.md",
                )
                uow.pipeline2_jobs.save(p2)
            uow.commit()

        claimed_ids: List[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_workers)
        worker_errors: List[Exception] = []

        def worker_loop():
            barrier.wait()
            while True:
                try:
                    with self.uow_factory.create() as uow:
                        claimed = uow.pipeline2_jobs.claim_next_pending()
                        if claimed is None:
                            break
                        with lock:
                            claimed_ids.append(claimed.id)
                except Exception as e:
                    with lock:
                        worker_errors.append(e)
                    break

        threads = [threading.Thread(target=worker_loop) for _ in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(worker_errors, [])
        self.assertEqual(len(claimed_ids), num_jobs)
        self.assertEqual(len(set(claimed_ids)), num_jobs)

        with self.uow_factory.create() as uow:
            cur = uow._conn.cursor()
            cur.execute("SELECT id, status, claimed_at FROM pipeline2_jobs")
            rows = cur.fetchall()
            self.assertEqual(len(rows), num_jobs)
            for r in rows:
                self.assertEqual(r["status"], "processing")
                self.assertIsNotNone(r["claimed_at"])

    def test_two_workers_competing_for_single_pipeline2_job(self):
        """
        Tests two concurrent workers attempting to claim the single eligible Pipeline 2 job.
        Verifies that exactly one worker succeeds and the other receives None.
        """
        with self.uow_factory.create() as uow:
            src = Job(id=None, file_name="single_p2.pdf", file_path="/p.pdf", status=JobStatus.DONE)
            saved_src = uow.jobs.save(src)
            p2 = Pipeline2Job(id=None, source_job_id=saved_src.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            target_id = saved_p2.id
            uow.commit()

        results: List[Optional[Pipeline2Job]] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt_claim():
            barrier.wait()
            with self.uow_factory.create() as uow:
                res = uow.pipeline2_jobs.claim_next_pending()
                with lock:
                    results.append(res)

        t1 = threading.Thread(target=attempt_claim)
        t2 = threading.Thread(target=attempt_claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r is not None]
        failures = [r for r in results if r is None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(successes[0].id, target_id)
        self.assertEqual(successes[0].status, JobStatus.PROCESSING)

    def test_deserialization_failure_rolls_back_status_to_pending(self):
        """
        Verifies that when row deserialization or entity conversion fails during claim,
        the transaction rolls back before commit, ensuring the database row is NOT left
        stranded in 'processing' (remains 'pending').
        """
        with self.uow_factory.create() as uow:
            src = Job(id=None, file_name="corrupt.pdf", file_path="/p.pdf", status=JobStatus.DONE)
            saved_src = uow.jobs.save(src)
            p2 = Pipeline2Job(id=None, source_job_id=saved_src.id, status=JobStatus.PENDING, input_path="file:///in.md")
            saved_p2 = uow.pipeline2_jobs.save(p2)
            p2_id = saved_p2.id
            uow.commit()

        with self.uow_factory.create() as uow:
            original_row_to_entity = uow.pipeline2_jobs._row_to_entity

            def fail_deserialize(row):
                raise ValueError("Corrupted record payload")

            uow.pipeline2_jobs._row_to_entity = fail_deserialize

            with self.assertRaises(ValueError) as ctx:
                uow.pipeline2_jobs.claim_next_pending()

            self.assertIn("Corrupted record payload", str(ctx.exception))

        # Crucial invariant check: the row in SQLite must STILL be 'pending', NOT stranded in 'processing'!
        with self.uow_factory.create() as uow:
            cur = uow._conn.cursor()
            cur.execute("SELECT status, claimed_at FROM pipeline2_jobs WHERE id = ?", (p2_id,))
            row = cur.fetchone()
            self.assertEqual(
                row["status"],
                "pending",
                "Job status must remain 'pending' after deserialization failure during claim; must not be stranded in 'processing'!"
            )


if __name__ == "__main__":
    unittest.main()
