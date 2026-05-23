import logging
import hashlib
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.error import BadRequest
from db.database import get_conn, pg_fetchall
from datetime import datetime, timedelta, date
from telegram.ext import ContextTypes
from db.queries import (
    upsert_user_alias, update_user_field, set_budget,
    get_user_currency, get_user_budgets, delete_last_operation,
    set_category_limit, get_category_limit, list_category_limits, delete_category_limit,
    get_user_tz, log_category_feedback, insert_ml_observation,
    list_user_limits, get_limit_by_key, update_limit_amount, update_limit_period,
    resolve_limit_conflict_replace, delete_limit_by_key,
    get_smart_morning_limits_enabled, set_smart_morning_limits_enabled,
    get_limit_spent, adjust_limit_amount, record_category_confirmation
)
from routers.helpers import prompt_type_menu, prompt_category_menu
from ui.keyboards import ml_top2_kb
from services.analytics import build_report
from services.ml_prep import normalize_for_ml, normalize_alias_text
from services.ml_suggest import get_top2_suggestions
from db.queries import insert_operation

log = logging.getLogger(__name__)


def _fmt_money(v: int) -> str:
    return f"{int(v):,}".replace(',', ' ') + ' ₽'


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
    wl, ml = get_user_budgets(user_id)
    active_limits = len(list_user_limits(user_id))
    if not wl and not ml:
        return (
            '💰 Бюджеты\n\n'
            'Общий бюджет пока не задан.\n\n'
            'Бюджет помогает понять, сколько можно безопасно тратить за неделю или месяц.'
        )
    lines = ['💰 Бюджеты', '', 'Общий бюджет:']
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
        rows += [[InlineKeyboardButton('✏️ Изменить', callback_data='bud_edit')], [InlineKeyboardButton('🗑 Удалить', callback_data='bud_del')]]
    rows += [[InlineKeyboardButton('📂 Лимиты категорий', callback_data='lim_list')], [InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')]]
    return InlineKeyboardMarkup(rows)

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
            return await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        log.warning('limits_ui bad request: %s', e)
        raise
def _cl_period_label(p: str) -> str:
    return "неделя" if p == "week" else "месяц"

def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")

async def _cl_show_menu(q):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Установить лимит', callback_data='cl_set')],
        [InlineKeyboardButton('📋 Мои лимиты', callback_data='cl_list')],
        [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
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


def _fmt_money(v: int) -> str:
    return f"{int(v):,}".replace(',', ' ') + ' ₽'


async def _lim_show_list(q, user_id: int):
    rows = list_user_limits(user_id)
    log.info('list_limits user=%s count=%s', user_id, len(rows))
    if not rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Добавить лимит', callback_data='cl_set')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')],
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
    btns.append([InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')])
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
    remaining = int(row['amount']) - int(spent)
    text = (
        f"*{_md_escape(row['category'])}*\n"
        f"{_lim_period_label(row['period'])}\n"
        f"Лимит: {_fmt_money(int(row['amount']))}\n"
        f"Потрачено: {_fmt_money(int(spent))}\n"
        f"Осталось: {_fmt_money(int(remaining))}"
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

    def _fetch_last_op(chat_id):
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

    last = _fetch_last_op(cid) if cid is not None else None

    if data == 'op_edit':
        if not last:
            try:
                await q.answer('Нет последней записи для изменения', show_alert=True)
            except Exception:
                pass
            return
        context.user_data['edit_ctx'] = last
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('📂 Изменить категорию', callback_data='op_edit_cat')],
            [InlineKeyboardButton('◀️ Назад', callback_data='op_edit_back')],
        ])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text('Что изменить?', reply_markup=kb)
        return

    if data == 'op_edit_cat':
        if not last:
            try:
                await q.answer('Нет последней записи', show_alert=True)
            except Exception:
                pass
            return
        p = context.user_data.setdefault('pending', {})
        p['amt'] = last['amount']
        try:
            from datetime import datetime as _dt
            p['time'] = _dt.combine(last['op_date'], _dt.min.time())
        except Exception:
            p['time'] = datetime.now()
        p['note'] = None
        p['merch'] = last['category']
        context.user_data['edit_mode'] = True
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
            InlineKeyboardButton('✏️ Изменить', callback_data='op_edit'),
        ]])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text('Готово.', reply_markup=kb)
        context.user_data.pop('edit_mode', None)
        return

