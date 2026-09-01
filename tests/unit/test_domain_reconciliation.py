# ============================================================
#  tests/unit/test_domain_reconciliation.py
# ============================================================

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
import inspect
import os
import unittest
from typing import Set, get_type_hints

import tests.characterization.conftest_base
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, Pipeline2Job, JobStatus, JobType
from core.entities.settings import AppSettings
from core.policies.job_state_policy import (
    JobStateTransitionPolicy,
    InvalidStateTransitionError,
)
import core.policies as core_policies
import core.ai.types as core_ai_types
from application.events import (
    JobProgressEvent,
    ApiSwitchEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
)
from application.ports.notifier import (
    IApplicationEventPublisher,
    IProgressNotifier,
    ProgressNotifierEventAdapter,
    EventPublisherNotifierAdapter,
)
from application.ports.repositories import (
    IJobRepository,
    IPipeline2JobRepository,
    IPromptRepository,
    IApiRepository,
    ISettingsRepository,
)
from application.ports.unit_of_work import IUnitOfWork
from application.services.settings_service import LocalSettingsService
from application.services.job_submission import JobSubmissionService
from application.services.quick_convert import QuickConvertService
from application.services.job_execution import JobExecutionService


class TestApiSlotReconciliation(unittest.TestCase):
    """Verifies single canonical ApiSlot with strict desktop semantics and CredentialRef invariant."""

    def test_canonical_api_slot_has_no_raw_api_key_field(self):
        slot = ApiSlot(
            id=1,
            provider="google",
            label="Gemini Flash",
            slot_type="byok",
            credential_ref=CredentialRef("key_1", "google", "byok"),
            selected_model="gemini-3.5-flash",
        )
        self.assertFalse(hasattr(slot, "api_key"))
        self.assertEqual(slot.id, 1)
        self.assertEqual(slot.provider, "google")
        self.assertEqual(slot.slot_type, "byok")
        self.assertEqual(str(slot.credential_ref), "byok:google:key_1")

    def test_canonical_api_slot_accepts_only_desktop_slot_semantics(self):
        # Valid types
        slot_byok = ApiSlot(
            id=1,
            provider="google",
            label="BYOK",
            slot_type="byok",
            credential_ref=CredentialRef("1", "google", "byok"),
        )
        self.assertEqual(slot_byok.slot_type, "byok")

        slot_custom = ApiSlot(
            id=2,
            provider="custom",
            label="Custom",
            slot_type="custom",
            credential_ref=CredentialRef("2", "custom", "custom"),
        )
        self.assertEqual(slot_custom.slot_type, "custom")

        # Invalid legacy / server types raise ValueError
        with self.assertRaises(ValueError):
            ApiSlot(
                id=3,
                provider="google",
                label="Invalid",
                slot_type="private",
                credential_ref=CredentialRef("3", "google", "private"),
            )

        with self.assertRaises(ValueError):
            ApiSlot(
                id=4,
                provider="openai",
                label="Invalid Public",
                slot_type="public",
                credential_ref=CredentialRef("4", "openai", "public"),
            )

    def test_api_slot_rejects_non_credential_ref(self):
        with self.assertRaises(ValueError):
            ApiSlot(
                id=5,
                provider="google",
                label="Bad",
                slot_type="byok",
                credential_ref="not_a_credential_ref",  # type: ignore
            )

    def test_api_slot_from_dict_and_to_dict(self):
        data = {
            "id": 10,
            "provider": "openai",
            "label": "GPT-4o BYOK",
            "type": "byok",
            "selected_model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o", "gpt-4o-mini"],
        }
        slot = ApiSlot.from_dict(data)
        self.assertEqual(slot.id, 10)
        self.assertEqual(slot.provider, "openai")
        self.assertEqual(slot.slot_type, "byok")
        self.assertIsNotNone(slot.credential_ref)
        self.assertEqual(slot.credential_ref.identifier, "10")

        serialized = slot.to_dict()
        self.assertNotIn("api_key", serialized)
        self.assertEqual(serialized["id"], 10)
        self.assertEqual(serialized["provider"], "openai")
        self.assertEqual(serialized["slot_type"], "byok")

    def test_duplicate_api_slot_removed_from_core_ai_types(self):
        self.assertFalse(hasattr(core_ai_types, "ApiSlot"))


