# ============================================================
#  tests/unit/test_phase5_telegram_migration.py
#  Comprehensive Phase 5 Architecture & End-to-End Integration Tests
# ============================================================

import os
import ast
import asyncio
import tempfile
import shutil
import unittest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

import tests.characterization.conftest_base
from config import PROJECT_ROOT
from application.ports.notifier import IProgressNotifier
from application.ports.unit_of_work import IUnitOfWorkFactory, IUnitOfWork
from application.ports.ai_provider import AIProviderPort
from interfaces.telegram.notifier import TelegramProgressNotifier
from interfaces.telegram.error_formatter import format_telegram_error
from core.entities.user import User, QuotaAllocation, UserPreferences
from core.entities.job import Job, JobStatus, Pipeline2Job
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.ai.types import VisionPromptRequest, TextPromptRequest, AIResponse
from core.ai.exceptions import AIChainExhaustedError, AIRateLimitError
from core.exceptions.domain_exceptions import (
    DomainError,
    QuotaExceededError,
    EntityNotFoundError,
    AuthenticationError,
)

from application.services.job_query import JobQueryService
from application.services.job_submission import JobSubmissionService
from application.services.job_execution import JobExecutionService
from application.services.job_recovery import JobRecoveryService
from application.services.prompt_service import PromptService
from application.services.api_service import ApiManagementService
from application.services.artifact_service import ArtifactService
from application.services.user_service import UserManagementService
from application.dto.job_dto import SubmitJobCommand, JobDetailDTO
from application.dto.prompt_dto import CreatePromptCommand
from application.dto.api_dto import DonateApiCommand, RegisterApiCommand
from application.dto.user_dto import UserDTO
from infrastructure.storage.local_storage import LocalStorageAdapter
from infrastructure.ai.executor_service import RateLimitedAIExecutor


# ════════════════════════════════════════════════════════════
#  In-Memory Mock Repositories & Unit of Work for Integration Tests
# ════════════════════════════════════════════════════════════

class MockJobRepo:
    def __init__(self):
        self.jobs = {}
        self.counter = 1

    def get_by_id(self, job_id: int):
        return self.jobs.get(job_id)

    def get_next_pending(self):
        for j in self.jobs.values():
            if j.status == JobStatus.PENDING:
                return j
        return None

    def list_by_user(self, user_id: int, limit: int = 10, offset: int = 0):
        user_jobs = [j for j in self.jobs.values() if getattr(j, "user_id", 1) == user_id]
        return user_jobs[offset : offset + limit]

    def count_by_user(self, user_id: int, status=None):
        if status:
            return sum(1 for j in self.jobs.values() if getattr(j, "user_id", 1) == user_id and j.status == status)
        return sum(1 for j in self.jobs.values() if getattr(j, "user_id", 1) == user_id)

    def save(self, job: Job) -> Job:
        if job.id is None:
            job.id = self.counter
            self.counter += 1
        self.jobs[job.id] = job
        return job

    def update_progress(self, job_id: int, processed_pages: int, switch_log: list, output_path: str = None):
        if job_id in self.jobs:
            self.jobs[job_id].processed_pages = processed_pages
            self.jobs[job_id].api_switch_log = switch_log
            if output_path:
                self.jobs[job_id].output_path = output_path

    def update_status(self, job_id: int, status: JobStatus, error_message: str = None):
        if job_id in self.jobs:
            self.jobs[job_id].status = status
            self.jobs[job_id].error_message = error_message

    def get_queue_position(self, job_id: int) -> int:
        pos = 1
        for j in self.jobs.values():
            if j.id == job_id:
                return pos
            if j.status == JobStatus.PENDING:
                pos += 1
        return pos

    def get_today_stats(self) -> dict:
        return {"total_jobs": len(self.jobs), "total_pages": 10, "in_queue": 1}


