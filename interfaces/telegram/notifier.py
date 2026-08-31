# ============================================================
#  interfaces/telegram/notifier.py
#  Telegram Progress & Completion Notifier Driving Adapter
# ============================================================

import io
import asyncio
import logging
from typing import Callable, Optional
from application.ports.notifier import IProgressNotifier
from application.ports.storage import IArtifactStorage
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from config import BOT_TOKEN

logger = logging.getLogger("polpot.telegram.notifier")


class TelegramProgressNotifier(IProgressNotifier):
    """
    آداپتور اطلاع‌رسانی و ارسال نتایج تلگرام.
    رویدادهای دامنه را به پیام‌های کاربری و اسناد Markdown/ZIP تلگرام تبدیل و ارسال می‌کند.
    فاقد هرگونه منطق بیزینس یا دسترسی مستقیم به پایگاه‌داده.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        telegram_id_resolver: Optional[Callable[[int], Optional[int]]] = None,
        bot: Optional[object] = None,
        storage: Optional[IArtifactStorage] = None,
    ):
        self.bot_token = bot_token or BOT_TOKEN
        self.telegram_id_resolver = telegram_id_resolver
        self.bot = bot
        self.storage = storage

    def _get_bot(self):
        if self.bot is not None:
            return self.bot
        if self.bot_token:
            from telegram import Bot
            return Bot(token=self.bot_token)
        return None

    def _resolve_tg_id(self, user_id: int) -> Optional[int]:
        if self.telegram_id_resolver:
            try:
                return self.telegram_id_resolver(user_id)
            except Exception as e:
                logger.warning(f"Failed to resolve telegram_id for user_id={user_id}: {e}")
        return user_id if user_id > 1000 else None

    def _dispatch_text_sync(self, telegram_id: int, text: str) -> None:
        bot = self._get_bot()
        if not bot:
            return

        async def _send():
            try:
                await bot.send_message(chat_id=telegram_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Failed to send Telegram message to {telegram_id}: {e}")

        self._run_coro(_send())

    def _dispatch_document_sync(self, telegram_id: int, document_bytes: bytes, filename: str, caption: str = "") -> None:
        bot = self._get_bot()
        if not bot:
            return

        async def _send():
            try:
                buf = io.BytesIO(document_bytes)
                buf.name = filename
                await bot.send_document(
                    chat_id=telegram_id,
                    document=buf,
                    filename=filename,
                    caption=caption,
                )
            except Exception as e:
                logger.warning(f"Failed to send Telegram document {filename} to {telegram_id}: {e}")

        self._run_coro(_send())

    def _run_coro(self, coro) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(coro)
            else:
                loop.run_until_complete(coro)
        except Exception:
            try:
                asyncio.run(coro)
            except Exception as e:
                logger.warning(f"Could not execute Telegram dispatch coroutine: {e}")

    def notify_progress(self, user_id: int, job_id: int, processed_pages: int, total_pages: int) -> None:
        logger.debug(f"[Job {job_id}] Progress: {processed_pages}/{total_pages} (User {user_id})")

    def notify_api_switch(
        self,
        user_id: int,
        job_id: int,
        old_label: str,
        new_label: str,
        reason: str,
        page: int,
    ) -> None:
        tg_id = self._resolve_tg_id(user_id)
        if tg_id:
            reason_fa = {
                "rate_limit": "محدودیت نرخ درخواست (Rate Limit)",
                "timeout": "پایان مهلت پاسخگویی (Timeout)",
                "provider_unavailable": "عدم دسترسی به ارائه‌دهنده",
                "auth_error": "خطای کلید API",
                "model_not_found": "مدل یافت نشد",
            }.get(reason, reason)

            text = (
                f"🔄 *تغییر کلید هوش مصنوعی در صفحه {page}*\n\n"
                f"از: `{old_label}`\n"
                f"به: `{new_label}`\n"
                f"دلیل: {reason_fa}"
            )
            self._dispatch_text_sync(tg_id, text)

    def notify_job_completed(self, user_id: int, job_id: int, output_handle_uri: str) -> None:
        tg_id = self._resolve_tg_id(user_id)
        if not tg_id:
            return

        text = f"✅ پردازش کار #{job_id} با موفقیت به پایان رسید.\nدر حال ارسال فایل خروجی..."
        self._dispatch_text_sync(tg_id, text)

        # ارسال سند مارک‌داون خروجی در صورت وجود مخزن آرتیفکت
        if self.storage and output_handle_uri:
            try:
                handle = ArtifactHandle(
                    storage_backend=StorageBackendType.LOCAL_FS,
                    uri=output_handle_uri,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    job_id=job_id,
                    filename=f"job_{job_id}.md",
                )
                md_bytes = self.storage.retrieve(handle)
                self._dispatch_document_sync(
                    telegram_id=tg_id,
                    document_bytes=md_bytes,
                    filename=f"job_{job_id}.md",
                    caption=f"📄 فایل خروجی کار #{job_id}",
                )
            except Exception as e:
                logger.warning(f"Could not deliver completed markdown artifact to {tg_id}: {e}")

            # ارسال فایل تصاویر فشرده (ZIP) در صورت وجود
            try:
                zip_handle = ArtifactHandle(
                    storage_backend=StorageBackendType.LOCAL_FS,
                    uri=f"{job_id}/attachments.zip",
                    artifact_type=ArtifactType.ATTACHMENTS_ZIP,
                    job_id=job_id,
                    filename="attachments.zip",
                )
                zip_bytes = self.storage.retrieve(zip_handle)
                self._dispatch_document_sync(
                    telegram_id=tg_id,
                    document_bytes=zip_bytes,
                    filename="attachments.zip",
                    caption=f"🖼 تصاویر استخراج‌شده کار #{job_id}",
                )
            except Exception:
                pass

    def notify_job_failed(self, user_id: int, job_id: int, error_message: str) -> None:
        tg_id = self._resolve_tg_id(user_id)
        if tg_id:
            text = (
                f"❌ پردازش کار #{job_id} متوقف شد.\n"
                f"علت: {error_message}\n\n"
                "می‌توانید از طریق پنل شخصی وضعیت کار را بررسی یا تلاش مجدد کنید."
            )
            self._dispatch_text_sync(tg_id, text)
