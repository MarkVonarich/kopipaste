# jobs/scheduler.py — v2026.01.25-01
__version__ = "2026.01.25-01"

import asyncio
import logging
from telegram.ext import ContextTypes

from .daily import evening_reminder_job, weekly_report_job, monthly_report_job, smart_morning_limit_job, user_reminders_job
from services.currency import update_fx_rates
from services.automatic_notifications import process_due_notifications
from services.challenges import challenge_daily_prompt_job
from services.posthog_exporter import export_job_run, load_posthog_config
from settings import ENABLE_DAY_NUDGE, ENABLE_EVENING_REMINDER, ENABLE_SMART_MORNING_LIMITS, POSTHOG_EXPORT_INTERVAL_SECONDS

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


async def posthog_outbox_export_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(export_job_run)
    except Exception as e:
        log.warning("posthog_export: failed reason=%s", type(e).__name__)


async def automatic_notifications_due_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        counts = await process_due_notifications(context)
        if counts.get("claimed"):
            log.info(
                "automatic_notifications: claimed=%s sent=%s retrying=%s dead_letter=%s skipped=%s",
                counts.get("claimed"),
                counts.get("sent"),
                counts.get("retrying"),
                counts.get("dead_letter"),
                counts.get("skipped"),
            )
    except Exception as e:
        log.warning("automatic_notifications: failed reason=%s", type(e).__name__)


async def challenges_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        counts = await challenge_daily_prompt_job(context)
        if any(counts.values()):
            log.info(
                "challenges_prompt: sent=%s deferred=%s skipped=%s",
                counts.get("sent"),
                counts.get("deferred"),
                counts.get("skipped"),
            )
    except Exception as e:
        log.warning("challenges_prompt: failed reason=%s", type(e).__name__)


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
    app.job_queue.run_repeating(user_reminders_job, interval=300, first=260, name="user_reminders_job")
    log.info('Added job "user_reminders_job"')
    app.job_queue.run_repeating(automatic_notifications_due_job, interval=60, first=60, name="automatic_notifications_due")
    log.info('Added job "automatic_notifications_due"')
    app.job_queue.run_repeating(challenges_prompt_job, interval=3600, first=360, name="challenge_daily_prompt")
    log.info('Added job "challenge_daily_prompt"')

    posthog_config = load_posthog_config()
    if posthog_config.can_send:
        app.job_queue.run_repeating(
            posthog_outbox_export_job,
            interval=max(10, int(POSTHOG_EXPORT_INTERVAL_SECONDS)),
            first=300,
            name="posthog_outbox_export",
        )
        log.info('Added job "posthog_outbox_export"')
    else:
        log.info('Skipped job "posthog_outbox_export" reason=%s', posthog_config.error_code)
