# ============================================================
#  interfaces/desktop/workers/runtime.py
# ============================================================

import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, Optional

from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.notifier import IApplicationEventPublisher
from application.services.job_execution import JobExecutionService
from application.services.settings_service import LocalSettingsService
from application.events import JobStateChangedEvent, JobFailedEvent
from application.sanitizer import sanitize_error_message
from core.entities.job import JobStatus

logger = logging.getLogger("desktop.runtime")


class DesktopJobRuntime:
    """
    In-process concurrent desktop job runtime.
    Enforces logical worker capacity bounds (1..8) dynamically from AppSettings,
    coordinates deterministic claim-to-dispatch event ordering using start gates,
    handles claim-then-dispatch races with immediate state compensation,
    isolates and sanitizes unhandled worker crashes, supports immediate wakeups
    via threading.Event, and supports deterministic shutdown with enforced timeout.
    """

    MAX_BACKING_WORKERS: int = 8

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        job_execution_service: JobExecutionService,
        settings_service: Optional[LocalSettingsService] = None,
        event_publisher: Optional[IApplicationEventPublisher] = None,
        poll_interval: float = 0.05,
        max_workers: int = 8,
    ):
        self.uow_factory = uow_factory
        self.job_execution_service = job_execution_service
        self.settings_service = settings_service
        self.event_publisher = event_publisher
        self.poll_interval = poll_interval

        self._lock = threading.Lock()
        self._active_jobs: Dict[int, Future] = {}
        self._running = False
        self._paused = False
        self._wake_event = threading.Event()

        backing_capacity = max(1, min(self.MAX_BACKING_WORKERS, max_workers))
        self._executor = ThreadPoolExecutor(max_workers=backing_capacity, thread_name_prefix="PolpoJobWorker")
        self._dispatch_thread: Optional[threading.Thread] = None

    @property
    def max_concurrent_jobs(self) -> int:
        """Dynamically retrieves logical concurrency limit from settings service."""
        if self.settings_service:
            try:
                settings = self.settings_service.get_settings()
                return max(1, min(self.MAX_BACKING_WORKERS, settings.max_concurrent_jobs))
            except Exception:
                pass
        return 2

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_worker_count(self) -> int:
        with self._lock:
            return len(self._active_jobs)

    def wake(self) -> None:
        """Signals the dispatcher loop to evaluate pending work immediately."""
        self._wake_event.set()

    def start(self) -> None:
        """Starts background polling and worker dispatching loop."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._wake_event.clear()

        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="PolpoJobDispatcher",
            daemon=True,
        )
        self._dispatch_thread.start()
        logger.info("DesktopJobRuntime started.")

    def pause(self) -> None:
        """Pauses dispatching of new pending jobs. Active workers finish current jobs."""
        with self._lock:
            self._paused = True
        self._wake_event.set()
        logger.info("DesktopJobRuntime paused.")

    def resume(self) -> None:
        """Resumes dispatching of pending jobs."""
        with self._lock:
            self._paused = False
        self._wake_event.set()
        logger.info("DesktopJobRuntime resumed.")

    def cancel_active_jobs(self) -> None:
        """Requests cooperative cancellation for all currently executing jobs."""
        with self._lock:
            job_ids = list(self._active_jobs.keys())

        for jid in job_ids:
            try:
                self.job_execution_service.cancel_job(jid)
            except Exception as e:
                logger.warning("Failed to request cancellation for active job %d: %s", jid, e)

    def shutdown(self, wait: bool = True, timeout: float = 10.0) -> None:
        """
        Stops dispatcher thread, cancels active jobs, and drains thread pool
        with enforced timeout semantics without blocking indefinitely.
        """
        logger.info("Shutting down DesktopJobRuntime...")
        with self._lock:
            self._running = False
            self._paused = True
        self._wake_event.set()

        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=1.0)

        if wait:
            deadline = time.monotonic() + max(0.05, timeout)
            while time.monotonic() < deadline:
                with self._lock:
                    if len(self._active_jobs) == 0:
                        break
                time.sleep(0.05)

            with self._lock:
                remaining_count = len(self._active_jobs)

            if remaining_count > 0:
                logger.warning(
                    "DesktopJobRuntime shutdown timed out with %d active jobs still executing; "
                    "jobs remain in-flight or will be recovered on next startup.",
                    remaining_count,
                )
                self._executor.shutdown(wait=False, cancel_futures=True)
            else:
                self._executor.shutdown(wait=True)
                logger.info("DesktopJobRuntime shutdown complete.")
        else:
            self._executor.shutdown(wait=False, cancel_futures=True)
            logger.info("DesktopJobRuntime shutdown complete (no-wait).")

    def _dispatch_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
                paused = self._paused

            if not paused:
                try:
                    self._dispatch_pending_if_capacity_available()
                except Exception as e:
                    logger.exception("Error in DesktopJobRuntime dispatch loop: %s", e)

            # Wait for wake event or poll_interval timeout
            self._wake_event.wait(timeout=self.poll_interval)
            self._wake_event.clear()

    def _dispatch_pending_if_capacity_available(self) -> None:
        limit = self.max_concurrent_jobs
        with self._lock:
            if not self._running or self._paused:
                return
            if len(self._active_jobs) >= limit:
                return

        # Atomically claim next pending job in dedicated transaction
        claimed_job = None
        try:
            with self.uow_factory.create() as uow:
                claimed_job = uow.jobs.claim_next_pending()
                uow.commit()
        except Exception as e:
            logger.error("Failed to claim next pending job: %s", e)
            return

        if not claimed_job:
            return

        job_id = claimed_job.id
        start_gate = threading.Event()

        # Atomic dispatch registration under lock
        with self._lock:
            if not self._running:
                # Runtime was stopped during claim: compensate immediately
                self._compensate_unsubmitted_claim(job_id)
                return

            try:
                future = self._executor.submit(self._execute_worker, job_id, start_gate)
                self._active_jobs[job_id] = future
                future.add_done_callback(lambda f: self._on_worker_done(job_id, f))
            except Exception as submit_err:
                logger.error("Executor submit failed for claimed job %d: %s", job_id, submit_err)
                self._compensate_unsubmitted_claim(job_id)
                return

        # Deterministic Event Ordering Protocol:
        # Publish PENDING -> PROCESSING state event on dispatcher thread BEFORE releasing worker start gate
        if self.event_publisher:
            self.event_publisher.publish(
                JobStateChangedEvent(job_id=job_id, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING)
            )

        logger.info("Runtime claimed job %d. Releasing worker start gate.", job_id)
        start_gate.set()

    def _compensate_unsubmitted_claim(self, job_id: int) -> None:
        """Reverts a claimed job back to PENDING if dispatch cannot be completed."""
        try:
            with self.uow_factory.create() as uow:
                uow.jobs.update_status(job_id, JobStatus.PENDING)
                uow.commit()
            logger.info("Compensated claimed job %d back to PENDING due to aborted dispatch.", job_id)
        except Exception as e:
            logger.error("Failed to compensate unsubmitted claim for job %d: %s", job_id, e)

    def _execute_worker(self, job_id: int, start_gate: Optional[threading.Event] = None) -> None:
        if start_gate is not None:
            # Wait until PENDING -> PROCESSING state event has been published by the dispatcher
            start_gate.wait()

        with self._lock:
            if not self._running:
                return

        try:
            self.job_execution_service.execute_claimed_job(job_id)
        except Exception as e:
            clean_error = sanitize_error_message(str(e))
            logger.error(
                "CRITICAL: Unhandled worker thread crash on job %d: %s",
                job_id,
                clean_error,
            )
            try:
                with self.uow_factory.create() as uow:
                    uow.jobs.update_status(job_id, JobStatus.FAILED, error_message=f"Unhandled worker error: {clean_error}")
                    uow.commit()

                if self.event_publisher:
                    self.event_publisher.publish(
                        JobStateChangedEvent(job_id=job_id, old_status=JobStatus.PROCESSING, new_status=JobStatus.FAILED)
                    )
                    self.event_publisher.publish(
                        JobFailedEvent(job_id=job_id, error_message=f"Unhandled worker error: {clean_error}", is_retryable=False)
                    )
            except Exception as db_err:
                logger.error("Failed to record worker crash in database for job %d: %s", job_id, db_err)

    def _on_worker_done(self, job_id: int, future: Future) -> None:
        with self._lock:
            self._active_jobs.pop(job_id, None)
        logger.debug("Worker completed for job %d.", job_id)
