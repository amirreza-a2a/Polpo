# ============================================================
#  handlers/admin.py
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS
from database.models import (
    get_all_public_apis, add_public_api, toggle_public_api,
    delete_public_api, update_public_api_model_url,
    get_all_prompts, add_prompt, update_prompt, delete_prompt,
    get_today_stats, get_user,
    get_all_pipeline2_prompts, add_pipeline2_prompt,
    update_pipeline2_prompt, delete_pipeline2_prompt,
)
from services.api_manager import detect_provider_and_models, get_default_base_url

# ─── states ──────────────────────────────────────────────
ADD_PUB_KEY      = 20
ADD_PUB_LABEL    = 21
ADD_PUB_LIMIT    = 22
ADD_PUB_MODEL    = 23    # جدید
ADD_PUB_BASE_URL = 24    # جدید
EDIT_PUB_MODEL    = 25   # جدید
EDIT_PUB_BASE_URL = 26   # جدید

ADD_PROMPT_TITLE = 30
ADD_PROMPT_DESC  = 31
ADD_PROMPT_TEXT  = 32
EDIT_PROMPT_TEXT = 33

ADD_P2_PROMPT_TITLE = 40
ADD_P2_PROMPT_DESC  = 41
ADD_P2_PROMPT_TEXT  = 42

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ════════════════════════════════════════════════════════════
#  پنل اصلی
# ════════════════════════════════════════════════════════════

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    if not is_admin(user_tg.id):
        if update.message:
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
        [InlineKeyboardButton("🌐 مدیریت API عمومی", callback_data="adm_public_apis")],
        [InlineKeyboardButton("📝 پرامپت‌های Pipeline1",  callback_data="adm_prompts")],
        [InlineKeyboardButton("✨ پرامپت‌های Pipeline2",  callback_data="adm_p2_prompts")],
        [InlineKeyboardButton("📊 آمار کلی",           callback_data="adm_stats")],
    ])
    msg = update.message or (update.callback_query and update.callback_query.message)
    if msg:
        await msg.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  مدیریت API عمومی — لیست
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
            status    = "✅" if api["is_active"] else "🔴"
            donated   = f"(اهدایی از {api['donated_by']})" if api["donated_by"] else "(ادمین)"
            model_str = api.get("selected_model") or "—"
            url_str   = api.get("base_url") or "پیش‌فرض"
            lines.append(
                f"{status} *{api['label']}* {donated}\n"
                f"   مدل: `{model_str}` | URL: `{url_str}`\n"
                f"   مصرف: {api['pages_used_today']}/{api['daily_page_limit']}"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="adm_add_pub_api")]]

    for api in apis:
        toggle_label = "🔴 غیرفعال" if api["is_active"] else "✅ فعال"
        buttons.append([
            InlineKeyboardButton(
                f"{toggle_label} {api['label'][:12]}",
                callback_data=f"adm_toggle_pub:{api['id']}:{0 if api['is_active'] else 1}"
            ),
            InlineKeyboardButton("✏️ مدل/URL", callback_data=f"adm_edit_pub:{api['id']}"),
            InlineKeyboardButton("🗑",          callback_data=f"adm_del_pub:{api['id']}"),
        ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  افزودن API عمومی  (۵ مرحله)
# ════════════════════════════════════════════════════════════

async def start_add_public_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ *افزودن API عمومی — مرحله ۱/۵*\n\nAPI Key را ارسال کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_KEY


async def receive_pub_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    api_key = update.message.text.strip()
    await update.message.reply_text("⏳ در حال بررسی...")
    provider, models = detect_provider_and_models(api_key)
    if not provider:
        await update.message.reply_text("❌ API Key معتبر نیست. دوباره ارسال کنید:")
        return ADD_PUB_KEY

    context.user_data["adm_pub_key"]      = api_key
    context.user_data["adm_pub_provider"] = provider
    context.user_data["adm_pub_models"]   = models

    await _adm_send_model_selection(update.message, provider, models, step="۲/۵")
    return ADD_PUB_MODEL


async def receive_pub_api_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل را تایپ کنید:", parse_mode="Markdown")
        return ADD_PUB_MODEL
    context.user_data["adm_pub_model"] = choice
    await _adm_send_base_url_prompt(query, context, edit=True,
                                    provider=context.user_data["adm_pub_provider"],
                                    model=choice, step="۳/۵",
                                    cb_prefix="adm_pub")
    return ADD_PUB_BASE_URL


async def receive_pub_api_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return ADD_PUB_MODEL
    context.user_data["adm_pub_model"] = model
    await _adm_send_base_url_prompt(update.message, context, edit=False,
                                    provider=context.user_data["adm_pub_provider"],
                                    model=model, step="۳/۵",
                                    cb_prefix="adm_pub")
    return ADD_PUB_BASE_URL


async def receive_pub_api_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    provider = context.user_data.get("adm_pub_provider", "")
    context.user_data["adm_pub_base_url"] = get_default_base_url(provider)
    await query.edit_message_text(
        f"✅ Base URL: `{context.user_data['adm_pub_base_url'] or 'پیش‌فرض'}`\n\n"
        "📝 *مرحله ۴/۵* — نام این API را وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_LABEL


async def receive_pub_api_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:")
        return ADD_PUB_BASE_URL
    context.user_data["adm_pub_base_url"] = url
    await update.message.reply_text(
        f"✅ Base URL: `{url}`\n\n📝 *مرحله ۴/۵* — نام این API را وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_LABEL


async def receive_pub_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_pub_label"] = update.message.text.strip()
    await update.message.reply_text("📊 *مرحله ۵/۵* — محدودیت صفحه روزانه را وارد کنید:\nمثال: `500`",
                                    parse_mode="Markdown")
    return ADD_PUB_LIMIT


async def receive_pub_api_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید:")
        return ADD_PUB_LIMIT

    priority = len(get_all_public_apis()) + 1
    add_public_api(
        api_key        = context.user_data["adm_pub_key"],
        label          = context.user_data["adm_pub_label"],
        provider       = context.user_data["adm_pub_provider"],
        models         = context.user_data["adm_pub_models"],
        daily_limit    = limit,
        priority       = priority,
        selected_model = context.user_data.get("adm_pub_model"),
        base_url       = context.user_data.get("adm_pub_base_url"),
    )

    await update.message.reply_text(
        f"✅ *API عمومی اضافه شد!*\n\n"
        f"🏷 نام: {context.user_data['adm_pub_label']}\n"
        f"🔌 Provider: `{context.user_data['adm_pub_provider']}`\n"
        f"🤖 مدل: `{context.user_data.get('adm_pub_model')}`\n"
        f"🌐 Base URL: `{context.user_data.get('adm_pub_base_url') or 'پیش‌فرض'}`\n"
        f"📊 Limit: {limit} صفحه/روز",
        parse_mode="Markdown",
    )
    for k in ("adm_pub_key","adm_pub_provider","adm_pub_models",
              "adm_pub_model","adm_pub_base_url","adm_pub_label"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  ویرایش مدل/URL یک API عمومی موجود
# ════════════════════════════════════════════════════════════

async def start_edit_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    apis   = get_all_public_apis()
    target = next((a for a in apis if a["id"] == api_id), None)
    if not target:
        await query.answer("❌ API یافت نشد.", show_alert=True)
        return ConversationHandler.END

    context.user_data["edit_pub_id"]       = api_id
    context.user_data["edit_pub_provider"] = target["provider"]
    context.user_data["edit_pub_models"]   = target["supported_models"] or []

    await _adm_send_model_selection(
        query, target["provider"], target["supported_models"] or [],
        step="۱/۲ (ویرایش)", edit=True,
    )
    return EDIT_PUB_MODEL


async def receive_edit_pub_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل جدید را تایپ کنید:")
        return EDIT_PUB_MODEL
    context.user_data["edit_pub_model"] = choice
    await _adm_send_base_url_prompt(query, context, edit=True,
                                    provider=context.user_data["edit_pub_provider"],
                                    model=choice, step="۲/۲ (ویرایش)",
                                    cb_prefix="edit_pub")
    return EDIT_PUB_BASE_URL


async def receive_edit_pub_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    context.user_data["edit_pub_model"] = model
    await _adm_send_base_url_prompt(update.message, context, edit=False,
                                    provider=context.user_data["edit_pub_provider"],
                                    model=model, step="۲/۲ (ویرایش)",
                                    cb_prefix="edit_pub")
    return EDIT_PUB_BASE_URL


async def receive_edit_pub_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    provider = context.user_data.get("edit_pub_provider", "")
    base_url = get_default_base_url(provider)
    await _save_edit_pub(query, context, base_url, edit=True)
    return ConversationHandler.END


async def receive_edit_pub_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:")
        return EDIT_PUB_BASE_URL
    await _save_edit_pub(update.message, context, url, edit=False)
    return ConversationHandler.END


async def _save_edit_pub(msg_or_query, context, base_url, edit: bool):
    api_id = context.user_data["edit_pub_id"]
    model  = context.user_data["edit_pub_model"]
    update_public_api_model_url(api_id, model, base_url)

    text = (
        f"✅ API آپدیت شد!\n\n"
        f"🤖 مدل جدید: `{model}`\n"
        f"🌐 Base URL: `{base_url or 'پیش‌فرض'}`"
    )
    if edit and hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await msg_or_query.reply_text(text, parse_mode="Markdown")

    for k in ("edit_pub_id", "edit_pub_provider", "edit_pub_models",
              "edit_pub_model"):
        context.user_data.pop(k, None)


# ════════════════════════════════════════════════════════════
#  تایید/رد اهدا
# ════════════════════════════════════════════════════════════

async def approve_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()

    donated = context.bot_data.get(f"donation_{donor_tid}")
    if not donated:
        await query.edit_message_text("❌ اطلاعات اهدا پیدا نشد.")
        return

    add_public_api(
        api_key        = donated["api_key"],
        label          = f"اهدایی از {donor_tid}",
        provider       = donated["provider"],
        models         = donated["models"],
        daily_limit    = 200,
        priority       = len(get_all_public_apis()) + 1,
        donated_by     = donor_tid,
        selected_model = donated.get("selected_model"),
        base_url       = donated.get("base_url"),
    )

    model_str = donated.get("selected_model") or "—"
    url_str   = donated.get("base_url") or "پیش‌فرض"
    await query.edit_message_text(
        f"✅ API اهدایی از `{donor_tid}` تایید شد.\n"
        f"مدل: `{model_str}` | URL: `{url_str}`",
        parse_mode="Markdown",
    )
    await context.bot.send_message(donor_tid,
        "✅ API شما تایید شد و به لیست عمومی اضافه شد. ممنون!")


async def reject_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()
    await query.edit_message_text(f"❌ API اهدایی از {donor_tid} رد شد.")
    await context.bot.send_message(donor_tid, "❌ متأسفانه API شما تایید نشد.")


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
        row = [InlineKeyboardButton(
            f"{'🔴' if p['is_active'] else '✅'} {p['title'][:15]}",
            callback_data=f"adm_toggle_prompt:{p['id']}:{0 if p['is_active'] else 1}"
        )]
        if not p["is_default"]:
            row.append(InlineKeyboardButton("⭐", callback_data=f"adm_default_prompt:{p['id']}"))
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
    await update.message.reply_text(
        "متن کامل پرامپت را ارسال کنید:\n\n"
        "_(برای پرامپت‌های طولانی می‌توانید یک فایل .txt آپلود کنید)_",
        parse_mode="Markdown",
    )
    return ADD_PROMPT_TEXT


async def receive_prompt_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # دریافت متن — هم از پیام متنی هم از فایل .txt
    if update.message.document:
        try:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            prompt_text = content.decode("utf-8").strip()
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خواندن فایل: {e}")
            return ADD_PROMPT_TEXT
    else:
        prompt_text = update.message.text.strip()

    if not prompt_text:
        await update.message.reply_text("❌ متن پرامپت نمی‌تواند خالی باشد:")
        return ADD_PROMPT_TEXT

    prompts  = get_all_prompts()
    is_first = len(prompts) == 0
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
    await query.answer()
    update_prompt(int(parts[1]), is_active=bool(int(parts[2])))
    await show_prompts(update, context)


async def set_default_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    update_prompt(int(query.data.split(":")[1]), is_default=True)
    await show_prompts(update, context)


async def delete_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delete_prompt(int(query.data.split(":")[1]))
    await query.answer("✅ پرامپت حذف شد.", show_alert=True)
    await show_prompts(update, context)




async def toggle_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":")
    await query.answer()
    toggle_public_api(int(parts[1]), bool(int(parts[2])))
    await show_public_apis(update, context)


async def delete_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    delete_public_api(api_id)
    await query.answer("✅ API حذف شد.", show_alert=True)
    await show_public_apis(update, context)


async def adm_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_panel(update, context)


# ════════════════════════════════════════════════════════════
#  توابع کمکی
# ════════════════════════════════════════════════════════════

async def _adm_send_model_selection(msg_or_query, provider, models, step, edit=False):
    shown   = models[:8]
    buttons = []
    for i in range(0, len(shown), 2):
        row = [InlineKeyboardButton(shown[i], callback_data=f"adm_sel_model:{shown[i]}")]
        if i + 1 < len(shown):
            row.append(InlineKeyboardButton(shown[i+1], callback_data=f"adm_sel_model:{shown[i+1]}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ وارد کردن دستی",
                                         callback_data="adm_sel_model:__manual__")])
    preview = "\n".join(f"  • `{m}`" for m in models[:5])
    if len(models) > 5:
        preview += f"\n  _و {len(models)-5} مدل دیگر..._"
    text = (
        f"✅ Provider: `{provider}`\n\n"
        f"📋 *مرحله {step} — مدل را انتخاب کنید:*\n\n{preview}"
    )
    if edit and hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                              parse_mode="Markdown")
    else:
        target = msg_or_query if hasattr(msg_or_query, 'reply_text') else msg_or_query.message
        await target.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                 parse_mode="Markdown")


async def _adm_send_base_url_prompt(msg_or_query, context, edit, provider, model, step, cb_prefix):
    default_url  = get_default_base_url(provider)
    default_text = f"`{default_url}`" if default_url else "توسط SDK مدیریت می‌شود"
    text = (
        f"🌐 *مرحله {step} — Base URL*\n\n"
        f"مدل: `{model}`\n"
        f"پیش‌فرض `{provider}`: {default_text}\n\n"
        "برای endpoint سفارشی آدرس را تایپ کنید، در غیر این صورت:"
    )
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ استفاده از پیش‌فرض",
                              callback_data=f"{cb_prefix}_base_url:__default__")
    ]])
    if edit and hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
    else:
        target = msg_or_query if hasattr(msg_or_query, 'reply_text') else msg_or_query.message
        await target.reply_text(text, reply_markup=buttons, parse_mode="Markdown")
        
        



