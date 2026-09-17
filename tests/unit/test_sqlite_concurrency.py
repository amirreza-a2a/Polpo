# ============================================================
#  tests/unit/test_sqlite_concurrency.py
# ============================================================

import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import List, Optional

from core.entities.job import Job, JobStatus
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory


class TestSQLiteConcurrency(unittest.TestCase):
    """
    Tests atomic job claiming, concurrency safety, and race prevention
    across multiple concurrent worker threads using SQLite WAL mode.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "concurrency_test.db"
        self.db_manager = SQLiteDatabaseManager(self.db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multi_worker_atomic_claiming_no_duplicates(self):
        """
        Creates 50 pending jobs and launches 10 concurrent worker threads.
        Verifies that every job is claimed exactly once with zero duplicates
        and zero lock collision errors.
        """
        num_jobs = 50
        num_workers = 10

        # Enqueue 50 pending jobs
        with self.uow_factory.create() as uow:
            for i in range(num_jobs):
                job = Job(
                    id=None,
                    file_name=f"job_{i}.pdf",
                    file_path=f"/tmp/job_{i}.pdf",
                    total_pages=5,
                    status=JobStatus.PENDING,
                )
                uow.jobs.save(job)
            uow.commit()

        claimed_ids: List[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_workers)
        worker_errors: List[Exception] = []

        def worker_loop():
            # Synchronize start of all worker threads
            barrier.wait()
            while True:
                try:
                    # Each claim operation creates its own thread-isolated UoW
                    with self.uow_factory.create() as uow:
                        claimed = uow.jobs.claim_next_pending()
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

        # Assert no unexpected worker exceptions occurred
        self.assertEqual(worker_errors, [])

        # Assert exactly 50 claims were made
        self.assertEqual(len(claimed_ids), num_jobs)

        # Assert zero duplicate claims (all IDs unique)
        self.assertEqual(len(set(claimed_ids)), num_jobs)

        # Verify in database that all 50 jobs are in processing state and have claimed_at set
        with self.uow_factory.create() as uow:
            jobs = uow.jobs.list(limit=100)
            self.assertEqual(len(jobs), num_jobs)
            for j in jobs:
                self.assertEqual(j.status, JobStatus.PROCESSING)
                self.assertIsNotNone(j.claimed_at)

    def test_claim_job_direct_atomicity(self):
        """
        Tests two concurrent workers attempting to claim the exact same job_id.
        Verifies that exactly one worker succeeds and the other receives None.
        """
        with self.uow_factory.create() as uow:
            job = Job(
                id=None,
                file_name="race_target.pdf",
                file_path="/tmp/race.pdf",
                total_pages=1,
                status=JobStatus.PENDING,
            )
            saved = uow.jobs.save(job)
            target_id = saved.id
            uow.commit()

        results: List[Optional[Job]] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt_claim():
            barrier.wait()
            with self.uow_factory.create() as uow:
                res = uow.jobs.claim_job(target_id)
                with lock:
                    results.append(res)

        t1 = threading.Thread(target=attempt_claim)
        t2 = threading.Thread(target=attempt_claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one worker got the job, one got None
        successes = [r for r in results if r is not None]
        failures = [r for r in results if r is None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(successes[0].id, target_id)
        self.assertEqual(successes[0].status, JobStatus.PROCESSING)

    def test_concurrent_readers_and_writers_in_wal(self):
        """
        Tests concurrent reader queries executing while workers claim and update jobs.
        Verifies zero locking deadlocks or query failures in WAL mode.
        """
        num_jobs = 20
        with self.uow_factory.create() as uow:
            for i in range(num_jobs):
                uow.jobs.save(Job(id=None, file_name=f"rw_{i}.pdf", file_path="/p.pdf", total_pages=1))
            uow.commit()

        stop_event = threading.Event()
        read_counts: List[int] = []
        reader_errors: List[Exception] = []

        def reader_loop():
            while not stop_event.is_set():
                try:
                    with self.uow_factory.create() as uow:
                        jobs = uow.jobs.list(limit=50)
                        read_counts.append(len(jobs))
                except Exception as e:
                    reader_errors.append(e)

        def writer_loop():
            while True:
                with self.uow_factory.create() as uow:
                    claimed = uow.jobs.claim_next_pending()
                    if not claimed:
                        break
                    uow.jobs.update_progress(claimed.id, processed_pages=1, switch_log=[])
                    uow.jobs.update_status(claimed.id, JobStatus.DONE)
                    uow.commit()

        reader_thread = threading.Thread(target=reader_loop)
        writer_threads = [threading.Thread(target=writer_loop) for _ in range(4)]

        reader_thread.start()
        for wt in writer_threads:
            wt.start()
        for wt in writer_threads:
            wt.join()

        stop_event.set()
        reader_thread.join()

        self.assertEqual(reader_errors, [])
        self.assertGreater(len(read_counts), 0)

    def test_uow_begin_immediate_lifecycle(self):
        """Verifies begin_immediate acquires an immediate transaction and enforces single-transaction rules."""
        with self.uow_factory.create() as uow:
            self.assertFalse(uow._conn.in_transaction)
            uow.begin_immediate()
            self.assertTrue(uow._conn.in_transaction)

            # Re-calling begin_immediate while active transaction exists raises RuntimeError
            with self.assertRaises(RuntimeError):
                uow.begin_immediate()

            uow.commit()
            self.assertFalse(uow._conn.in_transaction)

    def test_concurrent_canonical_version_reservation_serialized(self):
        """
        Launches 10 concurrent worker threads attempting to reserve consecutive canonical
        version watermarks on the same job using uow.begin_immediate().
        Verifies all allocations are strictly serialized with zero duplicates and zero collisions.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.save(
                Job(
                    id=None,
                    file_name="test.pdf",
                    file_path="/tmp/test.pdf",
                    total_pages=1,
                    status=JobStatus.DONE,
                    output_artifact_version_watermark=0,
                )
            )
            job_id = job.id
            uow.commit()

        num_threads = 10
        barrier = threading.Barrier(num_threads)
        allocated_versions: List[int] = []
        alloc_lock = threading.Lock()
        thread_errors: List[Exception] = []

        def reservation_worker():
            barrier.wait()
            try:
                with self.uow_factory.create() as uow:
                    uow.begin_immediate()
                    j = uow.jobs.get_by_id(job_id)
                    next_ver = j.output_artifact_version_watermark + 1
                    j.output_artifact_version_watermark = next_ver
                    uow.jobs.save(j)
                    uow.commit()
                    with alloc_lock:
                        allocated_versions.append(next_ver)
            except Exception as e:
                with alloc_lock:
                    thread_errors.append(e)

        threads = [threading.Thread(target=reservation_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(thread_errors, [])
        self.assertEqual(len(allocated_versions), num_threads)
        # Verify all 10 allocated versions are distinct (1 through 10)
        self.assertEqual(sorted(allocated_versions), list(range(1, num_threads + 1)))

        with self.uow_factory.create() as uow:
            final_job = uow.jobs.get_by_id(job_id)
            self.assertEqual(final_job.output_artifact_version_watermark, num_threads)


if __name__ == "__main__":
    unittest.main()
