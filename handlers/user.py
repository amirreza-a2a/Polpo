# ============================================================
#  handlers/user.py  –  پنل شخصی کاربر
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import DAILY_PAGE_LIMIT
from database.models import (
    get_or_create_user,
    get_user_private_apis,
    add_private_api,
    delete_private_api,
    update_private_api_priority,
    set_user_fallback,
    get_user_jobs,
    reset_daily_pages_if_needed,
    add_public_api,
    get_next_available_public_api,
)
from services.api_manager import detect_provider_and_models, get_default_base_url

# ─── states برای ConversationHandler ─────────────────────
WAITING_API_KEY      = 1
WAITING_API_LABEL    = 2
WAITING_DONATE_KEY   = 10
WAITING_API_MODEL    = 3    # جدید: انتخاب مدل
WAITING_API_BASE_URL = 4    # جدید: وارد کردن base_url


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
        [InlineKeyboardButton("🔑 API های من",          callback_data="panel_apis")],
        [InlineKeyboardButton("📋 تاریخچه",              callback_data="panel_history")],
        [InlineKeyboardButton("🎁 اهدای API عمومی",     callback_data="panel_donate")],
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
#  مدیریت API های خصوصی
# ════════════════════════════════════════════════════════════

async def show_my_apis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    apis    = get_user_private_apis(db_user["id"])

    if not apis:
        text = "🔑 *API های شخصی*\n\nشما هنوز هیچ API‌ای اضافه نکرده‌اید."
    else:
        lines = ["🔑 *API های شخصی شما:*\n"]
        for api in apis:
            selected = api.get("selected_model") or "—"
            base     = api.get("base_url") or "پیش‌فرض"
            lines.append(
                f"*{api['chain_priority']}.* {api['label']}\n"
                f"   Provider: `{api['provider']}`\n"
                f"   مدل: `{selected}`\n"
                f"   Base URL: `{base}`\n"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="add_api")]]

    if apis:
        del_buttons = [
            InlineKeyboardButton(f"🗑 حذف {a['label']}", callback_data=f"del_api:{a['id']}")
            for a in apis
        ]
        buttons += [del_buttons[i:i+2] for i in range(0, len(del_buttons), 2)]

        if len(apis) > 1:
            buttons.append([
                InlineKeyboardButton("⬆️ تغییر اولویت‌ها", callback_data="reorder_apis")
            ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# ─── مرحله ۱: شروع افزودن API ────────────────────────────

async def start_add_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *افزودن API جدید — مرحله ۱/۴*\n\n"
        "لطفاً API Key خود را ارسال کنید:\n\n"
        "_(برای لغو /cancel را بزنید)_",
        parse_mode="Markdown",
    )
    return WAITING_API_KEY


# ─── مرحله ۱: دریافت API Key ─────────────────────────────

async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    context.user_data["new_api_key"]     = api_key
    context.user_data["new_api_base_url_for_detect"] = None  # هنوز base_url نداریم

    await update.message.reply_text("⏳ در حال تشخیص provider و مدل‌ها...")

    provider, models = detect_provider_and_models(api_key)

    if not provider:
        await update.message.reply_text(
            "❌ API Key معتبر نیست یا قابل شناسایی نیست.\n"
            "دوباره امتحان کنید یا /cancel را بزنید."
        )
        return WAITING_API_KEY

    context.user_data["new_api_provider"] = provider
    context.user_data["new_api_models"]   = models

    # ساخت دکمه‌های مدل (حداکثر ۸ مدل، دو تا در هر ردیف)
    buttons = []
    shown = models[:8]
    for i in range(0, len(shown), 2):
        row = [InlineKeyboardButton(shown[i], callback_data=f"sel_model:{shown[i]}")]
        if i + 1 < len(shown):
            row.append(InlineKeyboardButton(shown[i+1], callback_data=f"sel_model:{shown[i+1]}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ وارد کردن دستی", callback_data="sel_model:__manual__")])

    preview = "\n".join(f"  • `{m}`" for m in models[:5])
    if len(models) > 5:
        preview += f"\n  _و {len(models) - 5} مدل دیگر..._"

    await update.message.reply_text(
        f"✅ *Provider شناسایی شد: `{provider}`*\n\n"
        f"📋 *مرحله ۲/۴ — مدل را انتخاب کنید:*\n\n"
        f"{preview}",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return WAITING_API_MODEL


# ─── مرحله ۲: انتخاب مدل (دکمه) ─────────────────────────

async def receive_api_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()

    if choice == "__manual__":
        await query.edit_message_text(
            "✏️ *مرحله ۲/۴ — وارد کردن دستی مدل*\n\n"
            "نام مدل را تایپ کنید:\n"
            "مثال: `gemini-2.5-flash` یا `gpt-4o` یا `google/gemma-3-27b-it`",
            parse_mode="Markdown",
        )
        return WAITING_API_MODEL

    context.user_data["new_api_model"] = choice
    return await _ask_base_url(query, context, edit=True)


# ─── مرحله ۲: انتخاب مدل (تایپ دستی) ───────────────────

async def receive_api_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد. دوباره وارد کنید:")
        return WAITING_API_MODEL

    context.user_data["new_api_model"] = model
    return await _ask_base_url(update, context, edit=False)


# ─── کمکی: نمایش مرحله Base URL ─────────────────────────

async def _ask_base_url(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit: bool):
    provider    = context.user_data.get("new_api_provider", "")
    model       = context.user_data.get("new_api_model", "")
    default_url = get_default_base_url(provider)

    if default_url:
        default_text = f"پیش‌فرض `{provider}`: `{default_url}`"
    else:
        default_text = f"پیش‌فرض `{provider}`: توسط SDK مدیریت می‌شود (نیازی به URL نیست)"

    text = (
        f"🌐 *مرحله ۳/۴ — Base URL*\n\n"
        f"مدل انتخاب‌شده: `{model}`\n"
        f"{default_text}\n\n"
        "اگر می‌خواهید از endpoint سفارشی استفاده کنید (مثلاً پروکسی یا سرویس دیگر) "
        "آدرس را تایپ کنید.\n"
        "در غیر این صورت دکمه زیر را بزنید:"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ استفاده از پیش‌فرض", callback_data="sel_base_url:__default__")]
    ])

    if edit:
        await update_or_query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
    else:
        await update_or_query.message.reply_text(text, reply_markup=buttons, parse_mode="Markdown")

    return WAITING_API_BASE_URL