class MockPipeline2JobRepo:
    def __init__(self):
        self.p2_jobs = {}
        self.counter = 1

    def get_by_id(self, p2_id: int):
        return self.p2_jobs.get(p2_id)

    def get_next_pending(self):
        for j in self.p2_jobs.values():
            if j.status == JobStatus.PENDING:
                return j
        return None

    def save(self, p2_job: Pipeline2Job) -> Pipeline2Job:
        if p2_job.id is None:
            p2_job.id = self.counter
            self.counter += 1
        self.p2_jobs[p2_job.id] = p2_job
        return p2_job

    def update_status(self, p2_id: int, status: JobStatus, error_message: str = None):
        if p2_id in self.p2_jobs:
            self.p2_jobs[p2_id].status = status
            self.p2_jobs[p2_id].error_message = error_message

    def update_paths(self, p2_id: int, input_path: str = None, output_path: str = None):
        if p2_id in self.p2_jobs:
            if input_path:
                self.p2_jobs[p2_id].input_path = input_path
            if output_path:
                self.p2_jobs[p2_id].output_path = output_path

    def update_output_path(self, p2_id: int, output_path: str):
        self.update_paths(p2_id, output_path=output_path)


class MockUserRepo:
    def __init__(self):
        self.users = {}
        self.counter = 1

    def get_by_id(self, user_id: int):
        return self.users.get(user_id)

    def get_by_telegram_id(self, telegram_id: int):
        for u in self.users.values():
            if u.telegram_id == telegram_id:
                return u
        return None

    def get_by_username(self, username: str):
        for u in self.users.values():
            if u.username == username:
                return u
        return None

    def save(self, user: User) -> User:
        if user.id is None:
            user.id = self.counter
            self.counter += 1
        self.users[user.id] = user
        return user

    def increment_daily_pages(self, user_id: int, amount: int = 1):
        if user_id in self.users:
            self.users[user_id].quota.daily_pages_used += amount
            return self.users[user_id].quota.daily_pages_used
        return 0

    def reset_daily_quota(self, user_id: int):
        if user_id in self.users:
            self.users[user_id].quota.daily_pages_used = 0


class MockPromptRepo:
    def __init__(self):
        self.prompts = {}
        self.counter = 1
        self.quick_prompt = None

    def get_by_id(self, prompt_id: int):
        return self.prompts.get(prompt_id)

    def get_default(self, prompt_type: PromptType):
        for p in self.prompts.values():
            if p.prompt_type == prompt_type and p.is_default:
                return p
        for p in self.prompts.values():
            if p.prompt_type == prompt_type:
                return p
        return None

    def list_all(self, prompt_type: PromptType = None):
        if prompt_type:
            return [p for p in self.prompts.values() if p.prompt_type == prompt_type]
        return list(self.prompts.values())

    def save(self, prompt: Prompt) -> Prompt:
        if prompt.id is None:
            prompt.id = self.counter
            self.counter += 1
        self.prompts[prompt.id] = prompt
        return prompt

    def delete(self, prompt_id: int) -> bool:
        return self.prompts.pop(prompt_id, None) is not None

    def set_default(self, prompt_id: int, prompt_type: PromptType):
        for p in self.prompts.values():
            if p.prompt_type == prompt_type:
                p.is_default = (p.id == prompt_id)

    def toggle_active(self, prompt_id: int, is_active: bool) -> bool:
        return prompt_id in self.prompts

    def get_quick_convert_prompt(self):
        return self.quick_prompt

    def set_quick_convert_prompt(self, text: str):
        self.quick_prompt = text


class MockApiRepo:
    def __init__(self):
        self.apis = {}
        self.counter = 1

    def get_by_id(self, api_id: int, slot_type: str = "private"):
        return self.apis.get(api_id)

    def list_by_user(self, user_id: int, include_public: bool = False):
        return list(self.apis.values())

    def list_public(self):
        return list(self.apis.values())

    def save_private(self, user_id: int, provider: str, api_key: str, label: str, model: str = None, base_url: str = None):
        slot = ApiSlot(
            id=self.counter,
            provider=provider,
            label=label,
            slot_type="byok",
            selected_model=model or "default",
            base_url=base_url,
            credential_ref=CredentialRef(identifier=str(self.counter), provider=provider, slot_type="byok"),
        )
        self.apis[self.counter] = slot
        self.counter += 1
        return slot

    def save_public(self, provider: str, api_key: str, label: str, models: list, daily_limit: int = 200, selected_model: str = None, base_url: str = None, donated_by: int = None):
        slot = ApiSlot(
            id=self.counter,
            provider=provider,
            label=label,
            slot_type="byok",
            selected_model=selected_model or (models[0] if models else "default"),
            base_url=base_url,
            supported_models=models,
            credential_ref=CredentialRef(identifier=str(self.counter), provider=provider, slot_type="byok"),
        )
        self.apis[self.counter] = slot
        self.counter += 1
        return slot

    def toggle_public(self, api_id: int, is_active: bool) -> bool:
        return api_id in self.apis

    def update_public_model_url(self, api_id: int, selected_model: str = None, base_url: str = None) -> bool:
        return api_id in self.apis

    def delete_private(self, api_id: int, user_id: int) -> bool:
        return self.apis.pop(api_id, None) is not None

    def delete_public(self, api_id: int) -> bool:
        return self.apis.pop(api_id, None) is not None

    def report_pages_used(self, api_id: int, slot_type: str, pages: int = 1):
        pass


