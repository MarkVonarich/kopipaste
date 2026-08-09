import logging
import hashlib
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update, WebAppInfo
from telegram.error import BadRequest
from db.database import get_conn, pg_fetchall, pg_exec
from datetime import datetime, timedelta, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes
from db.queries import (
    upsert_user_alias, update_user_field, set_budget,
    get_user_currency, get_user_budgets, delete_last_operation,
    set_category_limit, get_category_limit, list_category_limits, delete_category_limit,
    get_user_locale, get_user_tz, log_category_feedback, insert_ml_observation,
    list_user_limits, get_limit_by_key, update_limit_amount, update_limit_period,
    resolve_limit_conflict_replace, delete_limit_by_key,
    get_smart_morning_limits_enabled, set_smart_morning_limits_enabled,
    get_limit_spent, adjust_limit_amount, record_category_confirmation, update_last_operation_fields, update_operation_fields_by_id,
    reminders_list, reminder_insert, reminder_get, reminder_update, reminder_delete
)
from routers.helpers import prompt_type_menu, prompt_category_menu
from ui.keyboards import (
    category_budget_picker_kb,
    export_menu_kb,
    help_menu_kb,
    limits_budgets_hub_kb,
    main_menu_kb as canonical_main_menu_kb,
    ml_top2_kb,
    reminders_menu_kb,
    settings_menu_kb,
)
from services.analytics import build_report
from services.ml_prep import normalize_for_ml, normalize_alias_text
from services.ml_suggest import get_top2_suggestions
from services.categories import (
    category_reference_counts,
    delete_category_without_operations,
    get_or_create_custom_category,
    hard_delete_category_with_operations,
    is_protected_category,
    list_managed_categories,
    normalize_category_name,
    normalized_category_key,
    rename_category,
    transfer_category,
)
from services.operations import cancel_operation_draft, commit_operation_draft, load_operation_draft, record_financial_operation
from services.reminders import (
    ReminderError,
    _next_monthly_date,
    delete_reminder as delete_shared_reminder,
    record_reminder as record_shared_reminder,
    snooze_reminder as snooze_shared_reminder,
    toggle_reminder as toggle_shared_reminder,
)
from services.reminder_totals import render_reminder_totals
from services.workspaces import create_group_workspace, is_active_telegram_member, join_group_workspace, resolve_workspace
from ui.messages import render_operation_confirmation
from services.export_xlsx import build_export_xlsx
from services.export_flow import clear_export_wait_flags, export_state_has_period, parse_export_date, preset_period, validate_export_period
from services.budgeting import build_budget_status, list_category_budget_groups, list_general_limits, period_bounds, render_limit_alert
from services.budgeting import create_category_budget_group, list_active_expense_categories
from services.automatic_notifications import DeliveryPolicy, is_quiet_local, queue_automatic_notification, quiet_hours_window, suppress_stale_timezone_sensitive_notifications
from services.challenges import achievements_for_user, upsert_assignments
from services.i18n import t
from services.notification_preferences import get_notification_preferences, grouped_notification_preferences, set_daily_notification_time, set_grouped_notification_preference, set_notification_timezone, set_quiet_hours_time, toggle_notification_preference, toggle_quiet_hours
from services.personal_data_deletion import delete_financial_history, delete_user_data, history_period_bounds, preview_delete_financial_history
from services.analytics_privacy import apply_account_deletion
from services.product_events import ProductEvent, track_product_event
from services.user_time import TIMEZONE_CHOICES, resolve_user_timezone, user_local_date
from settings import MINIAPP_PUBLIC_URL
from services.goals import (
    GoalError,
    add_goal_movement,
    create_goal,
    delete_goal_permanently,
    format_date_ru,
    format_money,
    get_goal,
    goal_status_label,
    list_goals,
    list_movements,
    parse_money,
    render_goal_card_text,
    set_goal_reminders,
    set_goal_status,
    update_goal_details,
    update_goal_plan,
)
from utils.money import MoneyParseError, format_money as format_money_value, to_decimal_money
from services.security_events import SecurityEvent, track_security_event
from services.records import send_operation_limit_alert
import tempfile
import os
from time import time as unix_time
from secrets import token_urlsafe

log = logging.getLogger(__name__)


def _integer_major_amount(value) -> Decimal | None:
    try:
        amount = to_decimal_money(value, positive=True)
    except (MoneyParseError, ValueError):
        return None
    return amount


def _repeat_label(r: str, d: dict) -> str:
    return {'none': 'не повторять', 'weekly': 'каждую неделю', 'monthly': 'каждый месяц', 'yearly': 'каждый год', 'custom_days': f"каждые {int(d.get('repeat_interval_days') or 1)} дней"}.get(r or 'none', r or 'none')


def _reminders_menu_kb(has_any: bool):
    return reminders_menu_kb(has_any)


async def _send_standard_op_confirmation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, dt: date, op_type: str, category: str, amount: int, comment: str, currency: str | None = None):
    second = InlineKeyboardButton('💰 Остаток', callback_data='status') if op_type == 'Расходы' else InlineKeyboardButton('💵 Доходы', callback_data='income_status')
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'),
        second,
        InlineKeyboardButton('✏️ Изменить', callback_data='op_edit'),
    ]])
    name = (getattr(user, 'full_name', None) or getattr(user, 'first_name', None) or getattr(user, 'username', None) or 'Пользователь')
    display_currency = currency or get_user_currency(chat_id)
    text = render_operation_confirmation(name=name, amount=amount, currency=display_currency, category=category, op_dt=dt, original=comment, op_kind=op_type)
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=kb)


def _budget_spent(user_id: int, period: str) -> int:
    today = date.today()
    if period == 'week':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        start = today.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = nxt - timedelta(days=1)
    rows = pg_fetchall("""
      SELECT COALESCE(SUM(amount),0)
      FROM public.operations
      WHERE user_id=%s AND type='Расходы' AND op_date BETWEEN %s AND %s
        AND COALESCE(type,'') <> 'noop' AND COALESCE(category,'') <> 'Без операций'
    """, (user_id, start, end))
    return int(rows[0][0] if rows else 0)


def _budgets_hub_text(user_id: int) -> str:
    try:
        wl, ml = get_user_budgets(user_id)
    except Exception:
        wl, ml = 0, 0
    try:
        active_limits = len(list_user_limits(user_id))
    except Exception:
        active_limits = 0
    if not wl and not ml:
        return (
            '💰 Бюджеты и Лимиты\n\n'
            'Общий бюджет пока не задан.\n\n'
            'Бюджет помогает понять, сколько можно безопасно тратить за неделю или месяц.'
        )
    lines = ['💰 Бюджеты и Лимиты', '', 'Общий бюджет:']
    if ml:
        spent = _budget_spent(user_id, 'month')
        rem = ml - spent
        lines += [f"Месяц — {_fmt_money(ml)}", f"Потрачено — {_fmt_money(spent)}", (f"Осталось — {_fmt_money(rem)}" if rem >= 0 else f"Перерасход — {_fmt_money(abs(rem))}")]
    if wl:
        spent_w = _budget_spent(user_id, 'week')
        rem_w = wl - spent_w
        lines += ['', f"Неделя — {_fmt_money(wl)}", f"Потрачено — {_fmt_money(spent_w)}", (f"Осталось — {_fmt_money(rem_w)}" if rem_w >= 0 else f"Перерасход — {_fmt_money(abs(rem_w))}")]
    lines += ['', f'Лимиты категорий:\n{active_limits} активных лимита']
    return '\n'.join(lines)


def _budgets_hub_kb(has_any: bool):
    rows = [[InlineKeyboardButton('➕ Добавить бюджет', callback_data='bud_add')]]
    if has_any:
        rows += [[InlineKeyboardButton('✏️ Управлять бюджетами', callback_data='bud_edit')]]
    rows += [[InlineKeyboardButton('📂 Лимиты категорий', callback_data='lim_list')], [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')]]
    return InlineKeyboardMarkup(rows)


def _limits_budgets_hub_kb(user_id: int, locale: str | None = None) -> InlineKeyboardMarkup:
    try:
        wl, ml = get_user_budgets(user_id)
    except Exception:
        wl, ml = 0, 0
    rows = []
    if ml and ml > 0:
        rows.append([InlineKeyboardButton(f'Месячный бюджет — {_fmt_money(ml)}', callback_data='bud_card|month')])
    if wl and wl > 0:
        rows.append([InlineKeyboardButton(f'Недельный бюджет — {_fmt_money(wl)}', callback_data='bud_card|week')])
    rows.extend(limits_budgets_hub_kb(locale).inline_keyboard)
    rows.append([InlineKeyboardButton('➕ Добавить бюджет', callback_data='bud_add')])
    return InlineKeyboardMarkup(rows)


def _receipt_render_list(cands: list[dict], warning: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    lines = ['🧾 Нашёл операции:', '']
    for i, c in enumerate(cands[:10], start=1):
        amount = _integer_major_amount(c.get('amount'))
        amount_label = f"{amount:,} ₽".replace(',', ' ') if amount is not None else 'дробная сумма'
        label = (c.get('category') or 'Прочее') if (c.get('type') or 'Расходы') == 'Расходы' else 'Доходы'
        lines.append(f"{i}. {label} — {amount_label} — {c.get('merchant') or 'Из изображения'}")
    if warning:
        lines.append('\n⚠️ Я не уверен в части строк, лучше проверь перед записью.')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Записать всё', callback_data='receipt_confirm_all')],
        [InlineKeyboardButton('✏️ Изменить', callback_data='receipt_review_one')],
        [InlineKeyboardButton('❌ Отмена', callback_data='receipt_cancel')],
    ])
    return '\n'.join(lines), kb


def _receipt_render_card(cands: list[dict], idx: int) -> tuple[str, InlineKeyboardMarkup]:
    c = cands[idx]
    dt = c.get('date') or ''
    try:
        dts = datetime.fromisoformat(dt).strftime('%d.%m.%Y')
    except Exception:
        dts = 'Сегодня'
    text = (
        f"Операция {idx + 1} из {len(cands)}\n"
        f"Тип: {c.get('type') or 'Расходы'}\n"
        f"Сумма: {_fmt_money(amount) if (amount := _integer_major_amount(c.get('amount'))) is not None else 'некорректная сумма'}\n" +
        f"Категория: {c.get('category') or 'Прочее'}\n"
        f"Комментарий: {c.get('merchant') or 'Из изображения'}\n"
        f"Дата: {dts}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Записать', callback_data='receipt_save_one')],
        [InlineKeyboardButton('💰 Сумма', callback_data='rcpt_amt'), InlineKeyboardButton('🏷 Категория', callback_data='rcpt_cat')],
        [InlineKeyboardButton('📅 Дата', callback_data='rcpt_date'), InlineKeyboardButton('🔁 Тип', callback_data='rcpt_type')],
        [InlineKeyboardButton('📝 Комментарий', callback_data='rcpt_comment')],
        [InlineKeyboardButton('⏭ Пропустить', callback_data='receipt_skip_one')],
        [InlineKeyboardButton('⬅️ К списку', callback_data='receipt_back_list'), InlineKeyboardButton('❌ Отмена', callback_data='receipt_cancel')],
    ])
    return text, kb


def _export_menu_kb() -> InlineKeyboardMarkup:
    return export_menu_kb()


def _export_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Сегодня', callback_data='exp_custom_start_today'), InlineKeyboardButton('Вчера', callback_data='exp_custom_start_yday')],
        [InlineKeyboardButton('1 число месяца', callback_data='exp_custom_start_first')],
        [InlineKeyboardButton('✏️ Ввести дату', callback_data='exp_custom_start_input')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='exp_menu'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_end_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Сегодня', callback_data='exp_custom_end_today'), InlineKeyboardButton('Вчера', callback_data='exp_custom_end_yday')],
        [InlineKeyboardButton('Конец месяца', callback_data='exp_custom_end_month')],
        [InlineKeyboardButton('✏️ Ввести дату', callback_data='exp_custom_end_input')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='exp_custom'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Скачать XLSX', callback_data='exp_dl')],
        [InlineKeyboardButton('🔁 Изменить период', callback_data='exp_custom'), InlineKeyboardButton('❌ Отмена', callback_data='exp_cancel')],
    ])


def _export_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📤 Экспорт ещё раз', callback_data='exp_menu')],
        [InlineKeyboardButton('⬅️ Главное меню', callback_data='start_main')],
    ])


def _export_rows(chat_id: int, dfrom: date, dto: date) -> list[dict]:
    rows = pg_fetchall("""SELECT id, op_date, type, category, amount, COALESCE(comment,''), COALESCE(to_jsonb(operations)->>'source', 'telegram') FROM public.operations
                        WHERE chat_id=%s AND op_date BETWEEN %s AND %s
                          AND COALESCE(type,'') <> 'noop' AND COALESCE(category,'') <> 'Без операций'
                        ORDER BY op_date, id""", (chat_id, dfrom, dto))
    return [{'id': r[0], 'op_date': r[1], 'type': r[2], 'category': r[3], 'amount': to_decimal_money(r[4]), 'comment': r[5], 'source': r[6]} for r in rows]


async def _export_preview(q, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    st = context.user_data.setdefault('export_state', {})
    clear_export_wait_flags(context.user_data)
    dfrom = date.fromisoformat(st['from'])
    dto = date.fromisoformat(st['to'])
    ok, error = validate_export_period(dfrom, dto)
    if not ok:
        st.pop('to', None)
        msg = 'Дата конца не может быть раньше даты начала.' if error == 'end_before_start' else 'Период слишком большой. Выберите диапазон до 5 лет.'
        return await _safe_edit_or_reply(q, f'⚠️ {msg}\n\nВыбери конец периода:', reply_markup=_export_end_kb())
    rows = _export_rows(chat_id, dfrom, dto)
    exp = sum((to_decimal_money(r['amount']) for r in rows if r['type'] == 'Расходы'), Decimal("0.00"))
    inc = sum((to_decimal_money(r['amount']) for r in rows if r['type'] == 'Доходы'), Decimal("0.00"))
    st['count'] = len(rows)
    st['preview_rows'] = rows
    log.info('export_preview period=%s..%s count=%s user_id=%s', dfrom, dto, len(rows), chat_id)
    return await _safe_edit_or_reply(
        q,
        f'📤 Экспорт\n\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\n'
        f'Операций: {len(rows)}\nРасходы: {_fmt_money(exp)}\nДоходы: {_fmt_money(inc)}\nБаланс: {_fmt_money(inc-exp)}\n\nСформировать файл?',
        reply_markup=_export_confirm_kb(),
    )


async def _is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return getattr(member, 'status', '') in {'creator', 'administrator'}
    except Exception as e:
        log.warning('group_admin_check_failed chat_id=%s user_id=%s reason=%s', chat_id, user_id, type(e).__name__)
        return False


async def _is_group_active_member(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return is_active_telegram_member(getattr(member, 'status', ''), getattr(member, 'is_member', None))
    except Exception as e:
        log.warning('group_member_check_failed chat_id=%s user_id=%s reason=%s', chat_id, user_id, type(e).__name__)
        return False


async def _handle_group_draft_callback(update, context: ContextTypes.DEFAULT_TYPE, data: str):
    q = update.callback_query
    actor_user_id = update.effective_user.id
    parts = data.split('|', 2)
    action = parts[0]
    draft_id = parts[1] if len(parts) > 1 else ''
    choice = parts[2] if len(parts) > 2 else ''

    draft = load_operation_draft(draft_id, actor_user_id=actor_user_id)
    if not draft:
        return await q.answer('This operation draft has expired. Send the operation again.', show_alert=True)
    if draft.get('status') == 'wrong_actor':
        track_security_event(SecurityEvent(
            event_name="foreign_draft_access",
            user_id=actor_user_id,
            workspace_id=draft.get("workspace_id"),
            chat_type=getattr(update.effective_chat, 'type', 'group') or 'group',
            rule_key="group_draft_actor",
            action_taken="denied",
            metadata={"handler": "group_draft_callback"},
        ))
        return await q.answer('Only the person who started this operation can finish it.', show_alert=True)
    if draft.get('status') not in {'draft', 'committed'}:
        return await q.answer('This operation draft has expired. Send the operation again.', show_alert=True)
    payload = draft.get('payload') or {}
    if action == 'gcancel':
        workspace_id = draft.get('workspace_id')
        result = cancel_operation_draft(
            draft_id=draft_id,
            actor_user_id=actor_user_id,
            chat_id=draft['chat_id'],
            workspace_id=workspace_id,
        )
        st = context.user_data.get('await_group_custom_category')
        if isinstance(st, dict) and st.get('draft_id') == draft_id and result['status'] in {'cancelled', 'already_committed', 'expired'}:
            context.user_data.pop('await_group_custom_category', None)
        if result['status'] == 'already_committed':
            return await q.answer('Операция уже была сохранена', show_alert=True)
        if result['status'] != 'cancelled':
            return await q.answer('This operation draft has expired. Send the operation again.', show_alert=True)
        await q.answer('Отменено')
        return await _safe_edit_or_reply(q, 'Операция отменена.')
    if action == 'gadd':
        if draft.get('status') != 'draft':
            return await q.answer('This operation is already completed.', show_alert=True)
        context.user_data['await_group_custom_category'] = {
            'draft_id': draft_id,
            'chat_id': draft['chat_id'],
            'workspace_id': draft.get('workspace_id'),
            'actor_user_id': actor_user_id,
        }
        await q.answer()
        return await q.message.reply_text('Введите название новой категории:')
    options = payload.get('category_options') or {}
    category = options.get(choice)
    if not category:
        return await q.answer('This operation draft has expired. Send the operation again.', show_alert=True)
    workspace = resolve_workspace(draft['chat_id'], actor_user_id, getattr(update.effective_chat, 'type', 'group'))
    if not workspace.is_configured or workspace.role not in {'owner', 'admin', 'member'}:
        track_security_event(SecurityEvent(
            event_name="foreign_workspace_access",
            user_id=actor_user_id,
            workspace_id=workspace.workspace_id,
            chat_type=getattr(update.effective_chat, 'type', 'group') or 'group',
            rule_key="group_draft_workspace",
            action_taken="denied",
            metadata={"handler": "group_draft_callback"},
        ))
        return await q.answer('You do not have permission to add operations in this workspace.', show_alert=True)
    result = commit_operation_draft(
        draft_id=draft_id,
        actor_user_id=actor_user_id,
        category=category,
        chat_id=draft['chat_id'],
        workspace_id=workspace.workspace_id,
        chat_type=getattr(update.effective_chat, 'type', 'group') or 'group',
        metadata={'draft_id': draft_id},
    )
    if result['status'] not in {'committed', 'already_committed'}:
        return await q.answer('This operation draft has expired. Send the operation again.', show_alert=True)
    if result['status'] == 'already_committed':
        return await q.answer('Операция уже была сохранена', show_alert=True)
    recorded = result['recorded']
    user_name = getattr(update.effective_user, 'full_name', None) or getattr(update.effective_user, 'username', None) or str(actor_user_id)
    text = (
        f"✅ Операция записана\n\n"
        f"{category} — {recorded.amount} {recorded.currency}\n"
        f"Пространство: {workspace.name}\n"
        f"Добавил(а): {user_name}"
    )
    await q.answer('Уже сохранено' if result['status'] == 'already_committed' else 'Сохранено')
    await send_operation_limit_alert(recorded, context)
    return await _safe_edit_or_reply(q, text)

# ──────────────────────────────────────────────────────────────────────────────
# Вспомогалки для меню лимитов
# ──────────────────────────────────────────────────────────────────────────────



async def _safe_edit_or_reply(q, text: str, reply_markup=None, parse_mode: str | None = None):
    try:
        return await q.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        msg = str(e).lower()
        if ('message is not modified' in msg) or ("message can't be edited" in msg) or ('query is too old' in msg):
            log.warning('limits_ui edit fallback: %s', e)
            if getattr(q, 'message', None):
                return await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            return None
        log.warning('limits_ui bad request: %s', e)
        raise
    except AttributeError as e:
        log.warning('callback_ui missing message fallback reason=%s', type(e).__name__)
        if getattr(q, 'message', None):
            return await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return None


async def render_main_menu(q, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('await_group_custom_category', None)
    context.user_data.pop('goal_draft', None)
    context.user_data.pop('goal_action', None)
    context.user_data.pop('goal_delete_confirm', None)
    clear_export_wait_flags(context.user_data)
    locale = get_user_locale(chat_id)
    return await _safe_edit_or_reply(q, t('menu.main.title', locale), reply_markup=canonical_main_menu_kb(locale))


async def render_settings_menu(q, chat_id: int):
    locale = get_user_locale(chat_id)
    return await _safe_edit_or_reply(q, t('menu.settings', locale), reply_markup=settings_menu_kb(locale))


async def render_limits_budgets_menu(q, chat_id: int):
    locale = get_user_locale(chat_id)
    return await _safe_edit_or_reply(q, _budgets_hub_text(chat_id), reply_markup=_limits_budgets_hub_kb(chat_id, locale))


async def render_export_menu(q):
    return await _safe_edit_or_reply(q, '📤 Экспорт записей\n\nВыбери период, за который выгрузить операции.', reply_markup=_export_menu_kb())


async def render_reminders_menu(q, chat_id: int, context: ContextTypes.DEFAULT_TYPE | None = None):
    if context is not None:
        context.user_data['notification_back'] = 'rem_menu'
    rows = reminders_list(chat_id, active_only=True)
    if not rows:
        return await _safe_edit_or_reply(
            q,
            '🔔 Напоминания\n\nПока ничего нет.\n\nМожно добавить подписку, платёж, будущую трату или доход — я напомню заранее.',
            reply_markup=_reminders_menu_kb(False),
        )
    lines = ['🔔 Напоминания', '', 'Активные:']
    btns = []
    for i, r in enumerate(rows[:5], start=1):
        lines.append(f"{i}. {r['title']} — {_fmt_money(r['amount'])}, {r['event_date'].day} число")
        btns.append([InlineKeyboardButton(f"Открыть: {r['title'][:20]}", callback_data=f"rem_o|{r['id']}")])
    lines.extend(['', render_reminder_totals(rows, get_user_locale(chat_id))])
    btns += _reminders_menu_kb(True).inline_keyboard
    return await _safe_edit_or_reply(q, '\n'.join(lines), reply_markup=InlineKeyboardMarkup(btns))


def _cbg_workspace_id(chat_id: int, actor_user_id: int, chat_type: str) -> int | None:
    return resolve_workspace(chat_id, actor_user_id, chat_type).workspace_id


def _cbg_options(user_id: int, workspace_id: int | None) -> list[dict]:
    return [item.__dict__ for item in list_active_expense_categories(user_id=user_id, workspace_id=workspace_id)]


def _cbg_selected_names(draft: dict, options: list[dict]) -> list[str]:
    by_token = {item["token"]: item["name"] for item in options}
    names = []
    for token in draft.get("selected_tokens") or []:
        name = by_token.get(token) or (draft.get("selected_categories") or {}).get(token)
        if name and name not in names:
            names.append(name)
    return names


async def _cbg_render_picker(q, context: ContextTypes.DEFAULT_TYPE, cid: int, *, page: int | None = None, note: str | None = None):
    draft = context.user_data.setdefault("cbg_draft", {})
    workspace_id = draft.get("workspace_id")
    options = _cbg_options(cid, workspace_id)
    draft["category_options"] = {item["token"]: item["name"] for item in options}
    if page is not None:
        draft["page"] = max(0, int(page))
    selected = set(draft.get("selected_tokens") or [])
    count = len(selected)
    title = draft.get("name") or "Бюджет из категорий"
    lines = [
        "🧩 Бюджет из категорий",
        "",
        f"Название: {title}",
        f"Выбрано категорий: {count}",
    ]
    if note:
        lines.extend(["", note])
    if not options:
        lines.extend(["", "Пока нет категорий расходов. Добавьте новую категорию или запишите расход."])
    context.user_data["cbg_draft"] = draft
    return await _safe_edit_or_reply(
        q,
        "\n".join(lines),
        reply_markup=category_budget_picker_kb(options, selected, page=int(draft.get("page") or 0)),
    )


def _cbg_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Неделя", callback_data="cbgp|period|week"), InlineKeyboardButton("Месяц", callback_data="cbgp|period|month")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="cbgp|back_amount"), InlineKeyboardButton("❌ Отмена", callback_data="cbgp|cancel")],
    ])


def _cbg_alerts_kb(enabled: bool) -> InlineKeyboardMarkup:
    label = "✅ Оповещения включены" if enabled else "⛔ Оповещения выключены"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="cbgp|alerts|toggle")],
        [InlineKeyboardButton("➡️ Продолжить", callback_data="cbgp|confirm")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="cbgp|back_period"), InlineKeyboardButton("❌ Отмена", callback_data="cbgp|cancel")],
    ])


def _cbg_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать бюджет", callback_data="cbgp|save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="cbgp|back_alerts"), InlineKeyboardButton("❌ Отмена", callback_data="cbgp|cancel")],
    ])


NOTIFICATION_TOGGLE_LABELS = {
    'morning': ('Утренние уведомления', 'morning_enabled', '✅ Утро: включено', '⛔ Утро: выключено'),
    'evening': ('Вечерние уведомления', 'evening_enabled', '✅ Вечер: включено', '⛔ Вечер: выключено'),
    'limits': ('Оповещения лимитов', 'limit_alerts_enabled', '💰 Лимиты: включены', '💰 Лимиты: выключены'),
    'budgets': ('Оповещения бюджетов', 'budget_alerts_enabled', '🧩 Бюджеты: включены', '🧩 Бюджеты: выключены'),
    'subscriptions': ('Оповещения подписок', 'subscription_alerts_enabled', '🔔 Подписки: включены', '🔕 Подписки: выключены'),
    'recurring': ('Регулярные траты', 'recurring_spend_alerts_enabled', '🔁 Регулярные траты: включены', '⛔ Регулярные траты: выключены'),
    'weekly': ('Недельные отчёты', 'weekly_reports_enabled', '📅 Недельные отчёты: включены', '📅 Недельные отчёты: выключены'),
    'monthly': ('Месячные отчёты', 'monthly_reports_enabled', '🗓 Месячные отчёты: включены', '🗓 Месячные отчёты: выключены'),
    'challenges': ('Челленджи', 'challenge_notifications_enabled', '🏆 Челленджи: включены', '🏆 Челленджи: выключены'),
    'goals': ('Цели', 'goal_notifications_enabled', '🎯 Цели: включены', '🎯 Цели: выключены'),
}