# ──────────────────────────────────────────────────────────────────────────────
# Главный callback-роутер
# ──────────────────────────────────────────────────────────────────────────────
async def callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    # === NOOP ("Без операций сегодня") ===
    if data == 'noop_today':
        cid = update.effective_chat.id
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COALESCE(tz_offset_min,180) FROM public.users WHERE user_id=%s", (cid,))
        row = cur.fetchone(); tz = int(row[0]) if row and row[0] is not None else 0
        local_today = (datetime.utcnow() + timedelta(minutes=tz)).date()
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
        cur.execute("SELECT COALESCE(tz_offset_min,180) FROM public.users WHERE user_id=%s", (cid,))
        row = cur.fetchone(); tz = int(row[0]) if row and row[0] is not None else 0
        local_today = (datetime.utcnow() + timedelta(minutes=tz)).date()
        cur.execute("DELETE FROM public.operations WHERE chat_id=%s AND op_date=%s AND type='noop'", (cid, local_today))
        conn.commit(); cur.close(); conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Отметку удалил. Если передумаешь — нажми ниже.', reply_markup=kb)
        return

    if data == 'noop_back':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Ок! Можешь отметить отсутствие операций позже.', reply_markup=kb)
        return

    cid = q.message.chat.id
    await q.answer()

    # inline-edit подменю
    if data and data.startswith('op_edit'):
        return await _op_edit_router(update, context)

    # Главное меню
    if data in ('start_main', 'back_main'):
        return await q.edit_message_text('🔷 Главное меню:', reply_markup=main_menu_kb())

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
        txt = (
            "Готово! Можете сразу писать мне операции, например:\n"
            "• молоко 150\n• пицца 450 вчера\n• зарплата 50000\n\n"
            "Если что — /settings."
        )
        return await q.edit_message_text(txt, reply_markup=main_menu_kb())

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

    # Настройки
    if data == 'menu_settings':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💱 Валюта', callback_data='menu_currency'),
             InlineKeyboardButton('⏰ Напоминание', callback_data='menu_reminder')],
            [InlineKeyboardButton('🔔 Оповещения', callback_data='menu_notifications')],
            [InlineKeyboardButton('💰 Бюджеты', callback_data='settings_budgets')],
            [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
            [InlineKeyboardButton('📉 Лимиты по категориям', callback_data='cl_menu')],
            [InlineKeyboardButton('◀️ Назад', callback_data='start_main')],
        ])
        return await q.edit_message_text('⚙️ Настройки:', reply_markup=kb)

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
        rem = int(amount) - int(spent)
        text = (
            f"💰 Бюджет: {'месяц' if period=='month' else 'неделя'}\n\n"
            f"Лимит: {_fmt_money(int(amount))}\n"
            f"Потрачено: {_fmt_money(int(spent))}\n"
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
        if period == 'month':
            set_budget(cid, month=new)
        else:
            set_budget(cid, week=new)
        await q.answer(f'Готово: {_fmt_money(new)}')
        q.data = f'bud_card|{period}'
        return await callback_handler(update, context)

    if data.startswith('bud_set_manual|'):
        period = data.split('|', 1)[1]
        context.user_data['budget_manual_period'] = period
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data=f'bud_card|{period}')]])
        return await _safe_edit_or_reply(q, 'Введи новую сумму бюджета.', reply_markup=kb)

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
            [InlineKeyboardButton('⬅️ Отмена', callback_data=f'bud_card|{period}')],
        ])
        return await _safe_edit_or_reply(q, f"Удалить бюджет?\n\n{'Месяц' if period=='month' else 'Неделя'} — {_fmt_money(int(amount or 0))}", reply_markup=kb)

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
        amount = int(context.user_data.get('budget_pending_amount', 0))
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
        morning_enabled = get_smart_morning_limits_enabled(cid)
        morning_row = '🌤 Утро: включено' if morning_enabled else '🌤 Утро: выключено'
        toggle_btn = '⛔ Выключить утро' if morning_enabled else '🌤 Включить утро'
        toggle_cb = 'notif_morning_off' if morning_enabled else 'notif_morning_on'
        text = (
            '🔔 Оповещения\n\n'
            '🌙 Вечер: включено\n'
            f'{morning_row}\n'
            '📊 Отчёты: включены\n\n'
            'Утро — только когда по лимитам есть полезный сигнал.'
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_btn, callback_data=toggle_cb)],
            [InlineKeyboardButton('⬅️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data == 'quick_sugg_off':
        set_quick_suggestions_enabled(cid, False)
        await q.answer('Быстрые записи выключены')
        q.data = 'menu_quick_suggestions'
        return await callback_handler(update, context)

    if data == 'receipt_cancel':
        context.user_data.pop('receipt_candidates', None)
        await q.answer('Отменено')
        return await _safe_edit_or_reply(q, '❌ Импорт отменён.')

    if data == 'receipt_review_one':
        await q.answer('Режим по одной будет добавлен следующим шагом')
        return await _safe_edit_or_reply(q, '✏️ Проверка по одной скоро появится. Сейчас можно выбрать «Записать всё» или «Отмена».')

    if data == 'receipt_confirm_all':
        cands = context.user_data.get('receipt_candidates') or []
        if not cands:
            await q.answer('Нет данных для записи', show_alert=True)
            return await _safe_edit_or_reply(q, 'Нет подготовленных операций для записи.')
        total = 0
        written = 0
        for c in cands:
            try:
                dt = datetime.fromisoformat(c['date']).date()
            except Exception:
                dt = date.today()
            amount = int(c.get('amount') or 0)
            if amount <= 0:
                continue
            insert_operation(cid, dt, c.get('type') or 'Расходы', c.get('category') or 'Другое', amount, c.get('merchant') or 'From image')
            total += amount
            written += 1
        context.user_data.pop('receipt_candidates', None)
        await q.answer('Готово')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('📊 Отчёт', callback_data='menu_report')],
            [InlineKeyboardButton('⬅️ Меню', callback_data='start_main')],
        ])
        return await _safe_edit_or_reply(q, f'✅ Записал {written} операции на сумму {total} ₽', reply_markup=kb)

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
            [InlineKeyboardButton('TMT 🇹🇲', callback_data='TMT')],
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

    # Часовой пояс
    if data == 'menu_tz':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Определить по месту 📍', callback_data='tz_detect')],
            [InlineKeyboardButton('Выбрать вручную', callback_data='tz_manual')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text('Выбор часового пояса:', reply_markup=kb)

    if data == 'tz_manual':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('UTC+2', callback_data='tz_set|120'),
             InlineKeyboardButton('UTC+3 (МСК)', callback_data='tz_set|180'),
             InlineKeyboardButton('UTC+4', callback_data='tz_set|240')],
            [InlineKeyboardButton('UTC+5', callback_data='tz_set|300'),
             InlineKeyboardButton('UTC+6', callback_data='tz_set|360'),
             InlineKeyboardButton('UTC+7', callback_data='tz_set|420')],
            [InlineKeyboardButton('UTC+8', callback_data='tz_set|480'),
             InlineKeyboardButton('UTC+9', callback_data='tz_set|540')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_tz')],
        ])
        return await q.edit_message_text('Выберите UTC-смещение:', reply_markup=kb)

    if data.startswith('tz_set|'):
        off = int(data.split('|', 1)[1])
        update_user_field(cid, 'tz_offset_min', off)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await q.edit_message_text(f"✅ Часовой пояс установлен: UTC{off//60:+d}", reply_markup=kb)

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
        await q.answer(f"Готово: {_fmt_money(int(res.get('new_amount', 0)))}")
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
        amt = int(context.user_data.get('cl_amount', 0)) + delta
        amt = max(0, amt)
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
        amount = int(context.user_data.get('cl_amount', 0))
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
        return await prompt_category_menu(update, context, include_add_button=False)

    if data == 'ml_toggle_income':
        p = context.user_data.get('pending', {})
        merch = p.get('merch', 'операция')
        amt = int(p.get('amt', 0) or 0)
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
        return await q.edit_message_text(f"Категория?\n{sign} {amt} ₽ • {merch}", parse_mode='Markdown', reply_markup=kb)

    if data.startswith('ml_pick|'):
        cat = data.split('|', 1)[1]
        p = context.user_data.pop('pending', {})
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
        return await q.edit_message_text(f'Введите название новой категории для "{merch}":')

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
        p = context.user_data.pop('pending', {})
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

        from services.records import record_operation
        if context.user_data.pop('edit_mode', False):
            try:
                delete_last_operation(cid)
            except Exception:
                pass
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
        txt = await build_report(period, str(cid))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_report')]])
        return await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=kb)

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
        cat = data.split('|', 1)[1]
        try:
            rows = pg_fetchall(
                """
                SELECT COALESCE(target,0) FROM public.goals
                 WHERE user_id=%s AND category=%s
                 LIMIT 1
                """,
                (cid, cat)
            )
            target = rows[0][0] if rows else 0
        except Exception:
            target = 0
        saved_rows = pg_fetchall(
            """
            SELECT COALESCE(SUM(amount),0) FROM public.operations
             WHERE chat_id=%s AND (type='Сбережения' OR type='Цель') AND category=%s
            """,
            (cid, cat)
        )
        saved = saved_rows[0][0] if saved_rows else 0
        pct = int(saved / target * 100) if target else 0
        bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
        remain = max(target - saved, 0)
        txt = (f"🎯 Цель «{cat}»\n"
               f"Накоплено: {saved}/{target} ({pct}%)\n"
               f"[{bar}]\n"
               f"Осталось: {remain} {get_user_currency(cid)}")
        return await q.message.reply_text(txt)

def main_menu_kb():
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