class MockDonationRepo:
    def __init__(self):
        self.donations = {}
        self.counter = 1

    def save_donation(self, user_id: int, provider: str, api_key: str, label: str, models: list) -> int:
        d_id = self.counter
        self.donations[d_id] = {
            "id": d_id,
            "user_id": user_id,
            "provider": provider,
            "api_key": api_key,
            "label": label,
            "models": models,
            "status": "pending",
        }
        self.counter += 1
        return d_id

    def get_by_id(self, donation_id: int):
        return self.donations.get(donation_id)

    def update_status(self, donation_id: int, status: str) -> bool:
        if donation_id in self.donations:
            self.donations[donation_id]["status"] = status
            return True
        return False

    def list_all(self):
        return list(self.donations.values())


class MockPublishIntentRepo:
    def __init__(self):
        self.intents = {}

    def get_by_id(self, intent_id: str):
        return self.intents.get(intent_id)

    def get_by_job_id(self, job_id: int):
        for it in self.intents.values():
            if it.job_id == job_id:
                return it
        return None

    def insert_intent(self, intent):
        self.intents[intent.intent_id] = intent
        return intent

    def update_status(self, intent_id: str, status: str):
        if intent_id in self.intents:
            self.intents[intent_id].status = status

    def update_intent_status(self, intent_id: str, status: str):
        self.update_status(intent_id, status)

    def delete_intent(self, intent_id: str):
        self.intents.pop(intent_id, None)


class MockDocumentVersionRepo:
    def __init__(self):
        self.versions = {}

    def get_latest(self, job_id: int):
        job_vers = [v for v in self.versions.values() if v.job_id == job_id]
        if not job_vers:
            return None
        return max(job_vers, key=lambda v: v.version)

    def get_latest_document_version(self, job_id: int):
        return self.get_latest(job_id)

    def insert_document_version(self, version_record, or_ignore: bool = False):
        self.versions[(version_record.job_id, version_record.version)] = version_record
        return version_record


class MockUnitOfWork(IUnitOfWork):
    def __init__(
        self,
        job_repo,
        p2_repo,
        user_repo,
        prompt_repo,
        api_repo,
        don_repo,
        publish_intent_repo=None,
        doc_version_repo=None,
    ):
        self.jobs = job_repo
        self.pipeline2_jobs = p2_repo
        self.users = user_repo
        self.prompts = prompt_repo
        self.apis = api_repo
        self.donations = don_repo
        self.publish_intents = publish_intent_repo or MockPublishIntentRepo()
        self.document_versions = doc_version_repo or MockDocumentVersionRepo()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


class MockUnitOfWorkFactory(IUnitOfWorkFactory):
    def __init__(self):
        self.job_repo = MockJobRepo()
        self.p2_repo = MockPipeline2JobRepo()
        self.user_repo = MockUserRepo()
        self.prompt_repo = MockPromptRepo()
        self.api_repo = MockApiRepo()
        self.don_repo = MockDonationRepo()
        self.publish_intent_repo = MockPublishIntentRepo()
        self.doc_version_repo = MockDocumentVersionRepo()

    def create(self) -> IUnitOfWork:
        return MockUnitOfWork(
            self.job_repo,
            self.p2_repo,
            self.user_repo,
            self.prompt_repo,
            self.api_repo,
            self.don_repo,
            self.publish_intent_repo,
            self.doc_version_repo,
        )


