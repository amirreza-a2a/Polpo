# ============================================================
#  main.py
# ============================================================

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
    ContextTypes,
)

from config import BOT_TOKEN, TEMP_DIR, OUTPUT_DIR
from pathlib import Path
from database.connection import init_db
from infrastructure.logging import setup_logging, get_logger


from handlers.common import start, unknown_command
from handlers.pdf import (
    handle_pdf, on_prompt_selected, on_api_source_selected, on_fallback_selected,
)


from handlers.quick_convert import handle_quick_photo


from handlers.user import (
    show_panel, show_my_apis,
    # تاریخچه و آرشیو (جدید)
    show_history, redeliver_job,
    show_resume_options, resume_same_api,
    resume_new_api, resume_api_source, resume_fallback,
    toggle_auto_retry,
    toggle_auto_p2_start, set_auto_p2_prompt,

    # افزودن API خصوصی
    start_add_api,
    receive_api_key,
    receive_api_model_callback, receive_api_model_text,
    receive_api_base_url_callback, receive_api_base_url_text,
    receive_api_label,
    # اهدا
    start_donate,
    receive_donate_key,
    receive_donate_model_callback, receive_donate_model_text,
    receive_donate_base_url_callback, receive_donate_base_url_text,
    # سایر
    delete_api_confirm, toggle_fallback, cancel,
    WAITING_API_KEY, WAITING_API_LABEL, WAITING_API_MODEL, WAITING_API_BASE_URL,
    WAITING_DONATE_KEY, WAITING_DONATE_MODEL, WAITING_DONATE_BASE_URL,
)

from handlers.pipeline2 import (
    start_pipeline2,
    on_p2_prompt_selected,
    on_p2_api_source_selected,
    on_p2_fallback_selected,
)


from handlers.admin import (
    admin_panel, show_public_apis,
    start_add_public_api,
    receive_pub_api_key,
    receive_pub_api_model_callback, receive_pub_api_model_text,
    receive_pub_api_base_url_callback, receive_pub_api_base_url_text,
    receive_pub_api_label, receive_pub_api_limit,
    start_edit_pub_api,
    receive_edit_pub_model_callback, receive_edit_pub_model_text,
    receive_edit_pub_base_url_callback, receive_edit_pub_base_url_text,
    toggle_pub_api, delete_pub_api,
    show_prompts, start_add_prompt,
    receive_prompt_title, receive_prompt_desc, receive_prompt_text,
    toggle_prompt, set_default_prompt, delete_prompt_handler,
    approve_donation, reject_donation, adm_back,
    ADD_PUB_KEY, ADD_PUB_MODEL, ADD_PUB_BASE_URL, ADD_PUB_LABEL, ADD_PUB_LIMIT,
    EDIT_PUB_MODEL, EDIT_PUB_BASE_URL,
    ADD_PROMPT_TITLE, ADD_PROMPT_DESC, ADD_PROMPT_TEXT,
    show_p2_prompts, start_add_p2_prompt,
    receive_p2_prompt_title, receive_p2_prompt_desc, receive_p2_prompt_text,
    toggle_p2_prompt, set_default_p2_prompt, delete_p2_prompt_handler,
    ADD_P2_PROMPT_TITLE, ADD_P2_PROMPT_DESC, ADD_P2_PROMPT_TEXT,
    # پرامپت تبدیل سریع (جدید)
    show_quick_convert_prompt, start_edit_quick_convert_prompt,
    receive_quick_convert_prompt,
    EDIT_QUICK_CONVERT_PROMPT,
)


