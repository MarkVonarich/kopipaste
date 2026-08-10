
# routers/messages.py — v2025.08.26-batch-05 (effective_message everywhere)
__version__ = "2025.08.26-batch-05"

import re
from datetime import datetime, timedelta, date
from decimal import Decimal
from telegram import ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from services.currency import detect_currency_token, convert_amount_if_needed
from services.export_flow import clear_export_wait_flags, parse_export_date, validate_export_period
from services.categories import (
    get_or_create_custom_category,
    category_reference_counts,
    is_protected_category,
    list_managed_categories,
    normalize_category_name,
    normalized_category_key,
)
from services.operations import category_options, commit_operation_draft, create_operation_draft, load_operation_draft, record_financial_operation
from services.records import get_user_alias, record_operation, send_operation_limit_alert
from services.reminders import _next_monthly_date
from services.workspaces import resolve_workspace
from routers.helpers import prompt_type_menu
from ui.keyboards import ml_top2_kb
from utils.parsing import parse_user_input, split_wo_date, parse_day_list
from utils.text import norm_text
from db.database import pg_fetchall
from db.queries import update_user_field, insert_ml_observation, update_limit_amount, get_limit_by_key, record_category_confirmation, get_user_tz, get_user_currency
from db.queries import update_last_operation_fields, update_operation_fields_by_id, get_last_operation, reminder_insert, reminder_update
from services.ml_prep import normalize_for_ml, normalize_alias_text
from services.ml_suggest import get_top2_suggestions
from services.receipt_parser import parse_receipt_image
from services.budgeting import create_category_budget_group, list_active_expense_categories, upsert_general_limit
from services.automatic_notifications import suppress_stale_timezone_sensitive_notifications
from services.notification_preferences import set_daily_notification_time, set_notification_timezone, set_quiet_hours_time
from services.i18n import resolve_locale, t
from services.personal_data_deletion import preview_delete_financial_history
from services.api_usage import ApiUsageEvent, track_api_usage
from services.product_events import ProductEvent, track_product_event
from services.user_time import is_valid_timezone_name, user_local_date
from services.voice_transcription import transcribe_telegram_voice, user_message_for_voice_reason
from services.goals import (
    GoalError,
    add_goal_movement,
    create_goal,
    format_date_ru,
    format_money,
    get_goal,
    parse_money,
    parse_nonnegative_money,
    render_goal_card_text,
    update_goal_details,
    update_goal_plan,
)
from ui.keyboards import category_budget_picker_kb
from settings import VOICE_TRANSCRIBE_MODEL, VOICE_TRANSCRIBE_PROVIDER
from utils.money import MoneyParseError, format_money as format_money_value, parse_decimal_amount_token, to_decimal_money
import logging
from time import time as unix_time
from secrets import token_urlsafe

log = logging.getLogger(__name__)
try:
    from timezonefinder import TimezoneFinder
except Exception:
    TimezoneFinder = None

BATCH_MAX = 25  # ограничение длины списка на один ввод


def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _message_privacy_locale(user_id: int, telegram_language_code: str | None = None) -> str:
    try:
        rows = pg_fetchall("SELECT locale FROM public.users WHERE user_id=%s LIMIT 1", (user_id,))
        saved = rows[0][0] if rows else None
    except Exception:
        saved = None
    return resolve_locale(saved, telegram_language_code)


def _history_period_label(start_date: date | None, end_date: date | None, locale: str) -> str:
    if start_date is None and end_date is None:
        return t('privacy.period.all', locale)
    fmt = "%d.%m.%Y"
    if start_date == end_date:
        return start_date.strftime(fmt)
    return f"{start_date.strftime(fmt)} — {end_date.strftime(fmt)}"


def _integer_major_amount(value) -> Decimal | None:
    try:
        amount = to_decimal_money(value, positive=True)
    except (MoneyParseError, ValueError):
        return None
    return amount




def _parse_amount_input(text: str) -> Decimal | None:
    t = (text or '').strip()
    m = list(re.finditer(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\d)", t))
    if not m:
        return None
    try:
        amount = parse_decimal_amount_token(m[-1].group(0))
    except (MoneyParseError, ValueError):
        return None
    return amount


def _parse_budget_amount(text: str) -> Decimal | None:
    try:
        return parse_decimal_amount_token(text or "")
    except (MoneyParseError, ValueError):
        return None


def _cbg_picker_markup(user_id: int, workspace_id: int | None, selected_tokens: set[str], page: int = 0):
    options = [item.__dict__ for item in list_active_expense_categories(user_id=user_id, workspace_id=workspace_id)]
    return options, category_budget_picker_kb(options, selected_tokens, page=page)


def _parse_reminder_title_amount(text: str) -> tuple[str, Decimal] | None:
    src = (text or '').strip()
    if not src:
        return None
    m = list(re.finditer(r"(\d+(?:[ \t]\d{3})*(?:[.,]\d+)?)", src))
    if not m:
        return None
    last = m[-1]
    amt_raw = last.group(1).replace(' ', '').replace(',', '.')
    try:
        amount = parse_decimal_amount_token(last.group(1))
    except (MoneyParseError, ValueError):
        return None
    title = (src[:last.start()] + src[last.end():]).strip()
    if not title:
        return None
    if amount <= 0 or amount >= Decimal("1000000000"):
        return None
    return norm_text(title), amount


def _parse_flexible_date(text: str):
    t = (text or '').strip().lower()
    if t == 'сегодня':
        return datetime.now().date()
    if t == 'вчера':
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).date()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', t):
            return datetime.strptime(t, '%Y-%m-%d').date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}', t):
            return datetime.strptime(t, '%d.%m.%Y').date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}', t):
            return datetime.strptime(f'{t}.{datetime.now().year}', '%d.%m.%Y').date()
    except Exception:
        return None
    return None


def _parse_reminder_event_date(text: str, today):
    t = (text or '').strip().lower()
    if t in {'сегодня', 'today'}:
        return today
    if t in {'завтра', 'tomorrow'}:
        from datetime import timedelta
        return today + timedelta(days=1)
    if t == 'послезавтра':
        from datetime import timedelta
        return today + timedelta(days=2)
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', t):
            return datetime.strptime(t, '%Y-%m-%d').date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{2,4}', t):
            fmt = '%d.%m.%Y' if len(t.split('.')[-1]) == 4 else '%d.%m.%y'
            return datetime.strptime(t, fmt).date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}', t):
            d = datetime.strptime(f'{t}.{today.year}', '%d.%m.%Y').date()
            return d if d >= today else d.replace(year=today.year + 1)
        m = re.fullmatch(r'(\d{1,2})(?:\s*(?:число|числа|-?го|го))?$', t)
        if m:
            day = int(m.group(1))
            if day < 1 or day > 31:
                return None
            from calendar import monthrange
            y, mo = today.year, today.month
            for _ in range(24):
                mdays = monthrange(y, mo)[1]
                if day <= mdays:
                    cand = today.replace(year=y, month=mo, day=day)
                    if cand >= today:
                        return cand
                mo += 1
                if mo > 12:
                    mo = 1; y += 1
    except Exception:
        return None
    return None


def _fmt_reminder_money(v) -> str:
    return format_money_value(v, "RUB")


def _goal_error_text(code: str) -> str:
    return {
        "invalid_amount": "Введите сумму больше нуля.",
        "past_deadline": "Срок цели не может быть в прошлом.",
        "insufficient_balance": "На цели доступна меньшая сумма.",
        "empty_name": "Введите непустое название цели.",
        "control_characters": "Название содержит служебные символы.",
        "name_too_long": "Название слишком длинное.",
        "duplicate_name": "Активная цель с таким названием уже есть.",
        "goal_not_found": "Эта кнопка устарела. Откройте цель заново.",
        "wrong_actor": "Эту цель может менять только владелец.",
    }.get(code, "Не удалось сохранить изменения. Данные цели не изменены. Попробуйте позже.")


def _goal_card_kb(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 К цели", callback_data=f"goal|o|{goal_id}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")],
    ])


def _goal_reminder_prompt_kb(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Включить напоминания", callback_data=f"goal|remtog|{goal_id}")],
        [InlineKeyboardButton("Пока без напоминаний", callback_data=f"goal|o|{goal_id}")],
    ])


def _goal_confirm_kb(token: str, goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"goal|confirm|{token}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal_id}"), InlineKeyboardButton("❌ Отмена", callback_data=f"goal|o|{goal_id}")],
    ])


def _goal_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать цель", callback_data="goal|save")],
        [InlineKeyboardButton("RUB", callback_data="goal|cur|RUB"), InlineKeyboardButton("USD", callback_data="goal|cur|USD"), InlineKeyboardButton("EUR", callback_data="goal|cur|EUR")],
        [InlineKeyboardButton("Отмена", callback_data="goal|cancel")],
    ])


def _goal_creation_preview(draft: dict) -> str:
    target = parse_money(draft.get("target_amount", "0"))
    saved = parse_nonnegative_money(draft.get("initial_amount") or "0")
    remaining = max(target - saved, Decimal("0"))
    return (
        "🎯 Проверьте цель\n\n"
        f"Название: {draft.get('display_name')}\n"
        f"Целевая сумма: {format_money(target, draft.get('currency') or 'RUB')}\n"
        f"Уже накоплено: {format_money(saved, draft.get('currency') or 'RUB')}\n"
        f"Осталось: {format_money(remaining, draft.get('currency') or 'RUB')}\n"
        f"Срок: {format_date_ru(date.fromisoformat(draft['deadline'])) if draft.get('deadline') else 'без срока'}\n"
        f"Валюта: {draft.get('currency') or 'RUB'}"
    )


def _goal_schedule_config(frequency: str) -> dict:
    if frequency == "monthly":
        return {"day": 5}
    if frequency == "twice_monthly":
        return {"days": [5, 20]}
    if frequency == "weekly":
        return {"weekday": 0}
    if frequency == "salary_monthly":
        return {"day": 5, "salary_payments_per_month": 1}
    if frequency == "salary_twice_monthly":
        return {"days": [5, 20], "salary_payments_per_month": 2}
    return {}