# ════════════════════════════════════════════════════════════
#  1. Static Architectural Invariant Checks
# ════════════════════════════════════════════════════════════

class TestPhase5ArchitecturalInvariants(unittest.TestCase):
    """
    Mandatory Phase 5 Architectural Rules:
    - handlers/ has 0 imports from database.models
    - handlers/ has 0 raw SQL statements
    - handlers/ has 0 direct AI SDK client instantiations
    - core/ and application/ have 0 imports from telegram / interfaces.telegram
    """

    def test_handlers_have_zero_database_models_imports(self):
        handlers_dir = os.path.join(PROJECT_ROOT, "handlers")
        for root, _, files in os.walk(handlers_dir):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                self.assertNotIn(
                                    "database.models",
                                    alias.name,
                                    f"Forbidden import '{alias.name}' in {file_path}",
                                )
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                self.assertNotIn(
                                    "database.models",
                                    node.module,
                                    f"Forbidden from-import from '{node.module}' in {file_path}",
                                )

    def test_handlers_have_zero_raw_sql(self):
        handlers_dir = os.path.join(PROJECT_ROOT, "handlers")
        sql_keywords = ["SELECT ", "INSERT INTO ", "UPDATE ", "DELETE FROM "]
        for root, _, files in os.walk(handlers_dir):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read().upper()
                        for kw in sql_keywords:
                            self.assertNotIn(
                                kw,
                                content,
                                f"Forbidden raw SQL '{kw}' found in {file_path}",
                            )

    def test_core_and_application_layers_have_zero_telegram_imports(self):
        for layer in ["core", "application"]:
            layer_dir = os.path.join(PROJECT_ROOT, layer)
            for root, _, files in os.walk(layer_dir):
                for file in files:
                    if file.endswith(".py"):
                        file_path = os.path.join(root, file)
                        with open(file_path, "r", encoding="utf-8") as f:
                            tree = ast.parse(f.read(), filename=file_path)

                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for alias in node.names:
                                    self.assertFalse(
                                        alias.name.startswith("telegram"),
                                        f"Forbidden telegram import '{alias.name}' in {file_path}",
                                    )
                            elif isinstance(node, ast.ImportFrom):
                                if node.module:
                                    self.assertFalse(
                                        node.module.startswith("telegram") or "interfaces.telegram" in node.module,
                                        f"Forbidden telegram from-import '{node.module}' in {file_path}",
                                    )


# ════════════════════════════════════════════════════════════
#  2. Telegram Progress Notifier Real Dispatch Tests
# ════════════════════════════════════════════════════════════

class TestTelegramProgressNotifierRealDispatch(unittest.TestCase):
    """
    Verifies real dispatch behavior of TelegramProgressNotifier:
    - Injected bot receives async send_message & send_document calls
    - notify_api_switch includes Persian reason translation
    - notify_job_completed delivers markdown output document
    - notify_job_failed notifies user
    """

    def setUp(self):
        self.mock_bot = MagicMock()
        self.mock_bot.send_message = AsyncMock()
        self.mock_bot.send_document = AsyncMock()
        self.mock_storage = MagicMock(spec=LocalStorageAdapter)
        self.notifier = TelegramProgressNotifier(
            bot=self.mock_bot,
            telegram_id_resolver=lambda uid: 998877 if uid == 1 else None,
            storage=self.mock_storage,
        )

    def test_notify_api_switch_dispatches_persian_reason_to_bot(self):
        self.notifier.notify_api_switch(
            user_id=1,
            job_id=42,
            old_label="Google Gemini",
            new_label="OpenAI GPT-4o",
            reason="rate_limit",
            page=3,
        )
        self.mock_bot.send_message.assert_called_once()
        args, kwargs = self.mock_bot.send_message.call_args
        self.assertEqual(kwargs["chat_id"], 998877)
        self.assertIn("محدودیت نرخ درخواست", kwargs["text"])
        self.assertIn("صفحه 3", kwargs["text"])

    def test_notify_job_completed_delivers_document(self):
        self.mock_storage.retrieve.return_value = b"# Completed Markdown\nText content."
        self.notifier.notify_job_completed(user_id=1, job_id=42, output_handle_uri="42/output.md")

        # 1 message + 2 documents (markdown + attachments)
        self.mock_bot.send_message.assert_called_once()
        self.assertEqual(self.mock_bot.send_document.call_count, 2)
        doc_kwargs = self.mock_bot.send_document.call_args_list[0][1]
        self.assertEqual(doc_kwargs["chat_id"], 998877)
        self.assertEqual(doc_kwargs["filename"], "job_42.md")


    def test_notify_job_failed_sends_alert(self):
        self.notifier.notify_job_failed(user_id=1, job_id=42, error_message="Fatal AI API crash")
        self.mock_bot.send_message.assert_called_once()
        msg_text = self.mock_bot.send_message.call_args[1]["text"]
        self.assertIn("متوقف شد", msg_text)
        self.assertIn("Fatal AI API crash", msg_text)

    def test_error_formatter_persian_translations(self):
        self.assertIn("سهمیه", format_telegram_error(QuotaExceededError("Limit reached")))
        self.assertIn("API", format_telegram_error(AIChainExhaustedError("All failed")))
        self.assertIn("یافت نشد", format_telegram_error(EntityNotFoundError("Job", 99)))
        self.assertIn("احراز هویت", format_telegram_error(AuthenticationError("Bad token")))
        self.assertIn("محدودیت نرخ", format_telegram_error(AIRateLimitError("Too many reqs", provider="google", status_code=429)))


