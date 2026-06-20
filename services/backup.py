# ============================================================
#  services/backup.py
# ============================================================

import asyncio
from telegram import Bot
from config import BOT_TOKEN, BACKUP_CHANNEL_ID
from database.models import update_job_backup
from utils.file_manager import cleanup_job_files, create_attachments_zip, has_attachments


async def upload_and_cleanup(job: dict, user_info: dict):
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

    md_msg_id  = None
    zip_msg_id = None

    try:
        # ─── آپلود MD ─────────────────────────────────────
        with open(output_path, "rb") as f:
            msg = await bot.send_document(
                chat_id  = BACKUP_CHANNEL_ID,
                document = f,
                filename = f"job_{job['id']}_{job['file_name']}.md",
                caption  = caption,
            )
        md_msg_id = msg.message_id
        print(f"✅ بکاپ MD جاب {job['id']} آپلود شد (msg_id={md_msg_id})")

        # ─── آپلود ZIP ────────────────────────────────────
        if has_attachments(job["id"]):
            zip_path = create_attachments_zip(job["id"])
            if zip_path:
                with open(zip_path, "rb") as f:
                    zip_msg = await bot.send_document(
                        chat_id  = BACKUP_CHANNEL_ID,
                        document = f,
                        filename = f"job_{job['id']}_attachments.zip",
                        caption  = f"Attachments for Job {job['id']}",
                    )
                zip_msg_id = zip_msg.message_id
                print(f"✅ بکاپ ZIP جاب {job['id']} آپلود شد (msg_id={zip_msg_id})")

    except Exception as e:
        print(f"⚠️ خطا در آپلود بکاپ جاب {job['id']}: {e}")

    # ذخیره هر دو شناسه (حتی اگر یکی None باشد)
    if md_msg_id:
        update_job_backup(job["id"], md_msg_id, zip_msg_id)

    cleanup_job_files(job)
    print(f"🗑️ فایل‌های موقت جاب {job['id']} حذف شدند.")


def run_backup(job: dict, user_info: dict):
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(upload_and_cleanup(job, user_info))