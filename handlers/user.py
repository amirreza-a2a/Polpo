# ============================================================
#  handlers/user.py  —  بخش تاریخچه و Resume اضافه شد
# ============================================================

import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import DAILY_PAGE_LIMIT, BACKUP_CHANNEL_ID
from database.models import (
    get_or_create_user, get_user, get_user_private_apis,
    add_private_api, delete_private_api, set_user_fallback,
    reset_daily_pages_if_needed,
    get_user_jobs_paginated, get_job_for_user,
    update_job_api_chain, requeue_job,
    set_auto_retry,
)
from services.api_manager import (
    detect_provider_and_models, get_default_base_url, build_api_chain,
)

# ─── states ──────────────────────────────────────────────
WAITING_API_KEY      = 1
WAITING_API_LABEL    = 2
WAITING_API_MODEL    = 3
WAITING_API_BASE_URL = 4

WAITING_DONATE_KEY      = 10
WAITING_DONATE_MODEL    = 11
WAITING_DONATE_BASE_URL = 12

HISTORY_PER_PAGE = 5

# ─── emoji وضعیت ─────────────────────────────────────────
STATUS_EMOJI = {
    "pending":    "⏳",
    "processing": "⚙️",
    "done":       "✅",
    "failed":     "❌",
    "paused":     "⏸",
}


# ════════════════════════════════════════════════════════════
#  پنل اصلی
# ════════════════════════════════════════════════════════════

async def show_panel(update, context):
    query   = update.callback_query
    user_tg = update.effective_user
    db_user = get_or_create_user(user_tg.id, user_tg.username)
    reset_daily_pages_if_needed(db_user["id"])

    pages_used = db_user["daily_pages_used"]
    bar        = "🟩" * int((pages_used / DAILY_PAGE_LIMIT) * 10) + "⬜" * (10 - int((pages_used / DAILY_PAGE_LIMIT) * 10))

    text = (
        f"🗂 *پنل شخصی شما*\n\n"
        f"📊 مصرف امروز: {pages_used}/{DAILY_PAGE_LIMIT} صفحه\n{bar}\n\n"
        f"🔄 Fallback: {'✅ فعال' if db_user['use_public_fallback'] else '🔒 غیرفعال'}\n"
        f"🔁 Auto-Retry: {'✅ فعال' if db_user.get('auto_retry') else '🔒 غیرفعال'}"
        + ("\n   _جاب‌های متوقف‌شده تا ۵ بار خودکار retry می‌شوند_" if db_user.get('auto_retry') else "")
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 API های من",      callback_data="panel_apis")],
        [InlineKeyboardButton("📋 تاریخچه",          callback_data="history_page:1")],
        [InlineKeyboardButton("🎁 اهدای API عمومی", callback_data="panel_donate")],
        [InlineKeyboardButton(
            "🔒 غیرفعال‌کردن Fallback" if db_user["use_public_fallback"] else "✅ فعال‌کردن Fallback",
            callback_data="toggle_fallback",
        )],
        [InlineKeyboardButton(
            "🔴 خاموش‌کردن Auto-Retry" if db_user.get("auto_retry") else "🔁 روشن‌کردن Auto-Retry",
            callback_data="toggle_auto_retry",
        )],
    ])

    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

# ════════════════════════════════════════════════════════════
#  تاریخچه صفحه‌بندی‌شده
# ════════════════════════════════════════════════════════════

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()

    page    = int(query.data.split(":")[1])
    db_user = get_or_create_user(user_tg.id)
    jobs, total = get_user_jobs_paginated(db_user["id"], page, HISTORY_PER_PAGE)
    total_pages = max(1, math.ceil(total / HISTORY_PER_PAGE))

    if not jobs:
        await query.edit_message_text(
            "📋 *تاریخچه*\n\nهیچ جابی نداشته‌اید.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")
            ]]),
            parse_mode="Markdown",
        )
        return

    lines = [f"📋 *تاریخچه* — صفحه {page}/{total_pages}\n"]
    for job in jobs:
        emoji = STATUS_EMOJI.get(job["status"], "❓")
        lines.append(
            f"{emoji} *#{job['id']}* {job['file_name']}\n"
            f"   {job['processed_pages']}/{job['total_pages']} صفحه — `{job['status']}`"
        )

    # دکمه‌های هر جاب
    action_buttons = []
    for job in jobs:
        row = []
        if job["status"] == "done" and job.get("backup_message_id"):
            row.append(InlineKeyboardButton(
                f"📥 #{job['id']}", callback_data=f"redeliver:{job['id']}"
            ))
        elif job["status"] in ("paused", "failed"):
            label = "▶️" if job["status"] == "paused" else "🔄"
            row.append(InlineKeyboardButton(
                f"{label} #{job['id']}", callback_data=f"resume_show:{job['id']}"
            ))
        if row:
            action_buttons.append(row)

    # دکمه‌های صفحه‌بندی
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"history_page:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"history_page:{page+1}"))

    buttons = action_buttons
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# ════════════════════════════════════════════════════════════
#  دریافت مجدد فایل‌های done
# ════════════════════════════════════════════════════════════