logger = get_logger("main")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    هندلر سراسری خطاهای تلگرام جهت لاگ‌کردن ساختاریافته استثناها و ارسال پیام مناسب به کاربر.
    """
    logger.error("Exception while handling an update:", exc_info=context.error)

    if isinstance(update, Update):
        try:
            error_msg = "⚠️ متأسفانه در پردازش درخواست شما خطایی رخ داد. لطفاً کمی بعد دوباره تلاش کنید."
            if update.effective_message:
                await update.effective_message.reply_text(error_msg)
            elif update.callback_query:
                await update.callback_query.answer(error_msg, show_alert=True)
        except Exception as e:
            logger.warning("Could not send error notification to user: %s", e)


def main():
    setup_logging()
    init_db()
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(global_error_handler)

    # ════════════════════════════════════════════════════════
    #  ConversationHandlers
    # ════════════════════════════════════════════════════════

    # ─── API خصوصی (۴ مرحله) ──────────────────────────────
    add_api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_api, pattern="^add_api$")],
        states={
            WAITING_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_key),
            ],
            WAITING_API_MODEL: [
                CallbackQueryHandler(receive_api_model_callback, pattern="^sel_model_new_api:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_model_text),
            ],
            WAITING_API_BASE_URL: [
                CallbackQueryHandler(receive_api_base_url_callback, pattern="^sel_base_url:__default__$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_base_url_text),
            ],
            WAITING_API_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_label),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── اهدای API (۴ مرحله) ──────────────────────────────
    donate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_donate, pattern="^panel_donate$")],
        states={
            WAITING_DONATE_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_donate_key),
            ],
            WAITING_DONATE_MODEL: [
                CallbackQueryHandler(receive_donate_model_callback, pattern="^sel_model_donate:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_donate_model_text),
            ],
            WAITING_DONATE_BASE_URL: [
                CallbackQueryHandler(receive_donate_base_url_callback, pattern="^sel_base_url:__default__$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_donate_base_url_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── افزودن API عمومی ادمین (۵ مرحله) ─────────────────
    add_pub_api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_public_api, pattern="^adm_add_pub_api$")],
        states={
            ADD_PUB_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_key),
            ],
            ADD_PUB_MODEL: [
                CallbackQueryHandler(receive_pub_api_model_callback, pattern="^adm_sel_model:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_model_text),
            ],
            ADD_PUB_BASE_URL: [
                CallbackQueryHandler(receive_pub_api_base_url_callback, pattern="^adm_pub_base_url:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_base_url_text),
            ],
            ADD_PUB_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_label),
            ],
            ADD_PUB_LIMIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pub_api_limit),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── ویرایش API عمومی (۲ مرحله) ───────────────────────
    edit_pub_api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_pub_api, pattern="^adm_edit_pub:")],
        states={
            EDIT_PUB_MODEL: [
                CallbackQueryHandler(receive_edit_pub_model_callback, pattern="^adm_sel_model:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_pub_model_text),
            ],
            EDIT_PUB_BASE_URL: [
                CallbackQueryHandler(receive_edit_pub_base_url_callback, pattern="^edit_pub_base_url:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_pub_base_url_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── افزودن پرامپت Pipeline1 (۳ مرحله) ─────────────────
    add_prompt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_prompt, pattern="^adm_add_prompt$")],
        states={
            ADD_PROMPT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_title)],
            ADD_PROMPT_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_desc)],
            ADD_PROMPT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt_text),
                MessageHandler(filters.Document.ALL, receive_prompt_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── افزودن پرامپت Pipeline2 (۳ مرحله) ─────────────────
    add_p2_prompt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_p2_prompt, pattern="^adm_add_p2_prompt$")],
        states={
            ADD_P2_PROMPT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_p2_prompt_title)],
            ADD_P2_PROMPT_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_p2_prompt_desc)],
            ADD_P2_PROMPT_TEXT:  [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_p2_prompt_text),
                MessageHandler(filters.Document.ALL, receive_p2_prompt_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ─── ویرایش پرامپت تبدیل سریع (۱ مرحله) ────────────────
    edit_quick_prompt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_quick_convert_prompt,
                                           pattern="^adm_edit_quick_prompt$")],
        states={
            EDIT_QUICK_CONVERT_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quick_convert_prompt),
                MessageHandler(filters.Document.ALL, receive_quick_convert_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ════════════════════════════════════════════════════════
    #  ثبت هندلرها
    # ════════════════════════════════════════════════════════

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("panel", lambda u, c: show_panel(u, c)))

    # ConversationHandlers (باید قبل از CallbackQueryHandlers عمومی باشند)
    app.add_handler(add_api_conv)
    app.add_handler(donate_conv)
    app.add_handler(add_pub_api_conv)
    app.add_handler(edit_pub_api_conv)
    app.add_handler(add_prompt_conv)
    app.add_handler(add_p2_prompt_conv)
    app.add_handler(edit_quick_prompt_conv)

    # PDF
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # تبدیل سریع تصویر (بدون دکمه، بدون صف)
    app.add_handler(MessageHandler(filters.PHOTO, handle_quick_photo))

    # ─── Callbacks کاربر ───────────────────────────────────
    app.add_handler(CallbackQueryHandler(show_panel,        pattern="^panel_main$"))
    app.add_handler(CallbackQueryHandler(show_my_apis,      pattern="^panel_apis$"))
    app.add_handler(CallbackQueryHandler(toggle_fallback,   pattern="^toggle_fallback$"))
    app.add_handler(CallbackQueryHandler(toggle_auto_retry, pattern="^toggle_auto_retry$"))
    app.add_handler(CallbackQueryHandler(delete_api_confirm,pattern="^del_api:"))

    # PDF flow
    app.add_handler(CallbackQueryHandler(on_prompt_selected,       pattern="^select_prompt:"))
    app.add_handler(CallbackQueryHandler(on_api_source_selected,   pattern="^api_source:"))
    app.add_handler(CallbackQueryHandler(on_fallback_selected,     pattern="^fallback:"))

    # ─── تاریخچه و آرشیو ────────────────────────────────────
    app.add_handler(CallbackQueryHandler(show_history,         pattern=r"^history_page:\d+$"))
    app.add_handler(CallbackQueryHandler(redeliver_job,        pattern=r"^redeliver:\d+$"))
    app.add_handler(CallbackQueryHandler(show_resume_options,  pattern=r"^resume_show:\d+$"))
    app.add_handler(CallbackQueryHandler(resume_same_api,      pattern=r"^resume_same:\d+$"))
    app.add_handler(CallbackQueryHandler(resume_new_api,       pattern=r"^resume_new_api:\d+$"))
    app.add_handler(CallbackQueryHandler(resume_api_source,    pattern=r"^resume_api_src:"))
    app.add_handler(CallbackQueryHandler(resume_fallback,      pattern=r"^resume_fallback:"))

    # ─── Pipeline 2 (کاربر) ────────────────────────────────
    app.add_handler(CallbackQueryHandler(toggle_auto_p2_start,        pattern="^toggle_auto_p2$"))
    app.add_handler(CallbackQueryHandler(set_auto_p2_prompt,          pattern=r"^set_auto_p2_prompt:\d+$"))
    app.add_handler(CallbackQueryHandler(start_pipeline2,             pattern=r"^start_p2:\d+$"))
    app.add_handler(CallbackQueryHandler(on_p2_prompt_selected,       pattern=r"^p2_select_prompt:\d+$"))
    app.add_handler(CallbackQueryHandler(on_p2_api_source_selected,   pattern="^p2_api_source:"))
    app.add_handler(CallbackQueryHandler(on_p2_fallback_selected,     pattern="^p2_fallback:"))

    # ─── Callbacks ادمین ────────────────────────────────────
    app.add_handler(CallbackQueryHandler(show_public_apis,      pattern="^adm_public_apis$"))
    app.add_handler(CallbackQueryHandler(show_prompts,          pattern="^adm_prompts$"))
    app.add_handler(CallbackQueryHandler(toggle_pub_api,        pattern="^adm_toggle_pub:"))
    app.add_handler(CallbackQueryHandler(delete_pub_api,        pattern="^adm_del_pub:"))
    app.add_handler(CallbackQueryHandler(toggle_prompt,         pattern="^adm_toggle_prompt:"))
    app.add_handler(CallbackQueryHandler(set_default_prompt,    pattern="^adm_default_prompt:"))
    app.add_handler(CallbackQueryHandler(delete_prompt_handler, pattern="^adm_del_prompt:"))
    app.add_handler(CallbackQueryHandler(approve_donation,      pattern="^admin_approve_donation:"))
    app.add_handler(CallbackQueryHandler(reject_donation,       pattern="^admin_reject_donation:"))
    app.add_handler(CallbackQueryHandler(adm_back,              pattern="^adm_back$"))

    # ─── Pipeline 2 (ادمین) ────────────────────────────────
    app.add_handler(CallbackQueryHandler(show_p2_prompts,          pattern="^adm_p2_prompts$"))
    app.add_handler(CallbackQueryHandler(toggle_p2_prompt,         pattern="^adm_toggle_p2_prompt:"))
    app.add_handler(CallbackQueryHandler(set_default_p2_prompt,    pattern="^adm_default_p2_prompt:"))
    app.add_handler(CallbackQueryHandler(delete_p2_prompt_handler, pattern="^adm_del_p2_prompt:"))

    # ─── پرامپت تبدیل سریع (ادمین) ──────────────────────────
    app.add_handler(CallbackQueryHandler(show_quick_convert_prompt, pattern="^adm_quick_prompt$"))

    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    print("🤖 ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()