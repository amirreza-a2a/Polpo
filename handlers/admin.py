# ============================================================
#  handlers/admin.py  —  Migrated to Application Services
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS
from infrastructure.composition import get_app_container
from application.dto.prompt_dto import CreatePromptCommand


# ─── states ──────────────────────────────────────────────
ADD_PUB_KEY      = 20
ADD_PUB_LABEL    = 21
ADD_PUB_LIMIT    = 22
ADD_PUB_MODEL    = 23
ADD_PUB_BASE_URL = 24
EDIT_PUB_MODEL    = 25
EDIT_PUB_BASE_URL = 26

ADD_PROMPT_TITLE = 30
ADD_PROMPT_DESC  = 31
ADD_PROMPT_TEXT  = 32
EDIT_PROMPT_TEXT = 33

ADD_P2_PROMPT_TITLE = 40
ADD_P2_PROMPT_DESC  = 41
ADD_P2_PROMPT_TEXT  = 42

EDIT_QUICK_CONVERT_PROMPT = 50


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

    container = get_app_container()
    stats = container.job_query_service.get_today_stats()
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
        [InlineKeyboardButton("⚡ پرامپت تبدیل سریع",   callback_data="adm_quick_prompt")],
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

    container = get_app_container()
    apis = container.api_service.list_public_apis()

    if not apis:
        text = "🌐 *API های عمومی*\n\nهیچ API عمومی‌ای ثبت نشده."
    else:
        lines = ["🌐 *API های عمومی:*\n"]
        for api in apis:
            model_str = api.selected_model or "—"
            url_str   = api.base_url or "پیش‌فرض"
            lines.append(
                f"• *{api.label}*\n"
                f"   مدل: `{model_str}` | URL: `{url_str}`"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن API جدید", callback_data="adm_add_pub_api")]]

    for api in apis:
        buttons.append([
            InlineKeyboardButton("✏️ مدل/URL", callback_data=f"adm_edit_pub:{api.id}"),
            InlineKeyboardButton("🗑",          callback_data=f"adm_del_pub:{api.id}"),
        ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


# ════════════════════════════════════════════════════════════
#  افزودن API عمومی
# ════════════════════════════════════════════════════════════

async def start_add_public_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    await query.edit_message_text(
        "🌐 *افزودن API عمومی — مرحله ۱/۵*\n\n"
        "API Key را ارسال کنید:\n\n_(برای لغو /cancel)_",
        parse_mode="Markdown",
    )
    return ADD_PUB_KEY


async def receive_pub_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    context.user_data["adm_pub_key"] = api_key
    await update.message.reply_text("⏳ در حال تشخیص provider...")
    container = get_app_container()
    detect_res = container.api_service.detect_provider_and_models(api_key)
    provider, models = detect_res.provider, detect_res.models
    if not provider:
        await update.message.reply_text("❌ کلید معتبر نیست. دوباره ارسال کنید:")
        return ADD_PUB_KEY

    context.user_data["adm_pub_provider"] = provider
    context.user_data["adm_pub_models"]   = models
    await _adm_send_model_selection(update.message, provider, models, step="۲/۵")
    return ADD_PUB_MODEL


async def receive_pub_api_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل را تایپ کنید:")
        return ADD_PUB_MODEL
    context.user_data["adm_pub_model"] = choice
    await _adm_send_base_url_prompt(
        query, context, edit=True,
        provider=context.user_data["adm_pub_provider"],
        model=choice, step="۳/۵", cb_prefix="adm_pub",
    )
    return ADD_PUB_BASE_URL


async def receive_pub_api_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return ADD_PUB_MODEL
    context.user_data["adm_pub_model"] = model
    await _adm_send_base_url_prompt(
        update.message, context, edit=False,
        provider=context.user_data["adm_pub_provider"],
        model=model, step="۳/۵", cb_prefix="adm_pub",
    )
    return ADD_PUB_BASE_URL


async def receive_pub_api_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    provider = context.user_data.get("adm_pub_provider", "")
    await query.answer()
    container = get_app_container()
    default_base_url = container.api_service.provider_detector.get_default_base_url(provider) if container.api_service.provider_detector else None
    context.user_data["adm_pub_base_url"] = default_base_url
    await query.edit_message_text(
        f"✅ Base URL: `{context.user_data['adm_pub_base_url'] or 'پیش‌فرض'}`\n\n"
        "📝 *مرحله ۴/۵* — یک نام (Label) برای این API وارد کنید:",
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
        f"✅ Base URL: `{url}`\n\n"
        "📝 *مرحله ۴/۵* — یک نام (Label) برای این API وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_PUB_LABEL


async def receive_pub_api_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_pub_label"] = update.message.text.strip()[:100]
    await update.message.reply_text(
        f"✅ Label: *{context.user_data['adm_pub_label']}*\n\n"
        "📊 *مرحله ۵/۵* — سقف مجاز صفحه در روز (عدد):",
        parse_mode="Markdown",
    )
    return ADD_PUB_LIMIT


async def receive_pub_api_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
        if limit <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد مثبت وارد کنید:")
        return ADD_PUB_LIMIT

    container = get_app_container()
    container.api_service.register_public_api(
        provider       = context.user_data["adm_pub_provider"],
        api_key        = context.user_data["adm_pub_key"],
        label          = context.user_data["adm_pub_label"],
        models         = context.user_data["adm_pub_models"],
        daily_limit    = limit,
        selected_model = context.user_data.get("adm_pub_model"),
        base_url       = context.user_data.get("adm_pub_base_url"),
    )

    model_str = context.user_data.get("adm_pub_model") or "—"
    url_str   = context.user_data.get("adm_pub_base_url") or "پیش‌فرض"
    await update.message.reply_text(
        f"✅ *API عمومی اضافه شد!*\n\n"
        f"🏷 Label: {context.user_data['adm_pub_label']}\n"
        f"🤖 مدل: `{model_str}`\n"
        f"🌐 URL: `{url_str}`\n"
        f"📊 سقف: {limit} صفحه/روز",
        parse_mode="Markdown",
    )
    for k in ("adm_pub_key","adm_pub_provider","adm_pub_models",
              "adm_pub_model","adm_pub_base_url","adm_pub_label"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  ویرایش مدل و Base URL یک API عمومی
# ════════════════════════════════════════════════════════════

async def start_edit_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    if not is_admin(update.effective_user.id):
        return

    container = get_app_container()
    public_apis = container.api_service.list_public_apis()
    api = next((a for a in public_apis if a.id == api_id), None)
    if not api:
        await query.answer("❌ API یافت نشد.", show_alert=True)
        return

    context.user_data["edit_pub_id"]       = api_id
    context.user_data["edit_pub_provider"] = api.provider
    models = api.supported_models or ["gemini-2.5-flash", "gpt-4o"]
    context.user_data["edit_pub_models"]   = models

    await _adm_send_model_selection(
        query, api.provider, models,
        step="۱/۲", edit=True,
    )
    return EDIT_PUB_MODEL


async def receive_edit_pub_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "__manual__":
        await query.edit_message_text("✏️ نام مدل را تایپ کنید:")
        return EDIT_PUB_MODEL
    context.user_data["edit_pub_model"] = choice
    await _adm_send_base_url_prompt(
        query, context, edit=True,
        provider=context.user_data["edit_pub_provider"],
        model=choice, step="۲/۲", cb_prefix="edit_pub",
    )
    return EDIT_PUB_BASE_URL


async def receive_edit_pub_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("❌ نام مدل نمی‌تواند خالی باشد:")
        return EDIT_PUB_MODEL
    context.user_data["edit_pub_model"] = model
    await _adm_send_base_url_prompt(
        update.message, context, edit=False,
        provider=context.user_data["edit_pub_provider"],
        model=model, step="۲/۲", cb_prefix="edit_pub",
    )
    return EDIT_PUB_BASE_URL


async def receive_edit_pub_base_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    provider = context.user_data.get("edit_pub_provider", "")
    await query.answer()
    container = get_app_container()
    base_url = container.api_service.provider_detector.get_default_base_url(provider) if container.api_service.provider_detector else None
    await _save_edited_pub_api(query, context, base_url, edit_msg=True)
    return ConversationHandler.END



async def receive_edit_pub_base_url_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ آدرس باید با `http://` یا `https://` شروع شود:")
        return EDIT_PUB_BASE_URL
    await _save_edited_pub_api(update.message, context, url, edit_msg=False)
    return ConversationHandler.END


async def _save_edited_pub_api(msg_or_query, context, base_url: str, edit_msg: bool):
    api_id = context.user_data["edit_pub_id"]
    model  = context.user_data.get("edit_pub_model")
    container = get_app_container()
    container.api_service.update_public_api_model_url(api_id, selected_model=model, base_url=base_url)

    text = (
        f"✅ *تنظیمات API عمومی بروزرسانی شد!*\n\n"
        f"🤖 مدل: `{model}`\n"
        f"🌐 Base URL: `{base_url or 'پیش‌فرض'}`"
    )
    if edit_msg and hasattr(msg_or_query, 'edit_message_text'):
        await msg_or_query.edit_message_text(text, parse_mode="Markdown")
    else:
        target = msg_or_query if hasattr(msg_or_query, 'reply_text') else msg_or_query.message
        await target.reply_text(text, parse_mode="Markdown")

    for k in ("edit_pub_id", "edit_pub_provider", "edit_pub_models",
              "edit_pub_model", "edit_pub_base_url"):
        context.user_data.pop(k, None)


def add_public_api(*args, **kwargs):
    """تابع کمکی جهت حفظ سازگاری ماژول با تست‌های مانیتورینگ کاراکتریزاسیون."""
    container = get_app_container()
    return container.api_service.register_public_api(*args, **kwargs)


# ════════════════════════════════════════════════════════════
#  تایید/رد اهدا
# ════════════════════════════════════════════════════════════

async def approve_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()

    donated = context.bot_data.get(f"donation_{donor_tid}")
    if not donated:
        await query.edit_message_text("❌ اطلاعات اهدا پیدا نشد.")
        return

    add_public_api(
        provider       = donated["provider"],
        api_key        = donated["api_key"],
        label          = f"اهدایی از {donor_tid}",
        models         = donated["models"],
        daily_limit    = 200,
        selected_model = donated.get("selected_model"),
        base_url       = donated.get("base_url"),
        donated_by     = donor_tid,
    )


    model_str = donated.get("selected_model") or "—"
    url_str   = donated.get("base_url") or "پیش‌فرض"
    await query.edit_message_text(
        f"✅ API اهدایی از `{donor_tid}` تایید شد.\n"
        f"مدل: `{model_str}` | URL: `{url_str}`",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(donor_tid, "✅ API شما تایید شد و به لیست عمومی اضافه شد. ممنون!")
    except Exception:
        pass


async def reject_donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    donor_tid = int(query.data.split(":")[1])
    await query.answer()
    await query.edit_message_text(f"❌ API اهدایی از {donor_tid} رد شد.")
    try:
        await context.bot.send_message(donor_tid, "❌ متأسفانه API شما تایید نشد.")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  مدیریت پرامپت‌ها
# ════════════════════════════════════════════════════════════

async def show_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    prompts = container.prompt_service.list_prompts("pipeline_1")
    if not prompts:
        text = "📝 *پرامپت‌ها*\n\nهیچ پرامپتی وجود ندارد."
    else:
        lines = ["📝 *پرامپت‌ها:*\n"]
        for p in prompts:
            default = " ⭐" if p.is_default else ""
            lines.append(f"• *{p.name}*{default}")
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن پرامپت", callback_data="adm_add_prompt")]]
    for p in prompts:
        buttons.append([
            InlineKeyboardButton("⭐ پیش‌فرض", callback_data=f"adm_default_prompt:{p.id}"),
            InlineKeyboardButton("🗑",         callback_data=f"adm_del_prompt:{p.id}"),
        ])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


async def start_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 *افزودن پرامپت — مرحله ۱/۳*\n\nعنوان پرامپت را وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_PROMPT_TITLE


async def receive_prompt_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_prompt_title"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ عنوان: *{context.user_data['adm_prompt_title']}*\n\n"
        "📝 *مرحله ۲/۳* — توضیحات کوتاه:",
        parse_mode="Markdown",
    )
    return ADD_PROMPT_DESC


async def receive_prompt_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["adm_prompt_desc"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 *مرحله ۳/۳* — متن کامل پرامپت را ارسال کنید:\n"
        "(برای متن طولانی می‌توانید یک فایل .txt آپلود کنید)"
    )
    return ADD_PROMPT_TEXT


async def receive_prompt_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    container = get_app_container()
    prompts  = container.prompt_service.list_prompts("pipeline_1")
    is_first = len(prompts) == 0
    container.prompt_service.create_prompt(CreatePromptCommand(
        name=context.user_data["adm_prompt_title"],
        text=prompt_text,
        prompt_type="pipeline_1",
        is_default=is_first,
    ))

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
    container = get_app_container()
    container.prompt_service.toggle_prompt(int(parts[1]), bool(int(parts[2])))
    await show_prompts(update, context)


async def set_default_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    container.prompt_service.set_default(int(query.data.split(":")[1]), "pipeline_1")
    await show_prompts(update, context)


async def delete_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    container.prompt_service.delete_prompt(int(query.data.split(":")[1]))
    await query.answer("✅ پرامپت حذف شد.", show_alert=True)
    await show_prompts(update, context)


async def toggle_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":")
    await query.answer()
    container = get_app_container()
    container.api_service.toggle_public_api(int(parts[1]), bool(int(parts[2])))
    await show_public_apis(update, context)


async def delete_pub_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    api_id = int(query.data.split(":")[1])
    await query.answer()
    container = get_app_container()
    container.api_service.delete_public_api(api_id)
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
    container = get_app_container()
    default_url = container.api_service.provider_detector.get_default_base_url(provider) if container.api_service.provider_detector else None
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
    container = get_app_container()
    prompts = container.prompt_service.list_prompts("pipeline_2")
    if not prompts:
        text = "✨ *پرامپت‌های Pipeline2*\n\nهیچ پرامپتی وجود ندارد."
    else:
        lines = ["✨ *پرامپت‌های Pipeline2:*\n"]
        for p in prompts:
            default = " ⭐" if p.is_default else ""
            lines.append(f"• *{p.name}*{default}")
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("➕ افزودن پرامپت", callback_data="adm_add_p2_prompt")]]
    for p in prompts:
        buttons.append([
            InlineKeyboardButton("⭐ پیش‌فرض", callback_data=f"adm_default_p2_prompt:{p.id}"),
            InlineKeyboardButton("🗑",         callback_data=f"adm_del_p2_prompt:{p.id}"),
        ])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="Markdown")


async def start_add_p2_prompt(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✨ *افزودن پرامپت Pipeline2 — مرحله ۱/۳*\n\nعنوان پرامپت را وارد کنید:",
        parse_mode="Markdown",
    )
    return ADD_P2_PROMPT_TITLE


async def receive_p2_prompt_title(update, context):
    context.user_data["adm_p2_prompt_title"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ عنوان: *{context.user_data['adm_p2_prompt_title']}*\n\n"
        "📝 *مرحله ۲/۳* — توضیحات کوتاه:",
        parse_mode="Markdown",
    )
    return ADD_P2_PROMPT_DESC


async def receive_p2_prompt_desc(update, context):
    context.user_data["adm_p2_prompt_desc"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 *مرحله ۳/۳* — متن کامل پرامپت Pipeline2 را ارسال کنید:\n"
        "(برای متن طولانی می‌توانید یک فایل .txt آپلود کنید)"
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

    container = get_app_container()
    prompts  = container.prompt_service.list_prompts("pipeline_2")
    is_first = len(prompts) == 0
    container.prompt_service.create_prompt(CreatePromptCommand(
        name=context.user_data["adm_p2_prompt_title"],
        text=prompt_text,
        prompt_type="pipeline_2",
        is_default=is_first,
    ))

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
    container = get_app_container()
    container.prompt_service.toggle_prompt(int(parts[1]), bool(int(parts[2])))
    await show_p2_prompts(update, context)


async def set_default_p2_prompt(update, context):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    container.prompt_service.set_default(int(query.data.split(":")[1]), "pipeline_2")
    await show_p2_prompts(update, context)


async def delete_p2_prompt_handler(update, context):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    container.prompt_service.delete_prompt(int(query.data.split(":")[1]))
    await query.answer("✅ پرامپت حذف شد.", show_alert=True)
    await show_p2_prompts(update, context)


async def show_quick_convert_prompt(update, context):
    query = update.callback_query
    await query.answer()
    container = get_app_container()
    current = container.prompt_service.get_quick_convert_prompt()

    if current:
        preview = current[:300] + ("..." if len(current) > 300 else "")
        text = f"⚡ *پرامپت تبدیل سریع (فعلی):*\n\n{preview}"
    else:
        text = "⚡ *پرامپت تبدیل سریع*\n\nهنوز پرامپتی تنظیم نشده است."

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✏️ ویرایش پرامپت" if current else "➕ تنظیم پرامپت",
            callback_data="adm_edit_quick_prompt",
        )],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")],
    ])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")


async def start_edit_quick_convert_prompt(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚡ متن کامل پرامپت تبدیل سریع را ارسال کنید:\n"
        "(برای متن طولانی می‌توانید یک فایل .txt آپلود کنید)"
    )
    return EDIT_QUICK_CONVERT_PROMPT


async def receive_quick_convert_prompt(update, context):
    if update.message.document:
        try:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            prompt_text = content.decode("utf-8").strip()
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خواندن فایل: {e}")
            return EDIT_QUICK_CONVERT_PROMPT
    else:
        prompt_text = update.message.text.strip()

    if not prompt_text:
        await update.message.reply_text("❌ متن پرامپت نمی‌تواند خالی باشد:")
        return EDIT_QUICK_CONVERT_PROMPT

    container = get_app_container()
    container.prompt_service.set_quick_convert_prompt(prompt_text)
    await update.message.reply_text("✅ پرامپت تبدیل سریع ذخیره شد.")
    return ConversationHandler.END
