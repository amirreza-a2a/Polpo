# ============================================================
#  main.py  –  نقطه شروع ربات
# ============================================================

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from config import BOT_TOKEN
from database.connection import init_db
from utils.file_manager import ensure_dirs

from handlers.common import start, unknown_command
from handlers.pdf import (
    handle_pdf,
    on_prompt_selected,
    on_api_source_selected,
    on_fallback_selected,
)
from handlers.user import (
    show_panel,
    show_my_apis,
    start_add_api,
    receive_api_key,
    receive_api_model_callback,      # جدید
    receive_api_model_text,          # جدید
    receive_api_base_url_callback,   # جدید
    receive_api_base_url_text,       # جدید
    receive_api_label,
    delete_api_confirm,
    show_history,
    start_donate,
    receive_donate_key,
    toggle_fallback,
    cancel,
    WAITING_API_KEY,
    WAITING_API_LABEL,
    WAITING_API_MODEL,               # جدید
    WAITING_API_BASE_URL,            # جدید
    WAITING_DONATE_KEY,
)
from handlers.admin import (
    admin_panel,
    show_public_apis,
    start_add_public_api,
    receive_pub_api_key,
    receive_pub_api_label,
    receive_pub_api_limit,
    toggle_pub_api,
    delete_pub_api,
    show_prompts,
    start_add_prompt,
    receive_prompt_title,
    receive_prompt_desc,
    receive_prompt_text,
    toggle_prompt,
    set_default_prompt,
    delete_prompt_handler,
    approve_donation,
    reject_donation,
    adm_back,
    ADD_PUB_KEY, ADD_PUB_LABEL, ADD_PUB_LIMIT,
    ADD_PROMPT_TITLE, ADD_PROMPT_DESC, ADD_PROMPT_TEXT,
)


def main():
    init_db()
    ensure_dirs()

    app = Application.builder().token(BOT_TOKEN).build()

    # ─── ConversationHandler: افزودن API خصوصی ─────────────
    add_api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_api, pattern="^add_api$")],
        states={
            # مرحله ۱: دریافت کلید
            WAITING_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_key),
            ],
            # مرحله ۲: انتخاب مدل (دکمه یا تایپ)
            WAITING_API_MODEL: [
                CallbackQueryHandler(receive_api_model_callback, pattern="^sel_model:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_model_text),
            ],
            # مرحله ۳: تنظیم base_url (دکمه یا تایپ)
            WAITING_API_BASE_URL: [
                CallbackQueryHandler(receive_api_base_url_callback, pattern="^sel_base_url:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_base_url_text),
            ],
            # مرحله ۴: دریافت نام
            WAITING_API_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_label),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── ConversationHandler: اهدای API ────────────────────
    donate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_donate, pattern="^panel_donate$")],
        states={
            WAITING_DONATE_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_donate_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── ConversationHandler: افزودن API عمومی (ادمین) ─────
    add_pub_api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_public_api, pattern="^adm_add_pub_api$")],
        states={
            ADD_PUB_KEY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_key)],
            ADD_PUB_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_label)],
            ADD_PUB_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_limit)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── ConversationHandler: افزودن پرامپت (ادمین) ────────
    add_prompt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_prompt, pattern="^adm_add_prompt$")],
        states={
            ADD_PROMPT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_title)],
            ADD_PROMPT_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_desc)],
            ADD_PROMPT_TEXT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── هندلرها ───────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("admin",  admin_panel))
    app.add_handler(CommandHandler("panel",  lambda u, c: show_panel(u, c)))

    # ConversationHandler ها (باید قبل از CallbackQueryHandler های عمومی)
    app.add_handler(add_api_conv)
    app.add_handler(donate_conv)
    app.add_handler(add_pub_api_conv)
    app.add_handler(add_prompt_conv)

    # PDF
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # Callback ها - کاربر
    app.add_handler(CallbackQueryHandler(show_panel,          pattern="^panel_main$"))
    app.add_handler(CallbackQueryHandler(show_my_apis,        pattern="^panel_apis$"))
    app.add_handler(CallbackQueryHandler(show_history,        pattern="^panel_history$"))
    app.add_handler(CallbackQueryHandler(toggle_fallback,     pattern="^toggle_fallback$"))
    app.add_handler(CallbackQueryHandler(delete_api_confirm,  pattern="^del_api:"))
    app.add_handler(CallbackQueryHandler(on_prompt_selected,  pattern="^select_prompt:"))
    app.add_handler(CallbackQueryHandler(on_api_source_selected, pattern="^api_source:"))
    app.add_handler(CallbackQueryHandler(on_fallback_selected,   pattern="^fallback:"))

    # Callback ها - ادمین
    app.add_handler(CallbackQueryHandler(show_public_apis,       pattern="^adm_public_apis$"))
    app.add_handler(CallbackQueryHandler(show_prompts,           pattern="^adm_prompts$"))
    app.add_handler(CallbackQueryHandler(toggle_pub_api,         pattern="^adm_toggle_pub:"))
    app.add_handler(CallbackQueryHandler(delete_pub_api,         pattern="^adm_del_pub:"))
    app.add_handler(CallbackQueryHandler(toggle_prompt,          pattern="^adm_toggle_prompt:"))
    app.add_handler(CallbackQueryHandler(set_default_prompt,     pattern="^adm_default_prompt:"))
    app.add_handler(CallbackQueryHandler(delete_prompt_handler,  pattern="^adm_del_prompt:"))
    app.add_handler(CallbackQueryHandler(approve_donation,       pattern="^admin_approve_donation:"))
    app.add_handler(CallbackQueryHandler(reject_donation,        pattern="^admin_reject_donation:"))
    app.add_handler(CallbackQueryHandler(adm_back,               pattern="^adm_back$"))

    # دستور ناشناخته
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    print("🤖 ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()