def _reminder_repeat_label(r: str, d: dict) -> str:
    return {
        'none': 'не повторять',
        'weekly': 'каждую неделю',
        'monthly': 'каждый месяц',
        'yearly': 'каждый год',
        'custom_days': f"каждые {int(d.get('repeat_interval_days') or 1)} дней",
    }.get(r or 'none', r or 'none')


def _reminder_date_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Сегодня', callback_data='rem_dt_today'), InlineKeyboardButton('Завтра', callback_data='rem_dt_tom')],
        [InlineKeyboardButton('1 число', callback_data='rem_dt_1'), InlineKeyboardButton('15 число', callback_data='rem_dt_15')],
        [InlineKeyboardButton('✏️ Ввести дату', callback_data='rem_dt_in')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
    ])


def _reminder_repeat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Не повторять', callback_data='rem_r_none')],
        [InlineKeyboardButton('Каждую неделю', callback_data='rem_r_week'), InlineKeyboardButton('Каждый месяц', callback_data='rem_r_month')],
        [InlineKeyboardButton('Каждый год', callback_data='rem_r_year'), InlineKeyboardButton('Свой период', callback_data='rem_r_custom')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
    ])


def _reminder_notify_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('В день события', callback_data='rem_n_0')],
        [InlineKeyboardButton('За 1 день', callback_data='rem_n_1'), InlineKeyboardButton('За 2 дня', callback_data='rem_n_2')],
        [InlineKeyboardButton('За 3 дня', callback_data='rem_n_3'), InlineKeyboardButton('За неделю', callback_data='rem_n_7')],
        [InlineKeyboardButton('✏️ Свой вариант', callback_data='rem_n_in')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
    ])