# ════════════════════════════════════════════════════════════
#  3. End-to-End Workflows (Pipeline 1, Fallback, Pipeline 2, Donations)
# ════════════════════════════════════════════════════════════

class TestPipeline1AndFallbackEndToEndWorkflow(unittest.TestCase):
    """
    End-to-End Integration:
    PDF intake -> Submission -> Worker execution -> Multi-slot Fallback -> Completion -> Artifact Retrieval.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageAdapter(base_dir=self.temp_dir)
        self.uow_factory = MockUnitOfWorkFactory()

        # Seed User
        user = User(
            id=1,
            telegram_id=55555,
            username="tester",
            quota=QuotaAllocation(daily_limit=50, daily_pages_used=0, last_active_date=datetime.now().date()),

            preferences=UserPreferences(use_public_fallback=True, auto_retry=True, auto_pipeline2=False),
        )
        self.uow_factory.user_repo.save(user)

        # Seed Prompt
        p = Prompt(id=1, name="OCR Prompt", text="Convert to MD", prompt_type=PromptType.PIPELINE_1, is_default=True)
        self.uow_factory.prompt_repo.save(p)

        # Seed 2 API Slots
        self.slot1 = self.uow_factory.api_repo.save_private(1, "google", "k1", "Google Primary", "gemini-3.5-flash")
        self.slot2 = self.uow_factory.api_repo.save_public("openai", "k2", "OpenAI Backup", ["gpt-4o"])

        # Setup Document Processor Mock
        self.doc_processor = MagicMock()
        self.doc_processor.get_page_count.return_value = 2
        self.doc_processor.render_page_to_jpeg.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 10
        self.doc_processor.extract_and_crop_images.side_effect = lambda markdown_text, page_jpeg_bytes, job_id, *args, **kwargs: (markdown_text, [])

        # Setup AI Adapters & Executor with Fallback
        self.adapter1 = MagicMock(spec=AIProviderPort)
        self.adapter2 = MagicMock(spec=AIProviderPort)

        def adapter_factory(slot: ApiSlot):
            if slot.id == self.slot1.id:
                return self.adapter1
            return self.adapter2

        self.rate_limiter = MagicMock()
        self.ai_executor = RateLimitedAIExecutor(adapter_factory=adapter_factory, rate_limiter=self.rate_limiter)
        self.mock_notifier = MagicMock(spec=IProgressNotifier)

        self.submission_service = JobSubmissionService(self.uow_factory, self.storage, self.doc_processor)
        self.query_service = JobQueryService(self.uow_factory)
        self.execution_service = JobExecutionService(
            self.uow_factory,
            self.storage,
            self.doc_processor,
            self.ai_executor,
            self.mock_notifier,
        )
        self.artifact_service = ArtifactService(self.storage, self.uow_factory)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pipeline1_end_to_end_with_ai_fallback(self):
        # 1. Submit PDF Job
        cmd = SubmitJobCommand(
            user_id=1,
            filename="document.pdf",
            file_bytes=b"%PDF-1.4 mock pdf content",
            prompt_id=1,
            api_chain_ids=[self.slot1.id, self.slot2.id],
        )
        resp = self.submission_service.submit_job(cmd)
        self.assertEqual(resp.status, "pending")
        job_id = resp.id

        # 2. Configure AI Provider: Page 1 succeeds on Slot 1. Page 2 fails on Slot 1 with 429 -> falls back to Slot 2
        self.adapter1.generate_vision.side_effect = [
            AIResponse(content="# Page 1 MD"),
            AIRateLimitError("Quota exceeded", provider="google", status_code=429),
        ]
        self.adapter2.generate_vision.return_value = AIResponse(content="# Page 2 MD from OpenAI")

        # 3. Worker executes the job
        completed_job = self.execution_service.execute_next_job()

        # 4. Verifications
        self.assertIsNotNone(completed_job)
        self.assertEqual(completed_job.status, JobStatus.DONE)
        self.assertEqual(completed_job.processed_pages, 2)

        # Verify api_switch notification was triggered with reason="rate_limit" and page=2
        self.mock_notifier.notify_api_switch.assert_called_once_with(
            1, job_id, "Google Primary", "OpenAI Backup", "rate_limit", 2
        )

        # Verify notify_job_completed dispatched
        self.mock_notifier.notify_job_completed.assert_called_once()

        # 5. Retrieve output artifact via ArtifactService
        artifact_dto = self.artifact_service.get_artifact(user_id=1, job_id=job_id, artifact_type="output_markdown")
        self.assertIn("Page 1 MD", artifact_dto.data.decode("utf-8"))
        self.assertIn("Page 2 MD from OpenAI", artifact_dto.data.decode("utf-8"))

    @patch("handlers.user.get_app_container")
    def test_telegram_redelivery_handler_flow(self, mock_get_container):
        import io
        from handlers.user import redeliver_job

        mock_container = MagicMock()
        mock_user = UserDTO(
            id=1, telegram_id=55555, username="tester", is_admin=False,
            daily_limit=50, daily_pages_used=2, remaining_pages=48,
            auto_retry=True, auto_pipeline2=False, use_public_fallback=True,
            default_prompt_id=None, default_pipeline2_prompt_id=None,
        )
        mock_job = JobDetailDTO(
            id=1, user_id=1, file_name="doc.pdf", status="done",
            total_pages=2, processed_pages=2, prompt_id=None, prompt_text=None,
            active_api_label=None, api_switch_log=[], output_path="1/out.md",
            error_message=None, auto_pipeline2=False, created_at=None, updated_at=None,
        )
        mock_container.user_service.get_or_create_telegram_user.return_value = mock_user
        mock_container.job_query_service.get_job_detail.return_value = mock_job
        mock_container.artifact_service.get_job_artifact_stream.return_value = (
            io.BytesIO(b"# Redelivered markdown file"),
            "output_1.md",
            "text/markdown",
        )
        mock_get_container.return_value = mock_container

        mock_update = MagicMock()
        mock_update.effective_user.id = 55555
        mock_update.effective_user.username = "tester"
        mock_update.callback_query.data = "redeliver:1"
        mock_update.callback_query.answer = AsyncMock()
        mock_update.callback_query.edit_message_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.bot.send_document = AsyncMock()

        asyncio.run(redeliver_job(mock_update, mock_context))

        mock_container.artifact_service.get_job_artifact_stream.assert_called_once_with(
            job_id=1, user_id=1, artifact_type="output_markdown"
        )
        mock_context.bot.send_document.assert_called_once()





class TestPipeline2EndToEndWorkflow(unittest.TestCase):
    """
    End-to-End Integration for Pipeline 2:
    Completed Pipeline 1 Job -> Submit Pipeline 2 Job -> Worker Execution -> Output Artifact Created -> Status Done.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageAdapter(base_dir=self.temp_dir)
        self.uow_factory = MockUnitOfWorkFactory()

        # Seed User
        user = User(
            id=1,
            telegram_id=55555,
            username="tester",
            quota=QuotaAllocation(daily_limit=50, daily_pages_used=2, last_active_date=datetime.now().date()),

            preferences=UserPreferences(use_public_fallback=True, auto_retry=True, auto_pipeline2=False),
        )
        self.uow_factory.user_repo.save(user)

        # Seed Completed Pipeline 1 Job with stored markdown output
        output_handle = self.storage.store(
            job_id=10,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            filename="output_10.md",
            data=b"# Raw Extracted Markdown Content",
            mime_type="text/markdown",
        )
        source_job = Job(
            id=10,
            file_name="source.pdf",
            file_path="10/source.pdf",
            total_pages=1,
            processed_pages=1,
            status=JobStatus.DONE,
            output_path=output_handle.uri,
            api_chain=[],
        )
        self.uow_factory.job_repo.save(source_job)

        # Seed Pipeline 2 Prompt
        p2_prompt = Prompt(id=2, name="P2 Refine", text="Refine Persian typography", prompt_type=PromptType.PIPELINE_2, is_default=True)
        self.uow_factory.prompt_repo.save(p2_prompt)

        # Seed API Slot & AI Adapter
        self.slot = self.uow_factory.api_repo.save_private(1, "openai", "sk-test", "OpenAI Refiner", "gpt-4o")
        self.adapter = MagicMock(spec=AIProviderPort)
        self.adapter.generate_text.return_value = AIResponse(content="# Refined Persian Markdown Output")

        self.ai_executor = RateLimitedAIExecutor(adapter_factory=lambda slot: self.adapter, rate_limiter=MagicMock())
        self.mock_notifier = MagicMock(spec=IProgressNotifier)

        self.submission_service = JobSubmissionService(self.uow_factory, self.storage, MagicMock())
        self.execution_service = JobExecutionService(
            self.uow_factory,
            self.storage,
            MagicMock(),
            self.ai_executor,
            self.mock_notifier,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pipeline2_submission_and_execution_lifecycle(self):
        # 1. Submit Pipeline 2 Job
        p2_id = self.submission_service.submit_pipeline2_job(
            source_job_id=10,
            user_id=1,
            prompt_id=2,
            api_chain_ids=[self.slot.id],
        )
        self.assertIsNotNone(p2_id)

        # 2. Worker executes Pipeline 2 Job
        completed_p2 = self.execution_service.execute_next_pipeline2_job()

        # 3. Verifications
        self.assertIsNotNone(completed_p2)
        self.assertEqual(completed_p2.status, JobStatus.DONE)
        self.assertIsNotNone(completed_p2.output_path)

        # 4. Verify artifact content stored
        p2_handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=completed_p2.output_path,
            artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
            job_id=10,
            filename="p2_out.md",
        )
        p2_data = self.storage.retrieve(p2_handle).decode("utf-8")
        self.assertEqual(p2_data, "# Refined Persian Markdown Output")
        self.mock_notifier.notify_job_completed.assert_called_once()


