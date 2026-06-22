# ============================================================
#  handlers/pdf.py
# ============================================================

import time
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import MAX_PDF_SIZE_MB, DAILY_PAGE_LIMIT, MAX_QUEUE_PER_USER, SOURCE_ARCHIVE_CHANNEL_ID
from database.models import (
    get_or_create_user, reset_daily_pages_if_needed,
    get_active_prompts, get_user_private_apis,
    create_job, count_user_pending_jobs, get_queue_position,
    update_job_source,
)
from services.api_manager import build_api_chain
from utils.file_manager import get_temp_path

import fitz


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg  = update.effective_user
    document = update.message.document

    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ لطفاً فقط فایل PDF ارسال کنید.")
        return

    size_mb = document.file_size / (1024 * 1024)
    if size_mb > MAX_PDF_SIZE_MB:
        await update.message.reply_text(
            f"❌ حجم فایل ({size_mb:.1f} MB) بیشتر از حد مجاز ({MAX_PDF_SIZE_MB} MB) است."
        )
        return

    db_user = get_or_create_user(user_tg.id, user_tg.username)
    reset_daily_pages_if_needed(db_user["id"])

    pending = count_user_pending_jobs(db_user["id"])
    if pending >= MAX_QUEUE_PER_USER:
        await update.message.reply_text(f"⏳ شما {pending} جاب در صف دارید. لطفاً صبر کنید.")
        return

    await update.message.reply_text("⏬ در حال دریافت فایل...")
    file_obj  = await document.get_file()
    temp_path = get_temp_path(f"{user_tg.id}_{int(time.time())}_{document.file_name}")
    await file_obj.download_to_drive(temp_path)

    try:
        doc         = fitz.open(temp_path)
        total_pages = len(doc)
        doc.close()
    except Exception:
        os.remove(temp_path)
        await update.message.reply_text("❌ خطا در خواندن PDF. فایل معتبر نیست.")
        return

    private_apis = get_user_private_apis(db_user["id"])
    if not private_apis:
        pages_left = DAILY_PAGE_LIMIT - db_user["daily_pages_used"]
        if total_pages > pages_left:
            os.remove(temp_path)
            await update.message.reply_text(
                f"❌ این PDF دارای {total_pages} صفحه است، اما شما فقط "
                f"{pages_left} صفحه از سهمیه امروز باقی دارید."
            )
            return

    context.user_data["pending_pdf"] = {
        "temp_path":        temp_path,
        "file_name":        document.file_name,
        "total_pages":      total_pages,
        "db_user":          db_user,
        "telegram_file_id": document.file_id,   # برای آرشیو سورس
    }

    prompts = get_active_prompts()
    if not prompts:
        await update.message.reply_text("❌ هیچ پرامپتی تنظیم نشده. با ادمین تماس بگیرید.")
        return

    buttons = [
        [InlineKeyboardButton(f"📝 {p['title']}", callback_data=f"select_prompt:{p['id']}")]
        for p in prompts
    ]
    await update.message.reply_text(
        f"✅ فایل *{document.file_name}* دریافت شد ({total_pages} صفحه)\n\n"
        "🔤 *پرامپت پردازش را انتخاب کنید:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def on_prompt_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    prompt_id = int(query.data.split(":")[1])
    await query.answer()
    context.user_data["selected_prompt_id"] = prompt_id

    db_user      = context.user_data["pending_pdf"]["db_user"]
    private_apis = get_user_private_apis(db_user["id"])

    if private_apis:
        buttons = [
            [InlineKeyboardButton("🔑 زنجیره API خصوصی من", callback_data="api_source:private")],
            [InlineKeyboardButton("🌐 API عمومی",            callback_data="api_source:public")],
        ]
        await query.edit_message_text(
            "🔌 *منبع API را انتخاب کنید:*",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    else:
        await _finalize_job(query, context, include_private=False, include_public=True)

async def on_api_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    source = query.data.split(":")[1]
    await query.answer()
    if source == "private":
        buttons = [
            [InlineKeyboardButton("✅ بله، اگر تمام شد از عمومی استفاده کن",
                                  callback_data="fallback:yes")],
            [InlineKeyboardButton("🔒 نه، فقط API خصوصی",
                                  callback_data="fallback:no")],
        ]
        await query.edit_message_text(
            "اگر API های خصوصی به limit رسیدند، از API عمومی استفاده شود؟",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        await _finalize_job(query, context, include_private=False, include_public=True)
            
        
async def on_fallback_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    fallback = query.data.split(":")[1]
    await query.answer()
    if fallback == "yes":
        await _finalize_job(query, context, include_private=True, include_public=True)
    else:
        await _finalize_job(query, context, include_private=True, include_public=False)
        

async def _finalize_job(query, context: ContextTypes.DEFAULT_TYPE,
                         include_private: bool, include_public: bool):
    pdf_data  = context.user_data["pending_pdf"]
    db_user   = pdf_data["db_user"]
    prompt_id = context.user_data["selected_prompt_id"]
    
    
    chain = build_api_chain(db_user["id"], include_private, include_public)
    if not chain:
        await query.edit_message_text(
            "❌ هیچ API‌ای در دسترس نیست.\n"
            "یک API خصوصی اضافه کنید یا بعداً دوباره امتحان کنید."
        )
        return

    first_model = chain[0].get("selected_model") or (chain[0]["models"] or ["gemini-3.5-flash"])[0]

    job_id = create_job(
        user_id     = db_user["id"],
        prompt_id   = prompt_id,
        file_path   = pdf_data["temp_path"],
        file_name   = pdf_data["file_name"],
        total_pages = pdf_data["total_pages"],
        api_chain   = chain,
        model       = first_model,
    )

    # ─── آرشیو PDF سورس در کانال ─────────────────────────
    try:
        with open(pdf_data["temp_path"], "rb") as f:
            archive_msg = await context.bot.send_document(
                chat_id  = SOURCE_ARCHIVE_CHANNEL_ID,
                document = f,
                filename = pdf_data["file_name"],
                caption  = (
                    f"📄 Source PDF\n"
                    f"Job: #{job_id} | User: {db_user['telegram_id']}\n"
                    f"Pages: {pdf_data['total_pages']}"
                ),
            )
        update_job_source(job_id, archive_msg.document.file_id, archive_msg.message_id)
        print(f"✅ سورس جاب {job_id} در آرشیو ذخیره شد.")
    except Exception as e:
        print(f"⚠️ خطا در آرشیو سورس جاب {job_id}: {e}")

    position = get_queue_position(job_id)
    await query.edit_message_text(
        f"✅ *جاب #{job_id} در صف قرار گرفت!*\n\n"
        f"📄 فایل: {pdf_data['file_name']}\n"
        f"📊 صفحات: {pdf_data['total_pages']}\n"
        f"📍 موقعیت در صف: {position}\n\n"
        "پس از پردازش فایل Markdown برای شما ارسال می‌شود.",
        parse_mode="Markdown",
    )

    context.user_data.pop("pending_pdf", None)
    context.user_data.pop("selected_prompt_id", None)