async def show_p2_prompts(update, context):
    query = update.callback_query
    await query.answer()
    prompts = get_all_pipeline2_prompts()
    if not prompts:
        text = "✨ *پرامپت‌های Pipeline2*\n\nهیچ پرامپتی وجود ندارد."
    else:
        lines = ["✨ *پرامپت‌های Pipeline2:*\n"]
        for p in prompts:
            status  = "✅" if p["is_active"] else "🔴"
            default = " ⭐" if p["is_default"] else ""
            lines.append(f"{status} *{p['title']}*{default}\n   {p['description'] or ''}")
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن پرامپت", callback_data="adm_add_p2_prompt")]]
    for p in prompts:
        row = [InlineKeyboardButton(
            f"{'🔴' if p['is_active'] else '✅'} {p['title'][:15]}",
            callback_data=f"adm_toggle_p2_prompt:{p['id']}:{0 if p['is_active'] else 1}"
        )]
        if not p["is_default"]:
            row.append(InlineKeyboardButton("⭐", callback_data=f"adm_default_p2_prompt:{p['id']}"))
        row.append(InlineKeyboardButton("🗑", callback_data=f"adm_del_p2_prompt:{p['id']}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


async def start_add_p2_prompt(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✨ عنوان پرامپت Pipeline2 را وارد کنید:")
    return ADD_P2_PROMPT_TITLE


async def receive_p2_prompt_title(update, context):
    context.user_data["adm_p2_prompt_title"] = update.message.text.strip()
    await update.message.reply_text("توضیح کوتاه برای کاربران وارد کنید:")
    return ADD_P2_PROMPT_DESC


async def receive_p2_prompt_desc(update, context):
    context.user_data["adm_p2_prompt_desc"] = update.message.text.strip()
    await update.message.reply_text(
        "متن کامل پرامپت را ارسال کنید:\n"
        "(برای پرامپت‌های طولانی می‌توانید یک فایل .txt آپلود کنید)"
    )
    return ADD_P2_PROMPT_TEXT


async def receive_p2_prompt_text(update, context):
    if update.message.document:
        try:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            prompt_text = content.decode("utf-8").strip()
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خواندن فایل: {e}")
            return ADD_P2_PROMPT_TEXT
    else:
        prompt_text = update.message.text.strip()

    if not prompt_text:
        await update.message.reply_text("❌ متن پرامپت نمی‌تواند خالی باشد:")
        return ADD_P2_PROMPT_TEXT

    prompts  = get_all_pipeline2_prompts()
    is_first = len(prompts) == 0
    add_pipeline2_prompt(
        title       = context.user_data["adm_p2_prompt_title"],
        description = context.user_data["adm_p2_prompt_desc"],
        prompt_text = prompt_text,
        is_default  = is_first,
        order       = len(prompts) + 1,
    )
    await update.message.reply_text(
        f"✅ پرامپت Pipeline2 *{context.user_data['adm_p2_prompt_title']}* اضافه شد!"
        + (" (پیش‌فرض)" if is_first else ""),
        parse_mode="Markdown",
    )
    for k in ("adm_p2_prompt_title", "adm_p2_prompt_desc"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def toggle_p2_prompt(update, context):
    query = update.callback_query
    parts = query.data.split(":")
    await query.answer()
    update_pipeline2_prompt(int(parts[1]), is_active=bool(int(parts[2])))
    await show_p2_prompts(update, context)


async def set_default_p2_prompt(update, context):
    query = update.callback_query
    await query.answer()
    update_pipeline2_prompt(int(query.data.split(":")[1]), is_default=True)
    await show_p2_prompts(update, context)


async def delete_p2_prompt_handler(update, context):
    query = update.callback_query
    await query.answer()
    delete_pipeline2_prompt(int(query.data.split(":")[1]))
    await query.answer("✅ پرامپت حذف شد.", show_alert=True)
    await show_p2_prompts(update, context)