# ============================================================
#  handlers/pdf.py  –  دریافت PDF و راه‌اندازی جاب
# ============================================================
import time
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes


from config import MAX_PDF_SIZE_MB, DAILY_PAGE_LIMIT, MAX_QUEUE_PER_USER
from database.models import (
    get_or_create_user,
    reset_daily_pages_if_needed,
    get_active_prompts,
    get_default_prompt,
    get_user_private_apis,
    create_job,
    count_user_pending_jobs,
    get_queue_position,
)
from services.api_manager import build_api_chain
from utils.file_manager import get_temp_path

import fitz  # PyMuPDF برای شمارش صفحات


# ─── دریافت PDF ──────────────────────────────────────────

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg  = update.effective_user
    document = update.message.document

    # فقط PDF قبول کن
    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ لطفاً فقط فایل PDF ارسال کنید.")
        return

    # بررسی حجم
    size_mb = document.file_size / (1024 * 1024)
    if size_mb > MAX_PDF_SIZE_MB:
        await update.message.reply_text(
            f"❌ حجم فایل ({size_mb:.1f} MB) بیشتر از حد مجاز ({MAX_PDF_SIZE_MB} MB) است."
        )
        return

    # دریافت یا ساخت کاربر
    db_user = get_or_create_user(user_tg.id, user_tg.username)
    reset_daily_pages_if_needed(db_user["id"])

    # بررسی تعداد جاب‌های در صف
    pending = count_user_pending_jobs(db_user["id"])
    if pending >= MAX_QUEUE_PER_USER:
        await update.message.reply_text(
            f"⏳ شما {pending} جاب در صف دارید. لطفاً صبر کنید."
        )
        return

    # دانلود فایل موقت
    await update.message.reply_text("⏬ در حال دریافت فایل...")
    file_obj  = await document.get_file()
    temp_path = get_temp_path(f"{user_tg.id}_{int(time.time())}_{document.file_name}")
    await file_obj.download_to_drive(temp_path)

    # شمارش صفحات
    try:
        doc         = fitz.open(temp_path)
        total_pages = len(doc)
        doc.close()
    except Exception:
        os.remove(temp_path)
        await update.message.reply_text("❌ خطا در خواندن PDF. فایل معتبر نیست.")
        return
    
    # بررسی محدودیت روزانه - فقط اگر API خصوصی ندارد
    private_apis = get_user_private_apis(db_user["id"])
    if not private_apis:
        pages_left = DAILY_PAGE_LIMIT - db_user["daily_pages_used"]
        if total_pages > pages_left:
            os.remove(temp_path)
            await update.message.reply_text(
                f"❌ این PDF دارای {total_pages} صفحه است، اما شما فقط "
                f"{pages_left} صفحه از سهمیه امروز باقی دارید.\n"
                "برای پردازش بیشتر، یک API خصوصی اضافه کنید."
            )
            return
    
    # ذخیره اطلاعات موقت در context
    context.user_data["pending_pdf"] = {
        "temp_path":   temp_path,
        "file_name":   document.file_name,
        "total_pages": total_pages,
        "db_user":     db_user,
    }

    # نمایش لیست پرامپت‌ها
    prompts = get_active_prompts()
    if not prompts:
        await update.message.reply_text("❌ هیچ پرامپتی تنظیم نشده. با ادمین تماس بگیرید.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"📝 {p['title']}",
            callback_data=f"select_prompt:{p['id']}"
        )]
        for p in prompts
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"✅ فایل *{document.file_name}* دریافت شد ({total_pages} صفحه)\n\n"
        "🔤 *پرامپت پردازش را انتخاب کنید:*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


# ─── انتخاب پرامپت ───────────────────────────────────────

async def on_prompt_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    prompt_id = int(query.data.split(":")[1])
    await query.answer()

    context.user_data["selected_prompt_id"] = prompt_id

    # بررسی وجود API خصوصی
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
        # API خصوصی ندارد → مستقیم API عمومی
        await _finalize_job(query, context, use_public=True)


# ─── انتخاب منبع API ─────────────────────────────────────

async def on_api_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    source = query.data.split(":")[1]    # private | public
    await query.answer()

    use_public = (source == "public")

    if source == "private":
        # نمایش گزینه fallback
        buttons = [
            [InlineKeyboardButton("✅ بله، اگر تمام شد از عمومی استفاده کن",
                                  callback_data="fallback:yes")],
            [InlineKeyboardButton("🔒 نه، فقط API خصوصی",
                                  callback_data="fallback:no")],
        ]
        await query.edit_message_text(
            "اگر همه API های خصوصی شما به limit رسیدند، از API عمومی استفاده شود؟",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        await _finalize_job(query, context, use_public=True)


async def on_fallback_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    fallback = query.data.split(":")[1]    # yes | no
    await query.answer()

    use_public = (fallback == "yes")
    await _finalize_job(query, context, use_public=use_public)


# ─── ساخت جاب نهایی ─────────────────────────────────────

async def _finalize_job(query, context: ContextTypes.DEFAULT_TYPE, use_public: bool):
    pdf_data  = context.user_data["pending_pdf"]
    db_user   = pdf_data["db_user"]
    prompt_id = context.user_data["selected_prompt_id"]

    # ساخت زنجیره API
    chain = build_api_chain(db_user["id"], use_public)

    if not chain:
        await query.edit_message_text(
            "❌ هیچ API‌ای در دسترس نیست.\n"
            "یک API خصوصی اضافه کنید یا بعداً دوباره امتحان کنید."
        )
        return

    # انتخاب اولین مدل از اولین API
    first_model = (chain[0]["models"] or ["gemini-2.5-flash"])[0]

    # ساخت جاب
    job_id = create_job(
        user_id     = db_user["id"],
        prompt_id   = prompt_id,
        file_path   = pdf_data["temp_path"],
        file_name   = pdf_data["file_name"],
        total_pages = pdf_data["total_pages"],
        api_chain   = chain,
        model       = first_model,
    )

    position = get_queue_position(job_id)

    await query.edit_message_text(
        f"✅ *جاب #{job_id} در صف قرار گرفت!*\n\n"
        f"📄 فایل: {pdf_data['file_name']}\n"
        f"📊 صفحات: {pdf_data['total_pages']}\n"
        f"📍 موقعیت در صف: {position}\n\n"
        "پس از پردازش فایل Markdown برای شما ارسال می‌شود.",
        parse_mode="Markdown",
    )

    # پاکسازی context
    context.user_data.pop("pending_pdf", None)
    context.user_data.pop("selected_prompt_id", None)
