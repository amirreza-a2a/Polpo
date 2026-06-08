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
from services.api_manager import detect_provider_and_models

# ─── states برای ConversationHandler ─────────────────────
WAITING_API_KEY   = 1
WAITING_API_LABEL = 2


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
            models_str = ", ".join((api["supported_models"] or [])[:2])
            lines.append(
                f"*{api['chain_priority']}.* {api['label']}\n"
                f"   Provider: `{api['provider']}`\n"
                f"   مدل‌ها: `{models_str}`\n"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="add_api")]]

    if apis:
        del_buttons = [
            InlineKeyboardButton(f"🗑 حذف {a['label']}", callback_data=f"del_api:{a['id']}")
            for a in apis
        ]
        # دو دکمه در هر ردیف
        buttons += [del_buttons[i:i+2] for i in range(0, len(del_buttons), 2)]

        # دکمه‌های تغییر اولویت
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


async def start_add_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *افزودن API جدید*\n\n"
        "لطفاً API Key خود را ارسال کنید:\n\n"
        "_(برای لغو /cancel را بزنید)_",
        parse_mode="Markdown",
    )
    return WAITING_API_KEY


async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    context.user_data["new_api_key"] = api_key

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

    models_preview = "\n".join(f"  • `{m}`" for m in models[:5])
    if len(models) > 5:
        models_preview += f"\n  _و {len(models) - 5} مدل دیگر..._"

    await update.message.reply_text(
        f"✅ *API شناسایی شد!*\n\n"
        f"🔌 Provider: `{provider}`\n"
        f"📋 مدل‌ها:\n{models_preview}\n\n"
        "حالا یک *نام* برای این API انتخاب کنید (مثلاً: کلید اصلی):",
        parse_mode="Markdown",
    )
    return WAITING_API_LABEL


async def receive_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label   = update.message.text.strip()[:100]
    user_tg = update.effective_user

    db_user  = get_or_create_user(user_tg.id)
    existing = get_user_private_apis(db_user["id"])
    priority = len(existing) + 1

    add_private_api(
        user_id  = db_user["id"],
        api_key  = context.user_data["new_api_key"],
        label    = label,
        provider = context.user_data["new_api_provider"],
        models   = context.user_data["new_api_models"],
        priority = priority,
    )

    await update.message.reply_text(
        f"✅ API *{label}* با موفقیت اضافه شد!\n"
        f"اولویت: {priority}",
        parse_mode="Markdown",
    )

    # پاکسازی context
    for key in ("new_api_key", "new_api_provider", "new_api_models"):
        context.user_data.pop(key, None)

    return ConversationHandler.END


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
    
    # ذخیره در bot_data تا ادمین بتواند دسترسی داشته باشد
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

    # اطلاع به ادمین
    await _notify_admin_donation(context, user_tg, api_key, provider, models)

    return ConversationHandler.END

async def _notify_admin_donation(context, user_tg, api_key, provider, models):
    from config import ADMIN_IDS
    models_str   = ", ".join(models[:3])
    masked_key   = api_key[:8] + "..." + api_key[-4:]

    # ذخیره در bot_data برای دسترسی ادمین
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
            # parse_mode حذف شد
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
