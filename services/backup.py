# ============================================================
#  services/backup.py  –  بکاپ به چنل تلگرام و حذف فایل‌ها
# ============================================================

import asyncio
from telegram import Bot
from config import BOT_TOKEN, BACKUP_CHANNEL_ID
from database.models import update_job_backup
from utils.file_manager import cleanup_job_files, create_attachments_zip, has_attachments


async def upload_and_cleanup(job: dict, user_info: dict):
    """
    فایل MD و در صورت وجود zip attachments را به چنل بکاپ آپلود می‌کند،
    سپس فایل‌های موقت را حذف می‌کند.
    """
    output_path = job.get("output_path")
    if not output_path:
        return

    bot = Bot(token=BOT_TOKEN)

    caption = (
        f"Job ID: {job['id']}\n"
        f"File: {job['file_name']}\n"
        f"Pages: {job['processed_pages']}\n"
        f"User: {user_info.get('telegram_id')}"
    )

    try:
        # آپلود فایل MD
        with open(output_path, "rb") as f:
            message = await bot.send_document(
                chat_id  = BACKUP_CHANNEL_ID,
                document = f,
                filename = f"job_{job['id']}_{job['file_name']}.md",
                caption  = caption,
            )
        update_job_backup(job["id"], message.message_id)
        print(f"✅ بکاپ MD جاب {job['id']} آپلود شد.")

        # آپلود zip attachments (اگر وجود داشت)
        if has_attachments(job["id"]):
            zip_path = create_attachments_zip(job["id"])
            if zip_path:
                with open(zip_path, "rb") as f:
                    await bot.send_document(
                        chat_id  = BACKUP_CHANNEL_ID,
                        document = f,
                        filename = f"job_{job['id']}_attachments.zip",
                        caption  = f"Attachments for Job {job['id']}",
                    )
                print(f"✅ بکاپ ZIP جاب {job['id']} آپلود شد.")

    except Exception as e:
        print(f"⚠️ خطا در آپلود بکاپ جاب {job['id']}: {e}")

    # حذف فایل‌های موقت صرف‌نظر از نتیجه آپلود
    cleanup_job_files(job)
    print(f"🗑️ فایل‌های موقت جاب {job['id']} حذف شدند.")


def run_backup(job: dict, user_info: dict):
    """نسخه sync برای فراخوانی از Worker."""
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(upload_and_cleanup(job, user_info))