GROUPED_NOTIFICATION_LABELS = {
    "daily": ("Ежедневные уведомления", "✅ Ежедневные уведомления", "⛔ Ежедневные уведомления"),
    "plans": ("Планы и контроль", "✅ Планы и контроль", "⛔ Планы и контроль"),
    "reports": ("Отчёты", "✅ Отчёты", "⛔ Отчёты"),
}


def _notif_label(prefs: dict, key: str) -> str:
    _, field, on_label, off_label = NOTIFICATION_TOGGLE_LABELS[key]
    default_value = False if key in {'challenges', 'goals'} else True
    return on_label if prefs.get(field, default_value) else off_label


def _notification_settings_markup(prefs: dict, back_dest: str) -> InlineKeyboardMarkup:
    grouped = grouped_notification_preferences_from_prefs(prefs)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_grouped_notif_label(grouped, 'daily'), callback_data='notif_group|daily')],
        [InlineKeyboardButton(_grouped_notif_label(grouped, 'plans'), callback_data='notif_group|plans')],
        [InlineKeyboardButton(_grouped_notif_label(grouped, 'reports'), callback_data='notif_group|reports')],
        [InlineKeyboardButton('🌙 Тихие часы', callback_data='notif_quiet_hours')],
        [InlineKeyboardButton('🕒 Время уведомлений', callback_data='notif_times')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='notif_tz')],
        [InlineKeyboardButton('⬅️ Назад', callback_data=back_dest)],
    ])


def grouped_notification_preferences_from_prefs(prefs: dict) -> dict:
    return {
        "daily_notifications": {"enabled": bool(prefs.get("morning_enabled", True) or prefs.get("evening_enabled", True))},
        "plans_control": {"enabled": bool(prefs.get("limit_alerts_enabled", True) or prefs.get("budget_alerts_enabled", True) or prefs.get("goal_notifications_enabled", False))},
        "reports": {"enabled": bool(prefs.get("weekly_reports_enabled", True) or prefs.get("monthly_reports_enabled", True))},
    }


def _grouped_notif_label(grouped: dict, key: str) -> str:
    title, on_label, off_label = GROUPED_NOTIFICATION_LABELS[key]
    enabled_key = "daily_notifications" if key == "daily" else "plans_control" if key == "plans" else "reports"
    return on_label if (grouped.get(enabled_key) or {}).get("enabled") else off_label


async def _render_notification_settings(q, cid: int, context: ContextTypes.DEFAULT_TYPE):
    back_dest = context.user_data.get('notification_back') or 'menu_settings'
    if back_dest not in {'menu_settings', 'lb_hub', 'rem_menu', 'start_main', 'chal|home'}:
        back_dest = 'menu_settings'
    prefs = get_notification_preferences(cid)
    grouped = grouped_notification_preferences_from_prefs(prefs)
    daily = "включены" if grouped["daily_notifications"]["enabled"] else "выключены"
    plans = "включены" if grouped["plans_control"]["enabled"] else "выключены"
    reports = "включены" if grouped["reports"]["enabled"] else "выключены"
    qh_start = prefs.get("quiet_hours_start") or "22:30"
    qh_end = prefs.get("quiet_hours_end") or "08:00"
    text = (
        '🔔 Оповещения\n\n'
        f"Ежедневные уведомления: {daily}\n"
        f"Утро {prefs['morning_time']} · Вечер {prefs['evening_time']}\n"
        "Короткие сообщения утром и вечером помогают не забывать записывать операции.\n\n"
        f"📊 Планы и контроль: {plans}\n"
        "Предупреждает о лимитах и бюджетах и напоминает о финансовых целях.\n\n"
        f"📅 Отчёты: {reports}\n"
        "Присылает финансовую сводку за неделю и месяц.\n\n"
        f"🌙 Тихие часы: {'включены' if prefs.get('quiet_hours_enabled') else 'выключены'} · {qh_start}–{qh_end}\n"
        "В это время автоматические сообщения не будут вас беспокоить.\n\n"
        f"Часовой пояс: {_notification_timezone_label(cid)}"
    )
    return await _safe_edit_or_reply(q, text, reply_markup=_notification_settings_markup(prefs, back_dest))


def _challenge_notification_settings_markup(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = 'Выключить' if enabled else 'Включить'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data='notif_toggle|challenges')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='menu_notifications')],
        [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')],
    ])


async def _render_challenge_notification_settings(q, cid: int):
    return await _legacy_challenge_response(q, cta=False, text="Оповещения о челленджах больше не используются.\nЧелленджи доступны в приложении.")


def _notification_times_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('☀️ Изменить утро', callback_data='notif_time|morning'), InlineKeyboardButton('🌙 Изменить вечер', callback_data='notif_time|evening')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='menu_notifications')],
    ])


async def _render_notification_times(q, cid: int):
    prefs = get_notification_preferences(cid)
    text = (
        "🕒 Время уведомлений\n\n"
        f"Утро: {prefs.get('morning_time') or '08:30'}\n"
        f"Вечер: {prefs.get('evening_time') or '20:30'}\n\n"
        "Ежедневные уведомления включаются одной настройкой, а время можно менять отдельно."
    )
    return await _safe_edit_or_reply(q, text, reply_markup=_notification_times_markup())


def _quiet_hours_markup(prefs: dict) -> InlineKeyboardMarkup:
    enabled = bool(prefs.get('quiet_hours_enabled'))
    label = '✅ Тихие часы' if enabled else '⛔ Тихие часы'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data='quiet|toggle')],
        [InlineKeyboardButton('22:30', callback_data='quiet|start|22:30'), InlineKeyboardButton('23:00', callback_data='quiet|start|23:00')],
        [InlineKeyboardButton('07:00', callback_data='quiet|end|07:00'), InlineKeyboardButton('08:00', callback_data='quiet|end|08:00')],
        [InlineKeyboardButton('🌙 Начало', callback_data='quiet|manual|start'), InlineKeyboardButton('☀️ Конец', callback_data='quiet|manual|end')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='menu_notifications')],
    ])


def _notification_timezone_label(user_id: int) -> str:
    resolved = resolve_user_timezone(user_id)
    return resolved.timezone_name


def _timezone_markup(current_tz: str, back_dest: str = "menu_notifications") -> InlineKeyboardMarkup:
    rows = []
    for idx in range(0, len(TIMEZONE_CHOICES), 2):
        row = []
        for label, tz_name in TIMEZONE_CHOICES[idx:idx + 2]:
            marker = "✅ " if tz_name == current_tz else ""
            row.append(InlineKeyboardButton(f"{marker}{label}", callback_data=f"tz|set|{tz_name}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("Другая IANA", callback_data="tz|manual")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_dest)])
    return InlineKeyboardMarkup(rows)


async def _render_notification_timezone(q, cid: int, *, back_dest: str = "menu_notifications"):
    resolved = resolve_user_timezone(cid)
    text = (
        "🕒 Часовой пояс\n\n"
        f"Текущий: {resolved.timezone_name}\n\n"
        "Он используется для будущих напоминаний, отчётов, лимитов, целей и тихих часов."
    )
    return await _safe_edit_or_reply(q, text, reply_markup=_timezone_markup(resolved.timezone_name, back_dest))


async def _render_quiet_hours(q, cid: int):
    prefs = get_notification_preferences(cid)
    start = prefs.get('quiet_hours_start') or '22:30'
    end = prefs.get('quiet_hours_end') or '08:00'
    text = (
        '🌙 Тихие часы\n\n'
        'Пауза работает только для автоматических напоминаний, отчётов, лимитов и челленджей. Если вы сами нажали кнопку или написали боту, ответ придёт сразу.\n\n'
        f"Статус: {'включены' if prefs.get('quiet_hours_enabled') else 'выключены'}\n"
        f"Период: {start}–{end}\n"
        f"Часовой пояс: {_notification_timezone_label(cid)}"
    )
    return await _safe_edit_or_reply(q, text, reply_markup=_quiet_hours_markup(prefs))


CHALLENGE_SECTION_LABELS = {
    "today": "Сегодня",
    "week": "Неделя",
    "month": "Месяц",
    "onboarding": "Старт",
}


def _challenge_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Сегодня", callback_data="chal|sec|today"), InlineKeyboardButton("Неделя", callback_data="chal|sec|week")],
        [InlineKeyboardButton("Месяц", callback_data="chal|sec|month"), InlineKeyboardButton("Старт", callback_data="chal|sec|onboarding")],
        [InlineKeyboardButton("🏅 Достижения", callback_data="chal|ach")],
        [InlineKeyboardButton("Как работает", callback_data="chal|how")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="start_main")],
    ])


async def _render_challenge_home(q, user_id: int):
    track_product_event(ProductEvent(
        event_name="challenge_screen_opened",
        user_id=user_id,
        status="success",
        properties={"section": "home"},
    ))
    text = "🏆 Челленджи\n\nВыберите период, достижения или справку."
    return await _safe_edit_or_reply(q, text, reply_markup=_challenge_home_kb())


def _render_challenge_cards(section: str, cards) -> tuple[str, InlineKeyboardMarkup]:
    label = CHALLENGE_SECTION_LABELS.get(section, section)
    lines = [f"🏆 {label}", ""]
    rows = []
    if not cards:
        lines.append("Все задания этого раздела уже выполнены.")
    for card in cards:
        done = "✅" if card.completed else "◻️"
        lines.extend([
            f"{done} {card.definition.title}",
            card.definition.description,
            f"Прогресс: {min(card.progress, card.target)}/{card.target}",
            "",
        ])
        if not card.completed:
            rows.append([InlineKeyboardButton(card.definition.cta_label[:48], callback_data=f"chal|cta|{card.definition.key}")])
    rows.append([InlineKeyboardButton("⬅️ Челленджи", callback_data="chal|home"), InlineKeyboardButton("🏠 Главное", callback_data="start_main")])
    return "\n".join(lines).strip(), InlineKeyboardMarkup(rows)


async def _render_challenge_section(q, user_id: int, section: str):
    if section not in CHALLENGE_SECTION_LABELS:
        return await q.answer("Раздел недоступен", show_alert=True)
    cards = upsert_assignments(user_id, section)
    track_product_event(ProductEvent(
        event_name="challenge_screen_opened",
        user_id=user_id,
        status="success",
        properties={"section": section},
    ))
    text, kb = _render_challenge_cards(section, cards)
    return await _safe_edit_or_reply(q, text, reply_markup=kb)


async def _render_challenge_achievements(q, user_id: int):
    rows = achievements_for_user(user_id)
    lines = ["🏅 Достижения", ""]
    earned_count = 0
    for item, earned_at in rows:
        earned = earned_at is not None
        earned_count += 1 if earned else 0
        lines.append(f"{'✅' if earned else '◻️'} {item.title} — {item.description}")
    lines.insert(1, f"{earned_count}/{len(rows)} получено")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Челленджи", callback_data="chal|home"), InlineKeyboardButton("🏠 Главное", callback_data="start_main")]])
    return await _safe_edit_or_reply(q, "\n".join(lines), reply_markup=kb)


async def _render_challenge_how(q):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Челленджи", callback_data="chal|home"), InlineKeyboardButton("🏠 Главное", callback_data="start_main")]])
    text = (
        "🏆 Как работает\n\n"
        "Челленджи считаются по вашим локальным действиям в боте: операциям, отчётам, напоминаниям, лимитам и настройкам. "
        "PostHog не используется как источник прогресса.\n\n"
        "Оповещения о челленджах подчиняются тихим часам."
    )
    return await _safe_edit_or_reply(q, text, reply_markup=kb)


def _challenge_destination(callback_data: str) -> str:
    if callback_data == "menu_history":
        return "history"
    if callback_data == "cat_menu":
        return "category_management"
    if callback_data == "menu_tz":
        return "timezone"
    return callback_data.split("|", 1)[0]


def _legacy_challenge_markup() -> InlineKeyboardMarkup:
    rows = []
    if MINIAPP_PUBLIC_URL:
        rows.append([InlineKeyboardButton("Открыть", web_app=WebAppInfo(url=MINIAPP_PUBLIC_URL))])
    rows.append([InlineKeyboardButton("Главное меню", callback_data="start_main")])
    return InlineKeyboardMarkup(rows)


async def _legacy_challenge_response(q, *, cta: bool = False, text: str | None = None):
    body = text or "Челленджи теперь доступны в КопиPaste."
    if cta:
        body += "\n\nОтправьте операцию сообщением, например:\nкофе 250"
    return await _safe_edit_or_reply(q, body, reply_markup=_legacy_challenge_markup())


def _goal_workspace(update) -> int | None:
    return resolve_workspace(
        update.effective_chat.id if update.effective_chat else update.effective_user.id,
        update.effective_user.id,
        getattr(update.effective_chat, 'type', 'private') or 'private',
    ).workspace_id


def _goal_nav_kb(back: str = "goal|home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")],
    ])


def _goal_home_kb(goals) -> InlineKeyboardMarkup:
    rows = []
    for goal in goals[:8]:
        rows.append([InlineKeyboardButton(goal.display_name[:40], callback_data=f"goal|o|{goal.id}")])
    rows.append([InlineKeyboardButton("➕ Создать цель", callback_data="goal|new")])
    if goals:
        rows.append([InlineKeyboardButton("✅ Завершённые", callback_data="goal|list|done"), InlineKeyboardButton("🗄 Архив", callback_data="goal|list|arch")])
    rows.append([InlineKeyboardButton("📘 Как это работает", callback_data="goal|how")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="start_main")])
    return InlineKeyboardMarkup(rows)


async def _render_goals_home(q, context: ContextTypes.DEFAULT_TYPE, update):
    user_id = update.effective_user.id
    workspace_id = _goal_workspace(update)
    goals = list_goals(user_id, workspace_id, status_group="active")
    track_product_event(ProductEvent(
        event_name="goal_screen_opened",
        user_id=user_id,
        workspace_id=workspace_id,
        status="success",
        properties={"section": "home"},
    ))
    if not goals:
        text = (
            "🎯 Финансовые цели\n\n"
            "Цель — это не просто сумма накоплений.\n"
            "Finuchet рассчитает план и подскажет следующий шаг."
        )
    else:
        lines = ["🎯 Финансовые цели", "", f"Активных целей: {len(goals)}", ""]
        for goal in goals[:5]:
            lines.append(f"{goal.display_name} — {format_money(goal.current_balance, goal.currency)} из {format_money(goal.target_amount, goal.currency)}")
        text = "\n".join(lines)
    context.user_data["goal_last_list"] = "home"
    return await _safe_edit_or_reply(q, text, reply_markup=_goal_home_kb(goals))


async def _render_goal_list(q, context: ContextTypes.DEFAULT_TYPE, update, group: str):
    user_id = update.effective_user.id
    workspace_id = _goal_workspace(update)
    status_group = "completed" if group == "done" else "archive"
    goals = list_goals(user_id, workspace_id, status_group=status_group)
    title = "✅ Завершённые цели" if group == "done" else "🗄 Архив целей"
    rows = [[InlineKeyboardButton(goal.display_name[:40], callback_data=f"goal|o|{goal.id}|{group}")] for goal in goals[:10]]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="goal|home"), InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")])
    text = title if goals else f"{title}\n\nЗдесь пока пусто."
    context.user_data["goal_last_list"] = group
    return await _safe_edit_or_reply(q, text, reply_markup=InlineKeyboardMarkup(rows))


def _goal_card_kb(goal_id: int, back_group: str = "home") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Пополнить", callback_data=f"goal|add|{goal_id}"), InlineKeyboardButton("➖ Снять", callback_data=f"goal|wd|{goal_id}")],
        [InlineKeyboardButton("📅 План", callback_data=f"goal|pl|{goal_id}"), InlineKeyboardButton("🔔 Напоминания", callback_data=f"goal|rem|{goal_id}")],
        [InlineKeyboardButton("✏️ Изменить", callback_data=f"goal|edit|{goal_id}"), InlineKeyboardButton("Ещё", callback_data=f"goal|more|{goal_id}")],
    ]
    if back_group == "done":
        back = "goal|list|done"
    elif back_group == "arch":
        back = "goal|list|arch"
    else:
        back = "goal|home"
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")])
    return InlineKeyboardMarkup(rows)


async def _render_goal_card(q, context: ContextTypes.DEFAULT_TYPE, update, goal_id: int, back_group: str | None = None):
    user_id = update.effective_user.id
    workspace_id = _goal_workspace(update)
    goal = get_goal(goal_id, user_id, workspace_id)
    if not goal:
        return await _safe_edit_or_reply(q, "Эта кнопка устарела. Откройте цель заново.", reply_markup=_goal_nav_kb("goal|home"))
    back = back_group or context.user_data.get("goal_last_list") or "home"
    return await _safe_edit_or_reply(q, render_goal_card_text(goal), reply_markup=_goal_card_kb(goal.id, back))


def _goal_plan_kb(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Успеть к дате", callback_data=f"goal|ps|{goal_id}|deadline")],
        [InlineKeyboardButton("💳 Комфортная сумма", callback_data=f"goal|ps|{goal_id}|contribution")],
        [InlineKeyboardButton("👐 Пока без плана", callback_data=f"goal|ps|{goal_id}|none")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal_id}"), InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")],
    ])


def _goal_reminder_prompt_kb(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Включить напоминания", callback_data=f"goal|remtog|{goal_id}")],
        [InlineKeyboardButton("Пока без напоминаний", callback_data=f"goal|o|{goal_id}")],
    ])


def _goal_frequency_kb(goal_id: int, strategy: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("После каждой зарплаты", callback_data=f"goal|fr|{goal_id}|{strategy}|salary_monthly")],
        [InlineKeyboardButton("Один раз в месяц", callback_data=f"goal|fr|{goal_id}|{strategy}|monthly")],
        [InlineKeyboardButton("Два раза в месяц", callback_data=f"goal|fr|{goal_id}|{strategy}|twice_monthly")],
        [InlineKeyboardButton("Раз в неделю", callback_data=f"goal|fr|{goal_id}|{strategy}|weekly")],
        [InlineKeyboardButton("Без расписания", callback_data=f"goal|fr|{goal_id}|{strategy}|none")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|pl|{goal_id}"), InlineKeyboardButton("❌ Отмена", callback_data=f"goal|o|{goal_id}")],
    ])


async def _render_goal_plan_menu(q, update, goal_id: int):
    goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
    if not goal:
        return await _safe_edit_or_reply(q, "Эта кнопка устарела. Откройте цель заново.", reply_markup=_goal_nav_kb("goal|home"))
    text = (
        "📅 План цели\n\n"
        f"Стратегия: {goal.strategy}\n"
        f"Периодичность: {goal.frequency}\n"
        f"Рекомендуемый взнос: {format_money(goal.planned_contribution_amount or 0, goal.currency)}\n"
        f"Следующая дата: {format_date_ru(goal.next_contribution_date)}\n\n"
        "Как вы хотите построить план?"
    )
    return await _safe_edit_or_reply(q, text, reply_markup=_goal_plan_kb(goal_id))


def _goal_more_kb(goal_id: int, status: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📋 История цели", callback_data=f"goal|hist|{goal_id}")]]
    if status == "paused":
        rows.append([InlineKeyboardButton("▶️ Возобновить", callback_data=f"goal|st|{goal_id}|active")])
    elif status == "active":
        rows.append([InlineKeyboardButton("⏸ Пауза", callback_data=f"goal|st|{goal_id}|paused")])
    if status == "archived":
        rows.append([InlineKeyboardButton("♻️ Восстановить", callback_data=f"goal|st|{goal_id}|active")])
        rows.append([InlineKeyboardButton("🗑 Удалить навсегда", callback_data=f"goal|del1|{goal_id}")])
    else:
        rows.append([InlineKeyboardButton("🗄 Архивировать", callback_data=f"goal|st|{goal_id}|archived")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal_id}"), InlineKeyboardButton("🏠 Главное меню", callback_data="start_main")])
    return InlineKeyboardMarkup(rows)


async def _render_goal_history(q, update, goal_id: int):
    user_id = update.effective_user.id
    workspace_id = _goal_workspace(update)
    goal = get_goal(goal_id, user_id, workspace_id)
    if not goal:
        return await _safe_edit_or_reply(q, "Цель не найдена.", reply_markup=_goal_nav_kb("goal|home"))
    rows = list_movements(goal.id, user_id, workspace_id, limit=10)
    labels = {"initial": "Старт", "contribution": "Пополнение", "withdrawal": "Снятие", "adjustment": "Корректировка"}
    lines = [f"📋 История цели\n\n{goal.display_name}", ""]
    if not rows:
        lines.append("Движений пока нет.")
    for item in rows:
        occurred = item.occurred_at.date() if hasattr(item.occurred_at, "date") else None
        lines.append(f"{format_date_ru(occurred)} — {labels.get(item.movement_type, item.movement_type)}: {format_money(item.amount, goal.currency)}")
    return await _safe_edit_or_reply(q, "\n".join(lines), reply_markup=_goal_nav_kb(f"goal|more|{goal.id}"))


def _goal_creation_preview(draft: dict) -> str:
    target = parse_money(draft.get("target_amount", "0"))
    saved = Decimal(str(draft.get("initial_amount") or "0"))
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


def _goal_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать цель", callback_data="goal|save")],
        [InlineKeyboardButton("RUB", callback_data="goal|cur|RUB"), InlineKeyboardButton("USD", callback_data="goal|cur|USD"), InlineKeyboardButton("EUR", callback_data="goal|cur|EUR")],
        [InlineKeyboardButton("Изменить название", callback_data="goal|new")],
        [InlineKeyboardButton("Отмена", callback_data="goal|cancel")],
    ])


def _goal_confirm_kb(token: str, goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"goal|confirm|{token}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal_id}"), InlineKeyboardButton("❌ Отмена", callback_data=f"goal|o|{goal_id}")],
    ])


def _goal_error_text(code: str) -> str:
    return {
        "invalid_amount": "Введите сумму больше нуля.",
        "past_deadline": "Срок цели не может быть в прошлом.",
        "insufficient_balance": "На цели недостаточно средств.",
        "empty_name": "Введите непустое название цели.",
        "control_characters": "Название содержит служебные символы.",
        "name_too_long": "Название слишком длинное.",
        "duplicate_name": "Активная цель с таким названием уже есть.",
        "goal_not_found": "Эта кнопка устарела. Откройте цель заново.",
        "wrong_actor": "Эту цель может менять только владелец.",
    }.get(code, "Не удалось сохранить изменения. Данные цели не изменены. Попробуйте позже.")


def _schedule_config_for_frequency(frequency: str) -> dict:
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


def _reminder_quiet_warning(draft: dict, user_id: int) -> tuple[bool, str | None]:
    try:
        event_date = draft.get("event_date")
        if not isinstance(event_date, date):
            return False, None
        days_before = int(draft.get("notify_days_before") or 0)
        due_date = event_date - timedelta(days=days_before)
        rows = pg_fetchall("SELECT COALESCE(reminder_hour, 20) FROM public.users WHERE user_id=%s LIMIT 1", (user_id,))
        hour = int(rows[0][0] if rows else 20)
        window = quiet_hours_window(user_id)
        if not window.enabled or window.start is None or window.end is None:
            return False, None
        local_due = datetime.combine(due_date, datetime.min.time().replace(hour=hour), tzinfo=ZoneInfo(window.timezone_name))
        if not is_quiet_local(local_due, window.start, window.end):
            return False, None
        return True, f"{due_date.strftime('%d.%m.%Y')} в {hour:02d}:00"
    except Exception:
        return False, None


def _save_reminder_draft(user_id: int, draft: dict) -> int:
    return reminder_insert(user_id, {
        'title': draft['title'],
        'rem_type': draft['rem_type'],
        'category': draft['category'],
        'amount': draft['amount'],
        'event_date': draft['event_date'],
        'repeat_rule': draft.get('repeat_rule', 'none'),
        'repeat_interval_days': draft.get('repeat_interval_days'),
        'notify_days_before': draft.get('notify_days_before', 1),
    })


def _privacy_locale(user_id: int, telegram_language_code: str | None = None) -> str:
    from services.i18n import resolve_locale
    try:
        rows = pg_fetchall("SELECT locale FROM public.users WHERE user_id=%s LIMIT 1", (user_id,))
        saved = rows[0][0] if rows else None
    except Exception:
        saved = None
    return resolve_locale(saved, telegram_language_code)


def _period_label(start_date: date | None, end_date: date | None, locale: str) -> str:
    if start_date is None and end_date is None:
        return t('privacy.period.all', locale)
    fmt = "%d.%m.%Y"
    if start_date == end_date:
        return start_date.strftime(fmt)
    return f"{start_date.strftime(fmt)} — {end_date.strftime(fmt)}"


def _history_period_kb(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.period.today', locale), callback_data='hist|period|today'),
         InlineKeyboardButton(t('privacy.period.last7', locale), callback_data='hist|period|last7')],
        [InlineKeyboardButton(t('privacy.period.this_month', locale), callback_data='hist|period|this_month'),
         InlineKeyboardButton(t('privacy.period.prev_month', locale), callback_data='hist|period|prev_month')],
        [InlineKeyboardButton(t('privacy.period.this_year', locale), callback_data='hist|period|this_year'),
         InlineKeyboardButton(t('privacy.period.all', locale), callback_data='hist|period|all')],
        [InlineKeyboardButton(t('privacy.period.custom', locale), callback_data='hist|custom|start')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')],
    ])


async def _render_privacy_menu(q, locale: str):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.export_data', locale), callback_data='exp_menu')],
        [InlineKeyboardButton(t('privacy.clear_history', locale), callback_data='hist|menu')],
        [InlineKeyboardButton(t('privacy.delete_account', locale), callback_data='privacy_delete_start')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='menu_settings')],
    ])
    return await _safe_edit_or_reply(q, f"{t('privacy.title', locale)}\n\n{t('privacy.body', locale)}", reply_markup=kb)


