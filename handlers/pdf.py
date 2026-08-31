# ============================================================
#  handlers/pdf.py  —  Migrated to Application Services
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import MAX_PDF_SIZE_MB, MAX_QUEUE_PER_USER, SOURCE_ARCHIVE_CHANNEL_ID
from infrastructure.composition import get_app_container
from application.dto.job_dto import SubmitJobCommand
from interfaces.telegram.error_formatter import format_telegram_error


def get_or_create_user(*args, **kwargs):
    """تابع کمکی جهت حفظ سازگاری با تست‌های کاراکتریزاسیون."""
    container = get_app_container()
    return container.user_service.get_or_create_telegram_user(*args, **kwargs)


def reset_daily_pages_if_needed(*args, **kwargs):
    """تابع کمکی جهت حفظ سازگاری با تست‌های کاراکتریزاسیون."""
    pass


def count_user_pending_jobs(*args, **kwargs):
    """تابع کمکی جهت حفظ سازگاری با تست‌های کاراکتریزاسیون."""
    container = get_app_container()
    return container.job_query_service.get_user_pending_job_count(*args, **kwargs)


def get_user_private_apis(*args, **kwargs):
    """تابع کمکی جهت حفظ سازگاری با تست‌های کاراکتریزاسیون."""
    container = get_app_container()
    return container.api_service.list_user_apis(*args, **kwargs)



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

    container = get_app_container()
    user_dto = container.user_service.get_or_create_telegram_user(user_tg.id, user_tg.username)

    pending = container.job_query_service.get_user_pending_job_count(user_dto.id)
    if pending >= MAX_QUEUE_PER_USER:
        await update.message.reply_text(f"⏳ شما {pending} جاب در صف دارید. لطفاً صبر کنید.")
        return

    await update.message.reply_text("⏬ در حال دریافت فایل...")
    file_obj = await document.get_file()
    file_bytes = bytes(await file_obj.download_as_bytearray())

    try:
        total_pages = container.doc_processor.get_page_count(file_bytes)
    except Exception:
        await update.message.reply_text("❌ خطا در خواندن PDF. فایل معتبر نیست.")
        return

    private_apis = container.api_service.list_user_apis(user_dto.id, include_public=False)
    if not private_apis:
        pages_left = user_dto.remaining_pages
        if total_pages > pages_left:
            await update.message.reply_text(
                f"❌ این PDF دارای {total_pages} صفحه است، اما شما فقط "
                f"{pages_left} صفحه از سهمیه امروز باقی دارید."
            )
            return

    context.user_data["pending_pdf"] = {
        "file_bytes":       file_bytes,
        "file_name":        document.file_name,
        "total_pages":      total_pages,
        "user_id":          user_dto.id,
        "telegram_id":      user_tg.id,
        "telegram_file_id": document.file_id,
    }

    prompts = container.prompt_service.list_prompts("pipeline_1")
    if not prompts:
        await update.message.reply_text("❌ هیچ پرامپتی تنظیم نشده. با ادمین تماس بگیرید.")
        return

    buttons = [
        [InlineKeyboardButton(f"📝 {p.name}", callback_data=f"select_prompt:{p.id}")]
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

    container = get_app_container()
    user_id = context.user_data["pending_pdf"]["user_id"]
    private_apis = container.api_service.list_user_apis(user_id, include_public=False)

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
    user_id   = pdf_data["user_id"]
    prompt_id = context.user_data["selected_prompt_id"]

    container = get_app_container()
    apis = container.api_service.list_user_apis(user_id, include_public=include_public)
    if not include_private:
        apis = [a for a in apis if a.slot_type == "public"]
    elif not include_public:
        apis = [a for a in apis if a.slot_type == "private"]

    if not apis:
        await query.edit_message_text(
            "❌ هیچ API‌ای در دسترس نیست.\n"
            "یک API خصوصی اضافه کنید یا بعداً دوباره امتحان کنید."
        )
        return

    chain_ids = [a.id for a in apis]
    user_dto = container.user_service.get_user_by_id(user_id)

    try:
        cmd = SubmitJobCommand(
            user_id=user_id,
            filename=pdf_data["file_name"],
            file_bytes=pdf_data["file_bytes"],
            prompt_id=prompt_id,
            api_chain_ids=chain_ids,
            auto_pipeline2=user_dto.auto_pipeline2,
            pipeline2_prompt_id=user_dto.default_pipeline2_prompt_id,
        )
        job_resp = container.job_submission_service.submit_job(cmd)
        job_id = job_resp.id

        # آرشیو تلگرام
        try:
            import io
            file_buf = io.BytesIO(pdf_data["file_bytes"])
            file_buf.name = pdf_data["file_name"]
            await context.bot.send_document(
                chat_id  = SOURCE_ARCHIVE_CHANNEL_ID,
                document = file_buf,
                filename = pdf_data["file_name"],
                caption  = (
                    f"📄 Source PDF\n"
                    f"Job: #{job_id} | User: {pdf_data['telegram_id']}\n"
                    f"Pages: {pdf_data['total_pages']}"
                ),
            )
        except Exception as e:
            pass

        position = container.job_query_service.get_queue_position(job_id)
        await query.edit_message_text(
            f"✅ *جاب #{job_id} در صف قرار گرفت!*\n\n"
            f"📄 فایل: {pdf_data['file_name']}\n"
            f"📊 صفحات: {pdf_data['total_pages']}\n"
            f"📍 موقعیت در صف: {position}\n\n"
            "پس از پردازش فایل Markdown برای شما ارسال می‌شود.",
            parse_mode="Markdown",
        )

    except Exception as e:
        error_msg = format_telegram_error(e)
        await query.edit_message_text(error_msg)

    finally:
        context.user_data.pop("pending_pdf", None)
        context.user_data.pop("selected_prompt_id", None)