async def redeliver_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    job     = get_job_for_user(job_id, db_user["id"])

    if not job:
        await query.answer("❌ جاب یافت نشد.", show_alert=True)
        return

    if job["status"] != "done":
        await query.answer("❌ این جاب هنوز تمام نشده.", show_alert=True)
        return

    if not job.get("backup_message_id"):
        await query.answer("❌ بکاپ این جاب موجود نیست.", show_alert=True)
        return

    await query.edit_message_text(f"📥 در حال ارسال فایل‌های جاب #{job_id}...")

    try:
        # ─── ارسال MD با copy_message ──────────────────────
        await context.bot.copy_message(
            chat_id      = user_tg.id,
            from_chat_id = BACKUP_CHANNEL_ID,
            message_id   = job["backup_message_id"],
            caption      = f"📄 {job['file_name']} — دریافت مجدد",
        )

        # ─── ارسال ZIP (اگر بکاپ داشت) ────────────────────
        if job.get("backup_zip_msg_id"):
            await context.bot.copy_message(
                chat_id      = user_tg.id,
                from_chat_id = BACKUP_CHANNEL_ID,
                message_id   = job["backup_zip_msg_id"],
                caption      = f"🖼 تصاویر {job['file_name']}",
            )

        await query.edit_message_text(
            f"✅ فایل‌های جاب *#{job_id}* ارسال شد.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await query.edit_message_text(f"❌ خطا در ارسال: {e}")


# ════════════════════════════════════════════════════════════
#  Resume — نمایش گزینه‌ها
# ════════════════════════════════════════════════════════════

async def show_resume_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    job     = get_job_for_user(job_id, db_user["id"])

    if not job:
        await query.answer("❌ جاب یافت نشد.", show_alert=True)
        return

    status_text = "متوقف‌شده" if job["status"] == "paused" else "ناموفق"
    error_text  = f"\n⚠️ دلیل: {job['error_message']}" if job.get("error_message") else ""

    text = (
        f"🔄 *جاب #{job_id}* — {status_text}\n\n"
        f"📄 فایل: {job['file_name']}\n"
        f"📊 پیشرفت: {job['processed_pages']}/{job['total_pages']} صفحه پردازش شده"
        f"{error_text}\n\n"
        "چطور ادامه دهیم؟"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ادامه با تنظیمات قبلی",  callback_data=f"resume_same:{job_id}")],
        [InlineKeyboardButton("⚙️ تغییر API و ادامه",      callback_data=f"resume_new_api:{job_id}")],
        [InlineKeyboardButton("🔙 بازگشت",                 callback_data="history_page:1")],
    ])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  Resume — با تنظیمات قبلی
# ════════════════════════════════════════════════════════════

async def resume_same_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    job     = get_job_for_user(job_id, db_user["id"])

    if not job or job["status"] not in ("paused", "failed"):
        await query.answer("❌ این جاب قابل ادامه نیست.", show_alert=True)
        return

    await query.edit_message_text(f"⏳ در حال آماده‌سازی ادامه جاب #{job_id}...")

    # بررسی وجود فایل (worker این را هم بررسی می‌کند، اما اینجا اطلاع می‌دهیم)
    import os
    file_exists = os.path.exists(job.get("file_path") or "")
    file_note   = "" if file_exists else "\n📥 فایل از آرشیو دانلود می‌شود."

    requeue_job(job_id)

    await query.edit_message_text(
        f"✅ *جاب #{job_id} مجدداً در صف قرار گرفت!*\n\n"
        f"📊 ادامه از صفحه {job['processed_pages'] + 1}/{job['total_pages']}"
        f"{file_note}",
        parse_mode="Markdown",
    )


# ════════════════════════════════════════════════════════════
#  Resume — با API جدید
# ════════════════════════════════════════════════════════════

