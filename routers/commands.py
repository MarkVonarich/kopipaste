# routers/commands.py — v2026.02.26-01
__version__ = "2026.02.26-01"

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes
from datetime import datetime, date, timedelta

from db.database import get_conn
from db.queries import ensure_user, get_user_budgets, get_user_currency, get_user_locale, get_ml_stats, get_personal_category_suggestion, get_global_category_suggestion, get_global_alias_exact, reminders_list
from services.ml_train import train_model
from ui.keyboards import export_menu_kb, help_menu_kb, limits_budgets_hub_kb, main_menu_kb, reminders_menu_kb, settings_menu_kb
from services.onboarding import onboarding_welcome
from settings import ADMIN_USER_IDS
from jobs.daily import previous_week_period, previous_month_period, build_weekly_report_text, build_monthly_report_text, _build_smart_morning_text
from services.ml_prep import normalize_alias_text, normalize_for_ml
from services.ml_suggest import get_top2_suggestions
from services.quick import get_quick_buttons
from services.reminder_totals import render_reminder_totals
from services.voice_transcription import voice_config_status
from services.activity import has_financial_activity_today
from services.i18n import resolve_locale, t
from services.notification_preview import build_preview, render_admin_preview
from services.personal_data_deletion import dry_run_delete_user_data, format_dry_run
from db.database import pg_fetchall
from time import time as unix_time
from services.acquisition import capture_acquisition
from services.product_events import ProductEvent, track_product_event
from services.security_events import SecurityEvent, track_security_event
from services.user_time import resolve_user_timezone, user_local_date
from scripts.analytics_status import analytics_status_counts, render_status
from services.posthog_exporter import export_status_counts


async def on_startup(app):
    from cache.global_dict import load_global_cache
    from services.currency import update_fx_rates

    load_global_cache()
    update_fx_rates()
    public_commands = [
        BotCommand('start', 'Главное меню'),
        BotCommand('settings', 'Настройки'),
        BotCommand('help', 'Помощь'),
    ]
    await app.bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())

    admin_commands = public_commands + [
        BotCommand('admin_reminders_preview', 'Диагностика напоминаний (admin)'),
        BotCommand('mlstats', 'ML-статистика (admin)'),
        BotCommand('mltrain', 'Обучить ML модель (admin)'),
        BotCommand('admin_weekly_report_preview', 'Превью недельного отчёта (admin)'),
        BotCommand('admin_monthly_report_preview', 'Превью месячного отчёта (admin)'),
        BotCommand('admin_smart_morning_preview', 'Превью утреннего лимит-сигнала (admin)'),
        BotCommand('admin_category_learning_debug', 'Диагностика global category learning (admin)'),
        BotCommand('admin_voice_status', 'Статус голосового ввода (admin)'),
        BotCommand('admin_activity_status', 'Статус activity/inactivity (admin)'),
    ]
    for admin_id in ADMIN_USER_IDS:
        await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = ensure_user(uid)
    payload = " ".join(context.args or []).strip()
    if payload:
        capture_acquisition(user_id=uid, payload=payload)
    track_product_event(ProductEvent(
        event_name="bot_started",
        user_id=uid,
        locale=getattr(update.effective_user, "language_code", None),
        status="success",
        properties={"is_new": bool(is_new), "has_start_payload": bool(payload)},
    ))
    if context.args and any(a.lower() in ('onboarding', 'ob') for a in context.args):
        is_new = True
    if is_new:
        return await onboarding_welcome(update, context)
    locale = get_user_locale(uid)
    await update.message.reply_text(t('menu.main.title', locale), reply_markup=main_menu_kb(locale))