class TestDonationAndPromptCRUDIntegration(unittest.TestCase):
    """
    Integration tests for:
    - User donation -> admin approve/reject
    - Prompt CRUD across pipeline1 & pipeline2 types
    """

    def setUp(self):
        self.uow_factory = MockUnitOfWorkFactory()
        self.api_service = ApiManagementService(self.uow_factory)
        self.prompt_service = PromptService(self.uow_factory)

    def test_donation_submission_and_approval_creates_public_api(self):
        # 1. User donates API
        cmd = DonateApiCommand(
            user_id=777,
            provider="google",
            api_key="donated_gemini_key",
            label="Donated Gemini",
            models=["gemini-3.5-flash"],
        )
        don_id = self.api_service.donate_api(cmd)
        self.assertIsNotNone(don_id)

        # 2. Admin approves donation
        slot_dto = self.api_service.approve_donation(donation_id=don_id, selected_model="gemini-3.5-flash")
        self.assertIsNotNone(slot_dto)
        self.assertEqual(slot_dto.slot_type, "byok")
        self.assertEqual(slot_dto.provider, "google")

        # 3. Verify public APIs list includes approved donation
        public_apis = self.api_service.list_public_apis()
        self.assertTrue(any(a.id == slot_dto.id for a in public_apis))

    def test_prompt_crud_and_canonical_types(self):
        # Create Pipeline 1 prompt
        p1 = self.prompt_service.create_prompt(CreatePromptCommand(
            name="P1 Prompt",
            text="Extract Markdown",
            prompt_type="pipeline_1",
            is_default=True,
        ))
        self.assertEqual(p1.prompt_type, "pipeline1")

        # Create Pipeline 2 prompt
        p2 = self.prompt_service.create_prompt(CreatePromptCommand(
            name="P2 Prompt",
            text="Refine Text",
            prompt_type="pipeline_2",
            is_default=False,
        ))
        self.assertEqual(p2.prompt_type, "pipeline2")

        # List Prompts
        p1_list = self.prompt_service.list_prompts("pipeline_1")
        p2_list = self.prompt_service.list_prompts("pipeline_2")
        self.assertEqual(len(p1_list), 1)
        self.assertEqual(len(p2_list), 1)

        # Quick Convert prompt
        self.prompt_service.set_quick_convert_prompt("Transcribe single image")
        self.assertEqual(self.prompt_service.get_quick_convert_prompt(), "Transcribe single image")