async def resume_new_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب نوع API برای resume."""
    query   = update.callback_query
    job_id  = int(query.data.split(":")[1])
    user_tg = update.effective_user
    await query.answer()

    db_user      = get_or_create_user(user_tg.id)
    private_apis = get_user_private_apis(db_user["id"])

    context.user_data["resume_job_id"] = job_id

    if private_apis:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 API خصوصی من",  callback_data=f"resume_api_src:{job_id}:private")],
            [InlineKeyboardButton("🌐 API عمومی",     callback_data=f"resume_api_src:{job_id}:public")],
            [InlineKeyboardButton("🔙 بازگشت",        callback_data=f"resume_show:{job_id}")],
        ])
        await query.edit_message_text(
            "⚙️ *انتخاب API برای ادامه پردازش:*",
            reply_markup=buttons,
            parse_mode="Markdown",
        )
    else:
        # API خصوصی ندارد → مستقیم با عمومی
        await _do_resume_with_chain(query, context, job_id, db_user["id"], use_public=True)


async def resume_api_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پس از انتخاب نوع API برای resume."""
    query   = update.callback_query
    parts   = query.data.split(":")
    job_id  = int(parts[1])
    source  = parts[2]   # private | public
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)

    if source == "private":
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، fallback به عمومی",
                                   callback_data=f"resume_fallback:{job_id}:yes")],
            [InlineKeyboardButton("🔒 فقط API خصوصی",
                                   callback_data=f"resume_fallback:{job_id}:no")],
        ])
        await query.edit_message_text(
            "اگر API خصوصی تمام شد، از API عمومی استفاده شود؟",
            reply_markup=buttons,
        )
    else:
        await _do_resume_with_chain(query, context, job_id, db_user["id"], use_public=True)


async def resume_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":")
    job_id  = int(parts[1])
    fb      = parts[2]
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    await _do_resume_with_chain(
        query, context, job_id, db_user["id"],
        use_public=(fb == "yes"),
    )


async def _do_resume_with_chain(query, context, job_id: int,
                                 user_db_id: int, use_public: bool):
    """chain جدید می‌سازد، جاب را آپدیت می‌کند، و requeue می‌کند."""
    chain = build_api_chain(user_db_id, use_public)
    if not chain:
        await query.edit_message_text(
            "❌ هیچ API‌ای در دسترس نیست. ابتدا یک API اضافه کنید."
        )
        return

    update_job_api_chain(job_id, chain)
    requeue_job(job_id)

    await query.edit_message_text(
        f"✅ *جاب #{job_id} با API جدید در صف قرار گرفت!*\n\n"
        f"API اول: `{chain[0]['label']}`",
        parse_mode="Markdown",
    )


# ════════════════════════════════════════════════════════════
#  بقیه توابع (از user.py قبلی — بدون تغییر)
# ════════════════════════════════════════════════════════════

async def show_my_apis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()
    db_user = get_or_create_user(user_tg.id)
    apis    = get_user_private_apis(db_user["id"])

    if not apis:
        text = "🔑 *API های شخصی*\n\nهنوز هیچ API‌ای اضافه نکرده‌اید."
    else:
        lines = ["🔑 *API های شخصی شما:*\n"]
        for api in apis:
            lines.append(
                f"*{api['chain_priority']}.* {api['label']}\n"
                f"   Provider: `{api['provider']}`\n"
                f"   مدل: `{api.get('selected_model') or '—'}`\n"
                f"   Base URL: `{api.get('base_url') or 'پیش‌فرض'}`\n"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="add_api")]]
    if apis:
        del_buttons = [
            InlineKeyboardButton(f"🗑 {a['label']}", callback_data=f"del_api:{a['id']}")
            for a in apis
        ]
        buttons += [del_buttons[i:i+2] for i in range(0, len(del_buttons), 2)]
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")])

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown"
    )


