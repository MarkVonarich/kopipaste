# routers/commands.py — v2026.02.26-01
__version__ = "2026.02.26-01"

from telegram import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from datetime import datetime, date, timedelta

from db.database import get_conn
from db.queries import ensure_user, get_user_budgets, get_user_currency, get_ml_stats, get_personal_category_suggestion, get_global_category_suggestion, get_global_alias_exact
from services.ml_train import train_model
from ui.keyboards import main_menu_kb
from services.onboarding import onboarding_welcome
from settings import ADMIN_USER_IDS, VOICE_INPUT_ENABLED, VOICE_TRANSCRIBE_PROVIDER, VOICE_TRANSCRIBE_MODEL
import os
import shutil
from jobs.daily import previous_week_period, previous_month_period, build_weekly_report_text, build_monthly_report_text, _build_smart_morning_text
from services.ml_prep import normalize_alias_text, normalize_for_ml
from services.ml_suggest import get_top2_suggestions
from services.quick import get_quick_buttons


async def on_startup(app):
    from cache.global_dict import load_global_cache
    from services.currency import update_fx_rates

    load_global_cache()
    update_fx_rates()
    await app.bot.set_my_commands([
        BotCommand('start', 'Главное меню / онбординг'),
        BotCommand('settings', 'Настройки'),
        BotCommand('budget', 'Показать бюджеты'),
        BotCommand('limits', 'Мои лимиты'),
        BotCommand('export', 'Экспорт XLSX/CSV'),
        BotCommand('about', 'О боте и зачем он нужен'),
        BotCommand('mlstats', 'ML-статистика top1/top2'),
        BotCommand('mltrain', 'Обучить ML модель (admin)'),
        BotCommand('admin_weekly_report_preview', 'Превью недельного отчёта (admin)'),
        BotCommand('admin_monthly_report_preview', 'Превью месячного отчёта (admin)'),
        BotCommand('admin_smart_morning_preview', 'Превью утреннего лимит-сигнала (admin)'),
        BotCommand('admin_category_learning_debug', 'Диагностика global category learning (admin)'),
    ])


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = ensure_user(uid)
    if context.args and any(a.lower() in ('onboarding', 'ob') for a in context.args):
        is_new = True
    if is_new:
        return await onboarding_welcome(update, context)
    await update.message.reply_text('🔷 Главное меню:', reply_markup=main_menu_kb())


async def cmd_settings(update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('💱 Валюта', callback_data='menu_currency'),
         InlineKeyboardButton('⏰ Напоминание', callback_data='menu_reminder')],
        [InlineKeyboardButton('🔔 Оповещения', callback_data='menu_notifications')],
        [InlineKeyboardButton('💰 Бюджеты', callback_data='settings_budgets')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
        [InlineKeyboardButton('◀️ В меню', callback_data='start_main')],
    ])
    await update.message.reply_text('⚙️ Настройки:', reply_markup=kb)


async def cmd_budget(update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('💰 Открыть бюджеты', callback_data='settings_budgets')],
    ])
    await update.message.reply_text('💰 Бюджеты теперь в настройках.', reply_markup=kb)


async def cmd_export(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    context.user_data.pop('export_state', None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📅 Текущий месяц', callback_data='exp_m')],
        [InlineKeyboardButton('🗓 Последние 14 дней', callback_data='exp_14')],
        [InlineKeyboardButton('⚙️ Свой период', callback_data='exp_custom')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='start_main')],
    ])
    import logging
    logging.getLogger(__name__).info('export_menu_opened user_id=%s', cid)
    await update.message.reply_text('📤 Экспорт записей\n\nВыбери период, за который выгрузить операции.', reply_markup=kb)


async def cmd_about(update, context: ContextTypes.DEFAULT_TYPE):
    from settings import SUPPORT_USERNAME
    txt = (
        "Я *КопиPaste* — делаю учёт денег простым и быстрым.\n\n"
        "⚙️ Как пользоваться:\n"
        "• Пишите коротко: «молоко 150», «пицца 450 вчера», «зарплата 70 000».\n"
        "• Если я знаю вашу привычную категорию — запишу сразу.\n"
        "• Если нет — подскажу и запомню ваш выбор.\n\n"
        "🎯 Зачем это всё: регулярный учёт помогает увидеть, куда утекают деньги, и снижает лишние траты.\n\n"
        "Команды: /start /settings /budget /export /mlstats\n"
        "Поддержка: @" + SUPPORT_USERNAME.lstrip('@')
    )
    await update.message.reply_text(txt, parse_mode='Markdown', disable_web_page_preview=True)


async def cmd_mlstats(update, context: ContextTypes.DEFAULT_TYPE):
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
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📌 Мои лимиты', callback_data='lim_list')],
    ])
    await update.message.reply_text('📌 Управление лимитами', reply_markup=kb)


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
    key_loaded = bool((os.getenv('OPENAI_API_KEY') or os.getenv('RECEIPT_OCR_API_KEY') or '').strip())
    txt = (
        f"VOICE_INPUT_ENABLED: {VOICE_INPUT_ENABLED}\n"
        f"VOICE_TRANSCRIBE_PROVIDER: {VOICE_TRANSCRIBE_PROVIDER}\n"
        f"VOICE_TRANSCRIBE_MODEL: {VOICE_TRANSCRIBE_MODEL}\n"
        f"OPENAI_KEY_LOADED: {key_loaded}\n"
        f"FFMPEG_AVAILABLE: {bool(shutil.which('ffmpeg'))}"
    )
    await update.message.reply_text(txt)
