# jobs/scheduler.py — v2026.01.25-01
__version__ = "2026.01.25-01"

import asyncio
import logging
from telegram.ext import ContextTypes

from .daily import day_nudge_job, evening_reminder_job
from services.currency import update_fx_rates

log = logging.getLogger("finbot.scheduler")


async def fx_update_job(context: ContextTypes.DEFAULT_TYPE):
    """
    PTB JobQueue ожидает async callback.
    update_fx_rates() — синхронная и возвращает dict, поэтому:
    - выполняем в отдельном потоке (не блокируем event loop)
    - не отдаём dict наружу (возвращаем None)
    """
    try:
        await asyncio.to_thread(update_fx_rates)
        log.info("fx_update: done")
    except Exception as e:
        # считаем это не критикой для работы бота в целом
        log.exception("fx_update: failed: %s", e)


def register_jobs(app):
    # 1) дневной «пинок» — каждые 5 мин
    app.job_queue.run_repeating(day_nudge_job, interval=300, first=60, name="day_nudge")

    # 2) вечернее — каждые 5 мин
    app.job_queue.run_repeating(evening_reminder_job, interval=300, first=120, name="evening_reminder")

    # 3) FX — раз в 12 часов
    app.job_queue.run_repeating(fx_update_job, interval=43200, first=180, name="fx_update")