def _export_end_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Сегодня', callback_data='exp_custom_end_today'), InlineKeyboardButton('Вчера', callback_data='exp_custom_end_yday')],
        [InlineKeyboardButton('Конец месяца', callback_data='exp_custom_end_month')],
        [InlineKeyboardButton('✏️ Ввести дату', callback_data='exp_custom_end_input')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='exp_custom'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Сегодня', callback_data='exp_custom_start_today'), InlineKeyboardButton('Вчера', callback_data='exp_custom_start_yday')],
        [InlineKeyboardButton('1 число месяца', callback_data='exp_custom_start_first')],
        [InlineKeyboardButton('✏️ Ввести дату', callback_data='exp_custom_start_input')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='exp_menu'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Скачать XLSX', callback_data='exp_dl')],
        [InlineKeyboardButton('🔁 Изменить период', callback_data='exp_custom'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_rows(chat_id: int, dfrom: date, dto: date) -> list[dict]:
    rows = pg_fetchall("""SELECT id, op_date, type, category, amount, COALESCE(comment,''), COALESCE(to_jsonb(operations)->>'source', 'telegram'), COALESCE(currency, %s) FROM public.operations
                        WHERE chat_id=%s AND op_date BETWEEN %s AND %s
                          AND COALESCE(type,'') <> 'noop' AND COALESCE(category,'') <> 'Без операций'
                        ORDER BY op_date, id""", (get_user_currency(chat_id), chat_id, dfrom, dto))
    return [{'id': r[0], 'op_date': r[1], 'type': r[2], 'category': r[3], 'amount': to_decimal_money(r[4]), 'comment': r[5], 'source': r[6], 'currency': r[7]} for r in rows]


def _ocr_warning_message(warning: str | None) -> str:
    if warning == 'insufficient_quota':
        return 'Recognition is temporarily unavailable because the service configuration requires attention.'
    if warning == 'rate_limit':
        return 'The recognition service is busy. Try again shortly.'
    if warning == 'auth_error':
        return 'Recognition is temporarily unavailable.'
    if warning == 'network_error':
        return 'Could not reach the recognition service. Try again.'
    if warning == 'no_operations':
        return 'Не нашёл финансовых операций на изображении.'
    if warning == 'malformed_response':
        return 'Сервис распознавания вернул неожиданный ответ. Попробуй ещё раз.'
    if warning in {'image_too_large', 'empty_image'}:
        return 'Не удалось прочитать изображение. Попробуй отправить скриншот крупнее.'
    return 'Не смог распознать фото. Попробуй ещё раз или пришли скрин крупнее.'


def _reminder_confirmation(d: dict) -> tuple[str, InlineKeyboardMarkup]:
    ev = d.get('event_date')
    rpt = d.get('repeat_rule', 'none')
    next_after = None
    if rpt == 'weekly':
        next_after = ev + timedelta(days=7)
    elif rpt == 'monthly':
        next_after = _next_monthly_date(ev)
    elif rpt == 'yearly':
        try:
            next_after = ev.replace(year=ev.year + 1)
        except Exception:
            next_after = ev.replace(month=2, day=28, year=ev.year + 1)
    elif rpt == 'custom_days':
        next_after = ev + timedelta(days=int(d.get('repeat_interval_days') or 1))
    date_label = 'Первое списание' if rpt != 'none' else 'Дата'
    txt = (
        f"🔔 Напоминание\n\n"
        f"{d.get('title','—')} — {_fmt_reminder_money(d.get('amount',0))}\n"
        f"Тип: {d.get('rem_type','Расходы')}\n"
        f"Категория: {d.get('category','Прочее')}\n"
        f"{date_label}: {ev.strftime('%d.%m.%Y')}\n"
        f"Повтор: {_reminder_repeat_label(rpt, d)}"
        + (f"\nСледующее после этого: {next_after.strftime('%d.%m.%Y')}" if next_after else '')
        + f"\nНапомнить: за {d.get('notify_days_before',1)} дня"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Сохранить', callback_data='rem_save')],
        [InlineKeyboardButton('✏️ Изменить', callback_data='rem_add')],
        [InlineKeyboardButton('❌ Отмена', callback_data='rem_menu')],
    ])
    return txt, kb


async def _safe_reply(emsg, text_md: str, reply_markup=None):
    """Reply in markdown, fallback to plain text if telegram rejects entities."""
    try:
        return await emsg.reply_text(text_md, parse_mode='Markdown', reply_markup=reply_markup)
    except Exception as e:
        log.warning("reply_text markdown failed, fallback plain: %s", e)
        plain = (text_md or '').replace('*', '').replace('_', '').replace('`', '')
        return await emsg.reply_text(plain, reply_markup=reply_markup)


def _is_group_chat(update) -> bool:
    chat_type = getattr(update.effective_chat, 'type', 'private') if update.effective_chat else 'private'
    return chat_type in {'group', 'supergroup'}


def _bot_identity(context: ContextTypes.DEFAULT_TYPE) -> tuple[int | None, str | None]:
    bot = getattr(context, "bot", None)
    bot_id = getattr(bot, "id", None)
    username = getattr(bot, "username", None)
    if username is None:
        app = getattr(context, "application", None)
        app_bot = getattr(app, "bot", None)
        username = getattr(app_bot, "username", None)
        bot_id = bot_id if bot_id is not None else getattr(app_bot, "id", None)
    username = str(username or "").lstrip("@").casefold() or None
    try:
        bot_id = int(bot_id) if bot_id is not None else None
    except (TypeError, ValueError):
        bot_id = None
    return bot_id, username


def _entity_text(message, entity) -> str:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    parser = getattr(message, "parse_entity", None)
    if callable(parser):
        try:
            return parser(entity)
        except Exception:
            pass
    offset = int(getattr(entity, "offset", 0) or 0)
    length = int(getattr(entity, "length", 0) or 0)
    return text[offset:offset + length]


def _message_mentions_bot(message, *, bot_id: int | None, bot_username: str | None) -> bool:
    for entity in getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []:
        entity_type = str(getattr(entity, "type", "") or "").lower()
        if entity_type == "mention":
            if not bot_username:
                continue
            if _entity_text(message, entity).lstrip("@").casefold() == bot_username:
                return True
        elif entity_type == "text_mention":
            user = getattr(entity, "user", None)
            if bot_id is not None and getattr(user, "id", None) == bot_id:
                return True
    return False


def _message_replies_to_bot(message, *, bot_id: int | None, bot_username: str | None) -> bool:
    replied = getattr(message, "reply_to_message", None)
    from_user = getattr(replied, "from_user", None) if replied else None
    if not from_user:
        return False
    if bot_id is not None and getattr(from_user, "id", None) == bot_id:
        return True
    return bool(bot_username and str(getattr(from_user, "username", "") or "").lstrip("@").casefold() == bot_username)


def _group_custom_category_state_matches(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    st = context.user_data.get("await_group_custom_category")
    if not isinstance(st, dict):
        return False
    try:
        return (
            int(st.get("chat_id") or 0) == int(getattr(update.effective_chat, "id", 0) or 0)
            and int(st.get("actor_user_id") or 0) == int(getattr(update.effective_user, "id", 0) or 0)
        )
    except (TypeError, ValueError):
        return False


def group_message_has_explicit_bot_intent(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_group_chat(update):
        return True
    if _group_custom_category_state_matches(update, context):
        return True
    message = update.effective_message or update.message
    if not message:
        return False
    bot_id, bot_username = _bot_identity(context)
    return (
        _message_mentions_bot(message, bot_id=bot_id, bot_username=bot_username)
        or _message_replies_to_bot(message, bot_id=bot_id, bot_username=bot_username)
    )


def strip_explicit_bot_mention(update, context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    if not _is_group_chat(update):
        return text
    message = update.effective_message or update.message
    if not message:
        return text
    _bot_id, bot_username = _bot_identity(context)
    if not bot_username:
        return text
    ranges: list[tuple[int, int]] = []
    for entity in getattr(message, "entities", None) or []:
        if str(getattr(entity, "type", "") or "").lower() != "mention":
            continue
        if _entity_text(message, entity).lstrip("@").casefold() != bot_username:
            continue
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if text[offset:offset + length].lstrip("@").casefold() != bot_username:
            continue
        ranges.append((offset, offset + length))
    for start, end in sorted(ranges, reverse=True):
        text = text[:start] + text[end:]
    return " ".join(text.split())


def _guess_operation_type_from_text(text: str, merchant: str = "") -> str:
    t = f"{text or ''} {merchant or ''}".lower()
    if re.search(r"\b(salary|income|paycheck|wage|bonus|refund|cashback)\b", t) or re.search(r"зарплат|доход|пополн|кэшбэк|кешбэк", t):
        return 'Доходы'
    return 'Расходы'


def _group_setup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🧩 Создать пространство группы', callback_data='group_setup')],
        [InlineKeyboardButton('❌ Отмена', callback_data='noop_back')],
    ])


def _group_join_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Присоединиться к пространству группы', callback_data='group_join')],
    ])


def _group_category_kb(draft_id: str, cats: list[str]) -> InlineKeyboardMarkup:
    opts = category_options(cats)
    rows = [[InlineKeyboardButton(cat, callback_data=f'gpick|{draft_id}|{key}')] for key, cat in opts.items()]
    rows.append([InlineKeyboardButton('➕ Новая категория', callback_data=f'gadd|{draft_id}')])
    rows.append([InlineKeyboardButton('❌ Отмена', callback_data=f'gcancel|{draft_id}')])
    return InlineKeyboardMarkup(rows)


async def _process_group_text(update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    text = input_text or ""
    chat_id = update.effective_chat.id
    actor_user_id = update.effective_user.id
    emsg = update.effective_message
    workspace = resolve_workspace(chat_id, actor_user_id, getattr(update.effective_chat, 'type', 'group'))
    if not workspace.is_configured:
        return await emsg.reply_text(
            'Эта группа ещё не подключена к отдельному финансовому пространству. '
            'Администратор группы может создать его кнопкой ниже.',
            reply_markup=_group_setup_kb(),
        )
    if workspace.role not in {'owner', 'admin', 'member'}:
        return await emsg.reply_text(
            'Эта группа уже подключена. Нажмите кнопку ниже, чтобы присоединиться как участник и записывать операции.',
            reply_markup=_group_join_kb(),
        )
    try:
        merch_display, amt_raw, dt, src_curr = parse_user_input(text)
    except ValueError as e:
        return await emsg.reply_text('Не понял сумму. Пример: coffee 200')
    merch = norm_text(merch_display)
    amt_final, note = convert_amount_if_needed(actor_user_id, amt_raw, src_curr or detect_currency_token(text))
    op_type = _guess_operation_type_from_text(text, merch)
    top2, sugg_meta = get_top2_suggestions(actor_user_id, normalize_for_ml(text), op_type)
    if len(top2) < 2:
        top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
    cats = [top2[0]['cat'], top2[1]['cat']]
    draft_id = create_operation_draft(
        workspace=workspace,
        amount=amt_final,
        op_type=op_type,
        merchant=merch,
        op_date=dt,
        source=context.user_data.get('operation_source') or 'text',
        raw_text=text,
        categories=cats,
        note=note,
    )
    log.info('group_operation_draft_created chat_id=%s actor_user_id=%s workspace_id=%s draft_id=%s source=%s', chat_id, actor_user_id, workspace.workspace_id, draft_id, sugg_meta.get('source', 'baseline'))
    return await _safe_reply(
        emsg,
        f"Категория?\n➖ {format_money_value(amt_final, get_user_currency(actor_user_id))} • {_md_escape(merch)}\nПространство: {_md_escape(workspace.name)}",
        reply_markup=_group_category_kb(draft_id, cats),
    )


async def _process_free_text(update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    """
    Одна строка → тот же старый флоу.
    Используется и в одиночном режиме, и в батче (на текущем элементе).
    """
    text = input_text or ""
    cid  = update.effective_chat.id
    emsg = update.effective_message  # универсальный объект сообщения (и для callback'ов тоже)

    if _is_group_chat(update):
        if not group_message_has_explicit_bot_intent(update, context):
            return None
        text = strip_explicit_bot_mention(update, context, text)
        if not text:
            return None
        return await _process_group_text(update, context, text)

    for key in ("edit_mode", "edit_operation_id", "edit_ctx"):
        context.user_data.pop(key, None)

    try:
        merch_display, amt_raw, dt, src_curr = parse_user_input(text)
    except ValueError as e:
        reason = str(e)
        if reason == "no_amount":
            base, dt = split_wo_date(text)
            base = base.strip() or "операция"
            context.user_data['pending'] = {'merch': norm_text(base), 'time': dt}
            context.user_data['await_amount'] = True
            return await emsg.reply_text(f"Введите сумму для «{base}» (например, 250):")
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type='Расходы',
                action='parse_failed',
                meta={'source': 'telegram', 'error': reason},
            )
        except Exception:
            pass
        if reason == "bad_amount":
            return await emsg.reply_text("⚠️ Неверная сумма. Пример: «пицца 450 вчера».")
        return await emsg.reply_text("⚠️ Не понял сумму. Пример: «пицца 450 вчера». Нажмите «Примеры» в меню.")

    merch = norm_text(merch_display)

    # FX: детект валюты именно из текущего куска текста (важно для батча)
    src_curr = src_curr or detect_currency_token(text)
    amt_final, note = convert_amount_if_needed(cid, amt_raw, src_curr)

    alias = get_user_alias(cid, merch)
    if alias:
        typ, cat = alias
        # Чтобы raw_text в операции был ровно этот кусок
        context.user_data['batch_item_text'] = text
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type=typ,
                action='fallback_direct_write',
                chosen_category=cat,
                chosen_type=typ,
                meta={'source': 'telegram', 'flow': 'alias_hit'},
            )
        except Exception:
            pass
        return await record_operation(cat, amt_final, dt, typ, update, context, note, merchant=merch)

    op_type = _guess_operation_type_from_text(text, merch)
    normalized = normalize_for_ml(text)
    top2, sugg_meta = get_top2_suggestions(cid, normalized, op_type)
    if len(top2) < 2:
        top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
    cat1, cat2 = top2[0]['cat'], top2[1]['cat']
    context.user_data['pending'] = {
        'merch': merch,
        'amt': amt_final,
        'time': dt,
        'type': op_type,
        'source': context.user_data.get('operation_source') or 'text',
        'note': note,
        'ml_cat1': cat1,
        'ml_cat2': cat2,
        'ml_top2': top2,
        'ml_source': sugg_meta.get('source', 'baseline'),
        'ml_model_version': sugg_meta.get('model_version'),
    }
    try:
        insert_ml_observation(
            user_id=cid,
            chat_id=cid,
            raw_text=text,
            normalized_text=normalized,
            detected_type=op_type,
            action='suggest_shown',
            suggested_top2=top2,
            confidence_top1=top2[0].get('score'),
            meta={'source': sugg_meta.get('source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'currency_detected': src_curr, 'suggest': sugg_meta, 'model_version': sugg_meta.get('model_version'), 'trained_at': sugg_meta.get('trained_at')},
        )
    except Exception:
        pass

    msg = await _safe_reply(
        emsg,
        f"Категория?\n➖ {format_money_value(amt_final, get_user_currency(cid))} • {_md_escape(merch)}",
        reply_markup=ml_top2_kb(cat1, cat2),
    )
    context.user_data['suggest_msg_id'] = msg.message_id
    context.user_data['batch_item_text'] = text
    return


# ─────────────────────────────────────────────
# Батч-контроллер: последовательно, по одному
# ─────────────────────────────────────────────

async def _batch_start(update, context: ContextTypes.DEFAULT_TYPE, items: list[str]):
    context.user_data['batch_active'] = True
    context.user_data['batch_queue']  = list(items)  # копия
    context.user_data['batch_total']  = len(items)
    context.user_data['batch_done']   = 0
    await _batch_next(update, context)

async def _batch_next(update, context: ContextTypes.DEFAULT_TYPE):
    q = context.user_data.get('batch_queue') or []
    if not q:
        # Завершили батч
        context.user_data['batch_active'] = False
        context.user_data['batch_item_text'] = ""
        return
    item = q.pop(0)
    context.user_data['batch_queue'] = q
    context.user_data['batch_item_text'] = item
    await _process_free_text(update, context, item)

async def continue_batch_if_needed(update, context: ContextTypes.DEFAULT_TYPE):
    """Вызывается после успешной записи одной операции (из services.records)."""
    if not context.user_data.get('batch_active'):
        return
    context.user_data['batch_done'] = int(context.user_data.get('batch_done', 0)) + 1
    await _batch_next(update, context)


async def handle_photo(update, context: ContextTypes.DEFAULT_TYPE):
    if _is_group_chat(update) and not group_message_has_explicit_bot_intent(update, context):
        return None
    cid = update.effective_chat.id
    emsg = update.effective_message
    try:
        file = None
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        elif update.message.document and (update.message.document.mime_type or '').startswith('image/'):
            file = await update.message.document.get_file()
        if not file:
            return await emsg.reply_text('Не удалось прочитать изображение. Попробуй отправить фото ещё раз.')

        image_bytes = await file.download_as_bytearray()
        result = parse_receipt_image(bytes(image_bytes), cid)
        if not result.configured:
            return await emsg.reply_text('Фото получил, но распознавание пока не настроено на сервере.')

        if result.warning in {'provider_error', 'auth_error', 'insufficient_quota', 'rate_limit', 'network_error', 'image_too_large', 'empty_image', 'openai_pkg_missing', 'malformed_response'}:
            return await emsg.reply_text(_ocr_warning_message(result.warning))

        if not result.candidates:
            return await emsg.reply_text(_ocr_warning_message(result.warning))

        prepared = []
        for c in result.candidates:
            amount = _integer_major_amount(c.amount)
            if amount is None:
                continue
            prepared.append({
                'amount': str(amount),
                'category': c.category,
                'type': c.op_type,
                'date': c.op_date.isoformat(),
                'merchant': c.merchant,
                'confidence': c.confidence,
                'raw_text': c.raw_text,
            })
        if not prepared:
            return await emsg.reply_text('Не нашёл операций с корректной суммой. Попробуй отправить фото крупнее.')
        context.user_data['receipt_candidates'] = prepared
        context.user_data['receipt_warning'] = result.warning
        lines = ['🧾 Нашёл операции:', '']
        for i, c in enumerate(prepared[:20], start=1):
            major = c['category'] if c['type'] == 'Расходы' else 'Доходы'
            lines.append(f"{i}. {major} — {format_money_value(c['amount'], 'RUB')} — {c['merchant']}")
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Записать всё', callback_data='receipt_confirm_all')],
            [InlineKeyboardButton('✏️ Изменить', callback_data='receipt_review_one')],
            [InlineKeyboardButton('❌ Отмена', callback_data='receipt_cancel')],
        ])
        if result.warning:
            lines.append('\n⚠️ Я не уверен в части строк, лучше проверь перед записью.')
        await emsg.reply_text('\n'.join(lines), reply_markup=kb)
    except Exception as e:
        log.exception('receipt parse failed user=%s err=%s', cid, e)
        await emsg.reply_text('Не удалось обработать изображение. Попробуй ещё раз позже.')


async def handle_voice(update, context: ContextTypes.DEFAULT_TYPE):
    if _is_group_chat(update) and not group_message_has_explicit_bot_intent(update, context):
        return None
    emsg = update.effective_message
    msg = update.message
    media = getattr(msg, "voice", None) or getattr(msg, "audio", None)
    result = await transcribe_telegram_voice(media)
    uid = update.effective_user.id if update.effective_user else update.effective_chat.id
    if not result.ok:
        track_api_usage(ApiUsageEvent(
            provider=VOICE_TRANSCRIBE_PROVIDER,
            model=VOICE_TRANSCRIBE_MODEL,
            feature="voice_transcription",
            status="failed",
            user_id=uid,
            latency_ms=result.latency_ms,
            error_code=result.reason,
            metadata={"duration_seconds": int(getattr(media, "duration", 0) or 0)},
        ))
        log.info("voice_pipeline_failed user=%s reason=%s", update.effective_chat.id, result.reason)
        return await emsg.reply_text(user_message_for_voice_reason(result.reason or "voice_provider_request_failed"))

    track_api_usage(ApiUsageEvent(
        provider=result.provider,
        model=result.model,
        feature="voice_transcription",
        status="success",
        user_id=uid,
        latency_ms=result.latency_ms,
        metadata={
            "duration_seconds": int(getattr(media, "duration", 0) or 0),
            "normalized": bool(result.normalized_changed),
            "language": result.language,
        },
    ))
    log.info("voice_transcribe_ok user=%s normalized=%s lang=%s", update.effective_chat.id, result.normalized_changed, result.language)
    normalized_text = result.normalized_text
    try:
        parse_user_input(normalized_text)
    except ValueError as exc:
        reason = str(exc)
        track_api_usage(ApiUsageEvent(
            provider=result.provider,
            model=result.model,
            feature="voice_transcription",
            status="failed",
            user_id=uid,
            error_code="voice_parse_failed",
            metadata={"parse_reason": reason, "language": result.language},
        ))
        heard = normalized_text[:120]
        return await emsg.reply_text(
            f"Я услышал: «{heard}»\n\n"
            "Не удалось определить сумму или категорию.\n"
            "Попробуйте сказать, например:\n«Продукты 500»."
        )
    context.user_data['operation_source'] = 'voice'
    await _process_free_text(update, context, normalized_text)
    log.info('voice_to_text_flow: ok user=%s', update.effective_chat.id)
    return


# ─────────────────────────────────────────────
# Основной хэндлер входящих сообщений
# ─────────────────────────────────────────────

async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    cid  = update.effective_chat.id
    emsg = update.effective_message

    if _is_group_chat(update) and context.user_data.get('await_group_custom_category') and not _group_custom_category_state_matches(update, context):
        context.user_data.pop('await_group_custom_category', None)
        return await emsg.reply_text('Этот черновик операции больше не подходит к текущему чату или пользователю. Отправьте операцию заново.')

    if _is_group_chat(update):
        if not group_message_has_explicit_bot_intent(update, context):
            return None
        text = strip_explicit_bot_mention(update, context, text)
        if not text:
            return None

    delete_state = context.user_data.get('delete_my_data')
    if isinstance(delete_state, dict) and delete_state.get('step') == 'phrase':
        locale = _message_privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data.pop('delete_my_data', None)
        return await emsg.reply_text(
            t('privacy.stale', locale),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')]]),
        )

    goal_draft = context.user_data.get('goal_draft')
    if isinstance(goal_draft, dict):
        if goal_draft.get('actor_user_id') != update.effective_user.id or goal_draft.get('expires_at', 0) < unix_time():
            context.user_data.pop('goal_draft', None)
            return await emsg.reply_text('Черновик цели устарел. Начните заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎯 Цели', callback_data='goal|home')]]))
        step = goal_draft.get('step')
        try:
            if step == 'name':
                from services.goals import normalize_goal_name

                goal_draft['display_name'] = normalize_goal_name(text)
                goal_draft['step'] = 'target'
                goal_draft['expires_at'] = unix_time() + 1800
                context.user_data['goal_draft'] = goal_draft
                return await emsg.reply_text('Введите целевую сумму.')
            if step == 'target':
                goal_draft['target_amount'] = str(parse_money(text))
                goal_draft['step'] = 'deadline_choice'
                goal_draft['expires_at'] = unix_time() + 1800
                context.user_data['goal_draft'] = goal_draft
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton('Без срока', callback_data='goal|deadline|none')],
                    [InlineKeyboardButton('✏️ Ввести срок', callback_data='goal|deadline|input')],
                    [InlineKeyboardButton('❌ Отмена', callback_data='goal|cancel')],
                ])
                return await emsg.reply_text('У цели есть срок?', reply_markup=kb)
            if step == 'deadline':
                parsed = _parse_flexible_date(text)
                today = user_local_date(update.effective_user.id)
                if not parsed or parsed < today:
                    return await emsg.reply_text('Срок цели не может быть в прошлом. Введите дату ещё раз.')
                goal_draft['deadline'] = parsed.isoformat()
                goal_draft['step'] = 'initial'
                context.user_data['goal_draft'] = goal_draft
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton('Пока 0', callback_data='goal|saved|zero')],
                    [InlineKeyboardButton('✏️ Ввести сумму', callback_data='goal|saved|input')],
                    [InlineKeyboardButton('❌ Отмена', callback_data='goal|cancel')],
                ])
                return await emsg.reply_text('Сколько уже накоплено?', reply_markup=kb)
            if step == 'initial':
                goal_draft['initial_amount'] = str(parse_nonnegative_money(text))
                goal_draft['step'] = 'preview'
                context.user_data['goal_draft'] = goal_draft
                return await emsg.reply_text(_goal_creation_preview(goal_draft), reply_markup=_goal_preview_kb())
        except GoalError as exc:
            return await emsg.reply_text(_goal_error_text(str(exc)))

    goal_action = context.user_data.get('goal_action')
    if isinstance(goal_action, dict):
        if goal_action.get('actor_user_id') != update.effective_user.id or goal_action.get('expires_at', 0) < unix_time():
            context.user_data.pop('goal_action', None)
            return await emsg.reply_text('Действие с целью устарело. Откройте цель заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎯 Цели', callback_data='goal|home')]]))
        goal_id = int(goal_action.get('goal_id'))
        workspace_id = goal_action.get('workspace_id')
        mode = goal_action.get('mode')
        goal = get_goal(goal_id, update.effective_user.id, workspace_id)
        if not goal:
            context.user_data.pop('goal_action', None)
            return await emsg.reply_text('Цель не найдена.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎯 Цели', callback_data='goal|home')]]))
        try:
            if mode in {'contribution', 'withdrawal'}:
                amount = parse_money(text)
                token = token_urlsafe(8)
                goal_action.update({'token': token, 'amount': str(amount), 'expires_at': unix_time() + 900, 'used': False})
                context.user_data['goal_action'] = goal_action
                verb = 'пополнить' if mode == 'contribution' else 'снять'
                return await emsg.reply_text(f"Подтвердите: {verb} {format_money(amount, goal.currency)}.", reply_markup=_goal_confirm_kb(token, goal.id))
            if mode == 'adjustment':
                new_balance = parse_nonnegative_money(text)
                diff = new_balance - goal.current_balance
                token = token_urlsafe(8)
                goal_action.update({'token': token, 'new_balance': str(new_balance), 'expires_at': unix_time() + 900, 'used': False})
                context.user_data['goal_action'] = goal_action
                return await emsg.reply_text(
                    f"Корректировка прогресса\n\nБыло: {format_money(goal.current_balance, goal.currency)}\n"
                    f"Станет: {format_money(new_balance, goal.currency)}\n"
                    f"Корректировка: {format_money(abs(diff), goal.currency)}",
                    reply_markup=_goal_confirm_kb(token, goal.id),
                )
            if mode == 'plan_contribution':
                amount = parse_money(text)
                goal = update_goal_plan(
                    goal_id=goal.id,
                    owner_user_id=update.effective_user.id,
                    workspace_id=workspace_id,
                    strategy='contribution',
                    frequency=goal_action.get('frequency') or 'none',
                    deadline=goal.deadline,
                    comfortable_amount=amount,
                    schedule_config=_goal_schedule_config(goal_action.get('frequency') or 'none'),
                )
                context.user_data.pop('goal_action', None)
                return await emsg.reply_text("Хотите получать напоминания о плановых пополнениях?", reply_markup=_goal_reminder_prompt_kb(goal.id))
            if mode == 'plan_deadline':
                parsed = _parse_flexible_date(text)
                today = user_local_date(update.effective_user.id)
                if not parsed or parsed < today:
                    return await emsg.reply_text('Срок цели не может быть в прошлом. Введите дату ещё раз.')
                goal = update_goal_plan(
                    goal_id=goal.id,
                    owner_user_id=update.effective_user.id,
                    workspace_id=workspace_id,
                    strategy='deadline',
                    frequency=goal_action.get('frequency') or 'monthly',
                    deadline=parsed,
                    schedule_config=_goal_schedule_config(goal_action.get('frequency') or 'monthly'),
                )
                context.user_data.pop('goal_action', None)
                return await emsg.reply_text("Хотите получать напоминания о плановых пополнениях?", reply_markup=_goal_reminder_prompt_kb(goal.id))
            if mode == 'edit_name':
                goal = update_goal_details(goal_id=goal.id, owner_user_id=update.effective_user.id, workspace_id=workspace_id, display_name=text)
                context.user_data.pop('goal_action', None)
                return await emsg.reply_text(render_goal_card_text(goal), reply_markup=_goal_card_kb(goal.id))
            if mode == 'edit_target':
                goal = update_goal_details(goal_id=goal.id, owner_user_id=update.effective_user.id, workspace_id=workspace_id, target_amount=parse_money(text))
                context.user_data.pop('goal_action', None)
                return await emsg.reply_text(render_goal_card_text(goal), reply_markup=_goal_card_kb(goal.id))
            if mode == 'edit_deadline':
                parsed = None if text.strip().lower() in {'без срока', 'нет', 'none'} else _parse_flexible_date(text)
                if parsed is None and text.strip().lower() not in {'без срока', 'нет', 'none'}:
                    return await emsg.reply_text('Не понял дату. Напишите, например: 01.12.2026 или «без срока».')
                goal = update_goal_details(goal_id=goal.id, owner_user_id=update.effective_user.id, workspace_id=workspace_id, deadline=parsed)
                context.user_data.pop('goal_action', None)
                return await emsg.reply_text(render_goal_card_text(goal), reply_markup=_goal_card_kb(goal.id))
        except GoalError as exc:
            return await emsg.reply_text(_goal_error_text(str(exc)))
        except Exception:
            log.warning('goal_text_action_failed user_id=%s mode=%s', update.effective_user.id, mode)
            return await emsg.reply_text('Не удалось сохранить изменения. Данные цели не изменены. Попробуйте позже.')

    category_rename = context.user_data.get('category_rename_input')
    if isinstance(category_rename, dict):
        type_key = category_rename.get('type_key') or ('income' if category_rename.get('op_type') == 'Доходы' else 'expense')
        if (
            category_rename.get('actor_user_id') != update.effective_user.id
            or category_rename.get('expires_at', 0) < unix_time()
        ):
            context.user_data.pop('category_rename_input', None)
            return await emsg.reply_text('Подтверждение устарело. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
        source_name = category_rename.get('source') or ''
        if is_protected_category(source_name):
            context.user_data.pop('category_rename_input', None)
            return await emsg.reply_text('Эту системную категорию нельзя переименовать.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text or ""):
            return await emsg.reply_text('Название содержит служебные символы. Введите другое название.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')]]))
        try:
            new_name = normalize_category_name(text)
            source_key = normalized_category_key(source_name)
            new_key = normalized_category_key(new_name)
        except ValueError:
            return await emsg.reply_text('Введите непустое название категории.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')]]))
        op_type = category_rename.get('op_type') or 'Расходы'
        duplicate = None
        if new_key != source_key:
            for item in list_managed_categories(user_id=update.effective_user.id, workspace_id=category_rename.get('workspace_id'), op_type=op_type):
                if item.normalized_name == new_key:
                    duplicate = item
                    break
        token = token_urlsafe(8)
        context.user_data.pop('category_rename_input', None)
        if duplicate:
            counts = category_reference_counts(user_id=update.effective_user.id, workspace_id=category_rename.get('workspace_id'), op_type=op_type, category=source_name)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': update.effective_user.id,
                'workspace_id': category_rename.get('workspace_id'),
                'op_type': op_type,
                'type_key': type_key,
                'source': source_name,
                'destination': duplicate.name,
                'source_token': category_rename.get('source_token'),
                'mode': 'duplicate_merge',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('🔁 Перенести в существующую', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('✏️ Ввести другое', callback_data=f'cat|rename_again|{token}')],
                [InlineKeyboardButton('Открыть существующую', callback_data=f'cat|open_dup|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{type_key}|{category_rename.get('source_token')}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await emsg.reply_text(
                f"Категория «{duplicate.name}» уже есть.\n\nМожно перенести в неё записи из «{source_name}». Операций: {counts.operations}.",
                reply_markup=kb,
            )
        counts = category_reference_counts(user_id=update.effective_user.id, workspace_id=category_rename.get('workspace_id'), op_type=op_type, category=source_name)
        context.user_data['category_action'] = {
            'token': token,
            'actor_user_id': update.effective_user.id,
            'workspace_id': category_rename.get('workspace_id'),
            'op_type': op_type,
            'type_key': type_key,
            'source': source_name,
            'destination': new_name,
            'source_token': category_rename.get('source_token'),
            'mode': 'rename',
            'expires_at': unix_time() + 600,
            'used': False,
        }
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Переименовать', callback_data=f'cat|confirm|{token}')],
            [InlineKeyboardButton('✏️ Ввести другое', callback_data=f'cat|rename_again|{token}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{type_key}|{category_rename.get('source_token')}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
        ])
        return await emsg.reply_text(
            f"Переименовать категорию?\n\n{source_name} → {new_name}\nОпераций: {counts.operations}\nСвязанных настроек: {counts.total - counts.operations}",
            reply_markup=kb,
        )

    category_create = context.user_data.get('await_category_create')
    if isinstance(category_create, dict):
        if (
            category_create.get('actor_user_id') != update.effective_user.id
            or category_create.get('expires_at', 0) < unix_time()
        ):
            context.user_data.pop('await_category_create', None)
            return await emsg.reply_text('Подтверждение устарело. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
        try:
            result = get_or_create_custom_category(
                workspace_id=category_create.get('workspace_id'),
                user_id=update.effective_user.id,
                op_type=category_create.get('op_type') or 'Расходы',
                name=text,
            )
        except ValueError:
            return await emsg.reply_text('Введите непустое название категории.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data='cat_menu')]]))
        context.user_data.pop('await_category_create', None)
        if not result.created:
            return await emsg.reply_text(
                f'Категория «{result.name}» уже есть. Выберите другое название.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('➕ Добавить категорию', callback_data=f"cat|add|{category_create.get('type_key') or 'expense'}")], [InlineKeyboardButton('🏷 Категории', callback_data=f"cat|type|{category_create.get('type_key') or 'expense'}")]]),
            )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🏷 К категориям', callback_data=f"cat|type|{category_create.get('type_key') or 'expense'}")], [InlineKeyboardButton('➕ Добавить ещё', callback_data=f"cat|add|{category_create.get('type_key') or 'expense'}")]])
        return await emsg.reply_text(f'✅ Категория добавлена\n\n{result.name}', reply_markup=kb)

    history_state = context.user_data.get('history_delete_wizard')
    if isinstance(history_state, dict):
        locale = _message_privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        if history_state.get('actor_user_id') != update.effective_user.id or history_state.get('expires_at', 0) < unix_time():
            context.user_data.pop('history_delete_wizard', None)
            return await emsg.reply_text(
                t('privacy.stale', locale),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]),
            )
        today = user_local_date(update.effective_user.id)
        parsed = parse_export_date(text, today)
        if parsed is None:
            return await emsg.reply_text(
                t('privacy.custom.invalid', locale),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]),
            )
        if history_state.get('step') == 'start':
            history_state['start'] = parsed.isoformat()
            history_state['step'] = 'end'
            history_state['expires_at'] = unix_time() + 600
            context.user_data['history_delete_wizard'] = history_state
            return await emsg.reply_text(
                t('privacy.custom.end', locale),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|custom|start')]]),
            )
        start_date = date.fromisoformat(history_state['start'])
        end_date = parsed
        if end_date < start_date:
            return await emsg.reply_text(
                t('privacy.custom.end_before_start', locale),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|custom|start')]]),
            )
        preview = preview_delete_financial_history(update.effective_user.id, start_date, end_date)
        period = _history_period_label(start_date, end_date, locale)
        context.user_data.pop('history_delete_wizard', None)
        if preview.operation_count == 0:
            return await emsg.reply_text(
                t('privacy.history.zero', locale, period=period),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]),
            )
        token = token_urlsafe(8)
        context.user_data['history_delete_confirm'] = {
            'token': token,
            'actor_user_id': update.effective_user.id,
            'start': start_date.isoformat(),
            'end': end_date.isoformat(),
            'expires_at': unix_time() + 600,
            'used': False,
        }
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t('privacy.history.yes', locale), callback_data=f'hist|confirm|{token}'),
             InlineKeyboardButton(t('privacy.history.no', locale), callback_data='privacy_menu')],
            [InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')],
        ])
        return await emsg.reply_text(t('privacy.history.preview', locale, period=period, count=preview.operation_count), reply_markup=kb)

    if context.user_data.get('await_group_custom_category'):
        st = context.user_data.get('await_group_custom_category') or {}
        if not isinstance(st, dict):
            st = {'draft_id': st}
        draft_id = st.get('draft_id')
        if not _is_group_chat(update):
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('Черновик операции был создан в группе. Вернитесь в тот групповой чат и начните запись заново.')
        if int(st.get('chat_id') or 0) != int(update.effective_chat.id) or int(st.get('actor_user_id') or 0) != int(update.effective_user.id):
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('Этот черновик операции больше не подходит к текущему чату или пользователю. Отправьте операцию заново.')
        draft = load_operation_draft(draft_id, actor_user_id=update.effective_user.id)
        if draft and draft.get('status') == 'committed':
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('Операция уже была сохранена.')
        if not draft or draft.get('status') != 'draft':
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('This operation draft has expired. Send the operation again.')
        if int(draft['chat_id']) != int(st.get('chat_id')) or draft.get('workspace_id') != st.get('workspace_id'):
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('Этот черновик операции больше не подходит к текущему пространству. Отправьте операцию заново.')
        payload = draft.get('payload') or {}
        workspace = resolve_workspace(draft['chat_id'], update.effective_user.id, getattr(update.effective_chat, 'type', 'group'))
        if not workspace.is_configured or workspace.role not in {'owner', 'admin', 'member'}:
            context.user_data.pop('await_group_custom_category', None)
            return await emsg.reply_text('В этом пространстве у вас нет прав добавлять операции.')
        try:
            category_name = normalize_category_name(text)
        except ValueError:
            return await emsg.reply_text('⚠️ Введите название категории.')
        result = commit_operation_draft(
            draft_id=draft_id,
            actor_user_id=update.effective_user.id,
            category=category_name,
            chat_id=draft['chat_id'],
            workspace_id=workspace.workspace_id,
            chat_type=getattr(update.effective_chat, 'type', 'group') or 'group',
            metadata={'draft_id': draft_id, 'custom_category_requested': True},
        )
        context.user_data.pop('await_group_custom_category', None)
        if result['status'] == 'already_committed':
            return await emsg.reply_text('Операция уже была сохранена.')
        if result['status'] != 'committed':
            return await emsg.reply_text('This operation draft has expired. Send the operation again.')
        cat = get_or_create_custom_category(
            workspace_id=workspace.workspace_id,
            user_id=update.effective_user.id,
            op_type=payload.get('type') or 'Расходы',
            name=category_name,
        )
        recorded = result['recorded']
        name = getattr(update.effective_user, 'full_name', None) or getattr(update.effective_user, 'username', None) or str(update.effective_user.id)
        await emsg.reply_text(
            f"✅ Операция записана\n\n{cat.name} — {recorded.amount} {recorded.currency}\n"
            f"Пространство: {workspace.name}\nДобавил(а): {name}"
        )
        return await send_operation_limit_alert(recorded, context)

    if context.user_data.get('lim_edit_amount'):
        st = context.user_data.get('lim_edit_amount') or {}
        amount = _parse_amount_input(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 25000')
        if amount <= 0 or amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Сумма должна быть больше 0 и меньше 1 000 000 000')
        period = st.get('period')
        category = st.get('category')
        try:
            row = update_limit_amount(cid, period, category, amount)
        except Exception as e:
            log.exception('edit_amount failed user=%s period=%s cat=%s err=%s', cid, period, category, e)
            return await emsg.reply_text('Не смог сохранить, попробуй ещё раз.')
        context.user_data.pop('lim_edit_amount', None)
        if not row:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('📌 Мои лимиты', callback_data='lim_list')]])
            return await emsg.reply_text('Лимит не найден или уже изменён.', reply_markup=kb)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ К карточке', callback_data='lim_list')]])
        return await emsg.reply_text(f"✅ Сумма обновлена: {format_money_value(row['amount'], row['currency'])}", reply_markup=kb)

    if context.user_data.get('await_general_limit_amount'):
        amount = _parse_amount_input(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 60000')
        st = context.user_data.pop('await_general_limit_amount', {}) or {}
        limit_id = upsert_general_limit(
            user_id=cid,
            workspace_id=None,
            name='Общий лимит',
            amount=amount,
            period_type=st.get('period_type') or 'month',
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('💰 Лимиты и бюджеты', callback_data='lb_hub')]])
        return await emsg.reply_text(f'✅ Общий лимит создан: {format_money_value(amount, "RUB")}\nID: {limit_id}', reply_markup=kb)

    if context.user_data.pop('await_cbg_new_category', False):
        draft = context.user_data.get('cbg_draft') or {}
        workspace_id = draft.get('workspace_id')
        try:
            result = get_or_create_custom_category(workspace_id=workspace_id, user_id=cid, op_type='Расходы', name=text)
        except ValueError:
            context.user_data['await_cbg_new_category'] = True
            return await emsg.reply_text('Введите непустое название категории.')
        token = f"c{result.category_id}" if result.category_id is not None else None
        if token:
            selected = list(dict.fromkeys((draft.get('selected_tokens') or []) + [token]))
            selected_map = dict(draft.get('selected_categories') or {})
            selected_map[token] = result.name
            draft['selected_tokens'] = selected
            draft['selected_categories'] = selected_map
        draft['step'] = 'categories'
        context.user_data['cbg_draft'] = draft
        options, kb = _cbg_picker_markup(cid, workspace_id, set(draft.get('selected_tokens') or []), int(draft.get('page') or 0))
        draft['category_options'] = {item['token']: item['name'] for item in options}
        context.user_data['cbg_draft'] = draft
        return await emsg.reply_text(f'Категория добавлена: {result.name}\n\nВыбрано категорий: {len(draft.get("selected_tokens") or [])}', reply_markup=kb)

    if context.user_data.get('cbg_draft'):
        draft = context.user_data.get('cbg_draft') or {}
        step = draft.get('step')
        if step == 'name':
            name = (text or '').strip()[:120]
            if not name:
                return await emsg.reply_text('Введите название бюджета.')
            if 'workspace_id' not in draft:
                draft['workspace_id'] = resolve_workspace(cid, update.effective_user.id, getattr(update.effective_chat, 'type', 'private') or 'private').workspace_id
            draft['name'] = name
            draft['step'] = 'categories'
            draft.setdefault('selected_tokens', [])
            draft.setdefault('selected_categories', {})
            context.user_data['cbg_draft'] = draft
            options, kb = _cbg_picker_markup(cid, draft.get('workspace_id'), set(draft.get('selected_tokens') or []), int(draft.get('page') or 0))
            draft['category_options'] = {item['token']: item['name'] for item in options}
            context.user_data['cbg_draft'] = draft
            return await emsg.reply_text(f'Выберите категории для бюджета.\n\nВыбрано категорий: {len(draft.get("selected_tokens") or [])}', reply_markup=kb)
        if step == 'amount':
            amount = _parse_amount_input(text)
            if amount is None:
                return await emsg.reply_text('⚠️ Введите сумму числом, например: 42000')
            draft['amount'] = amount
            draft['step'] = 'period'
            context.user_data['cbg_draft'] = draft
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Неделя', callback_data='cbgp|period|week'), InlineKeyboardButton('Месяц', callback_data='cbgp|period|month')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='cbgp|back_categories'), InlineKeyboardButton('❌ Отмена', callback_data='cbgp|cancel')],
            ])
            return await emsg.reply_text('Выберите период бюджета.', reply_markup=kb)

    if context.user_data.get('await_rem_title_amount'):
        log.info('reminder_wizard_text state=await_rem_title_amount user=%s', cid)
        parsed = _parse_reminder_title_amount(text)
        if not parsed:
            log.info('reminder_wizard_title_amount_parsed ok=false user=%s', cid)
            context.user_data['await_rem_title_amount'] = True
            return await update.message.reply_text('Не понял сумму. Напиши так: ChatGPT 1990')

        log.info('reminder_wizard_title_amount_parsed ok=true user=%s', cid)
        context.user_data.pop('await_rem_title_amount', None)
        merch, amt = parsed
        draft = context.user_data.get('rem_draft') or {}
        rem_type = draft.get('rem_type') or 'Расходы'
        draft['title'] = merch
        draft['amount'] = str(amt)
        draft['rem_type'] = rem_type
        draft['step'] = 'category'
        context.user_data['rem_draft'] = draft
        context.user_data['await_rem_category'] = True
        log.info('reminder_wizard_next_step=category user=%s', cid)

        if rem_type == 'Доходы':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Зарплата', callback_data='rem_cat_salary'), InlineKeyboardButton('Переводы', callback_data='rem_cat_transfer')],
                [InlineKeyboardButton('Кэшбэк', callback_data='rem_cat_cashback'), InlineKeyboardButton('Прочее', callback_data='rem_cat_other')],
                [InlineKeyboardButton('✏️ Другая', callback_data='rem_cat_custom')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
            ])
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Заведения', callback_data='rem_cat_zav'), InlineKeyboardButton('Продукты', callback_data='rem_cat_prod')],
                [InlineKeyboardButton('Транспорт', callback_data='rem_cat_tr'), InlineKeyboardButton('Подписки', callback_data='rem_cat_sub')],
                [InlineKeyboardButton('Прочее', callback_data='rem_cat_other')],
                [InlineKeyboardButton('✏️ Другая', callback_data='rem_cat_custom')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
            ])
        try:
            log.info('reminder_wizard_category_ui_send_start user=%s', cid)
            await update.message.reply_text('Выбери категорию', reply_markup=kb)
            log.info('reminder_wizard_category_ui_send_ok user=%s', cid)
            return
        except Exception:
            log.exception('reminder_wizard_category_ui_send_failed user=%s', cid)
            try:
                await update.message.reply_text('Не смог открыть выбор категории. Попробуй /reminders ещё раз.')
            except Exception:
                pass
            return

    if context.user_data.get('await_rem_edit'):
        st = context.user_data.pop('await_rem_edit')
        rid, field = int(st['rid']), st['field']
        log.info('reminder_wizard_text state=%s user=%s', field, cid)
        try:
            if rid == -1:
                d = context.user_data.setdefault('rem_draft', {})
                if field == 'category_draft':
                    d['category'] = norm_text(text)[:64] or 'Прочее'
                    return await emsg.reply_text(
                        'Когда первое событие?\n\nМожно написать:\n19\n19 число\n19.06\n19.06.2026\nзавтра\n\nДля регулярных платежей это будет первая дата, дальше повтор настроим следующим шагом.',
                        reply_markup=_reminder_date_kb(),
                    )
                elif field == 'date_draft':
                    dd = _parse_reminder_event_date(text, datetime.now().date())
                    log.info('reminder_date_input raw=%s parsed_ok=%s user=%s', (text or '')[:32], bool(dd), cid)
                    if not dd:
                        return await emsg.reply_text('Не понял дату. Напиши, например: 19, 19 число или 19.06.2026')
                    d['event_date'] = dd
                    log.info('reminder_date_selected source=manual event_date=%s user=%s', dd.isoformat(), cid)
                    return await emsg.reply_text('Как часто повторять?', reply_markup=_reminder_repeat_kb())
                elif field == 'repeat_draft':
                    n = int((text or '').strip())
                    if n < 1 or n > 3650:
                        raise ValueError('repeat')
                    d['repeat_rule'] = 'custom_days'; d['repeat_interval_days'] = n
                    return await emsg.reply_text('Когда напомнить?', reply_markup=_reminder_notify_kb())
                elif field == 'notify_draft':
                    n = int((text or '').strip())
                    if n < 0 or n > 30:
                        raise ValueError('notify')
                    d['notify_days_before'] = n
                    txt, kb = _reminder_confirmation(d)
                    return await emsg.reply_text(txt, reply_markup=kb)
            if field == 'amount':
                a = _parse_amount_input(text)
                if a is None or a <= 0 or a >= 1_000_000_000:
                    raise ValueError('amount')
                reminder_update(cid, rid, amount=a)
            elif field == 'category':
                reminder_update(cid, rid, category=norm_text(text)[:64] or 'Прочее')
            elif field == 'date':
                d = _parse_reminder_event_date(text, datetime.now().date())
                log.info('reminder_date_input raw=%s parsed_ok=%s user=%s', (text or '')[:32], bool(d), cid)
                if not d:
                    return await emsg.reply_text('Не понял дату. Напиши, например: 19, 19 число или 19.06.2026')
                reminder_update(cid, rid, event_date=d)
                log.info('reminder_date_selected source=manual event_date=%s user=%s', d.isoformat(), cid)
            elif field == 'repeat':
                repeat_value = (text or '').strip().lower()
                if repeat_value.startswith('custom_days:'):
                    n = int(repeat_value.split(':', 1)[1]); reminder_update(cid, rid, repeat_rule='custom_days', repeat_interval_days=n)
                else:
                    reminder_update(cid, rid, repeat_rule=repeat_value, repeat_interval_days=None)
            elif field == 'notify':
                n = int((text or '').strip())
                if n < 0 or n > 30:
                    raise ValueError('notify')
                reminder_update(cid, rid, notify_days_before=n)
            elif field == 'type':
                v = 'Доходы' if 'доход' in (text or '').lower() else 'Расходы'
                reminder_update(cid, rid, rem_type=v)
        except Exception:
            context.user_data['await_rem_edit'] = st
            return await emsg.reply_text('⚠️ Некорректное значение, попробуй ещё раз.')
        return await emsg.reply_text('✅ Напоминание обновлено. Открой карточку через /reminders')

    op_edit_amount_id = context.user_data.pop('await_op_edit_amount', False)
    if op_edit_amount_id:
        amount = _parse_amount_input(text)
        if amount is None or amount <= 0 or amount >= 1_000_000_000:
            context.user_data['await_op_edit_amount'] = op_edit_amount_id
            return await emsg.reply_text('⚠️ Неверная сумма. Введите число >0 и <1 000 000 000')
        row = update_operation_fields_by_id(cid, int(op_edit_amount_id), amount=amount) if str(op_edit_amount_id).isdigit() else update_last_operation_fields(cid, amount=amount)
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Сумма обновлена.')

    op_edit_date_id = context.user_data.pop('await_op_edit_date', False)
    if op_edit_date_id:
        d = _parse_flexible_date(text)
        if not d:
            context.user_data['await_op_edit_date'] = op_edit_date_id
            return await emsg.reply_text('⚠️ Не понял дату. Пример: 24.05.2026')
        row = update_operation_fields_by_id(cid, int(op_edit_date_id), op_date=d) if str(op_edit_date_id).isdigit() else update_last_operation_fields(cid, op_date=d)
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Дата обновлена.')

    op_edit_comment_id = context.user_data.pop('await_op_edit_comment', False)
    if op_edit_comment_id:
        row = update_operation_fields_by_id(cid, int(op_edit_comment_id), comment=(text or '').strip()[:200]) if str(op_edit_comment_id).isdigit() else update_last_operation_fields(cid, comment=(text or '').strip()[:200])
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Комментарий обновлён.')

    if context.user_data.get('await_export_start') or context.user_data.get('await_export_end'):
        d = parse_export_date(text)
        if not d:
            return await emsg.reply_text('⚠️ Не понял дату. Формат: DD.MM.YYYY / DD.MM / YYYY-MM-DD', reply_markup=_export_end_kb() if context.user_data.get('await_export_end') else _export_start_kb())
        st = context.user_data.setdefault('export_state', {})
        if context.user_data.get('await_export_start'):
            st['from'] = d.isoformat()
            st['mode'] = 'custom'
            st['step'] = 'end'
            context.user_data.pop('await_export_start', None)
            context.user_data['await_export_end'] = True
            return await emsg.reply_text('Дата начала сохранена. Теперь выбери или введи конец периода:', reply_markup=_export_end_kb())
        st['to'] = d.isoformat()
        clear_export_wait_flags(context.user_data)
        if not st.get('from'):
            context.user_data.pop('export_state', None)
            return await emsg.reply_text('📤 Сессия экспорта устарела. Выбери период заново.', reply_markup=_export_start_kb())
        dfrom = date.fromisoformat(st['from'])
        dto = date.fromisoformat(st['to'])
        ok, error = validate_export_period(dfrom, dto)
        if not ok:
            st.pop('to', None)
            context.user_data['await_export_end'] = True
            msg = 'Дата конца не может быть раньше даты начала.' if error == 'end_before_start' else 'Период слишком большой. Выберите диапазон до 5 лет.'
            return await emsg.reply_text(f'⚠️ {msg}\n\nВведите конец периода ещё раз:', reply_markup=_export_end_kb())
        rows = _export_rows(cid, dfrom, dto)
        exp = sum((to_decimal_money(r['amount']) for r in rows if r['type'] == 'Расходы'), Decimal("0.00"))
        inc = sum((to_decimal_money(r['amount']) for r in rows if r['type'] == 'Доходы'), Decimal("0.00"))
        st['count'] = len(rows)
        st['preview_rows'] = rows
        st['step'] = 'confirm'
        return await emsg.reply_text(
            f'📤 Экспорт\n\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\n'
            f'Операций: {len(rows)}\nРасходы: {format_money_value(exp, "RUB")}\nДоходы: {format_money_value(inc, "RUB")}\nБаланс: {format_money_value(inc-exp, "RUB")}\n\nСформировать файл?',
            reply_markup=_export_confirm_kb(),
        )

    if context.user_data.pop('await_receipt_edit_text', False):
        idx = int(context.user_data.get('receipt_edit_idx') or -1)
        cands = context.user_data.get('receipt_candidates') or []
        if idx < 0 or idx >= len(cands):
            return await emsg.reply_text('Не нашёл операцию для редактирования.')
        field = context.user_data.pop('receipt_edit_field', 'full')
        try:
            if field == 'amount':
                v = _parse_amount_input(text)
                if v is None or v <= 0:
                    raise ValueError('amount')
                cands[idx]['amount'] = str(v)
            elif field == 'comment':
                cands[idx]['merchant'] = norm_text(text.strip())[:64]
            elif field == 'date':
                _m, _a, dt, _src_curr = parse_user_input(f'x 1 {text}')
                cands[idx]['date'] = dt.date().isoformat()
            elif field == 'category':
                cands[idx]['category'] = norm_text(text.strip())[:32] or 'Прочее'
            else:
                merch_display, amt_raw, dt, _src_curr = parse_user_input(text)
                cands[idx]['amount'] = str(amt_raw)
                cands[idx]['merchant'] = norm_text(merch_display)
                cands[idx]['date'] = dt.date().isoformat()
                if re.search(r'зарплат|доход|перевод|пополн', text.lower()):
                    cands[idx]['type'] = 'Доходы'
            context.user_data['receipt_candidates'] = cands
            context.user_data['receipt_review_idx'] = idx
            context.user_data.pop('receipt_edit_idx', None)
            return await emsg.reply_text('✅ Операция обновлена. Нажми «Проверить» или «Записать всё».')
        except Exception:
            context.user_data['await_receipt_edit_text'] = True
            return await emsg.reply_text('Не понял исправление. Пример: столовая 392')

    # ----- настройки и прочие ветки (без изменений) -----
    if context.user_data.pop('await_reminder_custom', False):
        m = re.search(r'\d{1,2}', text)
        if not m:
            return await emsg.reply_text("⚠️ Введите число от 0 до 23 (например, 20)")
        hour = int(m.group())
        if not (0 <= hour <= 23):
            return await emsg.reply_text("⚠️ Час должен быть 0–23")
        update_user_field(cid, 'reminder_hour', hour)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Напоминание каждый день в {hour:02d}:00", reply_markup=kb)

    quiet_time_state = context.user_data.pop('await_quiet_hours_time', None)
    if quiet_time_state:
        field = quiet_time_state.get('field')
        try:
            prefs = set_quiet_hours_time(cid, field, text.strip())
        except Exception:
            context.user_data['await_quiet_hours_time'] = quiet_time_state
            return await emsg.reply_text('⚠️ Введите время в формате HH:MM, например 22:30')
        start = prefs.get('quiet_hours_start') or '22:30'
        end = prefs.get('quiet_hours_end') or '08:00'
        track_product_event(ProductEvent(
            event_name="quiet_hours_updated",
            user_id=cid,
            status="success",
            properties={"field": field},
        ))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🌙 Тихие часы', callback_data='notif_quiet_hours')]])
        return await emsg.reply_text(f'✅ Тихие часы обновлены: {start}–{end}', reply_markup=kb)

    daily_time_state = context.user_data.pop('await_daily_notification_time', None)
    if daily_time_state:
        field = daily_time_state.get('field')
        if field == 'morning':
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('🕒 Время уведомлений', callback_data='notif_times')]])
            return await emsg.reply_text('Утренние автоматические уведомления отключены. Можно настроить вечернее время.', reply_markup=kb)
        try:
            prefs = set_daily_notification_time(cid, field, text.strip())
        except Exception:
            context.user_data['await_daily_notification_time'] = daily_time_state
            return await emsg.reply_text('⚠️ Введите время в формате HH:MM, например 08:30')
        value = prefs.get('morning_time') if field == 'morning' else prefs.get('evening_time')
        label = 'Утро' if field == 'morning' else 'Вечер'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🕒 Время уведомлений', callback_data='notif_times')]])
        return await emsg.reply_text(f'✅ {label}: {value}', reply_markup=kb)

    if context.user_data.pop('await_timezone_name', False):
        tz_name = text.strip()
        if not is_valid_timezone_name(tz_name):
            context.user_data['await_timezone_name'] = True
            return await emsg.reply_text('⚠️ Введите корректный IANA-часовой пояс, например Europe/Moscow')
        try:
            set_notification_timezone(cid, tz_name)
            skipped = suppress_stale_timezone_sensitive_notifications(cid)
        except Exception:
            context.user_data['await_timezone_name'] = True
            return await emsg.reply_text('⚠️ Не удалось сохранить часовой пояс. Попробуйте ещё раз.')
        track_product_event(ProductEvent(
            event_name="timezone_updated",
            user_id=cid,
            status="success",
            properties={"destination": "notifications", "stale_notifications_suppressed": bool(skipped)},
        ))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🕒 Часовой пояс', callback_data='notif_tz')]])
        return await emsg.reply_text(f'✅ Часовой пояс сохранён: {tz_name}', reply_markup=kb)

    if context.user_data.pop('setting_week', False):
        if not re.fullmatch(r'\d+', text.strip()):
            context.user_data['setting_week'] = True
            return await emsg.reply_text("⚠️ Введите целое число")
        from db.queries import set_budget
        set_budget(cid, week=int(text.strip()))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Недельный бюджет: {int(text)}", reply_markup=kb)

    if context.user_data.pop('setting_month', False):
        if not re.fullmatch(r'\d+', text.strip()):
            context.user_data['setting_month'] = True
            return await emsg.reply_text("⚠️ Введите целое число")
        from db.queries import set_budget
        set_budget(cid, month=int(text.strip()))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Месячный бюджет: {int(text)}", reply_markup=kb)

    if context.user_data.get('budget_add_period'):
        period = context.user_data.get('budget_add_period')
        amount = _parse_budget_amount(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 60000')
        if amount <= 0:
            return await emsg.reply_text('⚠️ Бюджет не может быть меньше 1 ₽')
        if amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Слишком большой бюджет')
        from db.queries import get_user_budgets, set_budget
        wl, ml = get_user_budgets(cid)
        cur = ml if period == 'month' else wl
        if cur and cur > 0:
            context.user_data['budget_pending_amount'] = amount
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Заменить', callback_data=f'bud_replace_confirm|{period}')],
                [InlineKeyboardButton('✏️ Ввести другую сумму', callback_data='bud_add')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')],
            ])
            return await emsg.reply_text(f"Бюджет на {'месяц' if period=='month' else 'неделю'} уже есть: {format_money_value(cur, 'RUB')}\n\nЗаменить его?", reply_markup=kb)
        if period == 'month':
            set_budget(cid, month=amount)
        else:
            set_budget(cid, week=amount)
        context.user_data.pop('budget_add_period', None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 К бюджетам', callback_data='settings_budgets')],
            [InlineKeyboardButton('➕ Добавить ещё', callback_data='bud_add')],
            [InlineKeyboardButton('⬅️ В настройки', callback_data='menu_settings')],
        ])
        return await emsg.reply_text(f"✅ Бюджет добавлен\n\n{'Месяц' if period=='month' else 'Неделя'} — {format_money_value(amount, 'RUB')}", reply_markup=kb)

    if context.user_data.get('budget_manual_period'):
        period = context.user_data.get('budget_manual_period')
        amount = _parse_budget_amount(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 60000')
        if amount <= 0:
            return await emsg.reply_text('⚠️ Бюджет не может быть меньше 1 ₽')
        if amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Слишком большой бюджет')
        context.user_data.pop('budget_manual_period', None)
        token = token_urlsafe(8)
        context.user_data['budget_pending_edit'] = {'token': token, 'actor_user_id': update.effective_user.id, 'period': period, 'amount': amount, 'expires_at': unix_time() + 600, 'used': False}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Сохранить', callback_data=f'bud_confirm|{token}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data=f'bud_card|{period}'), InlineKeyboardButton('❌ Отмена', callback_data='settings_budgets')],
        ])
        return await emsg.reply_text(f"Сохранить новый бюджет?\n\n{'Месяц' if period=='month' else 'Неделя'} — {format_money_value(amount, 'RUB')}", reply_markup=kb)

    if context.user_data.get('adding_category'):
        p = context.user_data.get('pending', {})
        new_cat = (text or "").strip()
        if not new_cat:
            return await emsg.reply_text("⚠️ Введите название категории")
        context.user_data.pop('adding_category', None)
        typ = p.get('type') or 'Расходы'
        merch = p.get('merch', 'операция')
        amt = p.get('amt', 0)
        dt  = p.get('time', datetime.now())
        note = p.get('note')
        workspace = resolve_workspace(cid, update.effective_user.id, getattr(update.effective_chat, 'type', 'private') or 'private')
        try:
            cat_result = get_or_create_custom_category(
                workspace_id=workspace.workspace_id,
                user_id=update.effective_user.id,
                op_type=typ,
                name=new_cat,
            )
        except ValueError:
            context.user_data['adding_category'] = True
            return await emsg.reply_text("⚠️ Введите название категории")
        alias_norm = normalize_alias_text(context.user_data.get('batch_item_text') or merch)
        record_category_confirmation(cid, context.user_data.get('batch_item_text') or merch, alias_norm, cat_result.name, typ, 'accept')
        await emsg.reply_text('✅ Категория сохранена.' if cat_result.created else '✅ Такая категория уже есть, использую её.')
        return await record_operation(cat_result.name, amt, dt, typ, update, context, note, merchant=merch)

    if context.user_data.pop('await_amount', False):
        src_curr = detect_currency_token(text or "")
        amt_raw = _parse_amount_input(text)
        if amt_raw is None:
            context.user_data['await_amount'] = True
            return await emsg.reply_text("⚠️ Введите сумму числом (например, 70 или 70 000)")
        p   = context.user_data.get('pending', {})
        merch = p.get('merch', 'операция'); dt = p.get('time', datetime.now())

        raw_text = text
        if not src_curr:
            src_curr = detect_currency_token(raw_text or "")
        amt_final, note = convert_amount_if_needed(cid, amt_raw, src_curr)

        alias = get_user_alias(cid, merch)
        if alias:
            typ, cat = alias
            context.user_data['batch_item_text'] = text
            try:
                insert_ml_observation(
                    user_id=cid,
                    chat_id=cid,
                    raw_text=text,
                    normalized_text=normalize_for_ml(text),
                    detected_type=typ,
                    action='fallback_direct_write',
                    chosen_category=cat,
                    chosen_type=typ,
                    meta={'source': 'telegram', 'flow': 'await_amount_alias_hit'},
                )
            except Exception:
                pass
            return await record_operation(cat, amt_final, dt, typ, update, context, note, merchant=merch)

        op_type = 'Расходы'
        normalized = normalize_for_ml(text)
        top2, sugg_meta = get_top2_suggestions(cid, normalized, op_type)
        if len(top2) < 2:
            top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
        cat1, cat2 = top2[0]['cat'], top2[1]['cat']
        context.user_data['pending'] = {
            'merch': merch,
            'amt': amt_final,
            'time': dt,
            'type': op_type,
            'note': note,
            'ml_cat1': cat1,
            'ml_cat2': cat2,
            'ml_top2': top2,
        }
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalized,
                detected_type=op_type,
                action='suggest_shown',
                suggested_top2=top2,
                confidence_top1=top2[0].get('score'),
                meta={'source': sugg_meta.get('source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'currency_detected': src_curr, 'flow': 'await_amount', 'suggest': sugg_meta, 'model_version': sugg_meta.get('model_version'), 'trained_at': sugg_meta.get('trained_at')},
            )
        except Exception:
            pass

        msg = await _safe_reply(
            emsg,
            f"Категория?\n➖ {format_money_value(amt_final, get_user_currency(cid))} • {_md_escape(merch)}",
            reply_markup=ml_top2_kb(cat1, cat2),
        )
        context.user_data['suggest_msg_id'] = msg.message_id
        context.user_data['batch_item_text'] = text
        return

    try:
        # ─────────── батч (последовательно) ───────────
        items = parse_day_list(text)
        if items:
            if context.user_data.get('batch_active'):
                return await emsg.reply_text("⚠️ Введите новый список после завершения текущего.")
            if len(items) > BATCH_MAX:
                return await emsg.reply_text(
                    f"⚠️ Слишком длинный список: {len(items)} элементов. Разбейте на части (≤ {BATCH_MAX})."
                )
            return await _batch_start(update, context, items)

        # основной путь: одна строка
        return await _process_free_text(update, context, text)
    except Exception as e:
        log.exception("handle_text failed: user=%s text=%r err=%s", cid, text, e)
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type='Расходы',
                action='parse_failed',
                meta={'source': 'telegram', 'error': str(e)[:200]},
            )
        except Exception:
            pass
        return await emsg.reply_text("Не получилось обработать сообщение. Попробуй ещё раз.")