class TestCorrection2JobQueryServiceBoundary(unittest.TestCase):
    """
    Correction 2: Dedicated JobQueryService owns all read/query responsibilities.
    JobSubmissionService does not own queries (CQRS compliance).
    """

    def test_job_query_service_methods_exist(self):
        mock_uow_factory = MagicMock(spec=IUnitOfWorkFactory)
        query_service = JobQueryService(uow_factory=mock_uow_factory)

        self.assertTrue(callable(getattr(query_service, "list_user_jobs", None)))
        self.assertTrue(callable(getattr(query_service, "get_job_detail", None)))
        self.assertTrue(callable(getattr(query_service, "get_user_pending_job_count", None)))
        self.assertTrue(callable(getattr(query_service, "get_user_job_count", None)))
        self.assertTrue(callable(getattr(query_service, "get_queue_position", None)))
        self.assertTrue(callable(getattr(query_service, "get_paginated_history", None)))
        self.assertTrue(callable(getattr(query_service, "get_today_stats", None)))

    def test_job_submission_service_has_no_query_methods(self):
        self.assertFalse(hasattr(JobSubmissionService, "list_user_jobs"))
        self.assertFalse(hasattr(JobSubmissionService, "get_job_detail"))
        self.assertFalse(hasattr(JobSubmissionService, "get_paginated_history"))


