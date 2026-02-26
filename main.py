import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# main.py — v2026.01.25-01
__version__ = "2026.01.25-01"

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from logging_config import setup_logging
from settings import TELEGRAM_TOKEN

from routers.commands import cmd_start, cmd_settings, cmd_budget, cmd_export, cmd_about, cmd_mlstats, cmd_mltrain, on_startup
from routers.callbacks import callback_handler
from routers.messages import handle_text, handle_location
from jobs.scheduler import register_jobs

# узкоспециализированные колбэки подсказок категорий (sugg_*)
from routers.suggestions import register as register_suggestions  # MUST be before generic CallbackQueryHandler


async def on_error(update, context):
    """
    Глобальный error-handler для PTB (включая JobQueue).
    Убирает "No error handlers are registered" и даёт единый лог ошибок.
    """
    try:
        # context.error содержит оригинальное исключение
        context.application.logger.exception("Unhandled error", exc_info=context.error)
    except Exception:
        # на всякий случай, чтобы не словить рекурсию логгера
        pass


def main():
    setup_logging()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(on_startup)
        .build()
    )

    # ✅ подключаем error-handler
    app.add_error_handler(on_error)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("mlstats", cmd_mlstats))
    app.add_handler(CommandHandler("mltrain", cmd_mltrain))

    # ✅ patterns ^sugg_… раньше общего callback_handler
    register_suggestions(app)

    # Callbacks + messages
    app.add_handler(CallbackQueryHandler(callback_handler))  # generic
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Jobs
    register_jobs(app)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