class TestCanonicalUnitOfWorkAndRepositories(unittest.TestCase):
    """Verifies canonical desktop IUnitOfWork has only settings, jobs, pipeline2_jobs, prompts, apis."""

    def test_canonical_unit_of_work_members(self):
        annotations = get_type_hints(IUnitOfWork)
        expected_canonical = {"settings", "jobs", "pipeline2_jobs", "prompts", "apis"}
        self.assertEqual(set(annotations.keys()), expected_canonical)
        self.assertNotIn("users", annotations)
        self.assertNotIn("donations", annotations)

    def test_canonical_repositories_do_not_import_user(self):
        canonical_repo_classes = [
            IJobRepository,
            IPipeline2JobRepository,
            IPromptRepository,
            IApiRepository,
            ISettingsRepository,
        ]
        for repo_cls in canonical_repo_classes:
            for name, method in inspect.getmembers(repo_cls, predicate=inspect.isfunction):
                sig = inspect.signature(method)
                for param in sig.parameters.values():
                    self.assertNotEqual(
                        param.annotation,
                        "User",
                        f"Canonical repo {repo_cls.__name__}.{name} parameter {param.name} must not type-hint User",
                    )


class TestAppSettingsEntity(unittest.TestCase):
    """Verifies AppSettings domain entity defaults and validation."""

    def test_app_settings_defaults(self):
        settings = AppSettings()
        self.assertEqual(settings.theme, "system")
        self.assertEqual(settings.max_concurrent_jobs, 2)
        self.assertTrue(settings.auto_retry)
        self.assertFalse(settings.auto_pipeline2)
        self.assertIsNone(settings.default_prompt_id)
        self.assertIsNone(settings.default_pipeline2_prompt_id)
        self.assertEqual(settings.artifact_retention_days, 30)
        self.assertEqual(settings.missed_schedule_policy, "prompt")

    def test_app_settings_theme_validation(self):
        AppSettings(theme="dark")
        AppSettings(theme="light")
        AppSettings(theme="system")
        with self.assertRaises(ValueError):
            AppSettings(theme="neon-blue")

    def test_app_settings_concurrency_range_validation(self):
        AppSettings(max_concurrent_jobs=1)
        AppSettings(max_concurrent_jobs=8)
        with self.assertRaises(ValueError):
            AppSettings(max_concurrent_jobs=0)
        with self.assertRaises(ValueError):
            AppSettings(max_concurrent_jobs=9)

    def test_app_settings_retention_validation(self):
        AppSettings(artifact_retention_days=1)
        with self.assertRaises(ValueError):
            AppSettings(artifact_retention_days=0)
        with self.assertRaises(ValueError):
            AppSettings(artifact_retention_days=-5)

    def test_app_settings_missed_schedule_policy_validation(self):
        AppSettings(missed_schedule_policy="run_immediately")
        AppSettings(missed_schedule_policy="prompt")
        AppSettings(missed_schedule_policy="mark_paused")
        with self.assertRaises(ValueError):
            AppSettings(missed_schedule_policy="delete_all")