class TestTelegramHandlerDelegation(unittest.TestCase):
    """
    Tests proving Telegram handlers delegate cleanly to Application Services via AppContainer.
    """

    @patch("handlers.common.get_app_container")
    def test_start_handler_delegates_to_user_service(self, mock_get_container):
        from handlers.common import start
        mock_container = MagicMock()
        mock_user = UserDTO(
            id=1,
            telegram_id=999,
            username="testuser",
            is_admin=False,
            daily_limit=50,
            daily_pages_used=0,
            remaining_pages=50,
            auto_retry=True,
            auto_pipeline2=False,
            use_public_fallback=True,
            default_prompt_id=None,
            default_pipeline2_prompt_id=None,
        )

        mock_container.user_service.get_or_create_telegram_user.return_value = mock_user
        mock_get_container.return_value = mock_container

        mock_update = MagicMock()
        mock_update.effective_user.id = 999
        mock_update.effective_user.username = "testuser"
        mock_update.message.reply_text = AsyncMock()

        asyncio.run(start(mock_update, MagicMock()))

        mock_container.user_service.get_or_create_telegram_user.assert_called_once_with(
            999, "testuser"
        )
        mock_update.message.reply_text.assert_called_once()

    @patch("services.worker.get_app_container")
    @patch("services.worker.acquire_lock", return_value=True)
    @patch("services.worker.release_lock")
    def test_worker_delegates_to_job_execution_service(self, mock_rel, mock_acq, mock_get_container):
        from services.worker import run
        mock_container = MagicMock()
        mock_container.job_execution_service.execute_next_job.return_value = None
        mock_container.job_execution_service.execute_next_pipeline2_job.return_value = None
        mock_get_container.return_value = mock_container

        run()

        mock_container.job_execution_service.execute_next_job.assert_called_once()
        mock_container.job_execution_service.execute_next_pipeline2_job.assert_called_once()
        mock_rel.assert_called_once()


if __name__ == "__main__":
    unittest.main()

