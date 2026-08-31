# ============================================================
#  handlers/quick_convert.py  —  Migrated to Application Services
# ============================================================

import io
from telegram import Update
from telegram.ext import ContextTypes

from infrastructure.composition import get_app_container
from application.dto.quick_convert_dto import QuickConvertCommand
from interfaces.telegram.error_formatter import format_telegram_error

TELEGRAM_MSG_LIMIT = 4096


async def handle_quick_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    container = get_app_container()
    user_dto = container.user_service.get_or_create_telegram_user(user_tg.id, user_tg.username)

    status_msg = await update.message.reply_text("⏳ در حال پردازش تصویر...")

    try:
        # دانلود بزرگ‌ترین سایز عکس
        photo = update.message.photo[-1]
        file_obj = await photo.get_file()
        photo_bytes = await file_obj.download_as_bytearray()

        cmd = QuickConvertCommand(
            user_id=user_dto.id,
            image_bytes=bytes(photo_bytes),
            mime_type="image/jpeg",
        )
        result_dto = container.quick_convert_service.convert_image(cmd)


        result = result_dto.markdown_content

        try:
            await status_msg.delete()
        except Exception:
            pass

        if len(result) <= TELEGRAM_MSG_LIMIT:
            await update.message.reply_text(result)
        else:
            file_buf = io.BytesIO(result.encode("utf-8"))
            file_buf.name = "converted.md"
            await update.message.reply_document(
                document=file_buf,
                filename="converted.md",
                caption="📄 خروجی طولانی بود و به‌صورت فایل ارسال شد.",
            )

    except Exception as e:
        error_msg = format_telegram_error(e)
        if status_msg:
            try:
                await status_msg.edit_text(error_msg)
            except Exception:
                await update.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)


def _call_vision_api(pil_img, prompt: str, api_entry: dict) -> str:
    """تابع کمکی جهت حفظ سازگاری با تست‌های کاراکتریزاسیون فازهای پیشین."""
    buf = io.BytesIO()
    img = pil_img.convert("RGB") if hasattr(pil_img, 'mode') and pil_img.mode in ("RGBA", "P") else pil_img
    img.save(buf, format="JPEG", quality=90)
    image_bytes = buf.getvalue()

    from services.ai_executor import execute_single_vision_request
    from core.ai.types import VisionPromptRequest
    req = VisionPromptRequest(
        prompt=prompt,
        image_bytes=image_bytes,
        mime_type="image/jpeg",
        model=api_entry.get("selected_model"),
    )
    response = execute_single_vision_request(req, api_entry)
    return response.content