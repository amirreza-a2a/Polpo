# ============================================================
#  handlers/quick_convert.py
#  تبدیل سریع یک تصویر منفرد به Markdown — بدون دکمه، بدون صف
# ============================================================

import io
from telegram import Update
from telegram.ext import ContextTypes
from PIL import Image

from config import DAILY_PAGE_LIMIT
from database.models import (
    get_or_create_user, reset_daily_pages_if_needed,
    increment_user_pages, get_quick_convert_prompt,
)
from services.api_manager import build_api_chain, get_default_model
from services.ai_executor import execute_vision_with_fallback, execute_single_vision_request
from core.ai.types import VisionPromptRequest

TELEGRAM_MSG_LIMIT = 4096


async def handle_quick_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    db_user = get_or_create_user(user_tg.id, user_tg.username)
    reset_daily_pages_if_needed(db_user["id"])

    if db_user["daily_pages_used"] >= DAILY_PAGE_LIMIT:
        await update.message.reply_text(
            f"❌ سهمیه‌ی روزانه‌ی شما ({DAILY_PAGE_LIMIT} صفحه) تمام شده است."
        )
        return

    prompt_text = get_quick_convert_prompt()
    if not prompt_text:
        await update.message.reply_text(
            "❌ پرامپت تبدیل سریع هنوز توسط ادمین تنظیم نشده است."
        )
        return

    chain = build_api_chain(
        db_user["id"],
        include_private=True,
        include_public=bool(db_user.get("use_public_fallback")),
    )
    if not chain:
        await update.message.reply_text(
            "❌ هیچ API‌ای در دسترس نیست. یک API خصوصی اضافه کنید."
        )
        return

    status_msg = await update.message.reply_text("⏳ در حال پردازش تصویر...")

    # ─── دانلود بزرگ‌ترین سایز عکس ──────────────────────────
    photo = update.message.photo[-1]
    file_obj = await photo.get_file()
    photo_bytes = await file_obj.download_as_bytearray()
    pil_img = Image.open(io.BytesIO(bytes(photo_bytes)))

    # ─── پردازش تصویر با اعمال متمرکز Fallback در ai_executor ──

    buf = io.BytesIO()
    img = pil_img.convert("RGB") if pil_img.mode in ("RGBA", "P") else pil_img
    img.save(buf, format="JPEG", quality=90)
    image_bytes = buf.getvalue()

    job_data = {
        "id": None,
        "user_id": db_user["id"],
        "api_chain": chain,
        "current_api_index": 0,
        "api_switch_log": [],
    }

    result, used_api = execute_vision_with_fallback(
        job=job_data,
        image_bytes=image_bytes,
        prompt=prompt_text,
        at_page=1,
        mime_type="image/jpeg",
    )

    if result is None:
        await status_msg.edit_text(
            "❌ پردازش تصویر با هیچ‌کدام از API‌های موجود موفق نبود.\n"
            "لطفاً بعداً دوباره امتحان کنید."
        )
        return

    increment_user_pages(db_user["id"], 1)

    # ─── ارسال نتیجه ─────────────────────────────────────────
    try:
        await status_msg.delete()
    except Exception:
        pass

    if len(result) <= TELEGRAM_MSG_LIMIT:
        await update.message.reply_text(result)
    else:
        # استثنا: خروجی خیلی طولانی → فایل
        import io as _io
        file_buf = _io.BytesIO(result.encode("utf-8"))
        file_buf.name = "converted.md"
        await update.message.reply_document(
            document=file_buf,
            filename="converted.md",
            caption="📄 خروجی طولانی بود و به‌صورت فایل ارسال شد.",
        )


def _call_vision_api(pil_img: Image.Image, prompt: str, api_entry: dict) -> str:
    buf = io.BytesIO()
    img = pil_img.convert("RGB") if pil_img.mode in ("RGBA", "P") else pil_img
    img.save(buf, format="JPEG", quality=90)
    image_bytes = buf.getvalue()

    req = VisionPromptRequest(
        prompt=prompt,
        image_bytes=image_bytes,
        mime_type="image/jpeg",
        model=api_entry.get("selected_model"),
    )
    response = execute_single_vision_request(req, api_entry)
    return response.content