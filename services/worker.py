# ============================================================
#  services/worker.py
# ============================================================

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WORKER_LOCK_FILE
from database.models import (
    get_next_pending_job, get_job, update_job_status,
    get_prompt_by_id, increment_user_pages,
    increment_job_retry_count, requeue_job_for_auto_retry,
)
from services.pdf_processor import process_job
from services.backup import run_backup
from utils.file_manager import get_output_path, has_attachments, create_attachments_zip

MAX_AUTO_RETRY = 5


def is_locked()    -> bool: return os.path.exists(WORKER_LOCK_FILE)
def acquire_lock():
    with open(WORKER_LOCK_FILE, "w") as f: f.write(str(os.getpid()))
def release_lock():
    if os.path.exists(WORKER_LOCK_FILE): os.remove(WORKER_LOCK_FILE)


async def _notify(telegram_id: int, text: str):
    from telegram import Bot
    from config import BOT_TOKEN
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.send_message(chat_id=telegram_id, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ خطا در ارسال پیام به {telegram_id}: {e}")


def notify_user(telegram_id: int, text: str):
    asyncio.run(_notify(telegram_id, text))


def on_api_switch(telegram_id: int, old: str, new: str):
    notify_user(telegram_id, f"🔄 API تغییر کرد\nاز: {old}\nبه: {new}")


# ─── بازیابی فایل PDF از آرشیو ───────────────────────────

async def _download_source_file(source_file_id: str, dest_path: str) -> bool:
    from telegram import Bot
    from config import BOT_TOKEN
    bot = Bot(token=BOT_TOKEN)
    try:
        tg_file = await bot.get_file(source_file_id)
        await tg_file.download_to_drive(dest_path)
        print(f"✅ فایل سورس دانلود شد: {dest_path}")
        return True
    except Exception as e:
        print(f"⚠️ خطا در دانلود فایل سورس: {e}")
        return False


def recover_source_file(job: dict) -> str | None:
    file_path = job.get("file_path")
    if file_path and os.path.exists(file_path):
        return file_path

    source_file_id = job.get("source_file_id")
    if not source_file_id:
        print(f"⚠️ جاب {job['id']}: هیچ source_file_id ذخیره نشده.")
        return None

    print(f"📥 جاب {job['id']}: فایل روی دیسک نیست، در حال دانلود از آرشیو...")
    from utils.file_manager import get_temp_path
    new_path = file_path or get_temp_path(f"resumed_job_{job['id']}.pdf")

    import nest_asyncio
    nest_asyncio.apply()
    success = asyncio.run(_download_source_file(source_file_id, new_path))
    return new_path if success else None


# ─── ارسال نتایج به کاربر ────────────────────────────────

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
                    caption  = f"📄 {job['file_name']} — پردازش کامل شد.",
                )
        except Exception as e:
            print(f"⚠️ خطا در ارسال MD به {telegram_id}: {e}")
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
                    caption  = f"🖼 تصاویر {job['file_name']}",
                )
        except Exception as e:
            print(f"⚠️ خطا در ارسال ZIP به {telegram_id}: {e}")
    asyncio.run(_send())


# ─── مدیریت توقف جاب (paused / failed) ───────────────────

def _handle_job_stopped(job: dict, user: dict):
    """
    وقتی جاب paused یا failed می‌شود تصمیم می‌گیرد:
    اگر auto_retry کاربر روشن است و retry_count < MAX → requeue خودکار
    در غیر این صورت → اطلاع برای resume دستی
    """
    fresh      = get_job(job["id"])
    status_msg = fresh.get("error_message") or "ظرفیت API تمام شد"

    if user.get("auto_retry"):
        current_retry = fresh.get("retry_count", 0)

        if current_retry < MAX_AUTO_RETRY:
            new_count = increment_job_retry_count(job["id"])
            requeue_job_for_auto_retry(job["id"])
            notify_user(
                user["telegram_id"],
                f"🔁 پردازش *{job['file_name']}* متوقف شد ولی به صورت "
                f"خودکار دوباره در صف قرار گرفت.\n"
                f"تلاش {new_count}/{MAX_AUTO_RETRY}\n"
                f"📊 پیشرفت: {job['processed_pages']}/{job['total_pages']} صفحه"
            )
            return

        # سقف تلاش‌ها پر شده
        notify_user(
            user["telegram_id"],
            f"❌ پردازش *{job['file_name']}* پس از {MAX_AUTO_RETRY} تلاش "
            f"خودکار هم ناموفق بود.\n"
            f"دلیل آخرین خطا: {status_msg}\n\n"
            "از پنل شخصی می‌توانید به‌صورت دستی با تنظیمات جدید ادامه دهید."
        )
        return

    # ─── auto_retry خاموش است — رفتار قبلی ────────────────
    if fresh["status"] == "paused":
        notify_user(
            user["telegram_id"],
            f"⏸ پردازش *{job['file_name']}* متوقف شد.\n"
            f"دلیل: {status_msg}\n\n"
            "از پنل شخصی می‌توانید ادامه پردازش را شروع کنید."
        )
    else:
        notify_user(
            user["telegram_id"],
            f"❌ پردازش *{job['file_name']}* با خطا مواجه شد.\n"
            f"دلیل: {status_msg}\n\n"
            "از پنل شخصی می‌توانید تلاش مجدد کنید."
        )


# ─── حلقه اصلی ───────────────────────────────────────────

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
        actual_path = recover_source_file(job)
        if not actual_path:
            update_job_status(
                job["id"], "failed",
                "فایل PDF قابل بازیابی نیست. لطفاً جاب را از نو ارسال کنید."
            )
            return

        job["file_path"] = actual_path
        update_job_status(job["id"], "processing")

        if not job.get("output_path"):
            job["output_path"] = get_output_path(job["id"])

        prompt = get_prompt_by_id(job["prompt_id"])
        if not prompt:
            update_job_status(job["id"], "failed", "پرامپت یافت نشد.")
            return

        user = get_user_by_id(job["user_id"])

        success = process_job(
            job            = job,
            prompt_text    = prompt["prompt_text"],
            notify_switch_callback = lambda uid, old, new: on_api_switch(
                user["telegram_id"], old, new
            ),
        )

        if success:
            update_job_status(job["id"], "done")
            increment_user_pages(job["user_id"], job["total_pages"])

            notify_user(
                user["telegram_id"],
                f"✅ پردازش *{job['file_name']}* تمام شد!\n"
                f"📊 {job['total_pages']} صفحه | در حال ارسال فایل‌ها..."
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
            _handle_job_stopped(job, user)

    except Exception as e:
        print(f"❌ خطای بحرانی در جاب {job['id']}: {e}")
        update_job_status(job["id"], "failed", str(e))
    finally:
        release_lock()


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