async def handle_location(update, context: ContextTypes.DEFAULT_TYPE):
    if _is_group_chat(update) and not group_message_has_explicit_bot_intent(update, context):
        return None
    cid = update.effective_chat.id
    emsg = update.effective_message
    loc = update.message.location
    try:
        await emsg.reply_text("Спасибо! Обрабатываю…", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass

    if not loc:
        return
    if not TimezoneFinder:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_tz')]])
        return await emsg.reply_text("⚠️ Автоопределение недоступно (нет timezonefinder). Выберите вручную.", reply_markup=kb)

    try:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lng=loc.longitude, lat=loc.latitude) or 'Europe/Moscow'
        set_notification_timezone(cid, tz_name)
        skipped = suppress_stale_timezone_sensitive_notifications(cid)
        track_product_event(ProductEvent(
            event_name="timezone_updated",
            user_id=cid,
            status="success",
            properties={"destination": "notifications", "stale_notifications_suppressed": bool(skipped)},
        ))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🕒 Часовой пояс', callback_data='notif_tz')]])
        return await emsg.reply_text(f"✅ Часовой пояс установлен: {tz_name}. Можно поправить вручную.", reply_markup=kb)
    except Exception:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_tz')]])
        return await emsg.reply_text("⚠️ Не удалось определить. Выберите вручную.", reply_markup=kb)
