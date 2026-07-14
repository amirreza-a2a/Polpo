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

    # ─── تلاش با اولین API، سپس سوئیچ در صورت خطا ──────────
    result = None
    used_api = None
    for api_entry in chain:
        result = _call_vision_api(pil_img, prompt_text, api_entry)
        if result is not None:
            used_api = api_entry
            break

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


def _call_vision_api(pil_img: Image.Image, prompt: str, api_entry: dict) -> str | None:
    api_key  = api_entry["api_key"]
    provider = api_entry["provider"]
    model    = api_entry.get("selected_model") or get_default_model(provider)
    base_url = api_entry.get("base_url")

    if provider == "google":
        try:
            from google import genai
            client   = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model    = model,
                contents = [pil_img, prompt],
            )
            return response.text
        except Exception as e:
            print(f"    ⚠️ Google vision error (model={model}): {e}")
            return None

    if provider in ("openai", "openrouter") or base_url:
        try:
            import openai as openai_lib
            import base64

            buf = io.BytesIO()
            img = pil_img.convert("RGB") if pil_img.mode in ("RGBA", "P") else pil_img
            img.save(buf, format="JPEG", quality=90)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            client = openai_lib.OpenAI(**client_kwargs)

            response = client.chat.completions.create(
                model    = model,
                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"    ⚠️ {provider} vision error (model={model}): {e}")
            return None

    print(f"    ⚠️ provider ناشناخته: {provider}")
    return None