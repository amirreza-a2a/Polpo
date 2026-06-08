# ============================================================
#  services/worker.py  –  موتور صف (اجرا شده توسط Cron Job)
# ============================================================

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WORKER_LOCK_FILE
from database.models import (
    get_next_pending_job,
    get_job,
    update_job_status,
    get_prompt_by_id,
    increment_user_pages,
)
from services.pdf_processor import process_job
from services.backup import run_backup
from utils.file_manager import (
    get_output_path,
    has_attachments,
    create_attachments_zip,
)


def is_locked() -> bool:
    return os.path.exists(WORKER_LOCK_FILE)


def acquire_lock():
    with open(WORKER_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def release_lock():
    if os.path.exists(WORKER_LOCK_FILE):
        os.remove(WORKER_LOCK_FILE)


async def _notify_user(telegram_id: int, text: str):
    from telegram import Bot
    from config import BOT_TOKEN
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.send_message(chat_id=telegram_id, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ خطا در ارسال پیام به {telegram_id}: {e}")


def notify_user(telegram_id: int, text: str):
    asyncio.run(_notify_user(telegram_id, text))


def on_api_switch(user_telegram_id: int, old_label: str, new_label: str):
    notify_user(
        user_telegram_id,
        f"🔄 API تغییر کرد\nاز: {old_label}\nبه: {new_label}",
    )


def run():
    if is_locked():
        print("⏭️ Worker دیگری در حال اجراست.")
        return

    job = get_next_pending_job()
    if not job:
        print("✅ صف خالی است.")
        return

    acquire_lock()
    print(f"🚀 شروع پردازش جاب {job['id']}...")

    try:
        update_job_status(job["id"], "processing")

        if not job.get("output_path"):
            job["output_path"] = get_output_path(job["id"])

        prompt = get_prompt_by_id(job["prompt_id"])
        if not prompt:
            update_job_status(job["id"], "failed", "پرامپت یافت نشد.")
            return

        user = get_user_by_id(job["user_id"])

        success = process_job(
            job=job,
            prompt_text=prompt["prompt_text"],
            notify_switch_callback=lambda uid, old, new: on_api_switch(
                user["telegram_id"], old, new
            ),
        )

        if success:
            update_job_status(job["id"], "done")
            increment_user_pages(job["user_id"], job["total_pages"])

            notify_user(
                user["telegram_id"],
                f"✅ پردازش {job['file_name']} تمام شد!\n"
                f"📊 {job['total_pages']} صفحه پردازش شد.\n"
                "در حال ارسال فایل‌ها..."
            )

            _send_result_to_user(user["telegram_id"], job)

            if has_attachments(job["id"]):
                zip_path = create_attachments_zip(job["id"])
                if zip_path:
                    _send_zip_to_user(user["telegram_id"], job, zip_path)

            job["output_path"] = job.get("output_path") or get_output_path(job["id"])
            print(f"📦 در حال آپلود بکاپ جاب {job['id']}...")
            run_backup(job, user)

        else:
            fresh_job = get_job(job["id"])
            if fresh_job["status"] == "paused":
                notify_user(
                    user["telegram_id"],
                    f"⏸ پردازش {job['file_name']} متوقف شد.\n"
                    f"دلیل: {fresh_job.get('error_message', 'ظرفیت API تمام شد')}\n"
                    "لطفاً یک API اضافه کنید یا فردا دوباره امتحان کنید."
                )
            else:
                notify_user(
                    user["telegram_id"],
                    f"❌ پردازش {job['file_name']} با خطا مواجه شد.\n"
                    f"دلیل: {fresh_job.get('error_message', 'خطای ناشناخته')}"
                )

    except Exception as e:
        print(f"❌ خطای بحرانی در جاب {job['id']}: {e}")
        update_job_status(job["id"], "failed", str(e))
    finally:
        release_lock()


def _send_result_to_user(telegram_id: int, job: dict):
    async def _send():
        from telegram import Bot
        from config import BOT_TOKEN
        bot = Bot(token=BOT_TOKEN)
        try:
            with open(job["output_path"], "rb") as f:
                await bot.send_document(
                    chat_id  = telegram_id,
                    document = f,
                    filename = f"{job['file_name']}.md",
                    caption  = f"📄 {job['file_name']} - پردازش کامل شد.",
                )
        except Exception as e:
            print(f"⚠️ خطا در ارسال MD به کاربر {telegram_id}: {e}")
    asyncio.run(_send())


def _send_zip_to_user(telegram_id: int, job: dict, zip_path: str):
    async def _send():
        from telegram import Bot
        from config import BOT_TOKEN
        bot = Bot(token=BOT_TOKEN)
        try:
            with open(zip_path, "rb") as f:
                await bot.send_document(
                    chat_id  = telegram_id,
                    document = f,
                    filename = "attachments.zip",
                    caption  = f"🖼 تصاویر و نمودارهای {job['file_name']}",
                )
        except Exception as e:
            print(f"⚠️ خطا در ارسال ZIP به کاربر {telegram_id}: {e}")
    asyncio.run(_send())


def get_user_by_id(user_id: int) -> dict:
    from database.connection import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
    conn.close()
    return user


if __name__ == "__main__":
    run()
