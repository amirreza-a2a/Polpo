# ============================================================
#  handlers/pipeline2.py  —  Migrated to Application Services
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from infrastructure.composition import get_app_container
from interfaces.telegram.error_formatter import format_telegram_error


# ════════════════════════════════════════════════════════════
#  شروع فلو — از دکمه "✨ پردازش هوشمند" در تاریخچه
# ════════════════════════════════════════════════════════════

async def start_pipeline2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    container = get_app_container()
    user_dto = container.user_service.get_or_create_telegram_user(user_tg.id, user_tg.username)

    try:
        job = container.job_query_service.get_job_detail(job_id, user_dto.id)
    except Exception:
        await query.answer("❌ این جاب آماده پردازش هوشمند نیست.", show_alert=True)
        return

    if not job or job.status != "done":
        await query.answer("❌ این جاب آماده پردازش هوشمند نیست.", show_alert=True)
        return

    prompts = container.prompt_service.list_prompts("pipeline_2")
    if not prompts:
        await query.answer("❌ هیچ پرامپتی برای پردازش هوشمند تنظیم نشده.", show_alert=True)
        return

    context.user_data["p2_source_job_id"] = job_id

    buttons = [
        [InlineKeyboardButton(f"✨ {p.name}", callback_data=f"p2_select_prompt:{p.id}")]
        for p in prompts
    ]
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="history_page:1")])

    await query.edit_message_text(
        f"✨ *پردازش هوشمند — {job.file_name}*\n\n"
        "🔤 نوع پردازش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# ════════════════════════════════════════════════════════════
#  انتخاب پرامپت → انتخاب API
# ════════════════════════════════════════════════════════════

async def on_p2_prompt_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    prompt_id = int(query.data.split(":")[1])
    await query.answer()
    context.user_data["p2_prompt_id"] = prompt_id

    container = get_app_container()
    user_dto = container.user_service.get_or_create_telegram_user(update.effective_user.id, update.effective_user.username)
    private_apis = container.api_service.list_user_apis(user_dto.id, include_public=False)

    if private_apis:
        buttons = [
            [InlineKeyboardButton("🔑 زنجیره API خصوصی من", callback_data="p2_api_source:private")],
            [InlineKeyboardButton("🌐 API عمومی",            callback_data="p2_api_source:public")],
        ]
        await query.edit_message_text(
            "🔌 *منبع API را انتخاب کنید:*",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    else:
        await _finalize_p2_job(query, context, include_private=False, include_public=True)


async def on_p2_api_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    source = query.data.split(":")[1]
    await query.answer()
    if source == "private":
        buttons = [
            [InlineKeyboardButton("✅ بله، اگر تمام شد از عمومی استفاده کن",
                                  callback_data="p2_fallback:yes")],
            [InlineKeyboardButton("🔒 نه، فقط API خصوصی",
                                  callback_data="p2_fallback:no")],
        ]
        await query.edit_message_text(
            "اگر API های خصوصی به limit رسیدند، از API عمومی استفاده شود؟",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        await _finalize_p2_job(query, context, include_private=False, include_public=True)


async def on_p2_fallback_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    fallback = query.data.split(":")[1]
    await query.answer()
    if fallback == "yes":
        await _finalize_p2_job(query, context, include_private=True, include_public=True)
    else:
        await _finalize_p2_job(query, context, include_private=True, include_public=False)


# ════════════════════════════════════════════════════════════
#  ساخت نهایی pipeline2_job
# ════════════════════════════════════════════════════════════

async def _finalize_p2_job(query, context: ContextTypes.DEFAULT_TYPE,
                            include_private: bool, include_public: bool):
    user_tg     = query.from_user
    container   = get_app_container()
    user_dto    = container.user_service.get_or_create_telegram_user(user_tg.id, user_tg.username)
    source_job_id = context.user_data.get("p2_source_job_id")
    prompt_id     = context.user_data.get("p2_prompt_id")

    try:
        source_job = container.job_query_service.get_job_detail(source_job_id, user_dto.id)
    except Exception:
        await query.edit_message_text("❌ جاب اصلی یافت نشد.")
        return

    apis = container.api_service.list_user_apis(user_dto.id, include_public=include_public)
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

    try:
        p2_id = container.job_submission_service.submit_pipeline2_job(
            source_job_id=source_job_id,
            user_id=user_dto.id,
            prompt_id=prompt_id,
            api_chain_ids=chain_ids,
        )

        await query.edit_message_text(
            f"✅ *پردازش هوشمند #{p2_id} در صف قرار گرفت!*\n\n"
            f"📄 فایل اصلی: {source_job.file_name}\n"
            f"🔌 API اول: `{apis[0].label}`\n\n"
            "پس از پردازش، فایل بهبودیافته برای شما ارسال می‌شود.",
            parse_mode="Markdown",
        )
    except Exception as e:
        error_msg = format_telegram_error(e)
        await query.edit_message_text(error_msg)
    finally:
        context.user_data.pop("p2_source_job_id", None)
        context.user_data.pop("p2_prompt_id", None)