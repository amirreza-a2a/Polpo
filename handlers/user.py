# ============================================================
#  handlers/user.py
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import DAILY_PAGE_LIMIT
from database.models import (
    get_or_create_user, get_user, get_user_private_apis,
    add_private_api, delete_private_api, set_user_fallback,
    get_user_jobs, reset_daily_pages_if_needed,
)
from services.api_manager import detect_provider_and_models, get_default_base_url

# ─── states ──────────────────────────────────────────────
WAITING_API_KEY      = 1
WAITING_API_LABEL    = 2
WAITING_API_MODEL    = 3
WAITING_API_BASE_URL = 4

WAITING_DONATE_KEY      = 10
WAITING_DONATE_MODEL    = 11
WAITING_DONATE_BASE_URL = 12


# ════════════════════════════════════════════════════════════
#  پنل اصلی
# ════════════════════════════════════════════════════════════

async def show_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user

    db_user = get_or_create_user(user_tg.id, user_tg.username)
    reset_daily_pages_if_needed(db_user["id"])

    pages_used = db_user["daily_pages_used"]
    bar_filled = int((pages_used / DAILY_PAGE_LIMIT) * 10)
    bar        = "🟩" * bar_filled + "⬜" * (10 - bar_filled)

    text = (
        f"🗂 *پنل شخصی شما*\n\n"
        f"📊 مصرف امروز: {pages_used}/{DAILY_PAGE_LIMIT} صفحه\n"
        f"{bar}\n\n"
        f"🔄 Fallback به API عمومی: "
        f"{'✅ فعال' if db_user['use_public_fallback'] else '🔒 غیرفعال'}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 API های من",      callback_data="panel_apis")],
        [InlineKeyboardButton("📋 تاریخچه",          callback_data="panel_history")],
        [InlineKeyboardButton("🎁 اهدای API عمومی", callback_data="panel_donate")],
        [InlineKeyboardButton(
            "🔒 غیرفعال‌کردن Fallback" if db_user["use_public_fallback"] else "✅ فعال‌کردن Fallback",
            callback_data="toggle_fallback"
        )],
    ])

    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  مدیریت API خصوصی  (۴ مرحله)
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

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


# ─── step 0: شروع ────────────────────────────────────────

async def start_add_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *افزودن API — مرحله ۱/۴*\n\nAPI Key را ارسال کنید:\n\n_(برای لغو /cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_API_KEY


# ─── step 1: دریافت کلید ─────────────────────────────────

async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    context.user_data["new_api_key"] = api_key
    await update.message.reply_text("⏳ در حال تشخیص provider...")
    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست. دوباره ارسال کنید:")
        return WAITING_API_KEY
    context.user_data["new_api_provider"] = provider
    context.user_data["new_api_models"]   = models
    await _send_model_selection(update.message, context, provider, models, step="۲/۴")
    return WAITING_API_MODEL


# ─── step 2: انتخاب مدل ──────────────────────────────────

async def receive_api_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text(
            "✏️ نام مدل را تایپ کنید:\nمثال: `gpt-4o` یا `gemini-2.5-flash`",
            parse_mode="Markdown",
        )
        return WAITING_API_MODEL
    context.user_data["new_api_model"] = choice
    await _send_base_url_prompt(query, context, edit=True, step="۳/۴")
    return WAITING_API_BASE_URL


async def receive_api_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return WAITING_API_MODEL
    context.user_data["new_api_model"] = model
    await _send_base_url_prompt(update.message, context, edit=False, step="۳/۴")
    return WAITING_API_BASE_URL


# ─── step 3: base_url ─────────────────────────────────────

async def receive_api_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    provider = context.user_data.get("new_api_provider", "")
    context.user_data["new_api_base_url"] = get_default_base_url(provider)
    await query.edit_message_text(
        f"✅ Base URL: `{context.user_data['new_api_base_url'] or 'پیش‌فرض'}`\n\n"
        "📝 *مرحله ۴/۴* — یک نام برای این API وارد کنید:",
        parse_mode="Markdown",
    )
    return WAITING_API_LABEL


async def receive_api_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:",
                                        parse_mode="Markdown")
        return WAITING_API_BASE_URL
    context.user_data["new_api_base_url"] = url
    await update.message.reply_text(
        f"✅ Base URL: `{url}`\n\n📝 *مرحله ۴/۴* — یک نام وارد کنید:",
        parse_mode="Markdown",
    )
    return WAITING_API_LABEL


