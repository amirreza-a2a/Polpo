# ============================================================
#  interfaces/desktop/composition.py
#  Canonical Desktop Composition Root & Dependency Injection
# ============================================================

import os
from pathlib import Path
from typing import Optional, Dict

from core.entities.api_slot import ApiSlot
from application.ports.ai_provider import AIProviderPort
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.security.keyring_resolver import KeyringCredentialResolver
from infrastructure.security.encrypted_store import EncryptedFileCredentialStore
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.rate_limiting.memory_rate_limiter import ThreadSafeMemoryRateLimiter
from infrastructure.events.event_bus import InMemoryEventBus
from infrastructure.ai.factory import create_ai_adapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from infrastructure.ai.provider_detector import AIProviderDetector

from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.schedule_service import ScheduleService
from application.services.job_query import JobQueryService
from application.services.quick_convert import QuickConvertService
from application.services.api_key_service import ApiKeyService
from application.services.settings_service import LocalSettingsService
from application.services.prompt_service import PromptService
from application.services.artifact_service import ArtifactService
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_viewer_service import MarkdownViewerService
from application.services.markdown_editor_service import MarkdownEditorService
from application.services.document_publication_service import DocumentPublicationService
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.math import MathSvgCache, MathJaxProcessSupervisor, MathJaxClient
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.workers.scheduler import DesktopJobScheduler


