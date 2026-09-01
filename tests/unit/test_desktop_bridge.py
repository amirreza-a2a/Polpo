# ============================================================
#  tests/unit/test_desktop_bridge.py
# ============================================================

import unittest
import threading
from datetime import datetime, timezone
from interfaces.desktop.qt_compat import QCoreApplication, QThread
from interfaces.desktop.bridge import QtSignalEventBridge
from infrastructure.events.event_bus import InMemoryEventBus
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
    MissedScheduleDetectedEvent,
    ScheduleUpdatedEvent,
)
from core.entities.job import JobStatus


class TestQtSignalEventBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure QCoreApplication exists for Qt Signal-Slot processing
        cls.app = QCoreApplication.instance()
        if cls.app is None:
            cls.app = QCoreApplication([])

    def setUp(self):
        self.event_bus = InMemoryEventBus()
        self.bridge = QtSignalEventBridge(self.event_bus)

    def tearDown(self):
        self.bridge.detach()

    def test_bridge_attachment_and_detach(self):
        self.assertTrue(self.bridge.is_attached)
        self.bridge.detach()
        self.assertFalse(self.bridge.is_attached)

        # Subsequent detach is a safe no-op
        self.bridge.detach()
        self.assertFalse(self.bridge.is_attached)

    def test_job_progress_event_translation(self):
        received = []
        self.bridge.job_progress_received.connect(
            lambda jid, proc, tot, pct: received.append((jid, proc, tot, pct))
        )

        self.event_bus.publish(JobProgressEvent(job_id=42, processed_pages=2, total_pages=10, percent=20.0))
        self.app.processEvents()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], (42, 2, 10, 20.0))

    def test_api_switch_event_translation(self):
        received = []
        self.bridge.api_switch_received.connect(
            lambda jid, old, new, reason, page: received.append((jid, old, new, reason, page))
        )

        self.event_bus.publish(
            ApiSwitchEvent(job_id=42, old_label="Key1", new_label="Key2", reason="Rate limit", page=3)
        )
        self.app.processEvents()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], (42, "Key1", "Key2", "Rate limit", 3))

    def test_job_completed_failed_cancelled_translations(self):
        completed = []
        failed = []
        cancelled = []

        self.bridge.job_completed_received.connect(lambda jid, uri: completed.append((jid, uri)))
        self.bridge.job_failed_received.connect(lambda jid, err, ret: failed.append((jid, err, ret)))
        self.bridge.job_cancelled_received.connect(lambda jid: cancelled.append(jid))

        self.event_bus.publish(JobCompletedEvent(job_id=1, output_artifact_uri="file:///out.md"))
        self.event_bus.publish(JobFailedEvent(job_id=2, error_message="Fatal AI error", is_retryable=False))
        self.event_bus.publish(JobCancelledEvent(job_id=3))
        self.app.processEvents()

        self.assertEqual(completed, [(1, "file:///out.md")])
        self.assertEqual(failed, [(2, "Fatal AI error", False)])
        self.assertEqual(cancelled, [3])

    def test_job_state_changed_and_schedule_translations(self):
        state_changed = []
        missed = []
        sched_updated = []

        self.bridge.job_state_changed_received.connect(lambda jid, o, n: state_changed.append((jid, o, n)))
        self.bridge.missed_schedule_received.connect(lambda jid, fn, s, p: missed.append((jid, fn, s, p)))
        self.bridge.schedule_updated_received.connect(lambda jid, s: sched_updated.append((jid, s)))

        now_dt = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        self.event_bus.publish(JobStateChangedEvent(job_id=5, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING))
        self.event_bus.publish(MissedScheduleDetectedEvent(job_id=6, file_name="doc.pdf", scheduled_at=now_dt, policy="prompt"))
        self.event_bus.publish(ScheduleUpdatedEvent(job_id=7, scheduled_at=now_dt))
        self.app.processEvents()

        self.assertEqual(state_changed, [(5, "pending", "processing")])
        self.assertEqual(missed, [(6, "doc.pdf", now_dt.isoformat(), "prompt")])
        self.assertEqual(sched_updated, [(7, now_dt.isoformat())])

    def test_worker_thread_event_emission_thread_affinity(self):
        """Worker thread publishes to EventBus; bridge emits signal; receiver slot runs on Main thread."""
        receiver_thread_id = []

        def on_progress(jid, proc, tot, pct):
            receiver_thread_id.append(threading.get_ident())

        self.bridge.job_progress_received.connect(on_progress)

        worker_thread_id = []

        def worker_task():
            worker_thread_id.append(threading.get_ident())
            self.event_bus.publish(JobProgressEvent(job_id=99, processed_pages=1, total_pages=5, percent=20.0))

        t = threading.Thread(target=worker_task)
        t.start()
        t.join()

        self.app.processEvents()

        self.assertEqual(len(receiver_thread_id), 1)
        self.assertNotEqual(worker_thread_id[0], threading.get_ident())
        self.assertEqual(receiver_thread_id[0], threading.get_ident())


if __name__ == "__main__":
    unittest.main()