# ─── step 4: label و ذخیره ───────────────────────────────

async def receive_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label   = update.message.text.strip()[:100]
    user_tg = update.effective_user
    db_user = get_or_create_user(user_tg.id)
    priority = len(get_user_private_apis(db_user["id"])) + 1

    add_private_api(
        user_id        = db_user["id"],
        api_key        = context.user_data["new_api_key"],
        label          = label,
        provider       = context.user_data["new_api_provider"],
        models         = context.user_data["new_api_models"],
        priority       = priority,
        selected_model = context.user_data.get("new_api_model"),
        base_url       = context.user_data.get("new_api_base_url"),
    )

    await update.message.reply_text(
        f"✅ *API اضافه شد!*\n\n"
        f"🏷 نام: {label}\n"
        f"🔌 Provider: `{context.user_data['new_api_provider']}`\n"
        f"🤖 مدل: `{context.user_data.get('new_api_model')}`\n"
        f"🌐 Base URL: `{context.user_data.get('new_api_base_url') or 'پیش‌فرض'}`",
        parse_mode="Markdown",
    )
    for k in ("new_api_key", "new_api_provider", "new_api_models",
              "new_api_model", "new_api_base_url"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  اهدای API عمومی  (۴ مرحله)
# ════════════════════════════════════════════════════════════

async def start_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎁 *اهدای API — مرحله ۱/۴*\n\nAPI Key را ارسال کنید:\n\n_(برای لغو /cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_DONATE_KEY


async def receive_donate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    await update.message.reply_text("⏳ در حال بررسی...")
    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست. دوباره ارسال کنید:")
        return WAITING_DONATE_KEY
    context.user_data["donate_key"]      = api_key
    context.user_data["donate_provider"] = provider
    context.user_data["donate_models"]   = models
    await _send_model_selection(update.message, context, provider, models,
                                prefix="donate", step="۲/۴")
    return WAITING_DONATE_MODEL


async def receive_donate_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text(
            "✏️ نام مدل را تایپ کنید:",
            parse_mode="Markdown",
        )
        return WAITING_DONATE_MODEL
    context.user_data["donate_model"] = choice
    await _send_base_url_prompt(query, context, edit=True, step="۳/۴",
                                provider=context.user_data["donate_provider"],
                                model=choice)
    return WAITING_DONATE_BASE_URL


async def receive_donate_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return WAITING_DONATE_MODEL
    context.user_data["donate_model"] = model
    await _send_base_url_prompt(update.message, context, edit=False, step="۳/۴",
                                provider=context.user_data["donate_provider"],
                                model=model)
    return WAITING_DONATE_BASE_URL


async def receive_donate_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    provider = context.user_data.get("donate_provider", "")
    context.user_data["donate_base_url"] = get_default_base_url(provider)
    await query.edit_message_text(
        f"✅ Base URL: `{context.user_data['donate_base_url'] or 'پیش‌فرض'}`\n\n"
        "📨 درخواست اهدا ارسال می‌شود...",
        parse_mode="Markdown",
    )
    await _finalize_donation(update, context)
    return ConversationHandler.END


async def receive_donate_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:")
        return WAITING_DONATE_BASE_URL
    context.user_data["donate_base_url"] = url
    await update.message.reply_text("📨 درخواست اهدا ارسال می‌شود...")
    await _finalize_donation(update, context)
    return ConversationHandler.END


async def _finalize_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    donate_data = {
        "api_key":        context.user_data["donate_key"],
        "provider":       context.user_data["donate_provider"],
        "models":         context.user_data["donate_models"],
        "selected_model": context.user_data.get("donate_model"),
        "base_url":       context.user_data.get("donate_base_url"),
        "donated_by":     user_tg.id,
    }
    context.bot_data[f"donation_{user_tg.id}"] = donate_data

    model_display = donate_data["selected_model"] or "—"
    url_display   = donate_data["base_url"]        or "پیش‌فرض"
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"✅ درخواست ارسال شد!\n\n"
        f"🤖 مدل: `{model_display}`\n"
        f"🌐 Base URL: `{url_display}`\n\n"
        "پس از بررسی ادمین اطلاع‌رسانی می‌شود.",
        parse_mode="Markdown",
    )

    from config import ADMIN_IDS
    masked = donate_data["api_key"][:8] + "..." + donate_data["api_key"][-4:]
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🎁 *درخواست اهدای API*\n\n"
                f"کاربر: `{user_tg.id}`\n"
                f"Provider: `{donate_data['provider']}`\n"
                f"مدل: `{model_display}`\n"
                f"Base URL: `{url_display}`\n"
                f"Key: `{masked}`"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تایید", callback_data=f"admin_approve_donation:{user_tg.id}"),
                InlineKeyboardButton("❌ رد",    callback_data=f"admin_reject_donation:{user_tg.id}"),
            ]]),
            parse_mode="Markdown",
        )

    for k in ("donate_key", "donate_provider", "donate_models",
              "donate_model", "donate_base_url"):
        context.user_data.pop(k, None)