class TestJobEntityReconciliation(unittest.TestCase):
    """Verifies Job entity desktop reconciliation and removal of multi-user identity."""

    def test_job_has_no_user_id_field(self):
        job_field_names = [f.name for f in fields(Job)]
        self.assertNotIn("user_id", job_field_names)

    def test_pipeline2_job_has_no_user_id_field(self):
        p2_field_names = [f.name for f in fields(Pipeline2Job)]
        self.assertNotIn("user_id", p2_field_names)

    def test_job_defaults_and_desktop_fields(self):
        now = datetime.now(timezone.utc)
        job = Job(
            id=1,
            file_name="paper.pdf",
            file_path="/tmp/paper.pdf",
            scheduled_at=now,
            cancel_requested=True,
            claimed_at=now,
        )
        self.assertEqual(job.id, 1)
        self.assertEqual(job.file_name, "paper.pdf")
        self.assertEqual(job.status, JobStatus.PENDING)
        self.assertEqual(job.scheduled_at, now)
        self.assertTrue(job.cancel_requested)
        self.assertEqual(job.claimed_at, now)
        self.assertFalse(job.is_terminal)

    def test_job_status_cancelled_is_terminal(self):
        self.assertEqual(JobStatus.CANCELLED.value, "cancelled")
        job = Job(
            id=1,
            file_name="paper.pdf",
            file_path="/tmp/paper.pdf",
            status=JobStatus.CANCELLED,
        )
        self.assertTrue(job.is_terminal)
        self.assertFalse(job.is_completed)

    def test_pipeline2_job_cancellation_and_terminal(self):
        p2 = Pipeline2Job(
            id=1,
            source_job_id=42,
            status=JobStatus.CANCELLED,
            cancel_requested=True,
        )
        self.assertTrue(p2.is_terminal)
        self.assertTrue(p2.cancel_requested)


class TestJobStateTransitionPolicyReconciliation(unittest.TestCase):
    """Verifies legal state transitions including CANCELLED."""

    def test_legal_cancellation_transitions(self):
        JobStateTransitionPolicy.validate_transition(JobStatus.PENDING, JobStatus.CANCELLED)
        JobStateTransitionPolicy.validate_transition(JobStatus.PROCESSING, JobStatus.CANCELLED)
        JobStateTransitionPolicy.validate_transition(JobStatus.PAUSED, JobStatus.CANCELLED)
        JobStateTransitionPolicy.validate_transition(JobStatus.FAILED, JobStatus.CANCELLED)
        JobStateTransitionPolicy.validate_transition(JobStatus.CANCELLED, JobStatus.PENDING)

    def test_invalid_transitions_from_done_or_cancelled(self):
        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.DONE, JobStatus.CANCELLED)

        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.CANCELLED, JobStatus.PROCESSING)

        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.CANCELLED, JobStatus.DONE)