async def cmd_settings(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    locale = get_user_locale(uid)
    await update.message.reply_text(t('menu.settings', locale), reply_markup=settings_menu_kb(locale))


def _command_privacy_locale(update, user_id: int) -> str:
    try:
        rows = pg_fetchall("SELECT locale FROM public.users WHERE user_id=%s LIMIT 1", (user_id,))
        saved = rows[0][0] if rows else None
    except Exception:
        saved = None
    return resolve_locale(saved, getattr(update.effective_user, "language_code", None))


async def cmd_delete_my_data(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    locale = _command_privacy_locale(update, uid)
    context.user_data['delete_my_data'] = {'actor_user_id': uid, 'step': 'explain', 'expires_at': unix_time() + 900}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.export_data', locale), callback_data='exp_menu')],
        [InlineKeyboardButton(t('privacy.delete.continue', locale), callback_data='privacy_delete_stage2')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')],
    ])
    await update.message.reply_text(t('privacy.delete.explain', locale), reply_markup=kb)


async def cmd_help(update, context: ContextTypes.DEFAULT_TYPE):
    from settings import SUPPORT_USERNAME
    uid = update.effective_user.id
    locale = get_user_locale(uid)

    txt = (
        "❓ Помощь\n\n"
        "Пишите операции обычным текстом: «кофе 250», «зарплата 70000», "
        "«такси 900 вчера». Можно отправить голосовое или фото чека, если эти функции включены.\n\n"
        "Через кнопки доступны бюджеты, лимиты, напоминания, экспорт и настройки. "
        "Команды меню: /start, /settings, /help.\n\n"
        "Поддержка: @" + SUPPORT_USERNAME.lstrip('@')
    )
    await update.message.reply_text(txt, disable_web_page_preview=True, reply_markup=help_menu_kb(locale))


async def cmd_budget(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    locale = get_user_locale(uid)
    await update.message.reply_text(t('limits_budgets.title', locale), reply_markup=limits_budgets_hub_kb(locale))


async def cmd_export(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    context.user_data.pop('export_state', None)
    import logging
    logging.getLogger(__name__).info('export_menu_opened user_id=%s', cid)
    await update.message.reply_text('📤 Экспорт записей\n\nВыбери период, за который выгрузить операции.', reply_markup=export_menu_kb())


async def cmd_reminders(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    rows = reminders_list(cid, active_only=True)
    if not rows:
        return await update.message.reply_text('🔔 Напоминания\n\nПока ничего нет.\n\nМожно добавить подписку, платёж, будущую трату или доход — я напомню заранее.', reply_markup=reminders_menu_kb(False))
    lines = ['🔔 Напоминания', '', 'Активные:']
    btns = []
    for i, r in enumerate(rows[:5], start=1):
        lines.append(f"{i}. {r['title']} — {int(r['amount']):,} ₽, {r['event_date'].day} число".replace(',', ' '))
        btns.append([InlineKeyboardButton(f"Открыть: {r['title'][:20]}", callback_data=f"rem_o|{r['id']}")])
    lines.extend(['', render_reminder_totals(rows, get_user_locale(cid))])
    btns.extend(reminders_menu_kb(True).inline_keyboard)
    await update.message.reply_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(btns))


async def cmd_about(update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_help(update, context)


async def cmd_mlstats(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_mlstats", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    cid = update.effective_chat.id
    stats = get_ml_stats(cid, days=30)
    picks = stats.get('picks', 0)
    top1 = stats.get('top1_hit', 0)
    top2 = stats.get('top2_hit', 0)
    b_picks = stats.get('baseline_picks', 0)
    b_top1 = stats.get('baseline_top1_hit', 0)
    b_top2 = stats.get('baseline_top2_hit', 0)
    m_picks = stats.get('model_picks', 0)
    m_top1 = stats.get('model_top1_hit', 0)
    m_top2 = stats.get('model_top2_hit', 0)

    def pct(v, n):
        return (v * 100.0 / n) if n else 0.0

    await update.message.reply_text(
        "ML stats (30д):\n"
        f"• overall picks: {picks}\n"
        f"• overall top1/top2: {top1} ({pct(top1, picks):.1f}%) / {top2} ({pct(top2, picks):.1f}%)\n"
        f"• baseline picks: {b_picks}; top1/top2: {b_top1} ({pct(b_top1, b_picks):.1f}%) / {b_top2} ({pct(b_top2, b_picks):.1f}%)\n"
        f"• model picks: {m_picks}; top1/top2: {m_top1} ({pct(m_top1, m_picks):.1f}%) / {m_top2} ({pct(m_top2, m_picks):.1f}%)"
    )


async def cmd_mltrain(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in ADMIN_USER_IDS:
        return await update.message.reply_text('⛔ Команда только для администратора.')

    await update.message.reply_text('🧠 Запускаю обучение модели...')
    try:
        report = train_model(days=180, op_type='Расходы', limit=20000)
    except Exception as e:
        return await update.message.reply_text(f'❌ Ошибка обучения: {e}')

    if not report.get('ok'):
        return await update.message.reply_text(
            f"⚠️ Обучение не выполнено: {report.get('error')} (samples={report.get('samples', 0)})"
        )

    await update.message.reply_text(
        "✅ ML model trained\n"
        f"• version: {report.get('model_version')}\n"
        f"• trained_at: {report.get('trained_at')}\n"
        f"• samples: {report.get('samples_total')}\n"
        f"• classes: {len(report.get('classes', []))}\n"
        f"• holdout top1/top2: {report.get('holdout_top1')} / {report.get('holdout_top2')}\n"
        f"• train_sec: {report.get('train_sec')}"
    )


async def cmd_limits(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    locale = get_user_locale(uid)
    await update.message.reply_text(t('limits_budgets.title', locale), reply_markup=limits_budgets_hub_kb(locale))


def _is_admin(update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in ADMIN_USER_IDS


async def cmd_admin_weekly_report_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_weekly_report_preview", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = user_local_date(uid)
    start, end = previous_week_period(today)
    txt = build_weekly_report_text(uid, start, end)
    await update.message.reply_text("[PREVIEW] Недельный отчёт\n\n" + txt)


async def cmd_admin_monthly_report_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_monthly_report_preview", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = user_local_date(uid)
    start, end = previous_month_period(today)
    txt = build_monthly_report_text(uid, start, end)
    await update.message.reply_text("[PREVIEW] Месячный отчёт\n\n" + txt)


async def cmd_admin_smart_morning_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_smart_morning_preview", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = user_local_date(uid)
    txt = _build_smart_morning_text(uid, today) or "Сигналов по лимитам сейчас нет."
    await update.message.reply_text("[PREVIEW] Утренний лимит-сигнал\n\n" + txt)


async def cmd_admin_category_learning_debug(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_category_learning_debug", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    raw = " ".join(context.args or []).strip()
    if not raw:
        return await update.message.reply_text('Использование: /admin_category_learning_debug <текст>')
    uid = update.effective_user.id
    alias_norm = normalize_alias_text(raw)
    ml_norm = normalize_for_ml(raw)
    personal = get_personal_category_suggestion(uid, alias_norm, 'Расходы')
    ga = get_global_alias_exact(alias_norm, 'Расходы')
    global_s = get_global_category_suggestion(alias_norm, 'Расходы')
    seed = 'Продукты' if alias_norm in {'дикси', 'пятерочка', 'пятёрочка', 'магнит', 'лента'} else None
    top2, meta = get_top2_suggestions(uid, ml_norm, 'Расходы')
    final_cat = (top2[0]['cat'] if top2 else '—')
    txt = (
        f"raw: {raw}\n"
        f"alias_norm: {alias_norm or '—'}\n"
        f"ml_norm: {ml_norm or '—'}\n"
        f"personal: {(personal.get('category') + ' (' + personal.get('reason') + ')') if personal else '—'}\n"
        f"global_alias: {(ga.get('category') + ' pop=' + str(ga.get('popularity'))) if ga else '—'}\n"
        f"global: {(global_s.get('category') if global_s else '—')}\n"
        f"global_conf: {(round(global_s.get('confidence', 0), 3) if global_s else '—')}\n"
        f"global_votes/distinct: {(str(global_s.get('votes_count')) + '/' + str(global_s.get('distinct_users'))) if global_s else '—'}\n"
        f"merchant_seed: {seed or '—'}\n"
        f"final: {final_cat}\n"
        f"reason: {meta.get('reason', '—')}"
    )
    await update.message.reply_text(txt)


async def cmd_admin_voice_status(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_voice_status", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    status = voice_config_status()
    txt = (
        f"enabled: {status.enabled}\n"
        f"provider: {status.provider or 'none'}\n"
        f"model: {status.model or 'none'}\n"
        f"credential_present: {status.credential_present}\n"
        f"dependency_available: {status.dependency_available}\n"
        f"ffmpeg_available: {status.ffmpeg_available}\n"
        f"ffprobe_available: {status.ffprobe_available}\n"
        f"max_duration_seconds: {status.max_duration_seconds}"
    )
    await update.message.reply_text(txt)


async def cmd_admin_activity_status(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_activity_status", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = int(context.args[0]) if context.args else update.effective_user.id
    tz_name = resolve_user_timezone(uid).timezone_name
    active = has_financial_activity_today(uid, tz_name)
    try:
        event_count = pg_fetchall(
            "SELECT COUNT(*) FROM public.financial_activity_events WHERE user_id=%s AND local_date=CURRENT_DATE",
            (uid,),
        )[0][0]
    except Exception:
        event_count = 'n/a'
    await update.message.reply_text(
        f"activity_status\nuser_id: {uid}\ntimezone: {tz_name}\nactivity_today: {active}\nactivity_events_today_utc: {event_count}"
    )


async def cmd_admin_reminders_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_reminders_preview", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    active = pg_fetchall("SELECT COUNT(*) FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE", (uid,))[0][0]
    today = user_local_date(uid)
    due = pg_fetchall("SELECT COUNT(*) FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE AND (event_date-notify_days_before)=%s", (uid, today))[0][0]
    nxt = pg_fetchall("SELECT title, event_date FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE ORDER BY event_date LIMIT 5", (uid,))
    lines = [f'active: {active}', f'due_today: {due}', 'next5:']
    for t, d in nxt:
        lines.append(f'- {t[:20]}: {d}')
    lines.append('job_enabled: true')
    await update.message.reply_text('\n'.join(lines))


def _admin_target_user(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if getattr(context, "args", None):
        try:
            return int(context.args[0])
        except Exception:
            return update.effective_user.id
    return update.effective_user.id


async def _cmd_admin_notification_preview(update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key=f"cmd_admin_{kind}_preview", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    target_user_id = _admin_target_user(update, context)
    preview = build_preview(target_user_id, kind)
    await update.message.reply_text(render_admin_preview(preview))


async def cmd_admin_notification_preview(update, context: ContextTypes.DEFAULT_TYPE):
    return await _cmd_admin_notification_preview(update, context, "auto")


async def cmd_admin_subscription_preview(update, context: ContextTypes.DEFAULT_TYPE):
    return await _cmd_admin_notification_preview(update, context, "subscription")


async def cmd_admin_recurring_spend_preview(update, context: ContextTypes.DEFAULT_TYPE):
    return await _cmd_admin_notification_preview(update, context, "recurring-spend")


async def cmd_admin_limit_alert_preview(update, context: ContextTypes.DEFAULT_TYPE):
    return await _cmd_admin_notification_preview(update, context, "limit")


async def cmd_admin_delete_data_dry_run(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_delete_data_dry_run", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    target_user_id = _admin_target_user(update, context)
    result = dry_run_delete_user_data(target_user_id)
    await update.message.reply_text(format_dry_run(result))


async def cmd_admin_analytics_status(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_analytics_status", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    try:
        await update.message.reply_text(render_status(analytics_status_counts()))
    except Exception as exc:
        await update.message.reply_text(f"analytics_status unavailable: {type(exc).__name__}")


async def cmd_admin_posthog_status(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_posthog_status", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    try:
        counts = export_status_counts()
        lines = [
            f"export_enabled: {counts['enabled']}",
            f"pending: {counts['pending']}",
            f"retrying: {counts['retrying']}",
            f"sent: {counts['sent']}",
            f"dead_letter: {counts['dead_letter']}",
            f"last_successful_export: {counts['last_sent_timestamp']}",
            f"last_safe_error_code: {counts['last_safe_error_code']}",
        ]
        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        await update.message.reply_text(f"posthog_status unavailable: {type(exc).__name__}")


async def cmd_admin_posthog_test_event(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        track_security_event(SecurityEvent(event_name="admin_command_denied", user_id=update.effective_user.id if update.effective_user else None, rule_key="cmd_admin_posthog_test_event", action_taken="denied"))
        return await update.message.reply_text('⛔ Команда только для администратора.')
    event_id = track_product_event(ProductEvent(
        event_name="posthog_connection_test",
        user_id=update.effective_user.id,
        source="admin_test",
        status="success",
        properties={"test": True},
    ))
    if event_id:
        await update.message.reply_text("PostHog test event queued through the normal outbox. It has not necessarily been sent yet.")
    else:
        await update.message.reply_text("PostHog test event could not be queued locally; check analytics schema/status.")