# ════════════════════════════════════════════════════════════
#  تاریخچه / Toggle Fallback / حذف API
# ════════════════════════════════════════════════════════════

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()
    db_user = get_or_create_user(user_tg.id)
    jobs    = get_user_jobs(db_user["id"], limit=10)
    if not jobs:
        text = "📋 *تاریخچه*\n\nهیچ جابی نداشته‌اید."
    else:
        emoji_map = {"pending":"⏳","processing":"⚙️","done":"✅","failed":"❌","paused":"⏸"}
        lines = ["📋 *۱۰ جاب اخیر:*\n"]
        for job in jobs:
            lines.append(
                f"{emoji_map.get(job['status'],'❓')} *{job['file_name']}*\n"
                f"   {job['processed_pages']}/{job['total_pages']} صفحه | {job['status']}"
            )
        text = "\n".join(lines)
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")]]),
        parse_mode="Markdown",
    )


async def delete_api_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    db_user = get_or_create_user(update.effective_user.id)
    deleted = delete_private_api(api_id, db_user["id"])
    await query.answer("✅ API حذف شد." if deleted else "❌ یافت نشد.", show_alert=True)
    await show_my_apis(update, context)


async def toggle_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()
    db_user      = get_user(user_tg.id)
    new_fallback = not bool(db_user["use_public_fallback"])
    set_user_fallback(user_tg.id, new_fallback)
    status = "✅ فعال" if new_fallback else "🔒 غیرفعال"
    await query.answer(f"Fallback: {status}", show_alert=True)
    await show_panel(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  توابع کمکی مشترک
# ════════════════════════════════════════════════════════════

async def _send_model_selection(msg_or_query, context, provider, models,
                                prefix="new_api", step="۲/۴"):
    """نمایش دکمه‌های انتخاب مدل."""
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
    if len(models) > 5:
        preview += f"\n  _و {len(models)-5} مدل دیگر..._"

    text = (
        f"✅ Provider: `{provider}`\n\n"
        f"📋 *مرحله {step} — مدل را انتخاب کنید:*\n\n{preview}"
    )

    if hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                              parse_mode="Markdown")
    else:
        await msg_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                       parse_mode="Markdown")


async def _send_base_url_prompt(msg_or_query, context, edit: bool,
                                step="۳/۴", provider: str = None,
                                model: str = None):
    """نمایش درخواست Base URL."""
    if provider is None:
        provider = (context.user_data.get("new_api_provider")
                    or context.user_data.get("donate_provider", ""))
    if model is None:
        model = (context.user_data.get("new_api_model")
                 or context.user_data.get("donate_model", ""))

    default_url  = get_default_base_url(provider)
    default_text = f"`{default_url}`" if default_url else "توسط SDK مدیریت می‌شود"

    text = (
        f"🌐 *مرحله {step} — Base URL*\n\n"
        f"مدل: `{model}`\n"
        f"پیش‌فرض `{provider}`: {default_text}\n\n"
        "برای endpoint سفارشی آدرس را تایپ کنید، در غیر این صورت دکمه زیر:"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ استفاده از پیش‌فرض",
                               callback_data="sel_base_url:__default__")]
    ])

    if edit and hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
    else:
        target = msg_or_query if hasattr(msg_or_query, 'reply_text') else msg_or_query.message
        await target.reply_text(text, reply_markup=buttons, parse_mode="Markdown")