# ─── مرحله ۳: Base URL (دکمه پیش‌فرض) ──────────────────

async def receive_api_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    provider    = context.user_data.get("new_api_provider", "")
    default_url = get_default_base_url(provider)
    context.user_data["new_api_base_url"] = default_url  # None یا URL پیش‌فرض

    await query.edit_message_text(
        f"✅ Base URL: `{default_url or 'پیش‌فرض'}`\n\n"
        "📝 *مرحله ۴/۴ — نام API*\n\n"
        "یک نام برای این API انتخاب کنید (مثلاً: کلید اصلی، GPT کاری):",
        parse_mode="Markdown",
    )
    return WAITING_API_LABEL


# ─── مرحله ۳: Base URL (تایپ دستی) ─────────────────────

async def receive_api_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text(
            "❌ آدرس معتبر نیست. باید با `http://` یا `https://` شروع شود.\n"
            "دوباره وارد کنید یا دکمه پیش‌فرض را بزنید:",
            parse_mode="Markdown",
        )
        return WAITING_API_BASE_URL

    context.user_data["new_api_base_url"] = url

    await update.message.reply_text(
        f"✅ Base URL تنظیم شد: `{url}`\n\n"
        "📝 *مرحله ۴/۴ — نام API*\n\n"
        "یک نام برای این API انتخاب کنید (مثلاً: کلید اصلی، GPT کاری):",
        parse_mode="Markdown",
    )
    return WAITING_API_LABEL


# ─── مرحله ۴: دریافت label و ذخیره API ──────────────────

async def receive_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label   = update.message.text.strip()[:100]
    user_tg = update.effective_user

    db_user  = get_or_create_user(user_tg.id)
    existing = get_user_private_apis(db_user["id"])
    priority = len(existing) + 1

    selected_model = context.user_data.get("new_api_model")
    base_url       = context.user_data.get("new_api_base_url")

    add_private_api(
        user_id        = db_user["id"],
        api_key        = context.user_data["new_api_key"],
        label          = label,
        provider       = context.user_data["new_api_provider"],
        models         = context.user_data["new_api_models"],
        priority       = priority,
        selected_model = selected_model,
        base_url       = base_url,
    )

    base_display = base_url or "پیش‌فرض"
    await update.message.reply_text(
        f"✅ *API با موفقیت اضافه شد!*\n\n"
        f"🏷 نام: {label}\n"
        f"🔌 Provider: `{context.user_data['new_api_provider']}`\n"
        f"🤖 مدل: `{selected_model}`\n"
        f"🌐 Base URL: `{base_display}`\n"
        f"📊 اولویت: {priority}",
        parse_mode="Markdown",
    )

    for key in ("new_api_key", "new_api_provider", "new_api_models",
                "new_api_model", "new_api_base_url"):
        context.user_data.pop(key, None)

    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  حذف API
