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
from ui.keyboards import help_menu_kb, limits_budgets_hub_kb, main_menu_kb, settings_menu_kb
from services.onboarding import onboarding_welcome
from settings import ADMIN_USER_IDS, VOICE_INPUT_ENABLED, VOICE_TRANSCRIBE_PROVIDER, VOICE_TRANSCRIBE_MODEL
import os
import shutil
from jobs.daily import previous_week_period, previous_month_period, build_weekly_report_text, build_monthly_report_text, _build_smart_morning_text
from services.ml_prep import normalize_alias_text, normalize_for_ml
from services.ml_suggest import get_top2_suggestions
from services.quick import get_quick_buttons
from services.reminder_totals import render_reminder_totals
from services.receipt_parser import ocr_credential_diagnostic
from services.activity import has_financial_activity_today
from services.i18n import t
from db.database import pg_fetchall


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
        BotCommand('admin_voice_status', 'Статус voice/OCR ключей (admin)'),
        BotCommand('admin_activity_status', 'Статус activity/inactivity (admin)'),
    ]
    for admin_id in ADMIN_USER_IDS:
        await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = ensure_user(uid)
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
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📅 Сегодня', callback_data='exp_today'), InlineKeyboardButton('🗓 7 дней', callback_data='exp_7')],
        [InlineKeyboardButton('🗓 14 дней', callback_data='exp_14')],
        [InlineKeyboardButton('📅 Текущий месяц', callback_data='exp_m'), InlineKeyboardButton('↩️ Прошлый месяц', callback_data='exp_pm')],
        [InlineKeyboardButton('📆 Текущий год', callback_data='exp_y'), InlineKeyboardButton('↩️ Прошлый год', callback_data='exp_py')],
        [InlineKeyboardButton('⚙️ Свой период', callback_data='exp_custom')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='start_main')],
    ])
    import logging
    logging.getLogger(__name__).info('export_menu_opened user_id=%s', cid)
    await update.message.reply_text('📤 Экспорт записей\n\nВыбери период, за который выгрузить операции.', reply_markup=kb)


async def cmd_reminders(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    rows = reminders_list(cid, active_only=True)
    if not rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Добавить', callback_data='rem_add')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')],
        ])
        return await update.message.reply_text('🔔 Напоминания\n\nПока ничего нет.\n\nМожно добавить подписку, платёж, будущую трату или доход — я напомню заранее.', reply_markup=kb)
    lines = ['🔔 Напоминания', '', 'Активные:']
    btns = []
    for i, r in enumerate(rows[:5], start=1):
        lines.append(f"{i}. {r['title']} — {int(r['amount']):,} ₽, {r['event_date'].day} число".replace(',', ' '))
        btns.append([InlineKeyboardButton(f"Открыть: {r['title'][:20]}", callback_data=f"rem_o|{r['id']}")])
    lines.extend(['', render_reminder_totals(rows, get_user_locale(cid))])
    btns.append([InlineKeyboardButton('➕ Добавить', callback_data='rem_add'), InlineKeyboardButton('📋 Все', callback_data='rem_all')])
    btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')])
    await update.message.reply_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(btns))


async def cmd_about(update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_help(update, context)


async def cmd_mlstats(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
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
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = datetime.utcnow().date()
    start, end = previous_week_period(today)
    txt = build_weekly_report_text(uid, start, end)
    await update.message.reply_text("[PREVIEW] Недельный отчёт\n\n" + txt)


async def cmd_admin_monthly_report_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = datetime.utcnow().date()
    start, end = previous_month_period(today)
    txt = build_monthly_report_text(uid, start, end)
    await update.message.reply_text("[PREVIEW] Месячный отчёт\n\n" + txt)


async def cmd_admin_smart_morning_preview(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    today = datetime.utcnow().date()
    txt = _build_smart_morning_text(uid, today) or "Сигналов по лимитам сейчас нет."
    await update.message.reply_text("[PREVIEW] Утренний лимит-сигнал\n\n" + txt)


async def cmd_admin_category_learning_debug(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
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
        return await update.message.reply_text('⛔ Команда только для администратора.')
    ocr_diag = ocr_credential_diagnostic()
    txt = (
        f"VOICE_INPUT_ENABLED: {VOICE_INPUT_ENABLED}\n"
        f"VOICE_TRANSCRIBE_PROVIDER: {VOICE_TRANSCRIBE_PROVIDER}\n"
        f"VOICE_TRANSCRIBE_MODEL: {VOICE_TRANSCRIBE_MODEL}\n"
        f"OPENAI_API_KEY configured: {ocr_diag['OPENAI_API_KEY_configured']}\n"
        f"RECEIPT_OCR_API_KEY configured: {ocr_diag['RECEIPT_OCR_API_KEY_configured']}\n"
        f"OCR credential source: {ocr_diag['selected_source']}\n"
        f"FFMPEG_AVAILABLE: {bool(shutil.which('ffmpeg'))}"
    )
    await update.message.reply_text(txt)


async def cmd_admin_activity_status(update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = int(context.args[0]) if context.args else update.effective_user.id
    try:
        rows = pg_fetchall(
            """
            SELECT COALESCE(np.timezone, uws.timezone, 'Europe/Moscow')
              FROM public.users u
              LEFT JOIN public.notification_preferences np ON np.user_id=u.user_id
              LEFT JOIN public.user_workspace_settings uws ON uws.user_id=u.user_id
             WHERE u.user_id=%s
             LIMIT 1
            """,
            (uid,),
        )
        tz_name = rows[0][0] if rows and rows[0][0] else 'Europe/Moscow'
    except Exception:
        tz_name = 'Europe/Moscow'
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
        return await update.message.reply_text('⛔ Команда только для администратора.')
    uid = update.effective_user.id
    active = pg_fetchall("SELECT COUNT(*) FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE", (uid,))[0][0]
    today = datetime.utcnow().date()
    due = pg_fetchall("SELECT COUNT(*) FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE AND (event_date-notify_days_before)=%s", (uid, today))[0][0]
    nxt = pg_fetchall("SELECT title, event_date FROM public.user_reminders WHERE user_id=%s AND is_active=TRUE ORDER BY event_date LIMIT 5", (uid,))
    lines = [f'active: {active}', f'due_today: {due}', 'next5:']
    for t, d in nxt:
        lines.append(f'- {t[:20]}: {d}')
    lines.append('job_enabled: true')
    await update.message.reply_text('\n'.join(lines))
