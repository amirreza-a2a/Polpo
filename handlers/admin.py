# ============================================================
#  handlers/admin.py  –  پنل مدیریت ادمین
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS
from database.models import (
    get_all_public_apis,
    add_public_api,
    toggle_public_api,
    update_public_api_priority,
    delete_public_api,
    get_all_prompts,
    add_prompt,
    update_prompt,
    delete_prompt,
    get_today_stats,
    get_user,
)
from services.api_manager import detect_provider_and_models

# ─── States ──────────────────────────────────────────────
ADD_PUB_KEY      = 20
ADD_PUB_LABEL    = 21
ADD_PUB_LIMIT    = 22
ADD_PROMPT_TITLE = 30
ADD_PROMPT_DESC  = 31
ADD_PROMPT_TEXT  = 32
EDIT_PROMPT_TEXT = 33


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ════════════════════════════════════════════════════════════
#  پنل اصلی ادمین
# ════════════════════════════════════════════════════════════

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    if not is_admin(user_tg.id):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    stats = get_today_stats()
    text  = (
        "⚙️ *پنل ادمین*\n\n"
        f"📊 *آمار امروز:*\n"
        f"  • جاب‌های ثبت‌شده: {stats['total_jobs']}\n"
        f"  • صفحات پردازش‌شده: {stats['total_pages']}\n"
        f"  • در صف: {stats['in_queue']}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 مدیریت API عمومی",  callback_data="adm_public_apis")],
        [InlineKeyboardButton("📝 مدیریت پرامپت‌ها",  callback_data="adm_prompts")],
        [InlineKeyboardButton("📊 آمار کلی",           callback_data="adm_stats")],
    ])

    msg = update.message or update.callback_query.message
    await msg.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  مدیریت API عمومی
# ════════════════════════════════════════════════════════════