# ════════════════════════════════════════════════════════════

async def delete_api_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()

    user_tg = update.effective_user
    db_user = get_or_create_user(user_tg.id)
    deleted = delete_private_api(api_id, db_user["id"])

    if deleted:
        await query.edit_message_text("✅ API با موفقیت حذف شد.")
    else:
        await query.edit_message_text("❌ API یافت نشد.")


# ════════════════════════════════════════════════════════════
#  تاریخچه
# ════════════════════════════════════════════════════════════

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()

    db_user = get_or_create_user(user_tg.id)
    jobs    = get_user_jobs(db_user["id"], limit=10)

    if not jobs:
        text = "📋 *تاریخچه*\n\nهنوز هیچ جابی نداشته‌اید."
    else:
        status_emoji = {
            "pending":    "⏳",
            "processing": "⚙️",
            "done":       "✅",
            "failed":     "❌",
            "paused":     "⏸",
        }
        lines = ["📋 *۱۰ جاب اخیر:*\n"]
        for job in jobs:
            emoji = status_emoji.get(job["status"], "❓")
            lines.append(
                f"{emoji} *{job['file_name']}*\n"
                f"   صفحات: {job['processed_pages']}/{job['total_pages']} | "
                f"وضعیت: {job['status']}"
            )
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="panel_main")]
        ]),
        parse_mode="Markdown",
    )


# ════════════════════════════════════════════════════════════
#  اهدای API عمومی
# ════════════════════════════════════════════════════════════

WAITING_DONATE_KEY = 10


async def start_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎁 *اهدای API عمومی*\n\n"
        "API Key که می‌خواهید اهدا کنید را ارسال کنید.\n"
        "این API پس از تایید ادمین برای همه کاربران فعال می‌شود.\n\n"
        "_(برای لغو /cancel را بزنید)_",
        parse_mode="Markdown",
    )
    return WAITING_DONATE_KEY


async def receive_donate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    user_tg = update.effective_user

    await update.message.reply_text("⏳ در حال بررسی API Key...")

    provider, models = detect_provider_and_models(api_key)

    if not provider:
        await update.message.reply_text(
            "❌ API Key معتبر نیست. دوباره امتحان کنید یا /cancel را بزنید."
        )
        return WAITING_DONATE_KEY

    context.bot_data[f"donation_{user_tg.id}"] = {
        "api_key":    api_key,
        "provider":   provider,
        "models":     models,
        "donated_by": user_tg.id,
    }

    await update.message.reply_text(
        f"✅ API معتبر است! (Provider: `{provider}`)\n\n"
        "درخواست شما برای ادمین ارسال شد. پس از تایید اطلاع‌رسانی می‌شود.",
        parse_mode="Markdown",
    )

    await _notify_admin_donation(context, user_tg, api_key, provider, models)

    return ConversationHandler.END


async def _notify_admin_donation(context, user_tg, api_key, provider, models):
    from config import ADMIN_IDS
    models_str = ", ".join(models[:3])
    masked_key = api_key[:8] + "..." + api_key[-4:]

    context.bot_data[f"donation_{user_tg.id}"] = {
        "api_key":    api_key,
        "provider":   provider,
        "models":     models,
        "donated_by": user_tg.id,
    }

    for admin_id in ADMIN_IDS:
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تایید", callback_data=f"admin_approve_donation:{user_tg.id}"),
                InlineKeyboardButton("❌ رد",    callback_data=f"admin_reject_donation:{user_tg.id}"),
            ]
        ])
        await context.bot.send_message(
            chat_id      = admin_id,
            text         = (
                f"درخواست اهدای API\n\n"
                f"کاربر: {user_tg.id}\n"
                f"Provider: {provider}\n"
                f"Key: {masked_key}\n"
                f"مدل‌ها: {models_str}"
            ),
            reply_markup = buttons,
        )


# ════════════════════════════════════════════════════════════
#  Toggle Fallback
# ════════════════════════════════════════════════════════════

async def toggle_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_tg = update.effective_user
    await query.answer()

    from database.models import get_user
    db_user      = get_user(user_tg.id)
    new_fallback = not bool(db_user["use_public_fallback"])
    set_user_fallback(user_tg.id, new_fallback)

    status = "✅ فعال" if new_fallback else "🔒 غیرفعال"
    await query.answer(f"Fallback به API عمومی: {status}", show_alert=True)
    await show_panel(update, context)


# ─── لغو ─────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END