async def _render_delete_start(q, cid: int, locale: str | None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.export_data', locale), callback_data='exp_menu')],
        [InlineKeyboardButton(t('privacy.delete.continue', locale), callback_data='privacy_delete_stage2')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')],
    ])
    return await _safe_edit_or_reply(q, t('privacy.delete.explain', locale), reply_markup=kb)


async def _render_delete_confirm(q, locale: str):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.delete.yes', locale), callback_data='privacy_delete_confirm'),
         InlineKeyboardButton(t('privacy.delete.no', locale), callback_data='privacy_delete_cancel')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_delete_start')],
    ])
    return await _safe_edit_or_reply(q, t('privacy.delete.confirm', locale), reply_markup=kb)


async def _render_history_menu(q, locale: str):
    return await _safe_edit_or_reply(q, f"{t('privacy.history.title', locale)}\n\n{t('privacy.history.body', locale)}", reply_markup=_history_period_kb(locale))


async def _render_history_preview(q, context: ContextTypes.DEFAULT_TYPE, user_id: int, locale: str, start_date: date | None, end_date: date | None):
    preview = preview_delete_financial_history(user_id, start_date, end_date)
    period = _period_label(start_date, end_date, locale)
    if preview.operation_count == 0:
        return await _safe_edit_or_reply(q, t('privacy.history.zero', locale, period=period), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]))
    token = token_urlsafe(8)
    context.user_data['history_delete_confirm'] = {
        'token': token,
        'actor_user_id': user_id,
        'start': start_date.isoformat() if start_date else None,
        'end': end_date.isoformat() if end_date else None,
        'expires_at': unix_time() + 600,
        'used': False,
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.history.yes', locale), callback_data=f'hist|confirm|{token}'),
         InlineKeyboardButton(t('privacy.history.no', locale), callback_data='privacy_menu')],
        [InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')],
    ])
    return await _safe_edit_or_reply(q, t('privacy.history.preview', locale, period=period, count=preview.operation_count), reply_markup=kb)


def _history_done_kb(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('privacy.history.main', locale), callback_data='start_main')],
        [InlineKeyboardButton(t('privacy.history.another', locale), callback_data='hist|menu')],
    ])


def _date_from_state(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _category_workspace(update) -> int | None:
    return resolve_workspace(
        update.effective_chat.id,
        update.effective_user.id,
        getattr(update.effective_chat, 'type', 'private') or 'private',
    ).workspace_id


def _category_token(index: int) -> str:
    return f"k{index}"


CATEGORY_TYPES = {
    "expense": {
        "op_type": "Расходы",
        "button": "💸 Расходы",
        "title": "Категории расходов",
        "singular": "расход",
        "list_label": "категории расходов",
    },
    "income": {
        "op_type": "Доходы",
        "button": "💰 Доходы",
        "title": "Категории доходов",
        "singular": "доход",
        "list_label": "категории доходов",
    },
}


def _category_type_key(op_type: str | None) -> str:
    return "income" if op_type == "Доходы" else "expense"


def _category_type_def(type_key: str | None) -> dict:
    return CATEGORY_TYPES.get(type_key or "", CATEGORY_TYPES["expense"])


def _category_type_selector_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💸 Расходы', callback_data='cat|type|expense')],
        [InlineKeyboardButton('💰 Доходы', callback_data='cat|type|income')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')],
        [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')],
    ])


async def _render_category_type_selector(q):
    return await _safe_edit_or_reply(
        q,
        'Категории\n\nВыберите тип, категории которого хотите настроить.',
        reply_markup=_category_type_selector_kb(),
    )


def _category_store(context: ContextTypes.DEFAULT_TYPE, user_id: int, workspace_id: int | None, op_type: str = "Расходы") -> list[dict]:
    categories = list_managed_categories(user_id=user_id, workspace_id=workspace_id, op_type=op_type)
    stored = []
    for idx, item in enumerate(categories[:80]):
        token = _category_token(idx)
        stored.append({
            "token": token,
            "name": item.name,
            "normalized_name": item.normalized_name,
            "op_type": item.op_type,
            "category_id": item.category_id,
            "source": item.source,
            "operation_count": item.operation_count,
            "has_budget": item.has_budget,
        })
    context.user_data["category_manage_options"] = {
        "actor_user_id": user_id,
        "workspace_id": workspace_id,
        "op_type": op_type,
        "items": {item["token"]: item for item in stored},
        "expires_at": unix_time() + 900,
    }
    return stored


def _category_from_token(context: ContextTypes.DEFAULT_TYPE, user_id: int, workspace_id: int | None, token: str) -> dict | None:
    st = context.user_data.get("category_manage_options") or {}
    if (
        not isinstance(st, dict)
        or st.get("actor_user_id") != user_id
        or st.get("workspace_id") != workspace_id
        or st.get("expires_at", 0) < unix_time()
    ):
        context.user_data.pop("category_manage_options", None)
        return None
    return (st.get("items") or {}).get(token)


def _category_find_token_by_name(context: ContextTypes.DEFAULT_TYPE, user_id: int, workspace_id: int | None, op_type: str, name: str) -> str | None:
    key = normalized_category_key(name)
    items = _category_store(context, user_id, workspace_id, op_type)
    for item in items:
        if item.get("normalized_name") == key:
            return item.get("token")
    return None


def _category_list_kb(items: list[dict], *, type_key: str = "expense", action: str = "open") -> InlineKeyboardMarkup:
    rows = []
    for item in items[:30]:
        count = f" · {item['operation_count']}" if item.get("operation_count") else ""
        rows.append([InlineKeyboardButton(f"{item['name'][:36]}{count}", callback_data=f"cat|{action}|{type_key}|{item['token']}")])
    rows.extend([
        [InlineKeyboardButton('➕ Добавить категорию', callback_data=f'cat|add|{type_key}')],
        [InlineKeyboardButton('🔁 Перенести записи', callback_data=f'cat|move_start|{type_key}')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='cat_menu'), InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')],
    ])
    return InlineKeyboardMarkup(rows)


async def _render_category_menu(q, context: ContextTypes.DEFAULT_TYPE, update):
    return await _render_category_type_selector(q)


async def _render_category_list(q, context: ContextTypes.DEFAULT_TYPE, update, type_key: str):
    workspace_id = _category_workspace(update)
    cfg = _category_type_def(type_key)
    items = _category_store(context, update.effective_user.id, workspace_id, cfg["op_type"])
    if not items:
        text = f"{cfg['title']}\n\nПока нет пользовательских категорий. Можно добавить новую или записать операцию — категория появится здесь."
    else:
        lines = [cfg["title"], '', f"Активные {cfg['list_label']}:"]
        for item in items[:12]:
            marker = ' · бюджет' if item.get('has_budget') else ''
            lines.append(f"• {item['name']} — {item['operation_count']} записей{marker}")
        text = '\n'.join(lines)
    return await _safe_edit_or_reply(q, text, reply_markup=_category_list_kb(items, type_key=type_key))


async def _render_category_card(q, context: ContextTypes.DEFAULT_TYPE, update, token: str, type_key: str = "expense"):
    workspace_id = _category_workspace(update)
    item = _category_from_token(context, update.effective_user.id, workspace_id, token)
    if not item:
        return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
    counts = category_reference_counts(user_id=update.effective_user.id, workspace_id=workspace_id, op_type=item['op_type'], category=item['name'])
    cfg = _category_type_def(_category_type_key(item.get('op_type')))
    budget = 'есть' if item.get('has_budget') or counts.category_limits else 'нет'
    blocked_note = '\nСистемные категории нельзя переименовать или удалить.' if is_protected_category(item['name']) else ''
    text = (
        f"🏷 {item['name']}\n\n"
        f"Тип: {cfg['singular']}\n"
        f"Записей: {counts.operations}\n"
        f"Бюджет/лимит: {budget}\n"
        f"Напоминаний: {counts.reminders}\n"
        f"ML-связей: {counts.aliases + counts.ml_observations}\n"
        f"Групп бюджетов: {counts.category_budget_groups}{blocked_note}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✏️ Переименовать', callback_data=f'cat|rename|{type_key}|{token}')],
        [InlineKeyboardButton('🔁 Перенести записи', callback_data=f'cat|move_from|{type_key}|{token}')],
        [InlineKeyboardButton('🗑 Удалить категорию', callback_data=f'cat|delete|{type_key}|{token}')],
        [InlineKeyboardButton('⬅️ Назад', callback_data=f'cat|type|{type_key}'), InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')],
    ])
    return await _safe_edit_or_reply(q, text, reply_markup=kb)


def _category_destination_kb(items: list[dict], source_token: str, prefix: str, *, type_key: str = "expense") -> InlineKeyboardMarkup:
    rows = []
    for item in items[:30]:
        if item["token"] == source_token:
            continue
        rows.append([InlineKeyboardButton(item["name"][:40], callback_data=f"{prefix}|{source_token}|{item['token']}")])
    rows.append([InlineKeyboardButton('⬅️ Назад', callback_data=f'cat|open|{type_key}|{source_token}'), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')])
    return InlineKeyboardMarkup(rows)


def _cl_period_label(p: str) -> str:
    return "неделя" if p == "week" else "месяц"

def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")

async def _cl_show_menu(q):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Установить лимит', callback_data='cl_set')],
        [InlineKeyboardButton('📋 Мои лимиты', callback_data='cl_list')],
        [InlineKeyboardButton('◀️ Назад', callback_data='lb_hub')],
    ])
    await q.edit_message_text('📉 Лимиты по категориям:', reply_markup=kb)

async def _cl_pick_period(q):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('Неделя', callback_data='cl_pick|week')],
        [InlineKeyboardButton('Месяц', callback_data='cl_pick|month')],
        [InlineKeyboardButton('◀️ Назад', callback_data='cl_menu')],
    ])
    await q.edit_message_text('Выберите период лимита:', reply_markup=kb)

def _cl_amount_kb() -> InlineKeyboardMarkup:
    # удобная «цифровая» клавиатура для корректировки суммы
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('−5000', callback_data='cl_adj|-5000'),
         InlineKeyboardButton('−1000', callback_data='cl_adj|-1000'),
         InlineKeyboardButton('−100',  callback_data='cl_adj|-100')],
        [InlineKeyboardButton('+100',  callback_data='cl_adj|+100'),
         InlineKeyboardButton('+1000', callback_data='cl_adj|+1000'),
         InlineKeyboardButton('+5000', callback_data='cl_adj|+5000')],
        [InlineKeyboardButton('Сброс', callback_data='cl_reset'),
         InlineKeyboardButton('✅ Сохранить', callback_data='cl_save')],
        [InlineKeyboardButton('◀️ Отмена', callback_data='cl_cancel')],
    ])

async def _cl_render_amount_screen(q, period: str, category: str, amount: int, currency: str):
    text = (f"Установить лимит на {_cl_period_label(period)} для категории "
            f"*{_md_escape(category)}*.\n\nТекущая сумма: *{amount} {currency}*")
    try:
        await q.edit_message_text(text, parse_mode='Markdown', reply_markup=_cl_amount_kb())
    except Exception:
        await q.message.reply_text(text, parse_mode='Markdown', reply_markup=_cl_amount_kb())



def _lim_period_label(p: str) -> str:
    return 'Неделя' if p == 'week' else 'Месяц'


def _lim_key(period: str, category: str) -> str:
    raw = f"{period}|{category}".encode('utf-8')
    return hashlib.sha1(raw).hexdigest()[:12]


def _lim_parse_key(user_id: int, payload: str):
    token = (payload or '').strip()
    for r in list_user_limits(user_id):
        if _lim_key(r['period'], r['category']) == token:
            return r['period'], r['category']
    return None, None


def _lim_card_kb(period: str, category: str):
    key = _lim_key(period, category)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('−1000', callback_data=f'lim_adj|{key}|m1000'),
         InlineKeyboardButton('−500', callback_data=f'lim_adj|{key}|m500'),
         InlineKeyboardButton('+500', callback_data=f'lim_adj|{key}|p500'),
         InlineKeyboardButton('+1000', callback_data=f'lim_adj|{key}|p1000')],
        [InlineKeyboardButton('✏️ Изменить сумму', callback_data=f'lim_edit_amount|{key}')],
        [InlineKeyboardButton('🗓 Неделя', callback_data=f'lim_edit_period|{key}|week'),
         InlineKeyboardButton('🗓 Месяц', callback_data=f'lim_edit_period|{key}|month')],
        [InlineKeyboardButton('🗑 Удалить', callback_data=f'lim_del|{key}')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='lim_list')],
    ])


def _fmt_money(v) -> str:
    return format_money_value(v, "RUB")


async def _lim_show_list(q, user_id: int):
    rows = list_user_limits(user_id)
    log.info('list_limits user=%s count=%s', user_id, len(rows))
    if not rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Добавить лимит', callback_data='cl_set')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')],
        ])
        log.info('list_limits: rendering via edit_message_text len=%s buttons=%s', len('Лимитов пока нет.'), 2)
        return await _safe_edit_or_reply(q, 'Лимитов пока нет.', reply_markup=kb)

    lines = ['📌 *Мои лимиты*']
    btns = []
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {_lim_period_label(r['period'])} • {_md_escape(r['category'])} • {r['amount']} {r['currency']}")
        btns.append([
            InlineKeyboardButton(
                f"Открыть: {_lim_period_label(r['period'])} / {r['category']}",
                callback_data=f"lim_open|{_lim_key(r['period'], r['category'])}"
            )
        ])
    btns.append([InlineKeyboardButton('➕ Добавить лимит', callback_data='cl_set')])
    btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')])
    text = '\n'.join(lines)
    cb_lens = [len(row[0].callback_data or '') for row in btns if row and row[0].callback_data]
    if cb_lens:
        log.info('list_limits: callback_data_len min=%s max=%s', min(cb_lens), max(cb_lens))
    log.info('list_limits: rendering via edit_message_text len=%s buttons=%s', len(text), len(btns))
    try:
        return await _safe_edit_or_reply(q, text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(btns))
    except Exception:
        log.exception('list_limits render failed user=%s', user_id)
        raise


async def _lim_show_card(q, user_id: int, period: str, category: str, note: str = ''):
    row = get_limit_by_key(user_id, period, category)
    if not row:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ К списку', callback_data='lim_list')]])
        return await q.edit_message_text('Лимит не найден или уже изменён.', reply_markup=kb)

    log.info('open_limit user=%s period=%s category=%s amount=%s', user_id, period, category, row['amount'])
    spent = get_limit_spent(user_id, row['period'], row['category'])
    remaining = to_decimal_money(row['amount']) - to_decimal_money(spent)
    text = (
        f"*{_md_escape(row['category'])}*\n"
        f"{_lim_period_label(row['period'])}\n"
        f"Лимит: {_fmt_money(row['amount'])}\n"
        f"Потрачено: {_fmt_money(spent)}\n"
        f"Осталось: {_fmt_money(remaining)}"
    )
    if note:
        text += f"\n\n{note}"
    await q.edit_message_text(text, parse_mode='Markdown', reply_markup=_lim_card_kb(row['period'], row['category']))


async def _cl_show_list(q, user_id: int):
    rows = list_category_limits(user_id)  # [(period, amount, currency, category), ...]
    if not rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Установить', callback_data='cl_set')],
            [InlineKeyboardButton('◀️ Назад', callback_data='cl_menu')],
        ])
        return await q.edit_message_text('Пока нет лимитов. Создадим?', reply_markup=kb)
    lines = []
    buttons = []
    for i, (period, amt, cur, cat) in enumerate(rows, 1):
        lines.append(f"{i}. {_cl_period_label(period)} — *{_md_escape(cat)}*: {amt} {cur}")
        buttons.append([InlineKeyboardButton(f'✏️ Изменить ({_cl_period_label(period)})', callback_data=f'cl_edit|{period}|{cat}'),
                        InlineKeyboardButton('🗑 Удалить', callback_data=f'cl_del|{period}|{cat}')])
    buttons.append([InlineKeyboardButton('◀️ Назад', callback_data='cl_menu')])
    txt = "Мои лимиты:\n" + "\n".join(lines)
    try:
        await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        await q.message.reply_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))

