# ============================================================
#  services/worker.py
# ============================================================

import sys
import os
import asyncio

import errno
import fcntl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WORKER_LOCK_FILE
from database.models import (
    get_next_pending_job, get_job, update_job_status,
    get_prompt_by_id, increment_user_pages,
    increment_job_retry_count, requeue_job_for_auto_retry,
    # pipeline2
    get_next_pending_pipeline2_job, get_pipeline2_job,
    update_pipeline2_job_status, get_pipeline2_prompt_by_id,
    increment_pipeline2_retry_count, requeue_pipeline2_job,
    create_pipeline2_job, get_default_pipeline2_prompt,
)
from services.pdf_processor import process_job
from services.pipeline2_processor import process_pipeline2_job
from services.backup import run_backup
from services.api_manager import build_api_chain, get_default_model
from utils.file_manager import get_output_path, has_attachments, create_attachments_zip

MAX_AUTO_RETRY = 5

_lock_file_fd = None


def acquire_lock() -> bool:
    """
    قفل غیرمسدودکننده (non-blocking) با fcntl.flock روی فایل WORKER_LOCK_FILE دریافت می‌کند.
    اگر قفل موفق باشد True برمی‌گرداند.
    اگر Worker دیگری در حال حاضر قفل را داشته باشد False برمی‌گرداند.
    خطاهای واقعی فایل‌سیستم یا دسترسی (مانند PermissionError) بالا انداخته می‌شوند.
    سیستم‌عامل با پایان یا کرش پروسس، قفل را خودکار آزاد می‌کند.
    """
    global _lock_file_fd
    file_fd = None
    try:
        file_fd = open(WORKER_LOCK_FILE, "a+")
        fcntl.flock(file_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        file_fd.seek(0)
        file_fd.truncate()
        file_fd.write(f"{os.getpid()}\n")
        file_fd.flush()
        _lock_file_fd = file_fd
        return True
    except BlockingIOError:
        if file_fd is not None:
            try:
                file_fd.close()
            except Exception:
                pass
        return False
    except OSError as e:
        if file_fd is not None:
            try:
                file_fd.close()
            except Exception:
                pass
        if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        raise


def release_lock():
    """قفل فایل را آزاد می‌کند و فایل را می‌بندد."""
    global _lock_file_fd
    if _lock_file_fd is not None:
        try:
            fcntl.flock(_lock_file_fd.fileno(), fcntl.LOCK_UN)
            _lock_file_fd.close()
        except Exception:
            pass
        _lock_file_fd = None


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


def get_user_by_id(user_id: int) -> dict:
    from database.connection import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
    conn.close()
    return user


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


# ─── ارسال نتایج به کاربر (pipeline 1) ───────────────────

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


def _send_pipeline2_result_to_user(telegram_id: int, p2_job: dict, file_name: str):
    async def _send():
        from telegram import Bot
        from config import BOT_TOKEN
        bot = Bot(token=BOT_TOKEN)
        try:
            with open(p2_job["output_path"], "rb") as f:
                await bot.send_document(
                    chat_id  = telegram_id,
                    document = f,
                    filename = os.path.basename(p2_job["output_path"]),
                    caption  = f"✨ نسخه پردازش‌شده‌ی {file_name}",
                )
        except Exception as e:
            print(f"⚠️ خطا در ارسال خروجی pipeline2 به {telegram_id}: {e}")
    asyncio.run(_send())


# ─── مدیریت توقف جاب (paused / failed) — Pipeline 1 ──────

def _handle_job_stopped(job: dict, user: dict):
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

        notify_user(
            user["telegram_id"],
            f"❌ پردازش *{job['file_name']}* پس از {MAX_AUTO_RETRY} تلاش "
            f"خودکار هم ناموفق بود.\n"
            f"دلیل آخرین خطا: {status_msg}\n\n"
            "از پنل شخصی می‌توانید به‌صورت دستی با تنظیمات جدید ادامه دهید."
        )
        return

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


# ─── شروع خودکار Pipeline2 پس از اتمام Pipeline1 ─────────

def _maybe_trigger_auto_pipeline2(job: dict, user: dict):
    """اگر کاربر auto_pipeline2 را روشن کرده، یک pipeline2_job خودکار می‌سازد."""
    if not user.get("auto_pipeline2"):
        return

    prompt_id = user.get("auto_pipeline2_prompt_id")
    prompt    = get_pipeline2_prompt_by_id(prompt_id) if prompt_id else get_default_pipeline2_prompt()

    if not prompt:
        print(f"⚠️ کاربر {user['telegram_id']}: auto_pipeline2 روشن ولی پرامپتی یافت نشد.")
        return

    chain = build_api_chain(user["id"], include_private=True, include_public=bool(user.get("use_public_fallback")))
    if not chain:
        print(f"⚠️ کاربر {user['telegram_id']}: هیچ API‌ای برای auto_pipeline2 در دسترس نیست.")
        return

    first_model = chain[0].get("selected_model") or get_default_model(chain[0]["provider"])
    p2_id = create_pipeline2_job(
        source_job_id = job["id"],
        user_id       = user["id"],
        prompt_id     = prompt["id"],
        api_chain     = chain,
        model         = first_model,
    )
    print(f"✨ pipeline2_job #{p2_id} به صورت خودکار برای جاب {job['id']} ساخته شد.")
    notify_user(
        user["telegram_id"],
        f"✨ پردازش هوشمند *{job['file_name']}* به صورت خودکار شروع شد."
    )


# ─── Pipeline 1: حلقه پردازش ──────────────────────────────

def _run_pipeline1(job: dict) -> bool:
    """یک جاب pipeline1 را پردازش می‌کند. True اگر انجام شد (چه موفق چه نه)."""
    actual_path = recover_source_file(job)
    if not actual_path:
        update_job_status(
            job["id"], "failed",
            "فایل PDF قابل بازیابی نیست. لطفاً جاب را از نو ارسال کنید."
        )
        return True

    job["file_path"] = actual_path
    update_job_status(job["id"], "processing")

    if not job.get("output_path"):
        job["output_path"] = get_output_path(job["id"])

    prompt = get_prompt_by_id(job["prompt_id"])
    if not prompt:
        update_job_status(job["id"], "failed", "پرامپت یافت نشد.")
        return True

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

        # ─── trigger خودکار pipeline2 ───────────────────────
        fresh_job = get_job(job["id"])  # output_path تازه را بگیر
        _maybe_trigger_auto_pipeline2(fresh_job, user)

    else:
        _handle_job_stopped(job, user)

    return True


# ─── Pipeline 2: حلقه پردازش ──────────────────────────────

def _handle_pipeline2_stopped(p2_job: dict, user: dict, source_job: dict):
    fresh      = get_pipeline2_job(p2_job["id"])
    status_msg = fresh.get("error_message") or "ظرفیت API تمام شد"

    if user.get("auto_retry"):
        current_retry = fresh.get("retry_count", 0)
        if current_retry < MAX_AUTO_RETRY:
            new_count = increment_pipeline2_retry_count(p2_job["id"])
            requeue_pipeline2_job(p2_job["id"])
            notify_user(
                user["telegram_id"],
                f"🔁 پردازش هوشمند *{source_job['file_name']}* متوقف شد ولی "
                f"خودکار دوباره در صف قرار گرفت.\nتلاش {new_count}/{MAX_AUTO_RETRY}"
            )
            return
        notify_user(
            user["telegram_id"],
            f"❌ پردازش هوشمند *{source_job['file_name']}* پس از {MAX_AUTO_RETRY} "
            f"تلاش خودکار هم ناموفق بود.\nدلیل: {status_msg}\n\n"
            "از پنل شخصی می‌توانید به‌صورت دستی ادامه دهید."
        )
        return

    if fresh["status"] == "paused":
        notify_user(
            user["telegram_id"],
            f"⏸ پردازش هوشمند *{source_job['file_name']}* متوقف شد.\n"
            f"دلیل: {status_msg}\n\nاز پنل شخصی می‌توانید ادامه دهید."
        )
    else:
        notify_user(
            user["telegram_id"],
            f"❌ پردازش هوشمند *{source_job['file_name']}* با خطا مواجه شد.\n"
            f"دلیل: {status_msg}\n\nاز پنل شخصی می‌توانید تلاش مجدد کنید."
        )


def _run_pipeline2(p2_job: dict) -> bool:
    """یک جاب pipeline2 را پردازش می‌کند."""
    source_job = get_job(p2_job["source_job_id"])
    if not source_job:
        update_pipeline2_job_status(p2_job["id"], "failed", "جاب اصلی یافت نشد.")
        return True

    update_pipeline2_job_status(p2_job["id"], "processing")

    prompt = get_pipeline2_prompt_by_id(p2_job["prompt_id"])
    if not prompt:
        update_pipeline2_job_status(p2_job["id"], "failed", "پرامپت pipeline2 یافت نشد.")
        return True

    user = get_user_by_id(p2_job["user_id"])

    success = process_pipeline2_job(
        p2_job      = p2_job,
        source_job  = source_job,
        prompt_text = prompt["prompt_text"],
        notify_switch_callback = lambda uid, old, new: on_api_switch(
            user["telegram_id"], old, new
        ),
    )

    if success:
        update_pipeline2_job_status(p2_job["id"], "done")
        fresh_p2 = get_pipeline2_job(p2_job["id"])
        notify_user(
            user["telegram_id"],
            f"✅ پردازش هوشمند *{source_job['file_name']}* تمام شد!"
        )
        _send_pipeline2_result_to_user(user["telegram_id"], fresh_p2, source_job["file_name"])
    else:
        _handle_pipeline2_stopped(p2_job, user, source_job)

    return True


# ─── حلقه اصلی ───────────────────────────────────────────

def run():
    try:
        acquired = acquire_lock()
    except Exception as e:
        print(f"❌ خطای دسترسی یا فایل‌سیستم در فایل قفل Worker: {e}")
        sys.exit(1)

    if not acquired:
        print("⏭️ Worker دیگری در حال اجراست.")
        return

    try:
        # ─── اول صف Pipeline1 ──────────────────────────────
        job = get_next_pending_job()
        if job:
            print(f"🚀 شروع پردازش جاب {job['id']} (pipeline1)...")
            try:
                _run_pipeline1(job)
            except Exception as e:
                print(f"❌ خطای بحرانی در جاب {job['id']}: {e}")
                update_job_status(job["id"], "failed", str(e))
            return

        # ─── اگر خالی بود، صف Pipeline2 ────────────────────
        p2_job = get_next_pending_pipeline2_job()
        if p2_job:
            print(f"🚀 شروع پردازش pipeline2_job {p2_job['id']}...")
            try:
                _run_pipeline2(p2_job)
            except Exception as e:
                print(f"❌ خطای بحرانی در pipeline2_job {p2_job['id']}: {e}")
                update_pipeline2_job_status(p2_job["id"], "failed", str(e))
            return

        print("✅ هر دو صف خالی هستند.")

    finally:
        release_lock()


if __name__ == "__main__":
    run()