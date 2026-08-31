# ============================================================
#  infrastructure/composition.py
#  Composition Root & Dependency Injection Container
# ============================================================

from typing import Optional
from database.connection import DatabaseManager
from application.ports.ai_provider import AIProviderPort
from infrastructure.persistence.unit_of_work import MySQLUnitOfWorkFactory
from infrastructure.persistence.credential_resolver import MySQLCredentialResolver
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.rate_limiting.rate_limiter_adapter import RateLimiterAdapter
from infrastructure.notifier.event_notifier import InMemoryEventNotifier
from infrastructure.security.token_service import SecureTokenService
from infrastructure.ai.factory import create_ai_adapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor
from core.entities.api_slot import ApiSlot

from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.job_query import JobQueryService
from application.services.quick_convert import QuickConvertService
from application.services.user_service import UserManagementService
from application.services.prompt_service import PromptService
from application.services.api_service import ApiManagementService
from application.services.artifact_service import ArtifactService
from application.services.auth_service import AuthService


class AppContainer:
    """
    ریشه ترکیب (Composition Root) سیستم.
    تمام آداپتورهای زیرساختی و سرویس‌های کاربرد را با رعایت وارونگی وابستگی پیکربندی و متصل می‌کند.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        storage_adapter: Optional[LocalStorageAdapter] = None,
        token_secret: Optional[str] = None,
        notifier: Optional[InMemoryEventNotifier] = None,
    ):
        # 1. زیرساخت پایگاه‌داده و امنیت
        self.db_manager = db_manager or DatabaseManager()
        self.uow_factory = MySQLUnitOfWorkFactory(self.db_manager)
        self.credential_resolver = MySQLCredentialResolver(self.db_manager)
        self.token_service = SecureTokenService(secret_key=token_secret)

        # 2. زیرساخت فایل، پردازش سند و شبکه
        self.storage = storage_adapter or LocalStorageAdapter()
        self.doc_processor = PyMuPDFDocumentProcessor()
        self.rate_limiter = RateLimiterAdapter()
        self.notifier = notifier or InMemoryEventNotifier()


        # 3. Factory ایجاد آداپتور هوش مصنوعی برای هر اسلات
        def resolve_ai_adapter(slot: ApiSlot) -> AIProviderPort:
            raw_key = self.credential_resolver.resolve_api_key(slot.credential_ref)
            return create_ai_adapter(
                provider=slot.provider,
                api_key=raw_key,
                default_model=slot.selected_model,
                base_url=slot.base_url,
            )
        self.ai_adapter_factory = resolve_ai_adapter

        # 4. مجری هوش مصنوعی و Fallback متمرکز
        self.ai_executor = RateLimitedAIExecutor(
            adapter_factory=self.ai_adapter_factory,
            rate_limiter=self.rate_limiter,
        )

        # 5. سرویس‌های لایه کاربرد
        self.user_service = UserManagementService(self.uow_factory)
        self.prompt_service = PromptService(self.uow_factory)
        self.api_service = ApiManagementService(self.uow_factory)
        self.artifact_service = ArtifactService(self.storage, self.uow_factory)
        self.auth_service = AuthService(self.uow_factory, self.token_service)

        self.quick_convert_service = QuickConvertService(
            uow_factory=self.uow_factory,
            ai_executor=self.ai_executor,
        )

        self.job_submission_service = JobSubmissionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
        )

        self.job_query_service = JobQueryService(self.uow_factory)

        self.job_execution_service = JobExecutionService(
            uow_factory=self.uow_factory,
            storage=self.storage,
            doc_processor=self.doc_processor,
            ai_executor=self.ai_executor,
            notifier=self.notifier,
        )

        self.job_recovery_service = JobRecoveryService(self.uow_factory)


# Global container singleton
_container_instance: Optional[AppContainer] = None


def get_app_container() -> AppContainer:
    global _container_instance
    if _container_instance is None:
        _container_instance = AppContainer()
    return _container_instance