async def start_add_api(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *افزودن API — مرحله ۱/۴*\n\nAPI Key را ارسال کنید:\n\n_(برای لغو /cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_API_KEY


async def receive_api_key(update, context):
    api_key = update.message.text.strip()
    context.user_data["new_api_key"] = api_key
    await update.message.reply_text("⏳ در حال تشخیص provider...")
    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست. دوباره ارسال کنید:")
        return WAITING_API_KEY
    context.user_data["new_api_provider"] = provider
    context.user_data["new_api_models"]   = models
    await _send_model_selection(update.message, provider, models, step="۲/۴")
    return WAITING_API_MODEL


async def receive_api_model_callback(update, context):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل را تایپ کنید:", parse_mode="Markdown")
        return WAITING_API_MODEL
    context.user_data["new_api_model"] = choice
    await _send_base_url_prompt(query, context, edit=True, step="۳/۴")
    return WAITING_API_BASE_URL


async def receive_api_model_text(update, context):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return WAITING_API_MODEL
    context.user_data["new_api_model"] = model
    await _send_base_url_prompt(update.message, context, edit=False, step="۳/۴")
    return WAITING_API_BASE_URL


async def receive_api_base_url_callback(update, context):
    query    = update.callback_query
    provider = context.user_data.get("new_api_provider", "")
    await query.answer()
    context.user_data["new_api_base_url"] = get_default_base_url(provider)
    await query.edit_message_text(
        f"✅ Base URL: `{context.user_data['new_api_base_url'] or 'پیش‌فرض'}`\n\n"
        "📝 *مرحله ۴/۴* — یک نام وارد کنید:", parse_mode="Markdown",
    )
    return WAITING_API_LABEL


async def receive_api_base_url_text(update, context):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:",
                                        parse_mode="Markdown")
        return WAITING_API_BASE_URL
    context.user_data["new_api_base_url"] = url
    await update.message.reply_text(
        f"✅ Base URL: `{url}`\n\n📝 *مرحله ۴/۴* — یک نام وارد کنید:", parse_mode="Markdown"
    )
    return WAITING_API_LABEL


async def receive_api_label(update, context):
    label   = update.message.text.strip()[:100]
    user_tg = update.effective_user
    db_user = get_or_create_user(user_tg.id)
    priority = len(get_user_private_apis(db_user["id"])) + 1
    add_private_api(
        user_id=db_user["id"], api_key=context.user_data["new_api_key"],
        label=label, provider=context.user_data["new_api_provider"],
        models=context.user_data["new_api_models"], priority=priority,
        selected_model=context.user_data.get("new_api_model"),
        base_url=context.user_data.get("new_api_base_url"),
    )
    await update.message.reply_text(
        f"✅ *API اضافه شد!*\n🏷 {label} | 🤖 `{context.user_data.get('new_api_model')}`",
        parse_mode="Markdown",
    )
    for k in ("new_api_key","new_api_provider","new_api_models","new_api_model","new_api_base_url"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def start_donate(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎁 *اهدای API — مرحله ۱/۴*\n\nAPI Key را ارسال کنید:", parse_mode="Markdown",
    )
    return WAITING_DONATE_KEY


async def receive_donate_key(update, context):
    api_key = update.message.text.strip()
    await update.message.reply_text("⏳ در حال بررسی...")
    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست:")
        return WAITING_DONATE_KEY
    context.user_data["donate_key"]      = api_key
    context.user_data["donate_provider"] = provider
    context.user_data["donate_models"]   = models
    await _send_model_selection(update.message, provider, models,
                                prefix="donate", step="۲/۴")
    return WAITING_DONATE_MODEL


async def receive_donate_model_callback(update, context):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل را تایپ کنید:")
        return WAITING_DONATE_MODEL
    context.user_data["donate_model"] = choice
    await _send_base_url_prompt(query, context, edit=True, step="۳/۴",
                                provider=context.user_data["donate_provider"], model=choice)
    return WAITING_DONATE_BASE_URL


async def receive_donate_model_text(update, context):
    model = update.message.text.strip()
    context.user_data["donate_model"] = model
    await _send_base_url_prompt(update.message, context, edit=False, step="۳/۴",
                                provider=context.user_data["donate_provider"], model=model)
    return WAITING_DONATE_BASE_URL


async def receive_donate_base_url_callback(update, context):
    query    = update.callback_query
    provider = context.user_data.get("donate_provider", "")
    await query.answer()
    context.user_data["donate_base_url"] = get_default_base_url(provider)
    await query.edit_message_text("📨 در حال ارسال درخواست اهدا...", parse_mode="Markdown")
    await _finalize_donation(update, context)
    return ConversationHandler.END


async def receive_donate_base_url_text(update, context):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس معتبر وارد کنید:")
        return WAITING_DONATE_BASE_URL
    context.user_data["donate_base_url"] = url
    await update.message.reply_text("📨 در حال ارسال...")
    await _finalize_donation(update, context)
    return ConversationHandler.END


async def _finalize_donation(update, context):
    user_tg = update.effective_user
    data    = {
        "api_key": context.user_data["donate_key"],
        "provider": context.user_data["donate_provider"],
        "models": context.user_data["donate_models"],
        "selected_model": context.user_data.get("donate_model"),
        "base_url": context.user_data.get("donate_base_url"),
        "donated_by": user_tg.id,
    }
    context.bot_data[f"donation_{user_tg.id}"] = data
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"✅ درخواست ارسال شد!\n🤖 مدل: `{data['selected_model'] or '—'}`",
        parse_mode="Markdown",
    )
    from config import ADMIN_IDS
    masked = data["api_key"][:8] + "..." + data["api_key"][-4:]
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(f"🎁 *اهدای API*\nکاربر: `{user_tg.id}`\n"
                  f"Provider: `{data['provider']}`\nمدل: `{data['selected_model']}`\n"
                  f"Key: `{masked}`"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تایید", callback_data=f"admin_approve_donation:{user_tg.id}"),
                InlineKeyboardButton("❌ رد",    callback_data=f"admin_reject_donation:{user_tg.id}"),
            ]]),
            parse_mode="Markdown",
        )
    for k in ("donate_key","donate_provider","donate_models","donate_model","donate_base_url"):
        context.user_data.pop(k, None)


