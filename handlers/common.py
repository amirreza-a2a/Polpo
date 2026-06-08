# ============================================================
#  handlers/common.py  –  /start و موارد مشترک
# ============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.models import get_or_create_user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.username)

    text = (
        f"سلام *{user.first_name}* 👋\n\n"
        "به ربات تبدیل PDF به Markdown خوش آمدید.\n\n"
        "📌 *کارهایی که می‌توانید انجام دهید:*\n"
        "• ارسال PDF برای تبدیل\n"
        "• مدیریت API های شخصی\n"
        "• مشاهده وضعیت و تاریخچه\n\n"
        "برای شروع یک PDF ارسال کنید یا از منوی زیر استفاده کنید:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗂 پنل شخصی", callback_data="panel_main")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "دستور شناخته‌شده‌ای نیست. یک PDF ارسال کنید یا /start را بزنید."
    )
