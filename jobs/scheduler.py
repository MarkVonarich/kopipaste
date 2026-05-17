# jobs/scheduler.py — v2026.01.25-01
__version__ = "2026.01.25-01"

import asyncio
import logging
from telegram.ext import ContextTypes

from .daily import evening_reminder_job, weekly_report_job, monthly_report_job, smart_morning_limit_job
from services.currency import update_fx_rates
from settings import ENABLE_DAY_NUDGE, ENABLE_EVENING_REMINDER, ENABLE_SMART_MORNING_LIMITS

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
    log.info(
        "scheduler flags: ENABLE_DAY_NUDGE=%s ENABLE_EVENING_REMINDER=%s ENABLE_SMART_MORNING_LIMITS=%s",
        ENABLE_DAY_NUDGE,
        ENABLE_EVENING_REMINDER,
        ENABLE_SMART_MORNING_LIMITS,
    )

    # 1) day_nudge intentionally disabled by default
    if ENABLE_DAY_NUDGE:
        log.info('Added job "day_nudge" (disabled implementation placeholder)')
    else:
        log.info('Skipped job "day_nudge" by feature flag')

    # 2) вечернее — каждые 5 мин
    if ENABLE_EVENING_REMINDER:
        app.job_queue.run_repeating(evening_reminder_job, interval=300, first=120, name="evening_reminder")
        log.info('Added job "evening_reminder"')
    else:
        log.info('Skipped job "evening_reminder" by feature flag')

    # 3) FX — раз в 12 часов
    app.job_queue.run_repeating(fx_update_job, interval=43200, first=180, name="fx_update")
    log.info('Added job "fx_update"')

    # 4) Weekly report v1: every 5 minutes, sends only at local Monday 12:00 per user
    app.job_queue.run_repeating(weekly_report_job, interval=300, first=200, name="weekly_report")
    log.info('Added job "weekly_report"')

    # 5) Monthly report v1: every 5 minutes, sends only at local day=1 10:00 per user
    app.job_queue.run_repeating(monthly_report_job, interval=300, first=220, name="monthly_report")
    log.info('Added job "monthly_report"')

    # 6) Smart morning limits: every 5 minutes, sends only in local 09:00-11:00 window
    if ENABLE_SMART_MORNING_LIMITS:
        app.job_queue.run_repeating(smart_morning_limit_job, interval=300, first=240, name="smart_morning_limit")
        log.info('Added job "smart_morning_limit"')
    else:
        log.info('Skipped job "smart_morning_limit" by feature flag')
