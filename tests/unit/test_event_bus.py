# ============================================================
#  tests/unit/test_event_bus.py
# ============================================================

import unittest
from dataclasses import dataclass
from application.events import JobProgressEvent, JobCompletedEvent, JobStateChangedEvent
from core.entities.job import JobStatus
from infrastructure.events.event_bus import InMemoryEventBus


@dataclass(frozen=True)
class CustomTestEvent:
    message: str


class TestInMemoryEventBus(unittest.TestCase):
    """
    Unit tests for InMemoryEventBus verifying subscription, unsubscription,
    polymorphic dispatch, lock-free callback execution, and error isolation.
    """

    def setUp(self):
        self.bus = InMemoryEventBus()

    def test_subscribe_and_publish_event(self):
        received = []

        def handler(event: JobProgressEvent):
            received.append(event)

        sub_id = self.bus.subscribe(JobProgressEvent, handler)
        self.assertIsNotNone(sub_id)

        event = JobProgressEvent(job_id=42, processed_pages=3, total_pages=10, percent=30.0)
        self.bus.publish(event)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].job_id, 42)
        self.assertEqual(received[0].percent, 30.0)

    def test_unsubscribe(self):
        received = []
        sub_id = self.bus.subscribe(CustomTestEvent, lambda e: received.append(e))

        self.bus.publish(CustomTestEvent(message="First"))
        self.assertEqual(len(received), 1)

        unsub_ok = self.bus.unsubscribe(sub_id)
        self.assertTrue(unsub_ok)

        self.bus.publish(CustomTestEvent(message="Second"))
        self.assertEqual(len(received), 1)  # No new event received

        # Unsubscribing again returns False
        self.assertFalse(self.bus.unsubscribe(sub_id))

    def test_polymorphic_event_dispatch(self):
        """A subscriber for base 'object' receives all published events."""
        received_all = []
        self.bus.subscribe(object, lambda e: received_all.append(e))

        self.bus.publish(JobProgressEvent(job_id=1, processed_pages=1, total_pages=2, percent=50.0))
        self.bus.publish(JobStateChangedEvent(job_id=1, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING))

        self.assertEqual(len(received_all), 2)

    def test_subscriber_error_isolation(self):
        """Failing subscriber does not block other subscribers or crash publisher."""
        received_good = []

        def failing_handler(e):
            raise RuntimeError("Simulated subscriber crash!")

        def good_handler(e):
            received_good.append(e)

        self.bus.subscribe(CustomTestEvent, failing_handler)
        self.bus.subscribe(CustomTestEvent, good_handler)

        # publish must not raise
        self.bus.publish(CustomTestEvent(message="Safe dispatch"))
        self.assertEqual(len(received_good), 1)
        self.assertEqual(received_good[0].message, "Safe dispatch")

    def test_reentrant_subscription_during_publish(self):
        """Subscriber subscribing another handler during event dispatch must not deadlock."""
        received_second = []

        def outer_handler(e):
            self.bus.subscribe(CustomTestEvent, lambda e2: received_second.append(e2))

        self.bus.subscribe(CustomTestEvent, outer_handler)
        self.bus.publish(CustomTestEvent(message="Trigger reentrant subscription"))

        # Next event triggers both
        self.bus.publish(CustomTestEvent(message="Second event"))
        self.assertEqual(len(received_second), 1)


if __name__ == "__main__":
    unittest.main()