async def show_public_apis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return

    apis = get_all_public_apis()

    if not apis:
        text = "🌐 *API های عمومی*\n\nهیچ API عمومی‌ای ثبت نشده."
    else:
        lines = ["🌐 *API های عمومی:*\n"]
        for api in apis:
            status  = "✅" if api["is_active"] else "🔴"
            donated = f"(اهدا توسط {api['donated_by']})" if api["donated_by"] else "(ادمین)"
            lines.append(
                f"{status} *{api['label']}* {donated}\n"
                f"   اولویت: {api['priority']} | "
                f"مصرف امروز: {api['pages_used_today']}/{api['daily_page_limit']}"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="adm_add_pub_api")]]

    for api in apis:
        toggle_label = "🔴 غیرفعال" if api["is_active"] else "✅ فعال"
        buttons.append([
            InlineKeyboardButton(f"{toggle_label} {api['label'][:15]}",
                                 callback_data=f"adm_toggle_pub:{api['id']}:{1 if not api['is_active'] else 0}"),
            InlineKeyboardButton(f"🗑 حذف",
                                 callback_data=f"adm_del_pub:{api['id']}"),
        ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


async def start_add_public_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ *افزودن API عمومی*\n\nAPI Key را ارسال کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_KEY


async def receive_pub_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    api_key  = update.message.text.strip()
    await update.message.reply_text("⏳ در حال بررسی...")

    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست. دوباره ارسال کنید:")
        return ADD_PUB_KEY

    context.user_data["adm_pub_key"]      = api_key
    context.user_data["adm_pub_provider"] = provider
    context.user_data["adm_pub_models"]   = models

    await update.message.reply_text(
        f"✅ Provider: `{provider}`\n\nیک نام برای این API وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_LABEL


async def receive_pub_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_pub_label"] = update.message.text.strip()
    await update.message.reply_text("محدودیت صفحه روزانه را وارد کنید (مثلاً 500):")
    return ADD_PUB_LIMIT


async def receive_pub_api_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید:")
        return ADD_PUB_LIMIT

    existing = get_all_public_apis()
    priority = len(existing) + 1

    add_public_api(
        api_key     = context.user_data["adm_pub_key"],
        label       = context.user_data["adm_pub_label"],
        provider    = context.user_data["adm_pub_provider"],
        models      = context.user_data["adm_pub_models"],
        daily_limit = limit,
        priority    = priority,
    )

    await update.message.reply_text(
        f"✅ API عمومی *{context.user_data['adm_pub_label']}* اضافه شد!\n"
        f"اولویت: {priority} | Limit: {limit} صفحه/روز",
        parse_mode="Markdown",
    )
    for k in ("adm_pub_key", "adm_pub_provider", "adm_pub_models", "adm_pub_label"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def toggle_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":")
    api_id  = int(parts[1])
    active  = int(parts[2])
    await query.answer()
    toggle_public_api(api_id, bool(active))
    await show_public_apis(update, context)


async def delete_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    delete_public_api(api_id)
    await query.answer("✅ API حذف شد.", show_alert=True)
    await show_public_apis(update, context)


# ════════════════════════════════════════════════════════════
#  مدیریت پرامپت‌ها
# ════════════════════════════════════════════════════════════

async def show_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    prompts = get_all_prompts()
    if not prompts:
        text = "📝 *پرامپت‌ها*\n\nهیچ پرامپتی وجود ندارد."
    else:
        lines = ["📝 *پرامپت‌ها:*\n"]
        for p in prompts:
            status  = "✅" if p["is_active"] else "🔴"
            default = " ⭐" if p["is_default"] else ""
            lines.append(f"{status} *{p['title']}*{default}\n   {p['description'] or ''}")
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن پرامپت", callback_data="adm_add_prompt")]]
    for p in prompts:
        row = [
            InlineKeyboardButton(
                f"{'🔴' if p['is_active'] else '✅'} {p['title'][:15]}",
                callback_data=f"adm_toggle_prompt:{p['id']}:{0 if p['is_active'] else 1}"
            ),
        ]
        if not p["is_default"]:
            row.append(InlineKeyboardButton("⭐ پیش‌فرض", callback_data=f"adm_default_prompt:{p['id']}"))
        row.append(InlineKeyboardButton("🗑", callback_data=f"adm_del_prompt:{p['id']}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


async def start_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 عنوان پرامپت را وارد کنید:")
    return ADD_PROMPT_TITLE


async def receive_prompt_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_prompt_title"] = update.message.text.strip()
    await update.message.reply_text("توضیح کوتاه برای کاربران وارد کنید:")
    return ADD_PROMPT_DESC


async def receive_prompt_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_prompt_desc"] = update.message.text.strip()
    await update.message.reply_text("متن کامل پرامپت را وارد کنید:")
    return ADD_PROMPT_TEXT


async def receive_prompt_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt_text = update.message.text.strip()
    prompts     = get_all_prompts()
    is_first    = len(prompts) == 0

    add_prompt(
        title       = context.user_data["adm_prompt_title"],
        description = context.user_data["adm_prompt_desc"],
        prompt_text = prompt_text,
        is_default  = is_first,
        order       = len(prompts) + 1,
    )

    await update.message.reply_text(
        f"✅ پرامپت *{context.user_data['adm_prompt_title']}* اضافه شد!"
        + (" (پیش‌فرض)" if is_first else ""),
        parse_mode="Markdown",
    )
    for k in ("adm_prompt_title", "adm_prompt_desc"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def toggle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    parts     = query.data.split(":")
    prompt_id = int(parts[1])
    active    = int(parts[2])
    await query.answer()
    update_prompt(prompt_id, is_active=bool(active))
    await show_prompts(update, context)


async def set_default_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    prompt_id = int(query.data.split(":")[1])
    await query.answer()
    update_prompt(prompt_id, is_default=True)
    await show_prompts(update, context)


async def delete_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    prompt_id = int(query.data.split(":")[1])
    await query.answer()
    delete_prompt(prompt_id)
    await query.answer("✅ پرامپت حذف شد.", show_alert=True)
    await show_prompts(update, context)


# ════════════════════════════════════════════════════════════
#  تایید/رد اهدای API
# ════════════════════════════════════════════════════════════

async def approve_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()

    donated_data = context.bot_data.get(f"donation_{donor_tid}")
    if not donated_data:
        await query.edit_message_text("❌ اطلاعات اهدا پیدا نشد.")
        return

    existing = get_all_public_apis()
    add_public_api(
        api_key     = donated_data["api_key"],
        label       = f"اهدایی از {donor_tid}",
        provider    = donated_data["provider"],
        models      = donated_data["models"],
        daily_limit = 200,
        priority    = len(existing) + 1,
        donated_by  = donor_tid,
    )

    await query.edit_message_text(f"✅ API اهدایی از {donor_tid} تایید و اضافه شد.")
    await context.bot.send_message(donor_tid, "✅ API شما تایید شد و به لیست عمومی اضافه شد. ممنون!")


async def reject_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()
    await query.edit_message_text(f"❌ API اهدایی از {donor_tid} رد شد.")
    await context.bot.send_message(donor_tid, "❌ متأسفانه API شما تایید نشد.")


async def adm_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_panel(update, context)