class DesktopAppContainer:
    """
    Canonical Desktop Composition Root.
    Assembles local-first SQLite persistence, OS Keyring security, PyMuPDF document
    processing, memory rate limiting, AI execution, persistent scheduling, and application
    services into a single cohesive dependency graph.
    """

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        artifacts_dir: Optional[str | Path] = None,
        vault_path: Optional[str | Path] = None,
        passphrase: Optional[str] = None,
        keyring_service_name: str = "polpot_desktop",
        default_rpms: Optional[Dict[str, int]] = None,
        scheduler_tick_interval: float = 5.0,
    ):
        self._initialized: bool = False

        # 1. Platform-appropriate default paths if not provided
        if db_path is None:
            data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            base_data = Path(data_home) / "polpot"
            base_data.mkdir(parents=True, exist_ok=True)
            db_path = base_data / "polpot.db"
            if artifacts_dir is None:
                artifacts_dir = base_data / "artifacts"
            if vault_path is None:
                vault_path = base_data / "credentials.enc"
        else:
            db_path = Path(db_path)
            if artifacts_dir is None:
                artifacts_dir = db_path.parent / "artifacts"
            if vault_path is None:
                vault_path = db_path.parent / "credentials.enc"

        artifacts_dir = Path(artifacts_dir).resolve()
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 2. Persistence & Security Infrastructure
        self.db_manager = SQLiteDatabaseManager(db_path)
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)

        self.encrypted_store = EncryptedFileCredentialStore(vault_path, passphrase=passphrase)
        self.credential_resolver = KeyringCredentialResolver(
            service_name=keyring_service_name,
            fallback_store=self.encrypted_store,
        )

        # 3. Document, Storage, Rate Limiter, and Event Bus Infrastructure
        self.storage = LocalStorageAdapter(base_dir=str(artifacts_dir))
        self.doc_processor = PyMuPDFDocumentProcessor()
        self.rate_limiter = ThreadSafeMemoryRateLimiter(default_rpms=default_rpms)
        self.event_bus = InMemoryEventBus()
        self.provider_detector = AIProviderDetector()

        # 4. AI Adapter Factory with transient credential resolution
        def resolve_ai_adapter(slot: ApiSlot) -> AIProviderPort:
            raw_key = self.credential_resolver.resolve_api_key(slot.credential_ref)
            return create_ai_adapter(
                provider=slot.provider,
                api_key=raw_key,
                default_model=slot.selected_model,
                base_url=slot.base_url,
            )

        self.ai_adapter_factory = resolve_ai_adapter
        self.ai_executor = RateLimitedAIExecutor(
            adapter_factory=self.ai_adapter_factory,
            rate_limiter=self.rate_limiter,
        )

        # 5. Application Services
        self.settings_service = LocalSettingsService(self.uow_factory)
        self.prompt_service = PromptService(self.uow_factory)
        self.api_key_service = ApiKeyService(self.uow_factory, self.credential_resolver, self.provider_detector)
        self.artifact_service = ArtifactService(self.storage, self.uow_factory)
        self.quick_convert_service = QuickConvertService(self.uow_factory, self.ai_executor)
        self.job_query_service = JobQueryService(self.uow_factory)

        self.crop_staging_service = CropArtifactStagingService(
            base_dir=artifacts_dir / ".staging",
        )
        self.document_publication_service = DocumentPublicationService(
            uow_factory=self.uow_factory,
            artifacts_dir=artifacts_dir,
            staging_service=self.crop_staging_service,
        )

        self.job_submission_service = JobSubmissionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            event_publisher=self.event_bus,
        )

        self.job_execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            event_publisher=self.event_bus,
            document_publication_service=self.document_publication_service,
        )

        self.job_recovery_service = JobRecoveryService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
        )

        self.document_viewer_service = DocumentViewerService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

        self.math_svg_cache = MathSvgCache(capacity=1000)
        self.mathjax_supervisor = MathJaxProcessSupervisor()
        self.math_renderer = MathJaxClient(
            supervisor=self.mathjax_supervisor, cache=self.math_svg_cache
        )

        self.markdown_parser = MarkdownItParser()
        self.markdown_viewer_service = MarkdownViewerService(
            parser=self.markdown_parser,
            uow_factory=self.uow_factory,
            storage=self.storage,
            math_renderer=self.math_renderer,
        )

        self.markdown_editor_service = MarkdownEditorService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            document_publication_service=self.document_publication_service,
        )

        # 6. Desktop Concurrent Runtime & Persistent Scheduler
        self.runtime = DesktopJobRuntime(
            uow_factory=self.uow_factory,
            job_execution_service=self.job_execution_service,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
        )

        self.schedule_service = ScheduleService(
            uow_factory=self.uow_factory,
            event_publisher=self.event_bus,
            runtime_wake_fn=self.runtime.wake,
            execution_service=self.job_execution_service,
        )

        self.scheduler = DesktopJobScheduler(
            uow_factory=self.uow_factory,
            runtime=self.runtime,
            settings_service=self.settings_service,
            event_publisher=self.event_bus,
            tick_interval=scheduler_tick_interval,
        )

    def initialize(self) -> None:
        """
        Executes deterministic startup sequence:
        1. Applies SQLite schema migrations (applies 004).
        2. Reconciles legacy unversioned canonical documents (idempotent backfill).
        3. Reconciles crashed publication intents.
        4. Reconciles stale processing jobs to PAUSED.
        5. Reconciles missed schedules according to AppSettings policy.
        Sets initialized state flag.
        """
        self.migration_runner.run_migrations()
        self.document_publication_service.backfill_legacy_document_versions()
        self.document_publication_service.reconcile_startup_intents()
        self.job_recovery_service.reconcile_stale_jobs()
        self.job_recovery_service.reconcile_missed_schedules()
        self._initialized = True

    def start_runtime(self) -> None:
        """
        Starts the background worker dispatching runtime and persistent scheduler.
        Refuses to start if container has not completed initialization/recovery.
        """
        if not self._initialized:
            raise RuntimeError(
                "DesktopAppContainer must be initialized via initialize() before starting the runtime."
            )
        self.runtime.start()
        self.scheduler.start()

    def start(self) -> None:
        """Convenience lifecycle method to initialize container and start runtime in order."""
        if not self._initialized:
            self.initialize()
        self.start_runtime()

    def shutdown(self) -> None:
        """Shuts down the desktop scheduler, runtime, and controllers, releasing resources."""
        if hasattr(self, "markdown_viewer_controller") and self.markdown_viewer_controller:
            self.markdown_viewer_controller.shutdown()
        if hasattr(self, "document_viewer_controller") and self.document_viewer_controller:
            self.document_viewer_controller.shutdown()
        if hasattr(self, "mathjax_supervisor") and self.mathjax_supervisor:
            self.mathjax_supervisor.shutdown()
        self.scheduler.shutdown()
        self.runtime.shutdown()
