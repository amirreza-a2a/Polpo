# ============================================================
#  handlers/pipeline2.py  —  فلوی انتخاب پرامپت + API برای Pipeline 2
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.models import (
    get_or_create_user, get_job_for_user,
    get_active_pipeline2_prompts, get_user_private_apis,
    create_pipeline2_job,
)
from services.api_manager import build_api_chain, get_default_model


# ════════════════════════════════════════════════════════════
#  شروع فلو — از دکمه "✨ پردازش هوشمند" در تاریخچه
# ════════════════════════════════════════════════════════════

async def start_pipeline2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    job     = get_job_for_user(job_id, db_user["id"])

    if not job or job["status"] != "done":
        await query.answer("❌ این جاب آماده پردازش هوشمند نیست.", show_alert=True)
        return

    prompts = get_active_pipeline2_prompts()
    if not prompts:
        await query.answer("❌ هیچ پرامپتی برای پردازش هوشمند تنظیم نشده.", show_alert=True)
        return

    context.user_data["p2_source_job_id"] = job_id

    buttons = [
        [InlineKeyboardButton(f"✨ {p['title']}", callback_data=f"p2_select_prompt:{p['id']}")]
        for p in prompts
    ]
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="history_page:1")])

    await query.edit_message_text(
        f"✨ *پردازش هوشمند — {job['file_name']}*\n\n"
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

    db_user      = get_or_create_user(update.effective_user.id)
    private_apis = get_user_private_apis(db_user["id"])

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
    db_user     = get_or_create_user(user_tg.id)
    source_job_id = context.user_data.get("p2_source_job_id")
    prompt_id     = context.user_data.get("p2_prompt_id")

    source_job = get_job_for_user(source_job_id, db_user["id"])
    if not source_job:
        await query.edit_message_text("❌ جاب اصلی یافت نشد.")
        return

    chain = build_api_chain(db_user["id"], include_private, include_public)
    if not chain:
        await query.edit_message_text(
            "❌ هیچ API‌ای در دسترس نیست.\n"
            "یک API خصوصی اضافه کنید یا بعداً دوباره امتحان کنید."
        )
        return

    first_model = chain[0].get("selected_model") or get_default_model(chain[0]["provider"])

    p2_id = create_pipeline2_job(
        source_job_id = source_job_id,
        user_id       = db_user["id"],
        prompt_id     = prompt_id,
        api_chain     = chain,
        model         = first_model,
    )

    await query.edit_message_text(
        f"✅ *پردازش هوشمند #{p2_id} در صف قرار گرفت!*\n\n"
        f"📄 فایل اصلی: {source_job['file_name']}\n"
        f"🔌 API اول: `{chain[0]['label']}`\n\n"
        "پس از پردازش، فایل بهبودیافته برای شما ارسال می‌شود.",
        parse_mode="Markdown",
    )

    context.user_data.pop("p2_source_job_id", None)
    context.user_data.pop("p2_prompt_id", None)