async def delete_api_confirm(update, context):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    db_user = get_or_create_user(update.effective_user.id)
    deleted = delete_private_api(api_id, db_user["id"])
    await query.answer("✅ API حذف شد." if deleted else "❌ یافت نشد.", show_alert=True)
    await show_my_apis(update, context)


async def toggle_fallback(update, context):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()
    db_user      = get_user(user_tg.id)
    new_fallback = not bool(db_user["use_public_fallback"])
    set_user_fallback(user_tg.id, new_fallback)
    await query.answer(f"Fallback: {'✅ فعال' if new_fallback else '🔒 غیرفعال'}", show_alert=True)
    await show_panel(update, context)


async def toggle_auto_retry(update, context):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()
    db_user   = get_user(user_tg.id)
    new_value = not bool(db_user.get("auto_retry"))
    set_auto_retry(user_tg.id, new_value)
    await query.answer(
        f"Auto-Retry: {'✅ فعال شد' if new_value else '🔴 غیرفعال شد'}",
        show_alert=True,
    )
    await show_panel(update, context)

async def cancel(update, context):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END


# ─── توابع کمکی مدل/base_url ─────────────────────────────

async def _send_model_selection(msg, provider, models, prefix="new_api", step="۲/۴"):
    shown   = models[:8]
    buttons = []
    for i in range(0, len(shown), 2):
        row = [InlineKeyboardButton(shown[i], callback_data=f"sel_model_{prefix}:{shown[i]}")]
        if i + 1 < len(shown):
            row.append(InlineKeyboardButton(shown[i+1], callback_data=f"sel_model_{prefix}:{shown[i+1]}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ وارد کردن دستی",
                                         callback_data=f"sel_model_{prefix}:__manual__")])
    preview = "\n".join(f"  • `{m}`" for m in models[:5])
    text    = f"✅ Provider: `{provider}`\n\n📋 *مرحله {step}:*\n{preview}"
    if hasattr(msg, 'edit_message_text'):
        await msg.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                     parse_mode="Markdown")
    else:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                              parse_mode="Markdown")


async def _send_base_url_prompt(msg, context, edit, step="۳/۴",
                                provider=None, model=None):
    if provider is None:
        provider = (context.user_data.get("new_api_provider")
                    or context.user_data.get("donate_provider", ""))
    if model is None:
        model = (context.user_data.get("new_api_model")
                 or context.user_data.get("donate_model", ""))
    default_url  = get_default_base_url(provider)
    default_text = f"`{default_url}`" if default_url else "توسط SDK"
    text = (
        f"🌐 *مرحله {step} — Base URL*\n\n"
        f"مدل: `{model}`\nپیش‌فرض: {default_text}\n\n"
        "آدرس سفارشی وارد کنید یا دکمه زیر:"
    )
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ استفاده از پیش‌فرض", callback_data="sel_base_url:__default__")
    ]])
    if edit and hasattr(msg, 'edit_message_text'):
        await msg.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
    else:
        target = msg if hasattr(msg, 'reply_text') else msg.message
        await target.reply_text(text, reply_markup=buttons, parse_mode="Markdown")