# ──────────────────────────────────────────────────────────────────────────────
# Inline-редактор последней операции (как было)
# ──────────────────────────────────────────────────────────────────────────────
async def _op_edit_router(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = getattr(q, 'data', None)
    msg = getattr(q, 'message', None)
    cid = getattr(getattr(msg, 'chat', None), 'id', None)
    parts = (data or '').split('|', 1)
    requested_op_id = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None

    def _fetch_last_op(chat_id, operation_id=None):
        if operation_id:
            rows = pg_fetchall(
                """
                SELECT id, category, amount, type, op_date
                  FROM public.operations
                 WHERE chat_id=%s AND id=%s
                 LIMIT 1
                """,
                (chat_id, operation_id)
            )
        else:
            rows = pg_fetchall(
                """
                SELECT id, category, amount, type, op_date
                  FROM public.operations
                 WHERE chat_id=%s
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (chat_id,)
            )
        if rows:
            rid, cat, amt, typ, dt = rows[0]
            return {'id': rid, 'category': cat, 'amount': amt, 'type': typ, 'op_date': dt}
        return None

    last = _fetch_last_op(cid, requested_op_id) if cid is not None else None

    if data == 'op_edit' or (data or '').startswith('op_edit|'):
        if not last:
            try:
                await q.answer('Нет последней записи для изменения', show_alert=True)
            except Exception:
                pass
            return
        context.user_data['edit_ctx'] = last
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 Сумму', callback_data='op_e_amt')],
            [InlineKeyboardButton('🏷 Категорию', callback_data='op_edit_cat')],
            [InlineKeyboardButton('📅 Дату', callback_data='op_e_date')],
            [InlineKeyboardButton('🔁 Тип', callback_data='op_e_type')],
            [InlineKeyboardButton('📝 Комментарий', callback_data='op_e_com')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='op_edit_back')],
        ])
        try:
            await q.edit_message_text('Что изменить?', reply_markup=kb)
        except Exception:
            await q.message.reply_text('Что изменить?', reply_markup=kb)
        return

    if data == 'op_edit_cat':
        ctx = context.user_data.get('edit_ctx') or last
        if not ctx:
            try:
                await q.answer('Нет последней записи', show_alert=True)
            except Exception:
                pass
            return
        p = context.user_data.setdefault('pending', {})
        p['amt'] = ctx['amount']
        try:
            from datetime import datetime as _dt
            p['time'] = _dt.combine(ctx['op_date'], _dt.min.time())
        except Exception:
            p['time'] = datetime.now()
        p['note'] = None
        p['merch'] = ctx['category']
        p['edit_operation_id'] = ctx.get('id')
        context.user_data['edit_mode'] = True
        context.user_data['edit_operation_id'] = ctx.get('id')
        return await prompt_type_menu(update, context)

    if data == 'op_edit_back':
        ctx = context.user_data.get('edit_ctx') or last
        if ctx and ctx.get('type') == 'Расходы':
            second = InlineKeyboardButton('💰 Остаток', callback_data='status')
        elif ctx and ctx.get('type') == 'Доходы':
            second = InlineKeyboardButton('💵 Доходы', callback_data='income_status')
        elif ctx and ctx.get('type') == 'Инвестиции':
            second = InlineKeyboardButton('📊 Инвестиции (месяц)', callback_data='inv_status')
        elif ctx and ctx.get('type') in ('Сбережения', 'Цель'):
            cat = ctx.get('category', '')
            second = InlineKeyboardButton('🎯 Прогресс цели', callback_data=f'goal_status|{cat}')
        else:
            second = InlineKeyboardButton('💰 Остаток', callback_data='status')
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'),
            second,
            InlineKeyboardButton('✏️ Изменить', callback_data=f"op_edit|{ctx.get('id')}" if ctx and ctx.get('id') else 'op_edit'),
        ]])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text('Готово.', reply_markup=kb)
        context.user_data.pop('edit_mode', None)
        return

    if data == 'op_e_amt':
        context.user_data['await_op_edit_amount'] = (context.user_data.get('edit_ctx') or last or {}).get('id') or True
        await q.answer()
        return await q.message.reply_text('Введите новую сумму:')
    if data == 'op_e_date':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Сегодня', callback_data='op_e_date_t'), InlineKeyboardButton('Вчера', callback_data='op_e_date_y')],
            [InlineKeyboardButton('✏️ Ввести дату', callback_data='op_e_date_i')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='op_edit')],
        ])
        await q.answer()
        return await q.message.reply_text('Выбери дату:', reply_markup=kb)
    if data == 'op_e_date_t':
        op_id = (context.user_data.get('edit_ctx') or last or {}).get('id')
        (update_operation_fields_by_id(cid, op_id, op_date=date.today()) if op_id else update_last_operation_fields(cid, op_date=date.today()))
        await q.answer('Дата обновлена')
        return
    if data == 'op_e_date_y':
        op_id = (context.user_data.get('edit_ctx') or last or {}).get('id')
        (update_operation_fields_by_id(cid, op_id, op_date=date.today() - timedelta(days=1)) if op_id else update_last_operation_fields(cid, op_date=date.today() - timedelta(days=1)))
        await q.answer('Дата обновлена')
        return
    if data == 'op_e_date_i':
        context.user_data['await_op_edit_date'] = (context.user_data.get('edit_ctx') or last or {}).get('id') or True
        await q.answer()
        return await q.message.reply_text('Введи дату (24.05.2026 или 24.05 или сегодня/вчера):')
    if data == 'op_e_type':
        ctx = context.user_data.get('edit_ctx') or last
        new_type = 'Доходы' if ctx and ctx.get('type') != 'Доходы' else 'Расходы'
        op_id = (ctx or {}).get('id')
        (update_operation_fields_by_id(cid, op_id, op_type=new_type) if op_id else update_last_operation_fields(cid, op_type=new_type))
        await q.answer('Тип обновлён')
        return
    if data == 'op_e_com':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('🧹 Очистить', callback_data='op_e_com_c')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='op_edit')],
        ])
        context.user_data['await_op_edit_comment'] = (context.user_data.get('edit_ctx') or last or {}).get('id') or True
        await q.answer()
        return await q.message.reply_text('Введи новый комментарий:', reply_markup=kb)
    if data == 'op_e_com_c':
        op_id = (context.user_data.get('edit_ctx') or last or {}).get('id')
        (update_operation_fields_by_id(cid, op_id, comment='') if op_id else update_last_operation_fields(cid, comment=''))
        await q.answer('Комментарий очищен')
        return

# ──────────────────────────────────────────────────────────────────────────────
# Главный callback-роутер
# ──────────────────────────────────────────────────────────────────────────────
async def callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ''
    cid = update.effective_chat.id if update.effective_chat else update.effective_user.id

    if data == 'group_setup':
        chat = update.effective_chat
        user = update.effective_user
        if not chat or getattr(chat, 'type', 'private') not in {'group', 'supergroup'}:
            return await q.answer('Эта настройка доступна только в группе.', show_alert=True)
        if not await _is_group_admin(context, chat.id, user.id):
            track_security_event(SecurityEvent(
                event_name="admin_command_denied",
                user_id=user.id,
                chat_type=getattr(chat, 'type', None),
                rule_key="group_setup",
                action_taken="denied",
            ))
            return await q.answer('Создать пространство может только администратор группы.', show_alert=True)
        workspace_id = create_group_workspace(chat.id, user.id, getattr(chat, 'title', None))
        track_product_event(ProductEvent(
            event_name="workspace_created",
            user_id=user.id,
            workspace_id=workspace_id,
            workspace_kind="group",
            status="success",
        ))
        await q.answer('Пространство создано')
        return await _safe_edit_or_reply(
            q,
            f'🧩 Пространство группы создано.\n\nID: {workspace_id}\nУчастники группы могут присоединиться кнопкой в сообщении бота и записывать операции.',
        )

    if data == 'group_join':
        chat = update.effective_chat
        user = update.effective_user
        if not chat or getattr(chat, 'type', 'private') not in {'group', 'supergroup'}:
            return await q.answer('Присоединиться можно только из группы.', show_alert=True)
        if not await _is_group_active_member(context, chat.id, user.id):
            track_security_event(SecurityEvent(
                event_name="group_join_rejected",
                user_id=user.id,
                chat_type=getattr(chat, 'type', None),
                rule_key="telegram_membership",
                action_taken="denied",
            ))
            track_product_event(ProductEvent(event_name="workspace_join_rejected", user_id=user.id, workspace_kind="group", status="rejected"))
            return await q.answer('Не вижу вас активным участником этой группы.', show_alert=True)
        workspace = join_group_workspace(chat.id, user.id)
        track_product_event(ProductEvent(
            event_name="workspace_joined",
            user_id=user.id,
            workspace_id=workspace.workspace_id,
            workspace_kind=workspace.kind,
            status="success",
        ))
        await q.answer('Готово')
        return await _safe_edit_or_reply(
            q,
            f'✅ Вы присоединились к пространству группы.\n\nПространство: {workspace.name}\nТеперь можно записывать операции в этом чате.',
        )

    if data.startswith(('gpick|', 'gadd|', 'gcancel|')):
        return await _handle_group_draft_callback(update, context, data)

    if data == "chal|home" or data.startswith("chal|sec|") or data in {"chal|ach", "chal|how"}:
        await q.answer()
        return await _legacy_challenge_response(q)

    if data.startswith("chal|cta|"):
        await q.answer()
        key = data.split("|", 2)[2]
        track_product_event(ProductEvent(
            event_name="challenge_cta_opened",
            user_id=update.effective_user.id,
            status="success",
            properties={"challenge_key": key, "destination": "miniapp_only"},
        ))
        return await _legacy_challenge_response(q, cta=True)

    if data == "goal|home":
        await q.answer()
        return await _render_goals_home(q, context, update)

    if data == "goal|new":
        workspace = resolve_workspace(cid, update.effective_user.id, getattr(update.effective_chat, 'type', 'private') or 'private')
        context.user_data["goal_draft"] = {
            "actor_user_id": update.effective_user.id,
            "workspace_id": workspace.workspace_id,
            "step": "name",
            "currency": get_user_currency(update.effective_user.id),
            "expires_at": unix_time() + 1800,
        }
        track_product_event(ProductEvent(event_name="goal_creation_started", user_id=update.effective_user.id, workspace_id=workspace.workspace_id, status="started"))
        await q.answer()
        return await _safe_edit_or_reply(q, "🎯 Новая цель\n\nВведите название цели.", reply_markup=_goal_nav_kb("goal|home"))

    if data in {"goal|how"}:
        await q.answer()
        return await _safe_edit_or_reply(
            q,
            "📘 Как работают цели\n\n"
            "Цель — это финансовый план с отдельной внутренней историей движений. "
            "Пополнение цели не создаёт расход или доход и не меняет обычные итоги.\n\n"
            "Можно выбрать срок, комфортную сумму или оставить цель без расписания. "
            "Автоматические напоминания идут через общий диспетчер и соблюдают тихие часы.",
            reply_markup=_goal_nav_kb("goal|home"),
        )

    if data.startswith("goal|list|"):
        await q.answer()
        return await _render_goal_list(q, context, update, data.split("|", 2)[2])

    if data.startswith("goal|o|"):
        parts = data.split("|")
        await q.answer()
        return await _render_goal_card(q, context, update, int(parts[2]), parts[3] if len(parts) > 3 else None)

    if data.startswith("goal|sal|"):
        parts = data.split("|")
        if len(parts) < 5:
            return await q.answer("Кнопка устарела", show_alert=True)
        action, goal_id, operation_id = parts[2], int(parts[3]), int(parts[4])
        workspace_id = _goal_workspace(update)
        goal = get_goal(goal_id, update.effective_user.id, workspace_id)
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        if action == "a":
            amount = goal.planned_contribution_amount or goal.comfortable_amount
            if not amount or amount <= 0:
                return await q.answer("Сумма по плану пока недоступна", show_alert=True)
            goal, _movement, created = add_goal_movement(
                goal_id=goal.id,
                owner_user_id=update.effective_user.id,
                workspace_id=workspace_id,
                actor_user_id=update.effective_user.id,
                movement_type="contribution",
                amount=amount,
                source="income_suggestion",
                linked_operation_id=operation_id,
                idempotency_key=f"goal:{goal.id}:income:{operation_id}:accepted",
            )
            track_product_event(ProductEvent(event_name="goal_income_suggestion_accepted", user_id=update.effective_user.id, workspace_id=workspace_id, status="accepted", currency=goal.currency, properties={"source": "income_operation"}))
            await q.answer("Пополнение сохранено" if created else "Уже сохранено")
            return await _render_goal_card(q, context, update, goal.id)
        if action == "m":
            context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": workspace_id, "goal_id": goal.id, "mode": "contribution", "expires_at": unix_time() + 900}
            await q.answer()
            return await q.message.reply_text("Введите сумму пополнения цели.")
        if action == "s":
            text = f"🎯 Напоминание о цели\n\n{goal.display_name}\nПополнить цель можно из карточки."
            queue_automatic_notification(
                user_id=update.effective_user.id,
                workspace_id=workspace_id,
                notification_type="goal_salary_snooze",
                dedupe_key=f"goal:{goal.id}:salary_snooze:{operation_id}:tomorrow",
                policy=DeliveryPolicy.DEFER,
                template_key="goal_salary_snooze",
                payload={"text": text, "goal_id": goal.id, "buttons": [[{"label": "🎯 Открыть цель", "callback_data": f"goal|o|{goal.id}"}]]},
                original_scheduled_at=datetime.now() + timedelta(days=1),
            )
            track_product_event(ProductEvent(event_name="goal_income_suggestion_snoozed", user_id=update.effective_user.id, workspace_id=workspace_id, status="snoozed", properties={"source": "income_operation"}))
            return await q.answer("Напомню позже")
        if action == "x":
            track_product_event(ProductEvent(event_name="goal_income_suggestion_dismissed", user_id=update.effective_user.id, workspace_id=workspace_id, status="dismissed", properties={"source": "income_operation"}))
            return await q.answer("Пропущено")

    if data.startswith("goal|cur|"):
        draft = context.user_data.get("goal_draft") or {}
        if draft.get("actor_user_id") != update.effective_user.id or draft.get("expires_at", 0) < unix_time():
            context.user_data.pop("goal_draft", None)
            return await q.answer("Черновик устарел. Начните заново.", show_alert=True)
        draft["currency"] = data.split("|", 2)[2]
        context.user_data["goal_draft"] = draft
        await q.answer("Валюта обновлена")
        return await _safe_edit_or_reply(q, _goal_creation_preview(draft), reply_markup=_goal_preview_kb())

    if data == "goal|deadline|none":
        draft = context.user_data.get("goal_draft") or {}
        draft["deadline"] = None
        draft["step"] = "initial"
        context.user_data["goal_draft"] = draft
        await q.answer()
        return await _safe_edit_or_reply(q, "Сколько уже накоплено?\n\nМожно нажать «Пока 0».", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Пока 0", callback_data="goal|saved|zero")],
            [InlineKeyboardButton("✏️ Ввести сумму", callback_data="goal|saved|input")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="goal|new"), InlineKeyboardButton("❌ Отмена", callback_data="goal|cancel")],
        ]))

    if data == "goal|deadline|input":
        draft = context.user_data.get("goal_draft") or {}
        draft["step"] = "deadline"
        context.user_data["goal_draft"] = draft
        await q.answer()
        return await q.message.reply_text("Введите срок цели.\n\nНапример: 01.12.2026")

    if data == "goal|saved|zero":
        draft = context.user_data.get("goal_draft") or {}
        draft["initial_amount"] = "0"
        draft["step"] = "preview"
        context.user_data["goal_draft"] = draft
        await q.answer()
        return await _safe_edit_or_reply(q, _goal_creation_preview(draft), reply_markup=_goal_preview_kb())

    if data == "goal|saved|input":
        draft = context.user_data.get("goal_draft") or {}
        draft["step"] = "initial"
        context.user_data["goal_draft"] = draft
        await q.answer()
        return await q.message.reply_text("Введите уже накопленную сумму.")

    if data == "goal|cancel":
        context.user_data.pop("goal_draft", None)
        context.user_data.pop("goal_action", None)
        await q.answer("Отменено")
        return await _render_goals_home(q, context, update)

    if data == "goal|save":
        draft = context.user_data.get("goal_draft") or {}
        if draft.get("actor_user_id") != update.effective_user.id or draft.get("expires_at", 0) < unix_time():
            context.user_data.pop("goal_draft", None)
            return await q.answer("Черновик устарел. Начните заново.", show_alert=True)
        try:
            goal = create_goal(
                owner_user_id=update.effective_user.id,
                workspace_id=draft.get("workspace_id"),
                display_name=draft.get("display_name"),
                target_amount=draft.get("target_amount"),
                currency=draft.get("currency"),
                deadline=date.fromisoformat(draft["deadline"]) if draft.get("deadline") else None,
                initial_amount=draft.get("initial_amount") or "0",
            )
        except GoalError as exc:
            return await q.answer(_goal_error_text(str(exc)), show_alert=True)
        except Exception:
            log.warning("goal_create_failed user_id=%s", update.effective_user.id)
            return await _safe_edit_or_reply(q, "Не удалось сохранить цель. Данные цели не изменены. Попробуйте позже.", reply_markup=_goal_nav_kb("goal|home"))
        context.user_data.pop("goal_draft", None)
        await q.answer("Цель создана")
        return await _safe_edit_or_reply(q, "Как вы хотите построить план?", reply_markup=_goal_plan_kb(goal.id))

    if data.startswith("goal|pl|"):
        await q.answer()
        return await _render_goal_plan_menu(q, update, int(data.split("|")[2]))

    if data.startswith("goal|ps|"):
        _, _, goal_id_s, strategy = data.split("|", 3)
        goal_id = int(goal_id_s)
        if strategy == "none":
            goal = update_goal_plan(goal_id=goal_id, owner_user_id=update.effective_user.id, workspace_id=_goal_workspace(update), strategy="none", frequency="none")
            await q.answer("План обновлён")
            return await _safe_edit_or_reply(q, "Хотите получать напоминания о плановых пополнениях?", reply_markup=_goal_reminder_prompt_kb(goal.id))
        await q.answer()
        return await _safe_edit_or_reply(q, "Выберите периодичность пополнений.", reply_markup=_goal_frequency_kb(goal_id, strategy))

    if data.startswith("goal|fr|"):
        _, _, goal_id_s, strategy, frequency = data.split("|", 4)
        goal_id = int(goal_id_s)
        if strategy == "contribution":
            context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": goal_id, "mode": "plan_contribution", "frequency": frequency, "expires_at": unix_time() + 900}
            await q.answer()
            return await q.message.reply_text("Введите комфортную сумму одного пополнения.")
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        if not goal.deadline:
            context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": goal_id, "mode": "plan_deadline", "frequency": frequency, "expires_at": unix_time() + 900}
            await q.answer()
            return await q.message.reply_text("Введите срок цели для плана.")
        schedule = _schedule_config_for_frequency(frequency)
        goal = update_goal_plan(goal_id=goal_id, owner_user_id=update.effective_user.id, workspace_id=_goal_workspace(update), strategy="deadline", frequency=frequency, deadline=goal.deadline, schedule_config=schedule)
        await q.answer("План обновлён")
        return await _safe_edit_or_reply(q, "Хотите получать напоминания о плановых пополнениях?", reply_markup=_goal_reminder_prompt_kb(goal.id))

    if data.startswith(("goal|add|", "goal|wd|", "goal|adj|")):
        parts = data.split("|")
        mode = {"add": "contribution", "wd": "withdrawal", "adj": "adjustment"}[parts[1]]
        goal_id = int(parts[2])
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        if mode == "contribution":
            text = f"➕ Пополнение\n\nСейчас: {format_money(goal.current_balance, goal.currency)}"
            if goal.planned_contribution_amount:
                text += f"\nРекомендация: {format_money(goal.planned_contribution_amount, goal.currency)}"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Пополнить рекомендованную сумму", callback_data=f"goal|rec|{goal.id}")],
                [InlineKeyboardButton("Ввести другую сумму", callback_data=f"goal|amt|{goal.id}|contribution")],
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal.id}")],
            ])
            await q.answer()
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": goal.id, "mode": mode, "expires_at": unix_time() + 900}
        await q.answer()
        prompt = "Введите сумму снятия." if mode == "withdrawal" else "Введите новую текущую сумму цели."
        return await q.message.reply_text(prompt)

    if data.startswith("goal|rec|"):
        goal_id = int(data.split("|")[2])
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal or not goal.planned_contribution_amount:
            return await q.answer("Рекомендованная сумма пока недоступна", show_alert=True)
        token = token_urlsafe(8)
        context.user_data["goal_action"] = {"token": token, "actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": goal.id, "mode": "contribution", "amount": str(goal.planned_contribution_amount), "expires_at": unix_time() + 900, "used": False}
        return await _safe_edit_or_reply(q, f"Пополнить цель на {format_money(goal.planned_contribution_amount, goal.currency)}?", reply_markup=_goal_confirm_kb(token, goal.id))

    if data.startswith("goal|amt|"):
        _, _, goal_id_s, mode = data.split("|", 3)
        context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": int(goal_id_s), "mode": mode, "expires_at": unix_time() + 900}
        await q.answer()
        return await q.message.reply_text("Введите сумму.")

    if data.startswith("goal|confirm|"):
        token = data.split("|", 2)[2]
        st = context.user_data.get("goal_action") or {}
        if st.get("token") != token or st.get("actor_user_id") != update.effective_user.id or st.get("workspace_id") != _goal_workspace(update) or st.get("used") or st.get("expires_at", 0) < unix_time():
            context.user_data.pop("goal_action", None)
            return await q.answer("Действие устарело. Откройте цель заново.", show_alert=True)
        st["used"] = True
        context.user_data["goal_action"] = st
        mode = st.get("mode")
        try:
            if mode == "adjustment":
                goal, _movement, _created = add_goal_movement(goal_id=st["goal_id"], owner_user_id=update.effective_user.id, workspace_id=st.get("workspace_id"), actor_user_id=update.effective_user.id, movement_type="adjustment", new_balance=st.get("new_balance"), idempotency_key=f"goal:{st['goal_id']}:adjust:{token}")
            else:
                goal, _movement, _created = add_goal_movement(goal_id=st["goal_id"], owner_user_id=update.effective_user.id, workspace_id=st.get("workspace_id"), actor_user_id=update.effective_user.id, movement_type=mode, amount=st.get("amount"), idempotency_key=f"goal:{st['goal_id']}:{mode}:{token}")
        except GoalError as exc:
            context.user_data.pop("goal_action", None)
            return await q.answer(_goal_error_text(str(exc)), show_alert=True)
        context.user_data.pop("goal_action", None)
        await q.answer("Сохранено")
        return await _render_goal_card(q, context, update, goal.id)

    if data.startswith("goal|rem|"):
        goal_id = int(data.split("|")[2])
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Включить напоминания" if not goal.reminders_enabled else "Выключить напоминания", callback_data=f"goal|remtog|{goal.id}")],
            [InlineKeyboardButton("⚙️ Общие оповещения", callback_data="menu_notifications")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal.id}")],
        ])
        await q.answer()
        return await _safe_edit_or_reply(q, f"🔔 Напоминания цели\n\nДля доставки нужны и общая настройка «Цели», и включение на этой цели.\n\nНа цели: {'включены' if goal.reminders_enabled else 'выключены'}", reply_markup=kb)

    if data.startswith("goal|remtog|"):
        goal_id = int(data.split("|")[2])
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        enabling = not goal.reminders_enabled
        if enabling:
            prefs = get_notification_preferences(cid)
            if not prefs.get("goal_notifications_enabled", False):
                toggle_notification_preference(cid, "goals")
        goal = set_goal_reminders(goal.id, update.effective_user.id, _goal_workspace(update), enabling)
        await q.answer("Обновлено")
        return await _render_goal_card(q, context, update, goal.id)

    if data.startswith("goal|edit|"):
        goal_id = int(data.split("|")[2])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Название", callback_data=f"goal|ef|{goal_id}|name"), InlineKeyboardButton("Сумма", callback_data=f"goal|ef|{goal_id}|target")],
            [InlineKeyboardButton("Срок", callback_data=f"goal|ef|{goal_id}|deadline"), InlineKeyboardButton("Текущий прогресс", callback_data=f"goal|adj|{goal_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|o|{goal_id}")],
        ])
        await q.answer()
        return await _safe_edit_or_reply(q, "✏️ Что изменить?", reply_markup=kb)

    if data.startswith("goal|ef|"):
        _, _, goal_id_s, field = data.split("|", 3)
        context.user_data["goal_action"] = {"actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": int(goal_id_s), "mode": f"edit_{field}", "expires_at": unix_time() + 900}
        await q.answer()
        prompt = {"name": "Введите новое название.", "target": "Введите новую целевую сумму.", "deadline": "Введите новый срок или «без срока»."}.get(field, "Введите новое значение.")
        return await q.message.reply_text(prompt)

    if data.startswith("goal|more|"):
        goal_id = int(data.split("|")[2])
        goal = get_goal(goal_id, update.effective_user.id, _goal_workspace(update))
        if not goal:
            return await q.answer("Цель не найдена", show_alert=True)
        await q.answer()
        return await _safe_edit_or_reply(q, f"Ещё\n\n{goal.display_name}\n{goal_status_label(goal)}", reply_markup=_goal_more_kb(goal.id, goal.status))

    if data.startswith("goal|hist|"):
        await q.answer()
        return await _render_goal_history(q, update, int(data.split("|")[2]))

    if data.startswith("goal|st|"):
        _, _, goal_id_s, status = data.split("|", 3)
        event_status = "active" if status == "active" else status
        goal = set_goal_status(int(goal_id_s), update.effective_user.id, _goal_workspace(update), event_status)
        await q.answer("Обновлено")
        return await _render_goal_card(q, context, update, goal.id)

    if data.startswith("goal|del1|"):
        goal_id = int(data.split("|")[2])
        token = token_urlsafe(8)
        context.user_data["goal_delete_confirm"] = {"token": token, "actor_user_id": update.effective_user.id, "workspace_id": _goal_workspace(update), "goal_id": goal_id, "expires_at": unix_time() + 600}
        await q.answer()
        return await _safe_edit_or_reply(q, "Удалить цель навсегда?\n\nЭто удалит только цель, её движения и будущие goal-уведомления. Обычные финансовые операции останутся.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, продолжить", callback_data=f"goal|del2|{token}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"goal|more|{goal_id}")],
        ]))

    if data.startswith("goal|del2|"):
        token = data.split("|", 2)[2]
        st = context.user_data.get("goal_delete_confirm") or {}
        if st.get("token") != token or st.get("actor_user_id") != update.effective_user.id or st.get("workspace_id") != _goal_workspace(update) or st.get("expires_at", 0) < unix_time():
            context.user_data.pop("goal_delete_confirm", None)
            return await q.answer("Подтверждение устарело.", show_alert=True)
        count = delete_goal_permanently(st["goal_id"], update.effective_user.id, st.get("workspace_id"))
        context.user_data.pop("goal_delete_confirm", None)
        await q.answer("Удалено")
        return await _safe_edit_or_reply(q, f"✅ Цель удалена\n\nДвижений удалено: {count}", reply_markup=_goal_nav_kb("goal|home"))

    # === NOOP ("Без операций сегодня") ===
    if data == 'noop_today':
        cid = update.effective_chat.id
        conn = get_conn(); cur = conn.cursor()
        local_today = user_local_date(cid)
        try:
            cur.execute(
                "INSERT INTO public.operations (chat_id, user_id, op_date, type, category, amount, comment, raw_text) "
                "VALUES (%s,%s,%s,'noop','Без операций',0,'no-op day','noop_button') "
                "ON CONFLICT DO NOTHING",
                (cid, cid, local_today)
            )
            conn.commit()
        finally:
            cur.close(); conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Удалить', callback_data='noop_delete'),
                                    InlineKeyboardButton('Назад',   callback_data='noop_back')]])
        await q.edit_message_text('Отметил: *без операций сегодня*.', parse_mode='Markdown', reply_markup=kb)
        return

    if data == 'noop_delete':
        cid = update.effective_chat.id
        conn = get_conn(); cur = conn.cursor()
        local_today = user_local_date(cid)
        cur.execute("DELETE FROM public.operations WHERE chat_id=%s AND op_date=%s AND type='noop'", (cid, local_today))
        conn.commit(); cur.close(); conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Отметку удалил. Если передумаешь — нажми ниже.', reply_markup=kb)
        return

    if data == 'noop_back':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Ок! Можешь отметить отсутствие операций позже.', reply_markup=kb)
        return

    msg = getattr(q, 'message', None)
    chat = getattr(msg, 'chat', None) or update.effective_chat
    cid = getattr(chat, 'id', None)
    if cid is None:
        log.warning('callback_missing_chat callback_data=%s chat_type=%s', data, getattr(update.effective_chat, 'type', None))
        try:
            return await q.answer('Кнопка устарела. Откройте меню командой /start.', show_alert=True)
        except Exception:
            return
    await q.answer()

    # inline-edit подменю
    if data and data.startswith('op_edit'):
        return await _op_edit_router(update, context)

    # Главное меню
    if data in ('start_main', 'back_main'):
        return await render_main_menu(q, cid, context)

    # ── Онбординг (как было) ──
    if data == 'onb_curr':
        context.user_data['onb'] = True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('RUB 🇷🇺', callback_data='set_curr|RUB')],
            [InlineKeyboardButton('USD 🇺🇸', callback_data='set_curr|USD'),
             InlineKeyboardButton('EUR 🇪🇺', callback_data='set_curr|EUR')],
            [InlineKeyboardButton('Другие…', callback_data='menu_currency_more')],
            [InlineKeyboardButton('Пропустить', callback_data='onb_rem')],
        ])
        return await q.edit_message_text("Выберите валюту учёта:", reply_markup=kb)

    if data == 'onb_rem':
        context.user_data['onb'] = True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('19:00', callback_data='set_rem_hour|19'),
             InlineKeyboardButton('20:00', callback_data='set_rem_hour|20'),
             InlineKeyboardButton('21:00', callback_data='set_rem_hour|21')],
            [InlineKeyboardButton('Другое…', callback_data='set_rem_custom')],
            [InlineKeyboardButton('Пропустить', callback_data='onb_budget')],
        ])
        return await q.edit_message_text("Когда напоминать каждый день?", reply_markup=kb)

    if data == 'onb_budget':
        context.user_data['onb'] = True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Установить недельный бюджет', callback_data='set_week')],
            [InlineKeyboardButton('Установить месячный бюджет', callback_data='set_month')],
            [InlineKeyboardButton('Пропустить', callback_data='onb_finish')],
        ])
        return await q.edit_message_text("Настроим бюджеты или пропустим?", reply_markup=kb)

    if data == 'onb_finish':
        context.user_data.pop('onb', None)
        track_product_event(ProductEvent(event_name="onboarding_completed", user_id=cid, status="success"))
        txt = (
            "Готово! Можете сразу писать мне операции, например:\n"
            "• молоко 150\n• пицца 450 вчера\n• зарплата 50000\n\n"
            "Если что — /settings."
        )
        return await _safe_edit_or_reply(q, txt, reply_markup=canonical_main_menu_kb(get_user_locale(cid)))

    # Примеры / Поддержка
    if data == 'menu_examples':
        txt = (
            "📌 Примеры:\n"
            "• молоко 150\n"
            "• пицца 450 вчера\n"
            "• зарплата 70 000 01.08\n"
            "• такси 3500 10.02.2025\n\n"
            "Можно писать в любом регистре и с лишними пробелами — пойму 🙂"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='start_main')]])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == 'menu_support':
        link = "https://t.me/chiracredible"
        txt = f"Если что-то сломалось или есть идеи — пиши в саппорт: {link}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='start_main')]])
        return await q.edit_message_text(txt, reply_markup=kb, disable_web_page_preview=True)

    if data == 'menu_help':
        txt = (
            "❓ Помощь\n\n"
            "Пишите операции обычным текстом: «кофе 250», «зарплата 70000», "
            "«такси 900 вчера». Можно отправить голосовое или фото чека, если эти функции включены.\n\n"
            "Через кнопки доступны бюджеты, лимиты, напоминания, экспорт и настройки. "
            "Команды меню: /start, /settings, /help."
        )
        return await q.edit_message_text(txt, reply_markup=help_menu_kb(get_user_locale(cid)), disable_web_page_preview=True)

    # Настройки
    if data == 'menu_settings':
        context.user_data['notification_back'] = 'menu_settings'
        return await render_settings_menu(q, cid)

    if data in {'lb_hub', 'settings_budgets'}:
        context.user_data['notification_back'] = 'lb_hub'
        return await render_limits_budgets_menu(q, cid)

    if data == 'gl_menu':
        rows = list_general_limits(cid)
        if not rows:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('➕ Создать общий лимит', callback_data='gl_create')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')],
            ])
            return await _safe_edit_or_reply(q, '📊 Общий лимит\n\nОбщих лимитов пока нет.', reply_markup=kb)
        lines = ['📊 Общий лимит', '']
        for r in rows[:10]:
            lines.append(f"• {r['name']} — {_fmt_money(r['amount'])} ({r['period_type']})")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Создать общий лимит', callback_data='gl_create')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')],
        ])
        return await _safe_edit_or_reply(q, '\n'.join(lines), reply_markup=kb)

    if data == 'gl_create':
        context.user_data['await_general_limit_amount'] = {'period_type': 'month'}
        return await _safe_edit_or_reply(q, 'Введите общий месячный лимит расхода.\nНапример: 60000', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='gl_menu')]]))

    if data == 'cbg_menu':
        rows = list_category_budget_groups(cid)
        if not rows:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('➕ Создать бюджет из категорий', callback_data='cbg_create')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')],
            ])
            return await _safe_edit_or_reply(q, '🧩 Бюджет из категорий\n\nТаких бюджетов пока нет.', reply_markup=kb)
        lines = ['🧩 Бюджет из категорий', '']
        for r in rows[:10]:
            lines.append(f"• {r['name']} — {_fmt_money(r['amount'])}: {', '.join(r['categories'])}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Создать бюджет из категорий', callback_data='cbg_create')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')],
        ])
        return await _safe_edit_or_reply(q, '\n'.join(lines), reply_markup=kb)

    if data == 'cbg_create':
        workspace_id = _cbg_workspace_id(cid, update.effective_user.id, getattr(update.effective_chat, 'type', 'private') or 'private')
        context.user_data['cbg_draft'] = {'step': 'name', 'period_type': 'month', 'alerts_enabled': True, 'selected_tokens': [], 'selected_categories': {}, 'workspace_id': workspace_id}
        return await _safe_edit_or_reply(q, 'Введите название бюджета из категорий.\nНапример: Повседневные траты', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='cbg_menu')]]))

    if data.startswith('cbgp|'):
        parts = data.split('|')
        action = parts[1] if len(parts) > 1 else ''
        draft = context.user_data.get('cbg_draft') or {}
        if not draft:
            return await _safe_edit_or_reply(q, 'Черновик бюджета устарел. Создайте бюджет заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧩 К бюджетам', callback_data='cbg_menu')]]))
        options = _cbg_options(cid, draft.get('workspace_id'))
        option_names = {item['token']: item['name'] for item in options}
        selected = list(dict.fromkeys(draft.get('selected_tokens') or []))
        selected_map = dict(draft.get('selected_categories') or {})

        if action == 'noop':
            return await q.answer('Это номер страницы')
        if action == 'p':
            return await _cbg_render_picker(q, context, cid, page=int(parts[2] if len(parts) > 2 else 0))
        if action == 't':
            token = parts[2] if len(parts) > 2 else ''
            if token not in option_names:
                return await q.answer('Категория больше недоступна', show_alert=True)
            if token in selected:
                selected = [t for t in selected if t != token]
                selected_map.pop(token, None)
            else:
                selected.append(token)
                selected_map[token] = option_names[token]
            draft['selected_tokens'] = selected
            draft['selected_categories'] = selected_map
            context.user_data['cbg_draft'] = draft
            return await _cbg_render_picker(q, context, cid)
        if action == 'all':
            draft['selected_tokens'] = [item['token'] for item in options]
            draft['selected_categories'] = {item['token']: item['name'] for item in options}
            context.user_data['cbg_draft'] = draft
            return await _cbg_render_picker(q, context, cid)
        if action == 'clear':
            draft['selected_tokens'] = []
            draft['selected_categories'] = {}
            context.user_data['cbg_draft'] = draft
            return await _cbg_render_picker(q, context, cid)
        if action == 'new':
            context.user_data['await_cbg_new_category'] = True
            return await q.message.reply_text('Введите название новой категории для этого бюджета:')
        if action == 'cont':
            categories = _cbg_selected_names(draft, options)
            if not categories:
                return await q.answer('Выберите хотя бы одну категорию', show_alert=True)
            draft['categories'] = categories
            draft['step'] = 'amount'
            context.user_data['cbg_draft'] = draft
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='cbgp|back_categories'), InlineKeyboardButton('❌ Отмена', callback_data='cbgp|cancel')]])
            return await _safe_edit_or_reply(q, 'Введите сумму бюджета.\nНапример: 42000', reply_markup=kb)
        if action == 'back':
            draft['step'] = 'name'
            context.user_data['cbg_draft'] = draft
            return await _safe_edit_or_reply(q, f"Название сохранено: {draft.get('name') or '—'}\n\nВведите другое название или нажмите Отмена.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data='cbgp|cancel')]]))
        if action == 'back_categories':
            draft['step'] = 'categories'
            context.user_data['cbg_draft'] = draft
            return await _cbg_render_picker(q, context, cid)
        if action == 'back_amount':
            draft['step'] = 'amount'
            context.user_data['cbg_draft'] = draft
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='cbgp|back_categories'), InlineKeyboardButton('❌ Отмена', callback_data='cbgp|cancel')]])
            return await _safe_edit_or_reply(q, f"Сумма сохранена: {draft.get('amount') or '—'} ₽\n\nВведите сумму бюджета.", reply_markup=kb)
        if action == 'period':
            period = parts[2] if len(parts) > 2 else 'month'
            if period not in {'week', 'month'}:
                return await q.answer('Неверный период', show_alert=True)
            draft['period_type'] = period
            draft['step'] = 'alerts'
            context.user_data['cbg_draft'] = draft
            enabled = bool(draft.get('alerts_enabled', True))
            return await _safe_edit_or_reply(q, 'Настройте оповещения по порогам бюджета.', reply_markup=_cbg_alerts_kb(enabled))
        if action == 'back_period':
            draft['step'] = 'period'
            context.user_data['cbg_draft'] = draft
            return await _safe_edit_or_reply(q, 'Выберите период бюджета.', reply_markup=_cbg_period_kb())
        if action == 'alerts':
            draft['alerts_enabled'] = not bool(draft.get('alerts_enabled', True))
            context.user_data['cbg_draft'] = draft
            return await _safe_edit_or_reply(q, 'Настройте оповещения по порогам бюджета.', reply_markup=_cbg_alerts_kb(bool(draft.get('alerts_enabled', True))))
        if action == 'confirm':
            categories = draft.get('categories') or _cbg_selected_names(draft, options)
            text = (
                'Проверьте бюджет из категорий:\n\n'
                f"Название: {draft.get('name')}\n"
                f"Категории: {', '.join(categories)}\n"
                f"Сумма: {draft.get('amount')} ₽\n"
                f"Период: {'неделя' if draft.get('period_type') == 'week' else 'месяц'}\n"
                f"Оповещения: {'включены' if draft.get('alerts_enabled', True) else 'выключены'}"
            )
            return await _safe_edit_or_reply(q, text, reply_markup=_cbg_confirm_kb())
        if action == 'back_alerts':
            draft['step'] = 'alerts'
            context.user_data['cbg_draft'] = draft
            return await _safe_edit_or_reply(q, 'Настройте оповещения по порогам бюджета.', reply_markup=_cbg_alerts_kb(bool(draft.get('alerts_enabled', True))))
        if action == 'save':
            categories = draft.get('categories') or _cbg_selected_names(draft, options)
            if not categories or not draft.get('amount'):
                return await q.answer('Черновик неполный', show_alert=True)
            group_id = create_category_budget_group(
                user_id=cid,
                workspace_id=draft.get('workspace_id'),
                name=draft.get('name') or 'Бюджет из категорий',
                amount=to_decimal_money(draft.get('amount')),
                categories=categories,
                period_type=draft.get('period_type') or 'month',
                alerts_enabled=bool(draft.get('alerts_enabled', True)),
            )
            context.user_data.pop('cbg_draft', None)
            return await _safe_edit_or_reply(q, f'✅ Бюджет из категорий создан\n\nID: {group_id}', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧩 К бюджетам', callback_data='cbg_menu')], [InlineKeyboardButton('⬅️ Главное меню', callback_data='start_main')]]))
        if action == 'cancel':
            context.user_data.pop('cbg_draft', None)
            context.user_data.pop('await_cbg_new_category', None)
            return await _safe_edit_or_reply(q, 'Создание бюджета отменено.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧩 К бюджетам', callback_data='cbg_menu')]]))

    if data == 'lb_status':
        today = date.today()
        period = period_bounds('month', today)
        rows = _export_rows(cid, period.start, period.end)
        status = build_budget_status('Расходы месяца', 1, get_user_currency(cid), period, rows)
        text = (
            '📈 Статус расходов\n\n'
            f'Потрачено за месяц: {_fmt_money(status.spent)}\n'
            f'Операций: {len(rows)}'
        )
        return await _safe_edit_or_reply(q, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='lb_hub')]]))

    if data == 'workspace_menu':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Личное пространство', callback_data='workspace_personal')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text(
            '🧩 Пространства\n\nСейчас все личные операции остаются в личном пространстве. '
            'Основа для семейных, рабочих и групповых пространств готовится в backend-слое.',
            reply_markup=kb,
        )

    if data == 'workspace_personal':
        return await q.answer('Личное пространство выбрано')

    if data == 'cat_menu':
        context.user_data.pop('await_category_create', None)
        context.user_data.pop('category_rename_input', None)
        context.user_data.pop('category_action', None)
        return await _render_category_menu(q, context, update)

    if data.startswith('cat|'):
        parts = data.split('|')
        action = parts[1] if len(parts) > 1 else ''
        workspace_id = _category_workspace(update)
        user_id = update.effective_user.id
        type_key = parts[2] if len(parts) > 2 and parts[2] in CATEGORY_TYPES else 'expense'
        cfg = _category_type_def(type_key)

        if action == 'type' and len(parts) > 2:
            context.user_data.pop('await_category_create', None)
            context.user_data.pop('category_rename_input', None)
            context.user_data.pop('category_action', None)
            return await _render_category_list(q, context, update, type_key)
        if action == 'add' and len(parts) > 2 and parts[2] in CATEGORY_TYPES:
            context.user_data['await_category_create'] = {
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': cfg['op_type'],
                'type_key': type_key,
                'expires_at': unix_time() + 600,
            }
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data=f'cat|type|{type_key}'), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')]])
            return await _safe_edit_or_reply(q, 'Введите название новой категории:', reply_markup=kb)
        if action == 'open' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            return await _render_category_card(q, context, update, parts[3], type_key)
        if action == 'rename' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            if is_protected_category(source['name']):
                return await q.answer('Эту системную категорию нельзя переименовать.', show_alert=True)
            context.user_data['category_rename_input'] = {
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'type_key': type_key,
                'source': source['name'],
                'source_token': parts[3],
                'expires_at': unix_time() + 600,
            }
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')]])
            return await _safe_edit_or_reply(q, f"Введите новое название для категории «{source['name']}»:", reply_markup=kb)
        if action == 'rename_again' and len(parts) > 2:
            st = context.user_data.get('category_action') or {}
            token = parts[2]
            if not isinstance(st, dict) or st.get('token') != token or st.get('actor_user_id') != user_id or st.get('workspace_id') != workspace_id:
                return await q.answer('Действие устарело. Начните заново.', show_alert=True)
            retry_type_key = st.get('type_key') or _category_type_key(st.get('op_type'))
            context.user_data['category_rename_input'] = {
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': st.get('op_type'),
                'type_key': retry_type_key,
                'source': st.get('source'),
                'source_token': st.get('source_token'),
                'expires_at': unix_time() + 600,
            }
            return await _safe_edit_or_reply(q, f"Введите другое название для категории «{st.get('source')}»:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{retry_type_key}')]]))
        if action == 'open_dup' and len(parts) > 2:
            st = context.user_data.get('category_action') or {}
            token = parts[2]
            if not isinstance(st, dict) or st.get('token') != token or st.get('actor_user_id') != user_id or st.get('workspace_id') != workspace_id:
                return await q.answer('Действие устарело. Начните заново.', show_alert=True)
            dup_type_key = st.get('type_key') or _category_type_key(st.get('op_type'))
            dup_token = _category_find_token_by_name(context, user_id, workspace_id, st.get('op_type'), st.get('destination'))
            if not dup_token:
                return await _render_category_list(q, context, update, dup_type_key)
            return await _render_category_card(q, context, update, dup_token, dup_type_key)
        if action == 'move_start' and len(parts) > 2 and parts[2] in CATEGORY_TYPES:
            items = _category_store(context, user_id, workspace_id, cfg['op_type'])
            return await _safe_edit_or_reply(q, 'Выберите категорию, из которой перенести записи:', reply_markup=_category_list_kb(items, type_key=type_key, action='move_from'))
        if action == 'move_from' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source_token = parts[3]
            items = _category_store(context, user_id, workspace_id, cfg['op_type'])
            source = _category_from_token(context, user_id, workspace_id, source_token)
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            return await _safe_edit_or_reply(q, f"Куда перенести записи из категории «{source['name']}»?", reply_markup=_category_destination_kb(items, source_token, f'cat|move_to|{type_key}', type_key=type_key))
        if action == 'move_to' and len(parts) > 4 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            dest = _category_from_token(context, user_id, workspace_id, parts[4])
            if not source or not dest or source.get('op_type') != cfg['op_type'] or dest.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            if source['normalized_name'] == dest['normalized_name']:
                return await q.answer('Выберите другую категорию', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'type_key': type_key,
                'source': source['name'],
                'destination': dest['name'],
                'source_token': parts[3],
                'mode': 'move',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            text = (
                f"🔁 Перенести записи?\n\n"
                f"Откуда: {source['name']}\n"
                f"Куда: {dest['name']}\n"
                f"Операций: {counts.operations}\n"
                f"Связанных настроек: {counts.total - counts.operations}\n\n"
                "После переноса отчёты, аналитика, бюджеты и подсказки будут использовать новую категорию."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Перенести', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|move_from|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if action == 'delete' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            if is_protected_category(source['name']):
                return await q.answer('Эту системную категорию нельзя удалить.', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            refs = (
                f"Операций: {counts.operations}\n"
                f"Черновиков: {counts.drafts}\n"
                f"Бюджетов/лимитов: {counts.category_limits + counts.category_budget_groups}\n"
                f"Напоминаний: {counts.reminders}\n"
                f"ML-связей: {counts.aliases + counts.ml_observations}"
            )
            if counts.operations:
                text = (
                    f"Удаление категории «{source['name']}»\n\n"
                    f"{refs}\n\n"
                    "Можно перенести записи в другую категорию или удалить категорию вместе с операциями."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton('🔁 Перенести записи и удалить', callback_data=f"cat|delete_transfer|{type_key}|{parts[3]}")],
                    [InlineKeyboardButton('🗑 Удалить вместе с операциями', callback_data=f"cat|hard1|{type_key}|{parts[3]}")],
                    [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
                ])
                return await _safe_edit_or_reply(q, text, reply_markup=kb)
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'type_key': type_key,
                'source': source['name'],
                'destination': None,
                'mode': 'delete_empty',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('🗑 Удалить пустую категорию', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await _safe_edit_or_reply(q, f"Удалить категорию «{source['name']}»?\n\n{refs}", reply_markup=kb)
        if action == 'delete_transfer' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source_token = parts[3]
            items = _category_store(context, user_id, workspace_id, cfg['op_type'])
            source = _category_from_token(context, user_id, workspace_id, source_token)
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            return await _safe_edit_or_reply(q, f"Куда перенести записи из категории «{source['name']}» перед удалением?", reply_markup=_category_destination_kb(items, source_token, f'cat|delete_to|{type_key}', type_key=type_key))
        if action == 'delete_to' and len(parts) > 4 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            dest = _category_from_token(context, user_id, workspace_id, parts[4])
            if not source or not dest or source.get('op_type') != cfg['op_type'] or dest.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            if source['normalized_name'] == dest['normalized_name']:
                return await q.answer('Выберите другую категорию', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'type_key': type_key,
                'source': source['name'],
                'destination': dest['name'],
                'source_token': parts[3],
                'mode': 'delete_merge',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            budget_note = '\nБюджет исходной категории будет удалён; бюджет назначения сохранится.' if counts.category_limits else ''
            text = (
                f"🗑 Удалить категорию после переноса?\n\n"
                f"Категория: {source['name']}\n"
                f"Перенести в: {dest['name']}\n"
                f"Операций: {counts.operations}\n"
                f"Связанных настроек: {counts.total - counts.operations}{budget_note}\n\n"
                "Это действие изменит историю отчётов и аналитики."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Перенести и удалить', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|delete|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if action == 'hard1' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Продолжить', callback_data=f"cat|hard2|{type_key}|{parts[3]}")],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|delete|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await _safe_edit_or_reply(q, f"Безвозвратное удаление\n\nКатегория «{source['name']}» и {counts.operations} операций будут удалены. Отчёты по этим операциям больше не будут их учитывать.", reply_markup=kb)
        if action == 'hard2' and len(parts) > 3 and parts[2] in CATEGORY_TYPES:
            source = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or source.get('op_type') != cfg['op_type']:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{type_key}')]]))
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'type_key': type_key,
                'source': source['name'],
                'destination': None,
                'mode': 'hard_delete',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Удалить категорию и {counts.operations} операций", callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|hard1|{type_key}|{parts[3]}"), InlineKeyboardButton('❌ Отмена', callback_data=f'cat|type|{type_key}')],
            ])
            return await _safe_edit_or_reply(q, 'Последнее подтверждение\n\nЭто действие нельзя отменить.', reply_markup=kb)
        if action == 'post_delete' and len(parts) > 2:
            st = context.user_data.get('category_post_transfer') or {}
            token = parts[2]
            if (
                not isinstance(st, dict)
                or st.get('token') != token
                or st.get('actor_user_id') != user_id
                or st.get('workspace_id') != workspace_id
                or st.get('used')
                or st.get('expires_at', 0) < unix_time()
            ):
                context.user_data.pop('category_post_transfer', None)
                return await q.answer('Действие устарело. Начните заново.', show_alert=True)
            st['used'] = True
            context.user_data['category_post_transfer'] = st
            try:
                result = delete_category_without_operations(user_id=user_id, workspace_id=workspace_id, op_type=st['op_type'], category=st['source'])
            except ValueError:
                context.user_data.pop('category_post_transfer', None)
                return await q.answer('В исходной категории ещё есть связи. Откройте категорию заново.', show_alert=True)
            context.user_data.pop('category_post_transfer', None)
            context.user_data.pop('category_manage_options', None)
            done_type_key = st.get('type_key') or _category_type_key(st.get('op_type'))
            return await _safe_edit_or_reply(q, f"✅ Пустая категория удалена\n\n{result.source}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 К списку', callback_data=f'cat|type|{done_type_key}')], [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')]]))
        if action == 'confirm' and len(parts) > 2:
            st = context.user_data.get('category_action') or {}
            token = parts[2]
            if (
                not isinstance(st, dict)
                or st.get('token') != token
                or st.get('actor_user_id') != user_id
                or st.get('workspace_id') != workspace_id
                or st.get('used')
                or st.get('expires_at', 0) < unix_time()
            ):
                context.user_data.pop('category_action', None)
                return await q.answer('Подтверждение устарело. Начните заново.', show_alert=True)
            st['used'] = True
            context.user_data['category_action'] = st
            result_type_key = st.get('type_key') or _category_type_key(st.get('op_type'))
            try:
                if st['mode'] == 'delete_empty':
                    result = delete_category_without_operations(user_id=user_id, workspace_id=workspace_id, op_type=st['op_type'], category=st['source'])
                    message = f"✅ Категория удалена\n\n{result.source}"
                elif st['mode'] == 'rename':
                    result = rename_category(user_id=user_id, workspace_id=workspace_id, op_type=st['op_type'], source=st['source'], destination=st['destination'])
                    message = f"✅ Категория переименована\n\n{result.source} → {result.destination}\nОбновлено операций: {result.counts.operations}"
                elif st['mode'] == 'hard_delete':
                    result = hard_delete_category_with_operations(user_id=user_id, workspace_id=workspace_id, op_type=st['op_type'], category=st['source'])
                    message = f"✅ Категория удалена вместе с операциями\n\n{result.source}\nУдалено операций: {result.deleted_operation_count}"
                else:
                    result = transfer_category(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        op_type=st['op_type'],
                        source=st['source'],
                        destination=st['destination'],
                        archive_source=(st['mode'] == 'delete_merge'),
                        budget_resolution='transfer_source' if st['mode'] == 'move' else 'delete_source',
                    )
                    message = f"✅ Готово\n\n{result.source} → {result.destination}\nОбновлено операций: {result.counts.operations}"
                    if st['mode'] == 'delete_merge':
                        message += "\nИсходная категория скрыта из выбора."
                    else:
                        post_token = token_urlsafe(8)
                        context.user_data['category_post_transfer'] = {
                            'token': post_token,
                            'actor_user_id': user_id,
                            'workspace_id': workspace_id,
                            'op_type': st['op_type'],
                            'type_key': result_type_key,
                            'source': st['source'],
                            'expires_at': unix_time() + 600,
                            'used': False,
                        }
            except ValueError as e:
                context.user_data.pop('category_action', None)
                return await q.answer(str(e), show_alert=True)
            except Exception as e:
                context.user_data.pop('category_action', None)
                log.warning('category_action_failed reason=%s', type(e).__name__)
                return await _safe_edit_or_reply(q, 'Не удалось безопасно выполнить действие. Попробуйте позже.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data=f'cat|type|{result_type_key}')]]))
            context.user_data.pop('category_action', None)
            context.user_data.pop('category_manage_options', None)
            rows = []
            post = context.user_data.get('category_post_transfer') or {}
            if post and post.get('source') == st.get('source') and st.get('mode') in {'move', 'duplicate_merge'}:
                rows.append([InlineKeyboardButton('🗑 Удалить пустую исходную категорию', callback_data=f"cat|post_delete|{post['token']}")])
                rows.append([InlineKeyboardButton('Оставить исходную категорию', callback_data=f'cat|type|{result_type_key}')])
            rows.extend([[InlineKeyboardButton('🏷 К списку', callback_data=f'cat|type|{result_type_key}')], [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')]])
            return await _safe_edit_or_reply(q, message, reply_markup=InlineKeyboardMarkup(rows))
        if action == 'add_type':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Расход', callback_data='cat|add|expense'), InlineKeyboardButton('Доход', callback_data='cat|add|income')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='cat_menu'), InlineKeyboardButton('❌ Отмена', callback_data='menu_settings')],
            ])
            return await _safe_edit_or_reply(q, 'Какой тип категории добавить?', reply_markup=kb)
        if action == 'add':
            op_type = 'Доходы' if len(parts) > 2 and parts[2] == 'income' else 'Расходы'
            context.user_data['await_category_create'] = {
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': op_type,
                'expires_at': unix_time() + 600,
            }
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='cat|add_type'), InlineKeyboardButton('❌ Отмена', callback_data='cat_menu')]])
            return await _safe_edit_or_reply(q, 'Введите название новой категории:', reply_markup=kb)
        if action == 'open' and len(parts) > 2:
            return await _render_category_card(q, context, update, parts[2])
        if action == 'move_start':
            items = _category_store(context, user_id, workspace_id)
            return await _safe_edit_or_reply(q, 'Выберите категорию, из которой перенести записи:', reply_markup=_category_list_kb(items, action='move_from'))
        if action == 'move_from' and len(parts) > 2:
            source_token = parts[2]
            items = _category_store(context, user_id, workspace_id)
            source = _category_from_token(context, user_id, workspace_id, source_token)
            if not source:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
            return await _safe_edit_or_reply(q, f"Куда перенести записи из категории «{source['name']}»?", reply_markup=_category_destination_kb(items, source_token, 'cat|move_to'))
        if action == 'move_to' and len(parts) > 3:
            source = _category_from_token(context, user_id, workspace_id, parts[2])
            dest = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or not dest:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
            if source['normalized_name'] == dest['normalized_name']:
                return await q.answer('Выберите другую категорию', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'source': source['name'],
                'destination': dest['name'],
                'mode': 'move',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            text = (
                f"🔁 Перенести записи?\n\n"
                f"Откуда: {source['name']}\n"
                f"Куда: {dest['name']}\n"
                f"Будет изменено операций: {counts.operations}\n\n"
                "После переноса отчёты, аналитика и бюджеты будут использовать новую категорию."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Перенести', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|move_from|{parts[2]}"), InlineKeyboardButton('❌ Отмена', callback_data='cat_menu')],
            ])
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if action == 'delete' and len(parts) > 2:
            source = _category_from_token(context, user_id, workspace_id, parts[2])
            if not source:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
            if is_protected_category(source['name']):
                return await q.answer('Эту системную категорию нельзя удалить.', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            if counts.operations:
                items = _category_store(context, user_id, workspace_id)
                text = f"В этой категории есть {counts.operations} записей.\nВыберите категорию, в которую их нужно перенести:"
                return await _safe_edit_or_reply(q, text, reply_markup=_category_destination_kb(items, parts[2], 'cat|delete_to'))
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'source': source['name'],
                'destination': None,
                'mode': 'delete_empty',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            budget_note = '\nБюджет этой категории будет удалён.' if counts.category_limits else ''
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('🗑 Да, удалить', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|open|{parts[2]}"), InlineKeyboardButton('❌ Отмена', callback_data='cat_menu')],
            ])
            return await _safe_edit_or_reply(q, f"Удалить категорию «{source['name']}»?{budget_note}\n\nОпераций в ней нет.", reply_markup=kb)
        if action == 'delete_to' and len(parts) > 3:
            source = _category_from_token(context, user_id, workspace_id, parts[2])
            dest = _category_from_token(context, user_id, workspace_id, parts[3])
            if not source or not dest:
                return await _safe_edit_or_reply(q, 'Кнопка устарела. Откройте категории заново.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
            if source['normalized_name'] == dest['normalized_name']:
                return await q.answer('Выберите другую категорию', show_alert=True)
            counts = category_reference_counts(user_id=user_id, workspace_id=workspace_id, op_type=source['op_type'], category=source['name'])
            token = token_urlsafe(8)
            context.user_data['category_action'] = {
                'token': token,
                'actor_user_id': user_id,
                'workspace_id': workspace_id,
                'op_type': source['op_type'],
                'source': source['name'],
                'destination': dest['name'],
                'mode': 'delete_merge',
                'expires_at': unix_time() + 600,
                'used': False,
            }
            budget_note = '\nБюджет исходной категории будет удалён; бюджет назначения сохранится.' if counts.category_limits else ''
            text = (
                f"🗑 Удалить категорию после переноса?\n\n"
                f"Категория: {source['name']}\n"
                f"Перенести в: {dest['name']}\n"
                f"Записей: {counts.operations}{budget_note}\n\n"
                "Это действие изменит историю отчётов и аналитики."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Перенести и удалить', callback_data=f'cat|confirm|{token}')],
                [InlineKeyboardButton('⬅️ Назад', callback_data=f"cat|delete|{parts[2]}"), InlineKeyboardButton('❌ Отмена', callback_data='cat_menu')],
            ])
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if action == 'confirm' and len(parts) > 2:
            st = context.user_data.get('category_action') or {}
            token = parts[2]
            if (
                not isinstance(st, dict)
                or st.get('token') != token
                or st.get('actor_user_id') != user_id
                or st.get('workspace_id') != workspace_id
                or st.get('used')
                or st.get('expires_at', 0) < unix_time()
            ):
                context.user_data.pop('category_action', None)
                return await q.answer('Подтверждение устарело. Начните заново.', show_alert=True)
            st['used'] = True
            context.user_data['category_action'] = st
            try:
                if st['mode'] == 'delete_empty':
                    result = delete_category_without_operations(user_id=user_id, workspace_id=workspace_id, op_type=st['op_type'], category=st['source'])
                    message = f"✅ Категория удалена\n\n{result.source}"
                else:
                    result = transfer_category(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        op_type=st['op_type'],
                        source=st['source'],
                        destination=st['destination'],
                        archive_source=(st['mode'] == 'delete_merge'),
                        budget_resolution='delete_source',
                    )
                    message = (
                        f"✅ Готово\n\n"
                        f"{result.source} → {result.destination}\n"
                        f"Обновлено операций: {result.counts.operations}"
                    )
                    if st['mode'] == 'delete_merge':
                        message += "\nИсходная категория скрыта из выбора."
            except ValueError as e:
                context.user_data.pop('category_action', None)
                return await q.answer(str(e), show_alert=True)
            except Exception as e:
                context.user_data.pop('category_action', None)
                log.warning('category_action_failed reason=%s', type(e).__name__)
                return await _safe_edit_or_reply(q, 'Не удалось безопасно выполнить действие. Попробуйте позже.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏷 Категории', callback_data='cat_menu')]]))
            context.user_data.pop('category_action', None)
            context.user_data.pop('category_manage_options', None)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('🏷 К категориям', callback_data='cat_menu')], [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')]])
            return await _safe_edit_or_reply(q, message, reply_markup=kb)

    if data == 'privacy_menu':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data.pop('history_delete_confirm', None)
        context.user_data.pop('history_delete_wizard', None)
        track_product_event(ProductEvent(event_name="privacy_opened", user_id=update.effective_user.id, status="success"))
        return await _render_privacy_menu(q, locale)

    if data == 'hist|menu':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data.pop('history_delete_confirm', None)
        context.user_data.pop('history_delete_wizard', None)
        return await _render_history_menu(q, locale)

    if data.startswith('hist|period|'):
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        period = data.split('|', 2)[2]
        try:
            today = user_local_date(update.effective_user.id)
            start_date, end_date = history_period_bounds(period, today)
        except Exception:
            return await q.answer(t('privacy.stale', locale), show_alert=True)
        context.user_data.pop('history_delete_wizard', None)
        return await _render_history_preview(q, context, update.effective_user.id, locale, start_date, end_date)

    if data == 'hist|custom|start':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data.pop('history_delete_confirm', None)
        context.user_data['history_delete_wizard'] = {
            'actor_user_id': update.effective_user.id,
            'step': 'start',
            'expires_at': unix_time() + 600,
        }
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]])
        await q.answer()
        return await _safe_edit_or_reply(q, t('privacy.custom.start', locale), reply_markup=kb)

    if data.startswith('hist|confirm|'):
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        token = data.split('|', 2)[2]
        st = context.user_data.get('history_delete_confirm') or {}
        if (
            not isinstance(st, dict)
            or st.get('token') != token
            or st.get('actor_user_id') != update.effective_user.id
            or st.get('used')
            or st.get('expires_at', 0) < unix_time()
        ):
            context.user_data.pop('history_delete_confirm', None)
            return await q.answer(t('privacy.stale', locale), show_alert=True)
        st['used'] = True
        context.user_data['history_delete_confirm'] = st
        start_date = _date_from_state(st.get('start'))
        end_date = _date_from_state(st.get('end'))
        period = _period_label(start_date, end_date, locale)
        try:
            preview = preview_delete_financial_history(update.effective_user.id, start_date, end_date)
            if preview.operation_count == 0:
                context.user_data.pop('history_delete_confirm', None)
                return await _safe_edit_or_reply(
                    q,
                    t('privacy.history.zero', locale, period=period),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]),
                )
            result = delete_financial_history(update.effective_user.id, start_date, end_date)
            track_product_event(ProductEvent(
                event_name="financial_history_deleted",
                user_id=update.effective_user.id,
                status="success",
                properties={"operation_count": result.operation_count},
            ))
        except Exception as e:
            context.user_data.pop('history_delete_confirm', None)
            log.warning(
                'financial_history_delete_failed user_id=%s start=%s end=%s reason=%s',
                update.effective_user.id,
                start_date,
                end_date,
                type(e).__name__,
            )
            return await _safe_edit_or_reply(
                q,
                t('privacy.history.failed', locale),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='hist|menu')]]),
            )
        context.user_data.pop('history_delete_confirm', None)
        await q.answer()
        return await _safe_edit_or_reply(
            q,
            t('privacy.history.success', locale, count=result.operation_count, period=period),
            reply_markup=_history_done_kb(locale),
        )

    if data == 'privacy_delete_start':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data['delete_my_data'] = {'actor_user_id': update.effective_user.id, 'step': 'explain', 'expires_at': unix_time() + 900}
        return await _render_delete_start(q, cid, locale)

    if data == 'privacy_delete_stage2':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        st = context.user_data.get('delete_my_data') or {}
        if st.get('actor_user_id') != update.effective_user.id or st.get('expires_at', 0) < unix_time():
            context.user_data.pop('delete_my_data', None)
            return await q.answer(t('privacy.stale', locale), show_alert=True)
        st['step'] = 'confirm'
        st['expires_at'] = unix_time() + 600
        context.user_data['delete_my_data'] = st
        return await _render_delete_confirm(q, locale)

    if data == 'privacy_delete_cancel':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        context.user_data.pop('delete_my_data', None)
        return await _safe_edit_or_reply(q, t('privacy.cancelled', locale), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')]]))

    if data == 'privacy_delete_confirm':
        locale = _privacy_locale(update.effective_user.id, getattr(update.effective_user, 'language_code', None))
        st = context.user_data.get('delete_my_data') or {}
        if st.get('actor_user_id') != update.effective_user.id or st.get('step') != 'confirm' or st.get('expires_at', 0) < unix_time():
            context.user_data.pop('delete_my_data', None)
            return await q.answer(t('privacy.stale', locale), show_alert=True)
        try:
            apply_account_deletion(update.effective_user.id)
            delete_user_data(update.effective_user.id)
        except Exception as e:
            log.warning('personal_data_delete_failed user_id=%s reason=%s', update.effective_user.id, type(e).__name__)
            return await _safe_edit_or_reply(q, t('privacy.delete.failed', locale), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('privacy.back', locale), callback_data='privacy_menu')]]))
        context.user_data.clear()
        return await _safe_edit_or_reply(q, t('privacy.delete.success', locale))

    if data == 'rem_menu':
        return await render_reminders_menu(q, cid, context)

    if data == 'rem_all':
        rows = reminders_list(cid, active_only=False)
        if not rows:
            return await _safe_edit_or_reply(q, 'Нет напоминаний.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='rem_menu')]]))
        lines = ['📋 Все напоминания']
        btns = []
        for r in rows[:20]:
            st = 'вкл' if r['is_active'] else 'выкл'
            lines.append(f"• {r['title']} ({st})")
            btns.append([InlineKeyboardButton(f"{r['title'][:24]}", callback_data=f"rem_o|{r['id']}")])
        btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='rem_menu')])
        return await _safe_edit_or_reply(q, '\n'.join(lines), reply_markup=InlineKeyboardMarkup(btns))

    if data == 'rem_add':
        context.user_data['rem_draft'] = {'rem_type': 'Расходы', 'repeat_rule': 'none', 'notify_days_before': 1}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💸 Расход', callback_data='rem_t_exp'), InlineKeyboardButton('💰 Доход', callback_data='rem_t_inc')],
            [InlineKeyboardButton('🔁 Регулярный платёж', callback_data='rem_t_reg')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_menu')],
        ])
        return await _safe_edit_or_reply(q, 'Что напомнить?', reply_markup=kb)
    if data in {'rem_t_exp', 'rem_t_inc', 'rem_t_reg'}:
        d = context.user_data.setdefault('rem_draft', {})
        d['rem_type'] = 'Доходы' if data == 'rem_t_inc' else 'Расходы'
        if data == 'rem_t_reg':
            d['repeat_rule'] = 'monthly'
        context.user_data['await_rem_title_amount'] = True
        await q.answer()
        return await q.message.reply_text('Напиши название и сумму.\n\nПример:\nChatGPT 1990')

    if data.startswith('rem_o|'):
        rid = int(data.split('|', 1)[1]); r = reminder_get(cid, rid)
        if not r:
            return await _safe_edit_or_reply(q, 'Напоминание не найдено.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='rem_menu')]]))
        toggle_lbl = '⏸ Отключить' if r['is_active'] else '▶️ Включить'
        txt = (f"🔔 {r['title']}\n\nСумма: {_fmt_money(r['amount'])}\nТип: {r['rem_type']}\nКатегория: {r['category']}\n"
               f"Дата: {r['event_date'].strftime('%d.%m.%Y')}\nПовтор: {r['repeat_rule']}\nНапомнить: за {r['notify_days_before']} дн.\nСтатус: {'включено' if r['is_active'] else 'выключено'}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 Сумма', callback_data=f'rem_e_amt|{rid}'), InlineKeyboardButton('🏷 Категория', callback_data=f'rem_e_cat|{rid}')],
            [InlineKeyboardButton('📅 Дата', callback_data=f'rem_e_date|{rid}'), InlineKeyboardButton('🔁 Повтор', callback_data=f'rem_e_rep|{rid}')],
            [InlineKeyboardButton('⏰ Напоминать', callback_data=f'rem_e_not|{rid}'), InlineKeyboardButton('🔁 Тип', callback_data=f'rem_e_typ|{rid}')],
            [InlineKeyboardButton(toggle_lbl, callback_data=f'rem_tog|{rid}')],
            [InlineKeyboardButton('🗑 Удалить', callback_data=f'rem_delq|{rid}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_menu')],
        ])
        return await _safe_edit_or_reply(q, txt, reply_markup=kb)

    if data in {'rem_cat_zav', 'rem_cat_prod', 'rem_cat_tr', 'rem_cat_sub', 'rem_cat_other', 'rem_cat_custom', 'rem_cat_salary', 'rem_cat_transfer', 'rem_cat_cashback'}:
        d = context.user_data.setdefault('rem_draft', {})
        log.info('reminder_wizard_category_selected user=%s cb=%s', cid, data)
        if data == 'rem_cat_custom':
            context.user_data['await_rem_edit'] = {'rid': -1, 'field': 'category_draft'}
            await q.answer()
            return await q.message.reply_text('Введи категорию:')
        d['category'] = {
            'rem_cat_zav': 'Заведения', 'rem_cat_prod': 'Продукты', 'rem_cat_tr': 'Транспорт',
            'rem_cat_sub': 'Подписки', 'rem_cat_other': 'Прочее', 'rem_cat_salary': 'Зарплата',
            'rem_cat_transfer': 'Переводы', 'rem_cat_cashback': 'Кэшбэк'
        }[data]
        context.user_data.pop('await_rem_category', None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Сегодня', callback_data='rem_dt_today'), InlineKeyboardButton('Завтра', callback_data='rem_dt_tom')],
            [InlineKeyboardButton('1 число', callback_data='rem_dt_1'), InlineKeyboardButton('15 число', callback_data='rem_dt_15')],
            [InlineKeyboardButton('✏️ Ввести дату', callback_data='rem_dt_in')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
        ])
        await q.answer()
        msg = await q.message.reply_text('Когда первое событие?\n\nМожно написать:\n19\n19 число\n19.06\n19.06.2026\nзавтра\n\nДля регулярных платежей это будет первая дата, дальше повтор настроим следующим шагом.', reply_markup=kb)
        context.user_data['rem_last_msg_id'] = getattr(msg, 'message_id', None)
        log.info('reminder_wizard_date_ui_send_ok user=%s', cid)
        return
    if data in {'rem_dt_today', 'rem_dt_tom', 'rem_dt_1', 'rem_dt_15', 'rem_dt_in'}:
        d = context.user_data.setdefault('rem_draft', {})
        today_value = date.today()
        if data == 'rem_dt_today': d['event_date'] = today_value
        elif data == 'rem_dt_tom': d['event_date'] = today_value + timedelta(days=1)
        elif data == 'rem_dt_1':
            d['event_date'] = today_value.replace(day=1) if today_value.day <= 1 else _next_monthly_date(today_value.replace(day=1))
        elif data == 'rem_dt_15':
            d['event_date'] = today_value.replace(day=15) if today_value.day <= 15 else _next_monthly_date(today_value.replace(day=15))
        elif data == 'rem_dt_in':
            context.user_data['await_rem_edit'] = {'rid': -1, 'field': 'date_draft'}
            await q.answer()
            prompt = 'Когда первое списание?' if (d.get('repeat_rule') != 'none') else 'Когда первое событие?'
            return await q.message.reply_text(f'{prompt}\n\nМожно написать:\n19\n19 число\n19.06\n19.06.2026')
        log.info('reminder_date_selected source=button event_date=%s user=%s', d['event_date'].isoformat(), cid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Не повторять', callback_data='rem_r_none')],
            [InlineKeyboardButton('Каждую неделю', callback_data='rem_r_week'), InlineKeyboardButton('Каждый месяц', callback_data='rem_r_month')],
            [InlineKeyboardButton('Каждый год', callback_data='rem_r_year'), InlineKeyboardButton('Свой период', callback_data='rem_r_custom')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
        ])
        await q.answer()
        msg = await q.message.reply_text('Как часто повторять?', reply_markup=kb)
        context.user_data['rem_last_msg_id'] = getattr(msg, 'message_id', None)
        log.info('reminder_wizard_repeat_ui_send_ok user=%s', cid)
        return
    if data in {'rem_r_none', 'rem_r_week', 'rem_r_month', 'rem_r_year', 'rem_r_custom'}:
        d = context.user_data.setdefault('rem_draft', {})
        d['repeat_rule'] = {'rem_r_none': 'none', 'rem_r_week': 'weekly', 'rem_r_month': 'monthly', 'rem_r_year': 'yearly', 'rem_r_custom': 'custom_days'}[data]
        if data == 'rem_r_custom':
            context.user_data['await_rem_edit'] = {'rid': -1, 'field': 'repeat_draft'}
            await q.answer()
            return await q.message.reply_text('Через сколько дней повторять? (1..3650)')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('В день события', callback_data='rem_n_0')],
            [InlineKeyboardButton('За 1 день', callback_data='rem_n_1'), InlineKeyboardButton('За 2 дня', callback_data='rem_n_2')],
            [InlineKeyboardButton('За 3 дня', callback_data='rem_n_3'), InlineKeyboardButton('За неделю', callback_data='rem_n_7')],
            [InlineKeyboardButton('✏️ Свой вариант', callback_data='rem_n_in')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
        ])
        await q.answer()
        msg = await q.message.reply_text('Когда напомнить?', reply_markup=kb)
        context.user_data['rem_last_msg_id'] = getattr(msg, 'message_id', None)
        log.info('reminder_wizard_notify_ui_send_ok user=%s', cid)
        return
    if data in {'rem_n_0', 'rem_n_1', 'rem_n_2', 'rem_n_3', 'rem_n_7', 'rem_n_in'}:
        d = context.user_data.setdefault('rem_draft', {})
        if data == 'rem_n_in':
            context.user_data['await_rem_edit'] = {'rid': -1, 'field': 'notify_draft'}
            await q.answer()
            return await q.message.reply_text('За сколько дней напомнить? (0..30)')
        d['notify_days_before'] = int(data.split('_')[-1])
        ev = d.get('event_date')
        rpt = d.get('repeat_rule', 'none')
        next_after = None
        if rpt == 'weekly': next_after = ev + timedelta(days=7)
        elif rpt == 'monthly': next_after = _next_monthly_date(ev)
        elif rpt == 'yearly':
            try: next_after = ev.replace(year=ev.year + 1)
            except Exception: next_after = ev.replace(month=2, day=28, year=ev.year + 1)
        elif rpt == 'custom_days': next_after = ev + timedelta(days=int(d.get('repeat_interval_days') or 1))
        log.info('reminder_repeat_semantics repeat_rule=%s event_date=%s next_after=%s user=%s', rpt, ev.isoformat(), (next_after.isoformat() if next_after else '-'), cid)
        date_label = 'Первое списание' if rpt != 'none' else 'Дата'
        txt = (f"🔔 Напоминание\n\n{d.get('title','—')} — {_fmt_money(d.get('amount',0))}\nТип: {d.get('rem_type','Расходы')}\n"
               f"Категория: {d.get('category','Прочее')}\n{date_label}: {ev.strftime('%d.%m.%Y')}\nПовтор: {_repeat_label(rpt, d)}" +
               (f"\nСледующее после этого: {next_after.strftime('%d.%m.%Y')}" if next_after else '') +
               f"\nНапомнить: за {d.get('notify_days_before',1)} дня")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ Сохранить', callback_data='rem_save')], [InlineKeyboardButton('✏️ Изменить', callback_data='rem_add')], [InlineKeyboardButton('❌ Отмена', callback_data='rem_menu')]])
        await q.answer()
        return await _safe_edit_or_reply(q, txt, reply_markup=kb)
    if data == 'rem_quiet_time':
        context.user_data.pop('rem_quiet_confirmed', None)
        context.user_data['await_rem_edit'] = {'rid': -1, 'field': 'notify_draft'}
        await q.answer()
        return await q.message.reply_text('За сколько дней напомнить? (0..30)')

    if data in {'rem_save', 'rem_quiet_save'}:
        d = context.user_data.get('rem_draft') or {}
        if data == 'rem_save' and not context.user_data.get('rem_quiet_confirmed'):
            quiet, due_label = _reminder_quiet_warning(d, cid)
            if quiet:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton('✅ Сохранить с задержкой', callback_data='rem_quiet_save')],
                    [InlineKeyboardButton('✏️ Изменить время', callback_data='rem_quiet_time')],
                    [InlineKeyboardButton('❌ Отмена', callback_data='rem_menu')],
                ])
                await q.answer()
                return await _safe_edit_or_reply(
                    q,
                    f"🌙 В это время будут тихие часы.\n\nПлановое напоминание: {due_label}. Если сохранить, бот доставит его после окончания тихих часов.",
                    reply_markup=kb,
                )
        context.user_data.pop('rem_quiet_confirmed', None)
        rid = _save_reminder_draft(cid, d)
        log.info('reminder_saved user_id=%s reminder_id=%s', cid, rid)
        context.user_data.pop('rem_draft', None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('📋 К напоминаниям', callback_data='rem_menu')], [InlineKeyboardButton('➕ Добавить ещё', callback_data='rem_add')], [InlineKeyboardButton('⬅️ Главное меню', callback_data='start_main')]])
        await q.answer('Сохранено')
        return await _safe_edit_or_reply(q, '🔔 Напоминание сохранено.', reply_markup=kb)

    if data == 'settings_budgets':
        wl, ml = get_user_budgets(cid)
        has_any = bool((wl or 0) > 0 or (ml or 0) > 0)
        return await _safe_edit_or_reply(q, _budgets_hub_text(cid), reply_markup=_budgets_hub_kb(has_any))

    if data == 'bud_add':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Неделя', callback_data='bud_add_period|week')],
            [InlineKeyboardButton('Месяц', callback_data='bud_add_period|month')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')],
        ])
        return await _safe_edit_or_reply(q, 'Выбери период бюджета:', reply_markup=kb)

    if data.startswith('bud_add_period|'):
        period = data.split('|', 1)[1]
        context.user_data['budget_add_period'] = period
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='bud_add')]])
        return await _safe_edit_or_reply(q, 'Введи сумму бюджета.\nНапример: 60000', reply_markup=kb)

    if data == 'bud_edit':
        wl, ml = get_user_budgets(cid)
        btns = []
        if ml and ml > 0:
            btns.append([InlineKeyboardButton(f'Месяц — {_fmt_money(ml)}', callback_data='bud_card|month')])
        if wl and wl > 0:
            btns.append([InlineKeyboardButton(f'Неделя — {_fmt_money(wl)}', callback_data='bud_card|week')])
        btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')])
        return await _safe_edit_or_reply(q, 'Что изменить?', reply_markup=InlineKeyboardMarkup(btns))

    if data.startswith('bud_card|'):
        period = data.split('|', 1)[1]
        wl, ml = get_user_budgets(cid)
        amount = ml if period == 'month' else wl
        if not amount:
            return await _safe_edit_or_reply(q, 'Бюджет не найден.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')]]))
        spent = _budget_spent(cid, period)
        rem = to_decimal_money(amount) - to_decimal_money(spent)
        text = (
            f"💰 Бюджет: {'месяц' if period=='month' else 'неделя'}\n\n"
            f"Лимит: {_fmt_money(amount)}\n"
            f"Потрачено: {_fmt_money(spent)}\n"
            f"{'Осталось' if rem>=0 else 'Перерасход'}: {_fmt_money(abs(int(rem)))}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('−5000', callback_data=f'bud_adj|{period}|-5000'), InlineKeyboardButton('−1000', callback_data=f'bud_adj|{period}|-1000'), InlineKeyboardButton('+1000', callback_data=f'bud_adj|{period}|1000'), InlineKeyboardButton('+5000', callback_data=f'bud_adj|{period}|5000')],
            [InlineKeyboardButton('✏️ Ввести сумму', callback_data=f'bud_set_manual|{period}')],
            [InlineKeyboardButton('🗑 Удалить', callback_data=f'bud_del_one|{period}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='bud_edit')],
        ])
        return await _safe_edit_or_reply(q, text, reply_markup=kb)

    if data.startswith('bud_adj|'):
        _, period, delta_s = data.split('|', 2)
        delta = int(delta_s)
        wl, ml = get_user_budgets(cid)
        cur = int(ml if period == 'month' else wl or 0)
        new = cur + delta
        if new <= 0:
            return await q.answer('Бюджет не может быть меньше 1 ₽', show_alert=True)
        if new >= 1_000_000_000:
            return await q.answer('Слишком большой бюджет', show_alert=True)
        token = token_urlsafe(8)
        context.user_data['budget_pending_edit'] = {'token': token, 'actor_user_id': update.effective_user.id, 'period': period, 'amount': new, 'expires_at': unix_time() + 600, 'used': False}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Сохранить', callback_data=f'bud_confirm|{token}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data=f'bud_card|{period}'), InlineKeyboardButton('❌ Отмена', callback_data='settings_budgets')],
        ])
        return await _safe_edit_or_reply(q, f"Сохранить новый бюджет?\n\n{'Месяц' if period=='month' else 'Неделя'} — {_fmt_money(new)}", reply_markup=kb)

    if data.startswith('bud_set_manual|'):
        period = data.split('|', 1)[1]
        context.user_data['budget_manual_period'] = period
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data=f'bud_card|{period}'), InlineKeyboardButton('❌ Отмена', callback_data='settings_budgets')]])
        return await _safe_edit_or_reply(q, 'Введи новую сумму бюджета.', reply_markup=kb)

    if data.startswith('bud_confirm|'):
        token = data.split('|', 1)[1]
        st = context.user_data.get('budget_pending_edit') or {}
        if (
            not isinstance(st, dict)
            or st.get('token') != token
            or st.get('actor_user_id') != update.effective_user.id
            or st.get('used')
            or st.get('expires_at', 0) < unix_time()
        ):
            context.user_data.pop('budget_pending_edit', None)
            return await q.answer('Подтверждение устарело. Начните заново.', show_alert=True)
        st['used'] = True
        context.user_data['budget_pending_edit'] = st
        period = st['period']
        amount = to_decimal_money(st['amount'])
        if period == 'month':
            set_budget(cid, month=amount)
        else:
            set_budget(cid, week=amount)
        context.user_data.pop('budget_pending_edit', None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('💰 Открыть бюджет', callback_data=f'bud_card|{period}')], [InlineKeyboardButton('💰 К бюджетам', callback_data='settings_budgets')]])
        return await _safe_edit_or_reply(q, f"✅ Бюджет обновлён\n\n{'Месяц' if period=='month' else 'Неделя'} — {_fmt_money(amount)}", reply_markup=kb)

    if data == 'bud_del':
        wl, ml = get_user_budgets(cid)
        btns = []
        if ml and ml > 0:
            btns.append([InlineKeyboardButton(f'Месяц — {_fmt_money(ml)}', callback_data='bud_del_one|month')])
        if wl and wl > 0:
            btns.append([InlineKeyboardButton(f'Неделя — {_fmt_money(wl)}', callback_data='bud_del_one|week')])
        btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')])
        return await _safe_edit_or_reply(q, 'Что удалить?', reply_markup=InlineKeyboardMarkup(btns))

    if data.startswith('bud_del_one|'):
        period = data.split('|', 1)[1]
        wl, ml = get_user_budgets(cid)
        amount = ml if period == 'month' else wl
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('🗑 Да, удалить', callback_data=f'bud_del_yes|{period}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data=f'bud_card|{period}'), InlineKeyboardButton('❌ Отмена', callback_data='settings_budgets')],
        ])
        return await _safe_edit_or_reply(q, f"Удалить бюджет?\n\n{'Месяц' if period=='month' else 'Неделя'} — {_fmt_money(amount or 0)}\n\nОперации, категории, напоминания и рабочие пространства не будут удалены.", reply_markup=kb)

    if data.startswith('bud_del_yes|'):
        period = data.split('|', 1)[1]
        if period == 'month':
            set_budget(cid, month=0)
        else:
            set_budget(cid, week=0)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 К бюджетам', callback_data='settings_budgets')],
            [InlineKeyboardButton('➕ Добавить бюджет', callback_data='bud_add')],
            [InlineKeyboardButton('⬅️ В настройки', callback_data='menu_settings')],
        ])
        return await _safe_edit_or_reply(q, '✅ Бюджет удалён', reply_markup=kb)

    if data.startswith('bud_replace_confirm|'):
        period = data.split('|', 1)[1]
        amount = to_decimal_money(context.user_data.get('budget_pending_amount', 0))
        if period == 'month':
            set_budget(cid, month=amount)
        else:
            set_budget(cid, week=amount)
        context.user_data.pop('budget_add_period', None)
        context.user_data.pop('budget_pending_amount', None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 К бюджетам', callback_data='settings_budgets')],
            [InlineKeyboardButton('➕ Добавить ещё', callback_data='bud_add')],
            [InlineKeyboardButton('⬅️ В настройки', callback_data='menu_settings')],
        ])
        return await _safe_edit_or_reply(q, f"✅ Бюджет добавлен\n\n{'Месяц' if period=='month' else 'Неделя'} — {_fmt_money(amount)}", reply_markup=kb)

    if data == 'menu_notifications':
        return await _render_notification_settings(q, cid, context)

    if data == 'notif_challenges':
        await q.answer()
        return await _render_challenge_notification_settings(q, cid)

    if data.startswith('notif_group|'):
        key = data.split('|', 1)[1]
        group_key = "daily_notifications" if key == "daily" else "plans_control" if key == "plans" else "reports"
        try:
            current = grouped_notification_preferences(cid)
            enabled = not bool((current.get(group_key) or {}).get("enabled"))
            set_grouped_notification_preference(cid, key, enabled)
        except Exception:
            return await q.answer('Настройка станет доступна после миграции.', show_alert=True)
        label = GROUPED_NOTIFICATION_LABELS.get(key, ("Оповещения", "", ""))[0]
        await q.answer(f'{label} {"включены" if enabled else "выключены"}')
        return await _render_notification_settings(q, cid, context)

    if data.startswith('notif_toggle|'):
        key = data.split('|', 1)[1]
        if key == 'challenges':
            await q.answer('Оповещения о челленджах больше не используются.', show_alert=True)
            return await _render_challenge_notification_settings(q, cid)
        try:
            enabled = toggle_notification_preference(cid, key)
        except Exception:
            return await q.answer('Настройка станет доступна после миграции.', show_alert=True)
        label = NOTIFICATION_TOGGLE_LABELS.get(key, ('Оповещения', '', '', ''))[0]
        await q.answer(f'{label} {"включены" if enabled else "выключены"}')
        if key == 'goals':
            track_product_event(ProductEvent(
                event_name="goal_notifications_enabled" if enabled else "goal_notifications_disabled",
                user_id=update.effective_user.id,
                status="success",
            ))
        return await _render_notification_settings(q, cid, context)

    if data == 'notif_quiet_hours':
        return await _render_quiet_hours(q, cid)

    if data == 'notif_times':
        await q.answer()
        return await _render_notification_times(q, cid)

    if data.startswith('notif_time|'):
        field = data.split('|', 1)[1]
        if field not in {'morning', 'evening'}:
            return await q.answer('Настройка недоступна', show_alert=True)
        context.user_data['await_daily_notification_time'] = {'field': field}
        await q.answer()
        label = 'утра' if field == 'morning' else 'вечера'
        return await q.message.reply_text(f'Введите время для {label} в формате HH:MM, например 08:30')

    if data in {'notif_tz', 'menu_tz'}:
        return await _render_notification_timezone(q, cid, back_dest='menu_settings' if data == 'menu_tz' else 'menu_notifications')

    if data.startswith('tz|'):
        parts = data.split('|', 2)
        action = parts[1] if len(parts) > 1 else ''
        try:
            if action == 'set' and len(parts) > 2:
                prefs = set_notification_timezone(cid, parts[2])
                skipped = suppress_stale_timezone_sensitive_notifications(cid)
                track_product_event(ProductEvent(
                    event_name="timezone_updated",
                    user_id=update.effective_user.id,
                    status="success",
                    properties={"destination": "notifications", "stale_notifications_suppressed": bool(skipped)},
                ))
                await q.answer('Часовой пояс сохранён')
                return await _render_notification_timezone(q, cid)
            if action == 'manual':
                context.user_data['await_timezone_name'] = True
                await q.answer()
                return await q.message.reply_text('Введите IANA-часовой пояс, например Europe/Moscow')
        except ValueError:
            return await q.answer('Введите корректный IANA-часовой пояс', show_alert=True)
        except Exception:
            return await q.answer('Настройка станет доступна после миграции.', show_alert=True)

    if data.startswith('quiet|'):
        parts = data.split('|')
        action = parts[1] if len(parts) > 1 else ''
        try:
            if action == 'toggle':
                enabled = toggle_quiet_hours(cid)
                track_product_event(ProductEvent(
                    event_name="quiet_hours_enabled",
                    user_id=update.effective_user.id,
                    status="success",
                    properties={"enabled": bool(enabled)},
                ))
                await q.answer('Тихие часы включены' if enabled else 'Тихие часы выключены')
                return await _render_quiet_hours(q, cid)
            if action in {'start', 'end'} and len(parts) > 2:
                set_quiet_hours_time(cid, action, parts[2])
                track_product_event(ProductEvent(
                    event_name="quiet_hours_updated",
                    user_id=update.effective_user.id,
                    status="success",
                    properties={"field": action},
                ))
                await q.answer('Время сохранено')
                return await _render_quiet_hours(q, cid)
            if action == 'manual' and len(parts) > 2:
                field = parts[2]
                if field not in {'start', 'end'}:
                    return await q.answer('Неверное поле', show_alert=True)
                context.user_data['await_quiet_hours_time'] = {'field': field}
                return await q.message.reply_text('Введите время в формате HH:MM')
        except ValueError:
            return await q.answer('Введите время в формате HH:MM', show_alert=True)
        except Exception:
            return await q.answer('Настройка станет доступна после миграции.', show_alert=True)

    if data == 'quick_sugg_off':
        set_quick_suggestions_enabled(cid, False)
        await q.answer('Быстрые записи выключены')
        q.data = 'menu_quick_suggestions'
        return await callback_handler(update, context)

    if data == 'receipt_cancel':
        context.user_data.pop('receipt_candidates', None)
        context.user_data.pop('receipt_warning', None)
        context.user_data.pop('receipt_review_idx', None)
        context.user_data.pop('receipt_saved_count', None)
        context.user_data.pop('receipt_skipped_count', None)
        await q.answer('Отменено')
        return await _safe_edit_or_reply(q, '❌ Импорт отменён.')

    if data == 'receipt_review_one':
        cands = context.user_data.get('receipt_candidates') or []
        if not cands:
            await q.answer('Нет данных для проверки', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций для проверки.')
        context.user_data['receipt_review_idx'] = 0
        context.user_data['receipt_saved_count'] = 0
        context.user_data['receipt_skipped_count'] = 0
        await q.answer()
        text, kb = _receipt_render_card(cands, 0)
        return await _safe_edit_or_reply(q, text, reply_markup=kb)

    if data == 'receipt_back_list':
        cands = context.user_data.get('receipt_candidates') or []
        if not cands:
            await q.answer('Нет данных', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций.')
        await q.answer()
        text, kb = _receipt_render_list(cands, context.user_data.get('receipt_warning'))
        return await _safe_edit_or_reply(q, text, reply_markup=kb)

    if data in {'receipt_save_one', 'receipt_skip_one'}:
        cands = context.user_data.get('receipt_candidates') or []
        idx = int(context.user_data.get('receipt_review_idx') or 0)
        if not cands or idx >= len(cands):
            await q.answer('Нет данных', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций для проверки.')
        cur = cands[idx]
        if data == 'receipt_save_one':
            try:
                dt = datetime.fromisoformat(cur.get('date') or '').date()
            except Exception:
                dt = date.today()
            amount = _integer_major_amount(cur.get('amount'))
            if amount is None:
                await q.answer('Дробные суммы пока не поддерживаются', show_alert=True)
            else:
                recorded = record_financial_operation(
                    chat_id=cid,
                    actor_user_id=update.effective_user.id,
                    op_date=dt,
                    op_type=cur.get('type') or 'Расходы',
                    category=cur.get('category') or 'Другое',
                    amount=amount,
                    comment=cur.get('merchant') or 'From image',
                    source='ocr',
                    chat_type=getattr(update.effective_chat, 'type', 'private') or 'private',
                    raw_text=cur.get('raw_text') or cur.get('merchant'),
                )
                await _send_standard_op_confirmation(context, cid, update.effective_user, dt, cur.get('type') or 'Расходы', cur.get('category') or 'Другое', amount, cur.get('merchant') or 'From image')
                await send_operation_limit_alert(recorded, context)
                log.info('receipt_review: saved index=%s user=%s', idx, cid)
                context.user_data['receipt_saved_count'] = int(context.user_data.get('receipt_saved_count') or 0) + 1
                await q.answer('Сохранено')
        else:
            log.info('receipt_review: skipped index=%s user=%s', idx, cid)
            context.user_data['receipt_skipped_count'] = int(context.user_data.get('receipt_skipped_count') or 0) + 1
            await q.answer('Пропущено')
        cands.pop(idx)
        context.user_data['receipt_candidates'] = cands
        if not cands:
            context.user_data.pop('receipt_candidates', None)
            context.user_data.pop('receipt_review_idx', None)
            saved = int(context.user_data.pop('receipt_saved_count', 0) or 0)
            skipped = int(context.user_data.pop('receipt_skipped_count', 0) or 0)
            return await _safe_edit_or_reply(q, f'✅ Готово: записано {saved}, пропущено {skipped}.')
        next_idx = min(idx, len(cands) - 1)
        context.user_data['receipt_review_idx'] = next_idx
        text, kb = _receipt_render_card(cands, next_idx)
        return await _safe_edit_or_reply(q, text, reply_markup=kb)

    if data in {'rcpt_amt', 'rcpt_cat', 'rcpt_date', 'rcpt_type', 'rcpt_comment', 'rcpt_back_card', 'rcpt_amt_m100', 'rcpt_amt_m50', 'rcpt_amt_p50', 'rcpt_amt_p100', 'rcpt_cat_zav', 'rcpt_cat_prod', 'rcpt_cat_trans', 'rcpt_cat_other', 'rcpt_date_today', 'rcpt_date_yday', 'rcpt_comment_clear'}:
        cands = context.user_data.get('receipt_candidates') or []
        idx = int(context.user_data.get('receipt_review_idx') or 0)
        if not cands or idx >= len(cands):
            await q.answer('Нет данных', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций для проверки.')
        cur = cands[idx]
        if data == 'rcpt_back_card':
            await q.answer()
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if data == 'rcpt_amt':
            await q.answer()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('-100', callback_data='rcpt_amt_m100'), InlineKeyboardButton('-50', callback_data='rcpt_amt_m50'),
                 InlineKeyboardButton('+50', callback_data='rcpt_amt_p50'), InlineKeyboardButton('+100', callback_data='rcpt_amt_p100')],
                [InlineKeyboardButton('✏️ Ввести сумму', callback_data='rcpt_amt_input')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='rcpt_back_card')],
            ])
            return await _safe_edit_or_reply(q, 'Корректировка суммы', reply_markup=kb)
        if data.startswith('rcpt_amt_') and data != 'rcpt_amt_input':
            delta = {'rcpt_amt_m100': -100, 'rcpt_amt_m50': -50, 'rcpt_amt_p50': 50, 'rcpt_amt_p100': 100}.get(data, 0)
            cur['amount'] = str(max(Decimal("1.00"), (_integer_major_amount(cur.get('amount')) or Decimal("1.00")) + Decimal(delta)))
            context.user_data['receipt_candidates'] = cands
            await q.answer('Сумма обновлена')
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if data == 'rcpt_cat':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Заведения', callback_data='rcpt_cat_zav'), InlineKeyboardButton('Продукты', callback_data='rcpt_cat_prod')],
                [InlineKeyboardButton('Транспорт', callback_data='rcpt_cat_trans'), InlineKeyboardButton('Прочее', callback_data='rcpt_cat_other')],
                [InlineKeyboardButton('✏️ Другая', callback_data='rcpt_cat_input')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='rcpt_back_card')],
            ])
            await q.answer()
            return await _safe_edit_or_reply(q, 'Выберите категорию', reply_markup=kb)
        if data.startswith('rcpt_cat_') and data not in {'rcpt_cat', 'rcpt_cat_input'}:
            cur['category'] = {'rcpt_cat_zav': 'Заведения', 'rcpt_cat_prod': 'Продукты', 'rcpt_cat_trans': 'Транспорт', 'rcpt_cat_other': 'Прочее'}.get(data, cur.get('category'))
            context.user_data['receipt_candidates'] = cands
            await q.answer('Категория обновлена')
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if data == 'rcpt_date':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Сегодня', callback_data='rcpt_date_today'), InlineKeyboardButton('Вчера', callback_data='rcpt_date_yday')],
                [InlineKeyboardButton('✏️ Ввести дату', callback_data='rcpt_date_input')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='rcpt_back_card')],
            ])
            await q.answer()
            return await _safe_edit_or_reply(q, 'Выберите дату', reply_markup=kb)
        if data == 'rcpt_date_today':
            cur['date'] = date.today().isoformat()
        if data == 'rcpt_date_yday':
            cur['date'] = (date.today() - timedelta(days=1)).isoformat()
        if data in {'rcpt_date_today', 'rcpt_date_yday'}:
            context.user_data['receipt_candidates'] = cands
            await q.answer('Дата обновлена')
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if data == 'rcpt_type':
            cur['type'] = 'Доходы' if (cur.get('type') != 'Доходы') else 'Расходы'
            context.user_data['receipt_candidates'] = cands
            await q.answer('Тип обновлён')
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)
        if data == 'rcpt_comment':
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✏️ Ввести комментарий', callback_data='rcpt_comment_input')], [InlineKeyboardButton('Очистить', callback_data='rcpt_comment_clear')], [InlineKeyboardButton('⬅️ Назад', callback_data='rcpt_back_card')]])
            await q.answer()
            return await _safe_edit_or_reply(q, 'Комментарий', reply_markup=kb)
        if data == 'rcpt_comment_clear':
            cur['merchant'] = ''
            context.user_data['receipt_candidates'] = cands
            await q.answer('Очищено')
            text, kb = _receipt_render_card(cands, idx)
            return await _safe_edit_or_reply(q, text, reply_markup=kb)

    if data in {'rcpt_amt_input', 'rcpt_cat_input', 'rcpt_date_input', 'rcpt_comment_input'}:
        idx = int(context.user_data.get('receipt_review_idx') or 0)
        context.user_data['receipt_edit_idx'] = idx
        context.user_data['await_receipt_edit_text'] = True
        context.user_data['receipt_edit_field'] = {'rcpt_amt_input': 'amount', 'rcpt_cat_input': 'category', 'rcpt_date_input': 'date', 'rcpt_comment_input': 'comment'}[data]
        await q.answer()
        prompts = {'amount': 'Введите сумму числом, например 392', 'category': 'Введите категорию', 'date': 'Введите дату (например 23.05.2026)', 'comment': 'Введите комментарий'}
        return await q.message.reply_text(prompts[context.user_data['receipt_edit_field']])

    if data == 'receipt_confirm_all':
        cands = context.user_data.get('receipt_candidates') or []
        if not cands:
            await q.answer('Нет данных для записи', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций для записи.')
        total = Decimal("0.00")
        written = 0
        skipped = 0
        for c in cands:
            try:
                dt = datetime.fromisoformat(c['date']).date()
            except Exception:
                dt = date.today()
            amount = _integer_major_amount(c.get('amount'))
            if amount is None:
                skipped += 1
                continue
            recorded = record_financial_operation(
                chat_id=cid,
                actor_user_id=update.effective_user.id,
                op_date=dt,
                op_type=c.get('type') or 'Расходы',
                category=c.get('category') or 'Другое',
                amount=amount,
                comment=c.get('merchant') or 'From image',
                source='ocr',
                chat_type=getattr(update.effective_chat, 'type', 'private') or 'private',
                raw_text=c.get('raw_text') or c.get('merchant'),
            )
            await _send_standard_op_confirmation(context, cid, update.effective_user, dt, c.get('type') or 'Расходы', c.get('category') or 'Другое', amount, c.get('merchant') or 'From image')
            await send_operation_limit_alert(recorded, context)
            total += amount
            written += 1
        log.info('receipt_confirm_all: inserted=%s user=%s', written, cid)
        context.user_data.pop('receipt_candidates', None)
        context.user_data.pop('receipt_warning', None)
        context.user_data.pop('receipt_review_idx', None)
        await q.answer('Готово')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('📊 Отчёт', callback_data='menu_report')],
            [InlineKeyboardButton('⬅️ Меню', callback_data='start_main')],
        ])
        return await _safe_edit_or_reply(q, f'✅ Готово: записано {written}, пропущено {skipped}. Сумма: {_fmt_money(total)}', reply_markup=kb)

    if data == 'notif_morning_on':
        set_smart_morning_limits_enabled(cid, True)
        await q.answer('Утренние лимиты включены')
        q.data = 'menu_notifications'
        return await callback_handler(update, context)

    if data == 'notif_morning_off':
        set_smart_morning_limits_enabled(cid, False)
        await q.answer('Утренние лимиты выключены')
        q.data = 'menu_notifications'
        return await callback_handler(update, context)

    # Валюта
    if data == 'menu_currency':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('RUB 🇷🇺', callback_data='set_curr|RUB')],
            [InlineKeyboardButton('USD 🇺🇸', callback_data='set_curr|USD'),
             InlineKeyboardButton('EUR 🇪🇺', callback_data='set_curr|EUR')],
            [InlineKeyboardButton('Другие…', callback_data='menu_currency_more')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text('Выберите валюту учёта:', reply_markup=kb)

    if data == 'menu_currency_more':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('BYN 🇧🇾', callback_data='set_curr|BYN')],
            [InlineKeyboardButton('KZT 🇰🇿', callback_data='set_curr|KZT')],
            [InlineKeyboardButton('UZS 🇺🇿', callback_data='set_curr|UZS')],
            [InlineKeyboardButton('TMT 🇹🇲', callback_data='set_curr|TMT')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_currency')],
        ])
        return await q.edit_message_text('Другие валюты:', reply_markup=kb)

    if data.startswith('set_curr|'):
        code = data.split('|', 1)[1]
        update_user_field(cid, 'currency', code)
        if context.user_data.get('onb'):
            context.user_data['onb'] = True
            kb2 = InlineKeyboardMarkup([
                [InlineKeyboardButton('19:00', callback_data='set_rem_hour|19'),
                 InlineKeyboardButton('20:00', callback_data='set_rem_hour|20'),
                 InlineKeyboardButton('21:00', callback_data='set_rem_hour|21')],
                [InlineKeyboardButton('Другое…', callback_data='set_rem_custom')],
                [InlineKeyboardButton('Пропустить', callback_data='onb_budget')],
            ])
            return await q.edit_message_text(
                f"✅ Валюта установлена: {code}\n\nКогда напоминать каждый день?",
                reply_markup=kb2
            )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await q.edit_message_text(f"✅ Валюта установлена: {code}", reply_markup=kb)

    # Напоминания
    if data == 'menu_reminder':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('19:00', callback_data='set_rem_hour|19'),
             InlineKeyboardButton('20:00', callback_data='set_rem_hour|20'),
             InlineKeyboardButton('21:00', callback_data='set_rem_hour|21')],
            [InlineKeyboardButton('Другое…', callback_data='set_rem_custom')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text('Когда напоминать каждый день?', reply_markup=kb)

    if data.startswith('set_rem_hour|'):
        hour = int(data.split('|', 1)[1])
        update_user_field(cid, 'reminder_hour', hour)
        if context.user_data.get('onb'):
            context.user_data['onb'] = True
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Установить недельный бюджет', callback_data='set_week')],
                [InlineKeyboardButton('Установить месячный бюджет', callback_data='set_month')],
                [InlineKeyboardButton('Пропустить', callback_data='onb_finish')],
            ])
            return await q.edit_message_text(
                f"✅ Напоминание в {hour:02d}:00\n\nНастроим бюджеты или пропустим?",
                reply_markup=kb
            )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await q.edit_message_text(f"✅ Напоминание в {hour:02d}:00", reply_markup=kb)

    if data == 'set_rem_custom':
        context.user_data['await_reminder_custom'] = True
        if context.user_data.get('onb'):
            context.user_data['onb'] = True
            await q.message.reply_text("Введите час (0–23), во сколько напоминать каждый день:")
            try:
                await q.delete_message()
            except Exception:
                pass
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_reminder')]])
        return await q.edit_message_text("Введите час (0–23), во сколько напоминать:", reply_markup=kb)

    # ──────────────────────────────────────────────────────────────────────────
    # Лимиты по категориям — ветки
    # ──────────────────────────────────────────────────────────────────────────

    # Limits UX v1
    if data == 'lim_list':
        try:
            await q.answer()
        except Exception:
            pass
        return await _lim_show_list(q, cid)

    if data.startswith('lim_open|'):
        period, category = _lim_parse_key(cid, data.split('|', 1)[1])
        if not period:
            return await _lim_show_list(q, cid)
        return await _lim_show_card(q, cid, period, category)

    if data.startswith('lim_edit_amount|'):
        token = data.split('|', 1)[1]
        period, category = _lim_parse_key(cid, token)
        if not period:
            return await _lim_show_list(q, cid)
        row = get_limit_by_key(cid, period, category)
        if not row:
            return await _lim_show_list(q, cid)
        context.user_data['lim_edit_amount'] = {'period': period, 'category': category}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data=f'lim_open|{token}')]])
        return await q.edit_message_text(
            f"Введи новую сумму для {_md_escape(category)} ({_lim_period_label(period)}):",
            parse_mode='Markdown',
            reply_markup=kb,
        )

    if data.startswith('lim_adj|'):
        try:
            _, token, op = data.split('|', 2)
        except ValueError:
            log.warning('lim_adj bad payload user=%s data=%s', cid, data)
            return await _lim_show_list(q, cid)
        period, category = _lim_parse_key(cid, token)
        if not period:
            log.info('lim_adj invalid token user=%s token=%s', cid, token)
            return await _lim_show_list(q, cid)
        delta_map = {'p500': 500, 'p1000': 1000, 'm500': -500, 'm1000': -1000}
        delta = delta_map.get(op)
        if delta is None:
            log.warning('lim_adj invalid op user=%s op=%s', cid, op)
            return await _lim_show_card(q, cid, period, category)
        res = adjust_limit_amount(cid, period, category, delta)
        if res.get('status') == 'too_small':
            return await q.answer('Лимит не может быть меньше 1 ₽', show_alert=True)
        if res.get('status') == 'too_big':
            return await q.answer('Слишком большой лимит', show_alert=True)
        if res.get('status') != 'ok':
            log.info('lim_adj not ok user=%s period=%s category=%s status=%s', cid, period, category, res.get('status'))
            return await _lim_show_list(q, cid)
        log.info('lim_adj ok user=%s period=%s category=%s delta=%s old=%s new=%s', cid, period, category, delta, res.get('old_amount'), res.get('new_amount'))
        await q.answer(f"Готово: {_fmt_money(res.get('new_amount', 0))}")
        return await _lim_show_card(q, cid, period, category)

    if data.startswith('lim_edit_period|'):
        rest = data.split('|', 1)[1]
        payload, new_period = rest.rsplit('|', 1)
        period, category = _lim_parse_key(cid, payload)
        if not period:
            return await _lim_show_list(q, cid)
        if period == new_period:
            return await _lim_show_card(q, cid, period, category, note='Период уже выбран.')
        try:
            res = update_limit_period(cid, period, category, new_period)
            log.info('edit_period user=%s cat=%s old=%s new=%s status=%s', cid, category, period, new_period, res.get('status'))
        except Exception:
            log.exception('edit_period failed user=%s cat=%s old=%s new=%s', cid, category, period, new_period)
            return await _lim_show_card(q, cid, period, category, note='Не смог сохранить, попробуй ещё раз.')

        if res.get('status') == 'ok':
            return await _lim_show_card(q, cid, new_period, category, note='✅ Период обновлён.')
        if res.get('status') == 'conflict':
            context.user_data['lim_conflict'] = {'old_period': period, 'new_period': new_period, 'category': category}
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('Заменить существующий', callback_data='lim_conflict_replace')],
                [InlineKeyboardButton('Отмена', callback_data=f'lim_open|{token}')],
            ])
            return await q.edit_message_text(
                f"У тебя уже есть лимит для категории *{_md_escape(category)}* на период *{_lim_period_label(new_period)}*. Что сделать?",
                parse_mode='Markdown', reply_markup=kb,
            )
        return await _lim_show_list(q, cid)

    if data == 'lim_conflict_replace':
        c = context.user_data.get('lim_conflict') or {}
        if not c:
            return await _lim_show_list(q, cid)
        try:
            res = resolve_limit_conflict_replace(cid, c.get('old_period','week'), c.get('new_period','month'), c.get('category',''))
            log.info('conflict_resolution user=%s cat=%s old=%s new=%s status=%s', cid, c.get('category'), c.get('old_period'), c.get('new_period'), res.get('status'))
        except Exception:
            log.exception('conflict_resolution failed user=%s data=%s', cid, c)
            return await _lim_show_list(q, cid)
        context.user_data.pop('lim_conflict', None)
        if res.get('status') == 'ok':
            return await _lim_show_card(q, cid, c.get('new_period','month'), c.get('category',''), note='✅ Лимит объединён и обновлён.')
        return await _lim_show_list(q, cid)

    if data.startswith('lim_del|'):
        token = data.split('|', 1)[1]
        period, category = _lim_parse_key(cid, token)
        row = get_limit_by_key(cid, period, category)
        if not row:
            return await _lim_show_list(q, cid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Да', callback_data=f'lim_del_yes|{token}'),
             InlineKeyboardButton('Нет', callback_data=f'lim_open|{token}')],
        ])
        return await q.edit_message_text(
            f"Удалить лимит {_md_escape(category)} / {_lim_period_label(period)} / {row['amount']} {row['currency']}?",
            parse_mode='Markdown',
            reply_markup=kb,
        )

    if data.startswith('lim_del_yes|'):
        period, category = _lim_parse_key(cid, data.split('|', 1)[1])
        if not period:
            return await _lim_show_list(q, cid)
        try:
            delete_limit_by_key(cid, period, category)
            log.info('delete_limit user=%s period=%s cat=%s', cid, period, category)
        except Exception:
            log.exception('delete_limit failed user=%s period=%s cat=%s', cid, period, category)
            return await _lim_show_list(q, cid)
        return await _lim_show_list(q, cid)

    if data == 'lim_mute_soon':
        return await q.answer('Скоро в Stage 2.5', show_alert=False)

    if data == 'cl_menu':
        return await _cl_show_menu(q)

    if data == 'cl_set':
        return await _cl_pick_period(q)

    if data.startswith('cl_pick|'):
        period = data.split('|', 1)[1]  # week/month
        context.user_data['cl_mode'] = True
        context.user_data['cl_period'] = period
        # открываем стандартный выбор категории (тип фиксируем: Расходы)
        p = context.user_data.setdefault('pending', {})
        p['type'] = 'Расходы'
        p['merch'] = ''
        return await prompt_category_menu(update, context)

    if data == 'cl_list':
        return await _cl_show_list(q, cid)

    if data.startswith('cl_edit|'):
        _, period, category = data.split('|', 2)
        context.user_data['cl_mode'] = True
        context.user_data['cl_period'] = period
        context.user_data['cl_category'] = category
        cur = get_user_currency(cid)
        pair = get_category_limit(cid, period, category)
        amount = pair[0] if pair else 0
        context.user_data['cl_amount'] = amount
        return await _cl_render_amount_screen(q, period, category, amount, cur)

    if data.startswith('cl_del|'):
        _, period, category = data.split('|', 2)
        delete_category_limit(cid, period, category)
        try:
            await q.answer('Удалено')
        except Exception:
            pass
        return await _cl_show_list(q, cid)

    if data.startswith('cl_adj|'):
        delta = int(data.split('|', 1)[1])
        amt = max(Decimal("0.00"), to_decimal_money(context.user_data.get('cl_amount', 0)) + Decimal(delta))
        context.user_data['cl_amount'] = amt
        period = context.user_data.get('cl_period', 'week')
        category = context.user_data.get('cl_category', '')
        return await _cl_render_amount_screen(q, period, category, amt, get_user_currency(cid))

    if data == 'cl_reset':
        context.user_data['cl_amount'] = 0
        period = context.user_data.get('cl_period', 'week')
        category = context.user_data.get('cl_category', '')
        return await _cl_render_amount_screen(q, period, category, 0, get_user_currency(cid))

    if data == 'cl_save':
        period = context.user_data.get('cl_period', 'week')
        category = context.user_data.get('cl_category', '')
        amount = to_decimal_money(context.user_data.get('cl_amount', 0))
        set_category_limit(cid, period, category, amount, get_user_currency(cid))
        # очистим режим
        context.user_data.pop('cl_mode', None)
        context.user_data.pop('cl_period', None)
        context.user_data.pop('cl_category', None)
        context.user_data.pop('cl_amount', None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ В меню лимитов', callback_data='cl_menu')]])
        return await q.edit_message_text('✅ Лимит сохранён.', reply_markup=kb)

    if data == 'cl_cancel':
        context.user_data.pop('cl_mode', None)
        context.user_data.pop('cl_period', None)
        context.user_data.pop('cl_category', None)
        context.user_data.pop('cl_amount', None)
        return await _cl_show_menu(q)

    # Ветки sugg_* — обрабатываются в другом роутере (как было)


    if data == 'ml_other':
        p = context.user_data.get('pending', {})
        p['from_ml_decline'] = True
        context.user_data['pending'] = p
        raw_text = context.user_data.get('batch_item_text') or p.get('merch', '')
        suggested_top2 = [{'cat': p.get('ml_cat1', ''), 'score': None}, {'cat': p.get('ml_cat2', ''), 'score': None}]
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=raw_text,
                normalized_text=normalize_for_ml(raw_text),
                detected_type=p.get('type') or 'Расходы',
                action='other_category',
                suggested_top2=suggested_top2,
                meta={'source': p.get('ml_source', 'baseline'), 'stage': '2.3', 'merchant': p.get('merch', ''), 'model_version': p.get('ml_model_version')},
            )
        except Exception:
            pass
        try:
            log_category_feedback(
                user_id=cid,
                chat_id=cid,
                raw_text=p.get('merch', ''),
                norm_text=p.get('merch', ''),
                suggested_cat=p.get('ml_cat1', ''),
                chosen_cat='',
                op_type=p.get('type') or 'Расходы',
                event_type='decline',
            )
        except Exception:
            pass
        return await prompt_category_menu(update, context, include_add_button=True)

    if data == 'ml_new_cat':
        p = context.user_data.get('pending', {})
        merch = p.get('merch') or 'операция'
        context.user_data['adding_category'] = True
        await q.answer()
        return await q.message.reply_text(
            f'Введите название новой категории для "{merch}":',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data='start_main')]]),
        )

    if data == 'ml_toggle_income':
        p = context.user_data.get('pending', {})
        merch = p.get('merch', 'операция')
        amt = to_decimal_money(p.get('amt', 0))
        curr_type = p.get('type') or 'Расходы'
        new_type = 'Доходы' if curr_type == 'Расходы' else 'Расходы'
        sign = '➕' if new_type == 'Доходы' else '➖'
        raw_text = context.user_data.get('batch_item_text') or merch
        top2, sugg_meta = get_top2_suggestions(cid, normalize_for_ml(raw_text), new_type)
        if len(top2) < 2:
            top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
        cat1, cat2 = top2[0]['cat'], top2[1]['cat']
        p.update({'type': new_type, 'ml_cat1': cat1, 'ml_cat2': cat2, 'ml_top2': top2, 'ml_source': sugg_meta.get('source', 'baseline'), 'ml_model_version': sugg_meta.get('model_version')})
        context.user_data['pending'] = p
        suggested_top2 = top2
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=raw_text,
                normalized_text=normalize_for_ml(raw_text),
                detected_type=new_type,
                action='toggle_type',
                suggested_top2=suggested_top2,
                chosen_type=new_type,
                meta={'source': sugg_meta.get('source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'suggest': sugg_meta, 'model_version': sugg_meta.get('model_version'), 'trained_at': sugg_meta.get('trained_at')},
            )
        except Exception:
            pass
        kb = ml_top2_kb(cat1, cat2)
        return await q.edit_message_text(f"Категория?\n{sign} {format_money_value(amt, get_user_currency(cid))} • {merch}", parse_mode='Markdown', reply_markup=kb)

    if data.startswith('ml_pick|'):
        cat = data.split('|', 1)[1]
        p = context.user_data.get('pending') or {}
        typ = p.get('type') or 'Расходы'
        merch = p.get('merch', 'операция')
        amt = p.get('amt', 0)
        dt = p.get('time', datetime.now())
        note = p.get('note')
        raw_text = context.user_data.get('batch_item_text') or merch
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=raw_text,
                normalized_text=normalize_for_ml(raw_text),
                detected_type=typ,
                action='pick_cat',
                chosen_category=cat,
                chosen_type=typ,
                suggested_top2=p.get('ml_top2') or [{'cat': p.get('ml_cat1', ''), 'score': None}, {'cat': p.get('ml_cat2', ''), 'score': None}],
                meta={'source': p.get('ml_source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'picked': cat, 'model_version': p.get('ml_model_version')},
            )
        except Exception:
            pass
        alias_norm = normalize_alias_text(raw_text or merch)
        record_category_confirmation(cid, raw_text or merch, alias_norm, cat, typ, 'accept')
        if p.get('from_ml_decline'):
            try:
                log_category_feedback(
                    user_id=cid,
                    chat_id=cid,
                    raw_text=merch,
                    norm_text=merch,
                    suggested_cat=p.get('ml_cat1', ''),
                    chosen_cat=cat,
                    op_type=typ,
                    event_type='decline',
                )
            except Exception:
                pass
        try:
            log_category_feedback(
                user_id=cid,
                chat_id=cid,
                raw_text=raw_text or merch,
                norm_text=normalize_alias_text(raw_text or merch),
                suggested_cat=cat,
                chosen_cat=cat,
                op_type=typ,
                event_type='accept',
            )
        except Exception:
            pass
        from services.records import record_operation
        return await record_operation(cat, amt, dt, typ, update, context, note)

    # Выбор типа/категории вручную И/ИЛИ в режиме лимитов
    if data.startswith('type|'):
        typ = data.split('|', 1)[1]
        context.user_data.setdefault('pending', {})['type'] = typ
        return await prompt_category_menu(update, context)

    if data == 'add_cat':
        p = context.user_data.get('pending', {})
        merch = p.get('merch') or 'операция'
        context.user_data['adding_category'] = True
        await q.answer()
        return await q.message.reply_text(
            f'Введите название новой категории для "{merch}":',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена', callback_data='start_main')]]),
        )

    if data.startswith('use_cat|'):
        # если находимся в мастере лимитов — переходим к набору суммы, НЕ пишем операцию
        if context.user_data.get('cl_mode'):
            cat = data.split('|', 1)[1]
            context.user_data['cl_category'] = cat
            cur = get_user_currency(cid)
            exist = get_category_limit(cid, context.user_data.get('cl_period', 'week'), cat)
            amt = exist[0] if exist else 0
            context.user_data['cl_amount'] = amt
            return await _cl_render_amount_screen(q, context.user_data.get('cl_period', 'week'), cat, amt, cur)

        # обычный поток записи операции
        cat = data.split('|', 1)[1]
        p = context.user_data.get('pending') or {}
        typ = p.get('type') or 'Расходы'
        merch = p.get('merch', 'операция')
        amt = p.get('amt', 0)
        dt = p.get('time', datetime.now())
        note = p.get('note')

        alias_norm = normalize_alias_text(context.user_data.get('batch_item_text') or merch)
        record_category_confirmation(cid, context.user_data.get('batch_item_text') or merch, alias_norm, cat, typ, 'accept')
        try:
            log_category_feedback(
                user_id=cid,
                chat_id=cid,
                raw_text=merch,
                norm_text=merch,
                suggested_cat=cat,
                chosen_cat=cat,
                op_type=typ,
                event_type='accept',
            )
        except Exception:
            pass

        if context.user_data.pop('edit_mode', False):
            edit_operation_id = p.get('edit_operation_id') or context.user_data.pop('edit_operation_id', None)
            context.user_data.pop('pending', None)
            row = (
                update_operation_fields_by_id(cid, int(edit_operation_id), category=cat, op_type=typ)
                if str(edit_operation_id or '').isdigit()
                else update_last_operation_fields(cid, category=cat, op_type=typ)
            )
            context.user_data.pop('edit_ctx', None)
            context.user_data.pop('edit_operation_id', None)
            if not row:
                return await q.message.reply_text('Не нашёл операцию для изменения.')
            await q.answer('Категория обновлена')
            return await q.message.reply_text('✅ Категория обновлена.')

        from services.records import record_operation
        return await record_operation(cat, amt, dt, typ, update, context, note)

    # Отчёты
    if data == 'menu_report':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('📅 Сегодня', callback_data='rep|today')],
            [InlineKeyboardButton('📆 Неделя', callback_data='rep|week')],
            [InlineKeyboardButton('⌛ 2 недели', callback_data='rep|2weeks')],
            [InlineKeyboardButton('🗓️ Месяц', callback_data='rep|month')],
            [InlineKeyboardButton('◀️ Назад', callback_data='start_main')],
        ])
        return await q.edit_message_text('📊 Выберите период:', reply_markup=kb)

    if data.startswith('rep|'):
        period = data.split('|', 1)[1]
        if period == 'week':
            track_product_event(ProductEvent(event_name="weekly_report_opened", user_id=update.effective_user.id, status="success"))
        elif period == 'month':
            track_product_event(ProductEvent(event_name="monthly_report_opened", user_id=update.effective_user.id, status="success"))
        txt = await build_report(period, str(cid))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_report')]])
        return await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=kb)

    if data.startswith('rep_export|'):
        try:
            _, kind, start_s, end_s = data.split('|', 3)
            dfrom = date.fromisoformat(start_s)
            dto = date.fromisoformat(end_s)
            if dto < dfrom or (dto - dfrom).days > 370:
                raise ValueError('bad_period')
        except Exception:
            await q.answer('Кнопка устарела', show_alert=True)
            return await _safe_edit_or_reply(q, 'Не удалось определить период отчёта. Откройте экспорт из главного меню.', reply_markup=_export_menu_kb())
        rows = _export_rows(cid, dfrom, dto)
        fd, p = tempfile.mkstemp(prefix='kopipaste_report_export_', suffix='.xlsx')
        os.close(fd)
        try:
            build_export_xlsx(p, rows, dfrom, dto)
            label = 'неделю' if kind == 'w' else 'месяц'
            fname = f'kopipaste_{kind}_{dfrom.isoformat()}_{dto.isoformat()}.xlsx'
            with open(p, 'rb') as f:
                await context.bot.send_document(
                    chat_id=cid,
                    document=f,
                    filename=fname,
                    caption=f'📤 Экспорт за {label}\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\nОпераций: {len(rows)}',
                )
            await q.answer('Готово')
            return await _safe_edit_or_reply(
                q,
                f'✅ Экспорт отчёта готов\n\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\nОпераций: {len(rows)}',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📤 Экспорт ещё раз', callback_data='exp_menu')], [InlineKeyboardButton('🏠 Главное меню', callback_data='start_main')]]),
            )
        except Exception as e:
            log.warning('report_export_failed reason=%s', type(e).__name__)
            await q.answer('Ошибка', show_alert=True)
            return await _safe_edit_or_reply(q, 'Не удалось сформировать экспорт отчёта. Попробуйте через Главное меню → Экспорт.', reply_markup=_export_menu_kb())
        finally:
            if os.path.exists(p):
                os.remove(p)

    if data in {'exp_today', 'exp_7', 'exp_14', 'exp_m', 'exp_pm', 'exp_y', 'exp_py', 'exp_custom', 'exp_custom_start_today', 'exp_custom_start_yday', 'exp_custom_start_first', 'exp_custom_start_input',
                'exp_custom_end_today', 'exp_custom_end_yday', 'exp_custom_end_month', 'exp_custom_end_input', 'exp_dl', 'exp_reset', 'exp_cancel'}:
        today = date.today()
        st = context.user_data.setdefault('export_state', {})
        if data == 'exp_today':
            clear_export_wait_flags(context.user_data)
            period = preset_period('today', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_7':
            clear_export_wait_flags(context.user_data)
            period = preset_period('7', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_14':
            clear_export_wait_flags(context.user_data)
            period = preset_period('14', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_m':
            clear_export_wait_flags(context.user_data)
            period = preset_period('month', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_pm':
            clear_export_wait_flags(context.user_data)
            period = preset_period('previous_month', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_y':
            clear_export_wait_flags(context.user_data)
            period = preset_period('year', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_py':
            clear_export_wait_flags(context.user_data)
            period = preset_period('previous_year', today); st['from'] = period.start.isoformat(); st['to'] = period.end.isoformat()
        elif data == 'exp_custom':
            context.user_data['export_state'] = {'mode': 'custom', 'step': 'start'}
            clear_export_wait_flags(context.user_data)
            await q.answer()
            return await _safe_edit_or_reply(q, 'Выбери начало периода или введи дату:', reply_markup=_export_start_kb())
        elif data.startswith('exp_custom_start_'):
            clear_export_wait_flags(context.user_data)
            if data == 'exp_custom_start_today': st['from'] = today.isoformat()
            elif data == 'exp_custom_start_yday': st['from'] = (today - timedelta(days=1)).isoformat()
            elif data == 'exp_custom_start_first': st['from'] = today.replace(day=1).isoformat()
            elif data == 'exp_custom_start_input':
                context.user_data['await_export_start'] = True
                st['mode'] = 'custom'; st['step'] = 'start'
                await q.answer()
                return await q.message.reply_text('Введи дату начала (DD.MM.YYYY, DD.MM или YYYY-MM-DD):', reply_markup=_export_start_kb())
            st['mode'] = 'custom'; st['step'] = 'end'
            await q.answer()
            return await _safe_edit_or_reply(q, 'Начало сохранено. Теперь выбери конец периода или введи дату:', reply_markup=_export_end_kb())
        elif data.startswith('exp_custom_end_'):
            context.user_data.pop('await_export_end', None)
            if data == 'exp_custom_end_today': st['to'] = today.isoformat()
            elif data == 'exp_custom_end_yday': st['to'] = (today - timedelta(days=1)).isoformat()
            elif data == 'exp_custom_end_month':
                nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
                st['to'] = (nxt - timedelta(days=1)).isoformat()
            elif data == 'exp_custom_end_input':
                context.user_data['await_export_end'] = True
                st['mode'] = 'custom'; st['step'] = 'end'
                await q.answer()
                return await q.message.reply_text('Введи дату конца (DD.MM.YYYY, DD.MM или YYYY-MM-DD):', reply_markup=_export_end_kb())
            st['step'] = 'confirm'
        elif data == 'exp_reset':
            q.data = 'exp_custom'
            return await callback_handler(update, context)
        elif data == 'exp_cancel':
            context.user_data.pop('export_state', None)
            clear_export_wait_flags(context.user_data)
            await q.answer('Отменено')
            return await _safe_edit_or_reply(q, '📤 Экспорт отменён.', reply_markup=_export_menu_kb())
        elif data == 'exp_dl':
            pass

        if data != 'exp_dl':
            await q.answer()
            if not export_state_has_period(context.user_data):
                clear_export_wait_flags(context.user_data)
                return await _safe_edit_or_reply(q, '📤 Сессия экспорта устарела. Выбери период заново.', reply_markup=_export_menu_kb())
            track_product_event(ProductEvent(event_name="export_started", user_id=update.effective_user.id, status="started", entity_type="export"))
            return await _export_preview(q, context, cid)

        if not export_state_has_period(context.user_data):
            clear_export_wait_flags(context.user_data)
            await q.answer('Сессия экспорта устарела', show_alert=True)
            return await _safe_edit_or_reply(q, '📤 Сессия экспорта устарела. Выбери период заново.', reply_markup=_export_menu_kb())
        dfrom = date.fromisoformat(st['from']); dto = date.fromisoformat(st['to'])
        fd, p = tempfile.mkstemp(prefix='kopipaste_export_', suffix='.xlsx')
        os.close(fd)
        try:
            build_export_xlsx(p, st.get('preview_rows') or [], dfrom, dto)
            fname = f'kopipaste_export_{dfrom.isoformat()}_{dto.isoformat()}.xlsx'
            with open(p, 'rb') as f:
                await context.bot.send_document(chat_id=cid, document=f, filename=fname, caption=f'📤 Экспорт готов\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\nОпераций: {st.get("count", 0)}')
            log.info('export_xlsx_generated rows=%s user_id=%s', st.get('count', 0), cid)
            track_product_event(ProductEvent(
                event_name="export_completed",
                user_id=update.effective_user.id,
                status="success",
                entity_type="export",
                properties={"row_count": int(st.get("count", 0)), "period_days": (dto - dfrom).days + 1},
            ))
            await q.answer('Готово')
            workspace = 'Личное пространство'
            context.user_data.pop('export_state', None)
            clear_export_wait_flags(context.user_data)
            return await _safe_edit_or_reply(
                q,
                f'✅ Экспорт завершён\n\nПериод: {dfrom.strftime("%d.%m.%Y")}–{dto.strftime("%d.%m.%Y")}\n'
                f'Операций: {st.get("count", 0)}\nПространство: {workspace}',
                reply_markup=_export_done_kb(),
            )
        except Exception as e:
            log.warning('export_send_failed reason=%s user_id=%s', type(e).__name__, cid)
            track_product_event(ProductEvent(
                event_name="export_failed",
                user_id=update.effective_user.id,
                status="failed",
                entity_type="export",
                properties={"error_code": type(e).__name__.lower()},
            ))
            await q.answer('Ошибка', show_alert=True)
        finally:
            if os.path.exists(p):
                os.remove(p)
        return

    if data == 'exp_menu':
        clear_export_wait_flags(context.user_data)
        return await render_export_menu(q)

    if data.startswith('rem_tog|'):
        rid = int(data.split('|', 1)[1])
        try:
            toggle_shared_reminder(cid, rid)
        except ReminderError:
            return await q.answer('Не найдено', show_alert=True)
        await q.answer('Обновлено')
        q.data = f'rem_o|{rid}'
        return await callback_handler(update, context)
    if data.startswith('rem_delq|'):
        rid = int(data.split('|', 1)[1]); r = reminder_get(cid, rid)
        title = (r['title'] if r else 'напоминание')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🗑 Да, удалить', callback_data=f'rem_del|{rid}')], [InlineKeyboardButton('⬅️ Назад', callback_data=f'rem_o|{rid}')]])
        return await _safe_edit_or_reply(q, f'Удалить напоминание «{title}»?', reply_markup=kb)
    if data.startswith('rem_del|'):
        rid = int(data.split('|', 1)[1])
        delete_shared_reminder(cid, rid)
        await q.answer('Удалено')
        q.data = 'rem_menu'
        return await callback_handler(update, context)
    if data.startswith('rem_snz|'):
        rid = int(data.split('|', 1)[1])
        try:
            snooze_shared_reminder(cid, rid, days=1)
        except ReminderError:
            return await q.answer('Не найдено', show_alert=True)
        await q.answer('Напомню завтра')
        return
    if data.startswith('rem_rec|'):
        rid = int(data.split('|', 1)[1])
        chat_type = getattr(update.effective_chat, 'type', 'private') or 'private'
        try:
            ctx = resolve_workspace(cid, update.effective_user.id, chat_type)
            result = record_shared_reminder(user_id=cid, reminder_id=rid, workspace=ctx, chat_type=chat_type, post_commit=True)
        except ReminderError as exc:
            if exc.code == 'reminder_inactive':
                return await q.answer('Напоминание выключено', show_alert=True)
            return await q.answer('Не найдено', show_alert=True)
        if result.operation:
            recorded = result.operation
            await _send_standard_op_confirmation(context, cid, update.effective_user, recorded.operation_date, recorded.type, recorded.category, recorded.amount, recorded.comment, currency=recorded.currency)
            await send_operation_limit_alert(recorded, context)
            log.info('reminder_recorded reminder_id=%s', rid)
        await q.answer('Записано')
        return
    if any(data.startswith(p) for p in ['rem_e_amt|', 'rem_e_cat|', 'rem_e_date|', 'rem_e_rep|', 'rem_e_not|', 'rem_e_typ|']):
        k, rid = data.split('|', 1); rid = int(rid)
        field = {'rem_e_amt': 'amount', 'rem_e_cat': 'category', 'rem_e_date': 'date', 'rem_e_rep': 'repeat', 'rem_e_not': 'notify', 'rem_e_typ': 'type'}[k]
        context.user_data['await_rem_edit'] = {'rid': rid, 'field': field}
        prompts = {'amount': 'Введите сумму', 'category': 'Введите категорию', 'date': 'Введите дату (24.05.2026)', 'repeat': 'Введите: none|weekly|monthly|yearly|custom_days:14', 'notify': 'Введите дней до напоминания (0..30)', 'type': 'Введите тип: Расходы или Доходы'}
        await q.answer()
        return await q.message.reply_text(prompts[field])

    # Аналитика (как было)
    if data == 'menu_analytics':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💸 Расходы', callback_data='an|Расходы')],
            [InlineKeyboardButton('💰 Доходы', callback_data='an|Доходы')],
            [InlineKeyboardButton('📈 Инвестиции', callback_data='an|Инвестиции')],
            [InlineKeyboardButton('💾 Сбережения', callback_data='an|Сбережения')],
            [InlineKeyboardButton('◀️ Назад', callback_data='start_main')],
        ])
        return await q.edit_message_text('📈 Аналитика – выберите раздел:', reply_markup=kb)

    if data.startswith('an|'):
        section = data.split('|', 1)[1]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Неделя', callback_data=f'an_{section}|week')],
            [InlineKeyboardButton('Месяц', callback_data=f'an_{section}|month')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_analytics')],
        ])
        return await q.edit_message_text(f'📈 Аналитика «{section}»: период?', reply_markup=kb)

    # Удаление последней записи
    if data == 'del_last':
        delete_last_operation(cid)
        track_product_event(ProductEvent(event_name="operation_deleted", user_id=update.effective_user.id, status="success", entity_type="operation"))
        return await q.edit_message_text('🟦 Последняя запись этого чата удалена.')

    # Короткие статусы
    if data == 'status':
        today = date.today()
        start = today - timedelta(days=today.weekday())
        rows = pg_fetchall(
            """
            SELECT SUM(amount) FROM public.operations
             WHERE chat_id=%s AND type='Расходы' AND op_date BETWEEN %s AND %s
            """,
            (cid, start, today)
        )
        spent = rows[0][0] or 0
        wl, _ = get_user_budgets(cid)
        return await q.message.reply_text(
            f'💰 Остаток недели: {max((wl or 0) - spent, 0)} {get_user_currency(cid)}'
        )

    if data == 'income_status':
        today = date.today()
        first = today.replace(day=1)
        rows = pg_fetchall(
            """
            SELECT SUM(amount) FROM public.operations
             WHERE chat_id=%s AND type='Доходы' AND op_date BETWEEN %s AND %s
            """,
            (cid, first, today)
        )
        inc = rows[0][0] or 0
        return await q.message.reply_text(f'💵 Доходы за месяц: {inc} {get_user_currency(cid)}')

    if data == 'inv_status':
        today = date.today()
        first = today.replace(day=1)
        rows = pg_fetchall(
            """
            SELECT SUM(amount) FROM public.operations
             WHERE chat_id=%s AND type='Инвестиции' AND op_date BETWEEN %s AND %s
            """,
            (cid, first, today)
        )
        inv = rows[0][0] or 0
        return await q.message.reply_text(f'📊 Инвестировано за месяц: {inv} {get_user_currency(cid)}')

    if data.startswith('goal_status|'):
        await q.answer()
        return await _render_goals_home(q, context, update)

    log.warning(
        'unknown_callback_data callback_data=%s chat_type=%s',
        data,
        getattr(update.effective_chat, 'type', None),
    )
    track_security_event(SecurityEvent(
        event_name="unknown_callback",
        user_id=update.effective_user.id if update.effective_user else None,
        chat_type=getattr(update.effective_chat, 'type', None),
        rule_key="callback_handler",
        action_taken="main_menu_fallback",
        metadata={"callback_prefix": (data or "").split("|", 1)[0][:32]},
    ))
    try:
        await q.answer('Кнопка устарела. Открываю главное меню.', show_alert=False)
    except Exception:
        pass
    return await render_main_menu(q, cid, context)


def legacy_main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🧾 Примеры', callback_data='menu_examples'),
         InlineKeyboardButton('🆘 Поддержка', callback_data='menu_support')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='menu_settings'),
         InlineKeyboardButton('📊 Отчёты', callback_data='menu_report')],
        [InlineKeyboardButton('📈 Аналитика', callback_data='menu_analytics')],
    ])


def _parse_limit_step(data: str) -> int:
    # ожидаем 'limit:add:<int>'
    try:
        if not data.startswith('limit:add:'):
            return 0
        return int(data.split(':', 2)[2])
    except Exception:
        return 0


from telegram.ext import CallbackQueryHandler

def on_limit_adjust(update, context):
    cq = update.callback_query
    data = cq.data or ''
    step = 0
    try:
        if data.startswith('limit:add:'):
            step = int(data.split(':',2)[2])
    except Exception:
        step = 0
    if step == 0:
        cq.answer()  # неизвестный шаг
        return

    # Ниже должна быть ваша существующая логика чтения draft/лимита,
    # прибавления step и перерисовки клавиатуры.
    # Мы просто шлём дальше в вашу функцию 'apply_limit_step'
    try:
        return apply_limit_step(update, context, step)
    except NameError:
        # если в проекте другая функция — оставим мягко
        pass
    cq.answer()

# Регистрация хэндлера (если нет)
try:
    register_handler(CallbackQueryHandler(on_limit_adjust, pattern=r'^limit:add:-?\d+$'))
except Exception:
    pass