class TestApplicationEventsAndNotifierBoundary(unittest.TestCase):
    """Verifies single canonical application event publisher boundary and event immutability."""

    def test_canonical_event_publisher_protocol(self):
        class DummyPublisher:
            def __init__(self):
                self.events = []
            def publish(self, event):
                self.events.append(event)

        pub = DummyPublisher()
        self.assertIsInstance(pub, IApplicationEventPublisher)

    def test_progress_notifier_adapter_bridges_to_event_publisher(self):
        published_events = []
        class TestEventSink:
            def publish(self, event):
                published_events.append(event)

        adapter = ProgressNotifierEventAdapter(publisher=TestEventSink())
        adapter.notify_progress(user_id=1, job_id=10, processed_pages=3, total_pages=6)
        self.assertEqual(len(published_events), 1)
        self.assertIsInstance(published_events[0], JobProgressEvent)
        self.assertEqual(published_events[0].percent, 50.0)

        adapter.notify_api_switch(user_id=1, job_id=10, old_label="K1", new_label="K2", reason="rate_limit", page=4)
        self.assertIsInstance(published_events[1], ApiSwitchEvent)

        adapter.notify_job_completed(user_id=1, job_id=10, output_handle_uri="uri://out")
        self.assertIsInstance(published_events[2], JobCompletedEvent)

        adapter.notify_job_failed(user_id=1, job_id=10, error_message="Fatal")
        self.assertIsInstance(published_events[3], JobFailedEvent)

    def test_event_publisher_notifier_adapter_bridges_to_legacy_notifier(self):
        mock_legacy = unittest.mock.MagicMock(spec=IProgressNotifier)
        adapter = EventPublisherNotifierAdapter(notifier=mock_legacy)

        adapter.publish(JobProgressEvent(job_id=42, processed_pages=2, total_pages=4, percent=50.0))
        mock_legacy.notify_progress.assert_called_once_with(1, 42, 2, 4)

        adapter.publish(ApiSwitchEvent(job_id=42, old_label="A", new_label="B", reason="rate_limit", page=2))
        mock_legacy.notify_api_switch.assert_called_once_with(1, 42, "A", "B", "rate_limit", 2)

        adapter.publish(JobCompletedEvent(job_id=42, output_artifact_uri="uri://doc"))
        mock_legacy.notify_job_completed.assert_called_once_with(1, 42, "uri://doc")

        adapter.publish(JobFailedEvent(job_id=42, error_message="Failed"))
        mock_legacy.notify_job_failed.assert_called_once_with(1, 42, "Failed")

    def test_job_progress_event(self):
        ev = JobProgressEvent(job_id=42, processed_pages=5, total_pages=10, percent=50.0)
        self.assertEqual(ev.job_id, 42)
        self.assertEqual(ev.processed_pages, 5)
        self.assertEqual(ev.total_pages, 10)
        self.assertEqual(ev.percent, 50.0)
        with self.assertRaises(FrozenInstanceError):
            ev.processed_pages = 6  # type: ignore

    def test_api_switch_event(self):
        ev = ApiSwitchEvent(job_id=42, old_label="Key 1", new_label="Key 2", reason="rate_limit", page=3)
        self.assertEqual(ev.job_id, 42)
        self.assertEqual(ev.reason, "rate_limit")
        with self.assertRaises(FrozenInstanceError):
            ev.page = 4  # type: ignore

    def test_job_completed_event(self):
        ev = JobCompletedEvent(job_id=42, output_artifact_uri="local:///output_42.md")
        self.assertEqual(ev.output_artifact_uri, "local:///output_42.md")
        with self.assertRaises(FrozenInstanceError):
            ev.job_id = 99  # type: ignore

    def test_job_failed_event(self):
        ev = JobFailedEvent(job_id=42, error_message="Chain exhausted", is_retryable=True)
        self.assertTrue(ev.is_retryable)
        with self.assertRaises(FrozenInstanceError):
            ev.error_message = "Other"  # type: ignore

    def test_job_cancelled_event(self):
        ev = JobCancelledEvent(job_id=42)
        self.assertEqual(ev.job_id, 42)
        with self.assertRaises(FrozenInstanceError):
            ev.job_id = 1  # type: ignore

    def test_job_state_changed_event(self):
        ev = JobStateChangedEvent(job_id=42, old_status=JobStatus.PENDING, new_status=JobStatus.PROCESSING)
        self.assertEqual(ev.old_status, JobStatus.PENDING)
        self.assertEqual(ev.new_status, JobStatus.PROCESSING)
        with self.assertRaises(FrozenInstanceError):
            ev.new_status = JobStatus.DONE  # type: ignore


class TestDesktopServicesNoQuotaLogic(unittest.TestCase):
    """Verifies that no desktop application service implements daily quota reset/consumption logic."""

    def test_quota_policy_not_present_in_core_policies(self):
        self.assertFalse(hasattr(core_policies, "QuotaPolicy"))
        quota_policy_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "core",
            "policies",
            "quota_policy.py",
        )
        self.assertFalse(os.path.exists(quota_policy_file))

    def test_local_settings_service_manages_app_settings(self):
        mock_uow_factory = unittest.mock.MagicMock()
        mock_uow = unittest.mock.MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow
        mock_uow.settings.get.return_value = AppSettings(theme="dark", max_concurrent_jobs=4)
        mock_uow.settings.save.side_effect = lambda s: s

        service = LocalSettingsService(uow_factory=mock_uow_factory)
        settings = service.get_settings()
        self.assertEqual(settings.theme, "dark")
        self.assertEqual(settings.max_concurrent_jobs, 4)

        updated = service.update_settings(AppSettings(theme="light", max_concurrent_jobs=1))
        self.assertEqual(updated.theme, "light")
        self.assertEqual(updated.max_concurrent_jobs, 1)


