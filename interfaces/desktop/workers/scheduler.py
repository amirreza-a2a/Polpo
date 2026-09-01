# ============================================================
#  interfaces/desktop/workers/scheduler.py
# ============================================================

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.notifier import IApplicationEventPublisher
from application.services.settings_service import LocalSettingsService
from interfaces.desktop.workers.runtime import DesktopJobRuntime

logger = logging.getLogger("desktop.scheduler")


class DesktopJobScheduler:
    """
    In-process desktop job scheduler.
    Monitors scheduled jobs in SQLite, evaluates due times against UTC clock,
    and wakes the DesktopJobRuntime when pending work is ready for execution.

    Responsibilities:
    - Scheduling awareness & periodic heartbeat (default 5.0s)
    - UTC due-time evaluation
    - Waking the runtime dispatcher (latency optimization)

    Invariants:
    - NEVER claims jobs
    - NEVER executes jobs or changes status to PROCESSING
    - NEVER manages worker concurrency
    - Database operations are strictly lightweight index queries on (status='pending', scheduled_at <= now)
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        runtime: DesktopJobRuntime,
        settings_service: Optional[LocalSettingsService] = None,
        event_publisher: Optional[IApplicationEventPublisher] = None,
        tick_interval: float = 5.0,
    ):
        self.uow_factory = uow_factory
        self.runtime = runtime
        self.settings_service = settings_service
        self.event_publisher = event_publisher
        self.tick_interval = tick_interval

        self._lock = threading.Lock()
        self._running = False
        self._paused = False
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and not self._paused

    def start(self) -> None:
        """Starts the background scheduler ticker thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._wake_event.clear()

        self._thread = threading.Thread(
            target=self._tick_loop,
            name="PolpoJobScheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("DesktopJobScheduler started (tick_interval=%.1fs).", self.tick_interval)

    def pause(self) -> None:
        """Pauses due-job evaluation and runtime wakeups."""
        with self._lock:
            self._paused = True
        self._wake_event.set()
        logger.info("DesktopJobScheduler paused.")

    def resume(self) -> None:
        """Resumes due-job evaluation."""
        with self._lock:
            self._paused = False
        self._wake_event.set()
        logger.info("DesktopJobScheduler resumed.")

    def trigger_now(self) -> None:
        """Immediately evaluates due jobs without waiting for the next tick."""
        self._wake_event.set()

    def shutdown(self) -> None:
        """Stops the scheduler thread cleanly."""
        logger.info("Shutting down DesktopJobScheduler...")
        with self._lock:
            self._running = False
            self._paused = True
        self._wake_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("DesktopJobScheduler shutdown complete.")

    def _tick_loop(self) -> None:
        while True:
            # Wait for wake event or tick_interval timeout
            self._wake_event.wait(timeout=self.tick_interval)
            self._wake_event.clear()

            with self._lock:
                if not self._running:
                    break
                paused = self._paused

            if not paused:
                try:
                    self._evaluate_due_jobs()
                except Exception as e:
                    logger.exception("Error during scheduler due-job evaluation: %s", e)

    def _evaluate_due_jobs(self) -> None:
        """
        Queries for due pending jobs. If any are found, signals the runtime to evaluate claims.
        """
        now_utc = datetime.now(timezone.utc)
        due_count = 0
        try:
            with self.uow_factory.create() as uow:
                if hasattr(uow.jobs, "get_due_jobs"):
                    due_jobs = uow.jobs.get_due_jobs(as_of=now_utc)
                    due_count = len(due_jobs)
        except Exception as e:
            logger.error("Failed to query due jobs: %s", e)
            return

        if due_count > 0:
            logger.debug("Scheduler detected %d due pending job(s); waking runtime.", due_count)
            self.runtime.wake()