class TestArchitectureInvariants(unittest.TestCase):
    """AST tests proving zero framework/infrastructure leak into core/ or application/."""

    def setUp(self):
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    def _collect_imports_from_dir(self, directory_rel: str) -> Set[str]:
        target_dir = os.path.join(self.project_root, directory_rel)
        imported_modules: Set[str] = set()

        for root, _, files in os.walk(target_dir):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    with open(full_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=full_path)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for alias in node.names:
                                    imported_modules.add(alias.name.split(".")[0])
                            elif isinstance(node, ast.ImportFrom):
                                if node.module:
                                    imported_modules.add(node.module.split(".")[0])
        return imported_modules

    def test_core_layer_has_zero_forbidden_imports(self):
        forbidden = {
            "PySide6",
            "Qt",
            "sqlite3",
            "keyring",
            "fastapi",
            "starlette",
            "telegram",
            "infrastructure",
            "interfaces",
            "application",
            "pymysql",
            "PIL",
            "fitz",
        }
        imported = self._collect_imports_from_dir("core")
        violation = imported.intersection(forbidden)
        self.assertEqual(
            violation,
            set(),
            f"core/ layer must not import forbidden modules. Violations: {violation}",
        )

    def test_application_layer_has_zero_forbidden_imports(self):
        forbidden = {
            "PySide6",
            "Qt",
            "sqlite3",
            "keyring",
            "fastapi",
            "starlette",
            "telegram",
            "infrastructure",
            "interfaces",
            "pymysql",
            "PIL",
            "fitz",
        }
        imported = self._collect_imports_from_dir("application")
        violation = imported.intersection(forbidden)
        self.assertEqual(
            violation,
            set(),
            f"application/ layer must not import forbidden modules. Violations: {violation}",
        )

    def test_job_entities_have_zero_user_id_fields(self):
        """Verify that canonical Job and Pipeline2Job domain entities have no user_id field."""
        job_field_names = {f.name for f in fields(Job)}
        self.assertNotIn("user_id", job_field_names)

        p2_job_field_names = {f.name for f in fields(Pipeline2Job)}
        self.assertNotIn("user_id", p2_job_field_names)

    def test_api_slot_contains_no_raw_api_key_field(self):
        """Verify that ApiSlot contains only credential_ref and no api_key attribute."""
        slot_field_names = {f.name for f in fields(ApiSlot)}
        self.assertNotIn("api_key", slot_field_names)
        self.assertIn("credential_ref", slot_field_names)

    def test_job_cancellation_and_terminal_state_invariants(self):
        """Verify that JobStatus.CANCELLED is terminal and obeys formal state transition rules."""
        self.assertTrue(JobStatus.CANCELLED in JobStatus)
        job = Job(id=1, file_name="doc.pdf", file_path="", status=JobStatus.CANCELLED)
        self.assertTrue(job.is_terminal)

        p2 = Pipeline2Job(id=1, source_job_id=1, status=JobStatus.CANCELLED)
        self.assertTrue(p2.is_terminal)

        # Transition CANCELLED -> PENDING is legal for retrying/resuming
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.CANCELLED, JobStatus.PENDING))
        # Transition DONE -> CANCELLED is illegal
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.DONE, JobStatus.CANCELLED))
        with self.assertRaises(InvalidStateTransitionError):
            JobStateTransitionPolicy.validate_transition(JobStatus.DONE, JobStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
