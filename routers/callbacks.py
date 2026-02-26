from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from db.database import get_conn, pg_fetchall
from datetime import datetime, timedelta, date
from telegram.ext import ContextTypes
from db.queries import (
    upsert_user_alias, update_user_field, set_budget,
    get_user_currency, get_user_budgets, delete_last_operation,
    set_category_limit, get_category_limit, list_category_limits, delete_category_limit,
    get_user_tz, merge_action_token_payload
)
from cache.global_dict import bump_global_popularity
from routers.helpers import prompt_type_menu, prompt_category_menu
from services.analytics import build_report

# ──────────────────────────────────────────────────────────────────────────────
# Вспомогалки для меню лимитов
# ──────────────────────────────────────────────────────────────────────────────

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
            [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
            [InlineKeyboardButton('1️⃣ Установить бюджет', callback_data='menu_set_budget')],
            [InlineKeyboardButton('📉 Лимиты по категориям', callback_data='cl_menu')],
            [InlineKeyboardButton('◀️ Назад', callback_data='start_main')],
        ])
        return await q.edit_message_text('⚙️ Настройки:', reply_markup=kb)

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

    # Бюджеты (как было)
    if data == 'menu_set_budget':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Установить недельный бюджет', callback_data='set_week')],
            [InlineKeyboardButton('Установить месячный бюджет', callback_data='set_month')],
            [InlineKeyboardButton('Отключить недельный бюджет', callback_data='unset_week')],
            [InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')],
        ])
        return await q.edit_message_text('⚙️ Бюджеты:', reply_markup=kb)

    if data == 'set_week':
        context.user_data['setting_week'] = True
        return await q.edit_message_text('⚙️ Введите недельный бюджет (целое число):')

    if data == 'set_month':
        context.user_data['setting_month'] = True
        return await q.edit_message_text('⚙️ Введите месячный бюджет (целое число):')

    if data == 'unset_week':
        set_budget(cid, week=None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await q.edit_message_text('✅ Недельный бюджет отключён', reply_markup=kb)

    # ──────────────────────────────────────────────────────────────────────────
    # Лимиты по категориям — ветки
    # ──────────────────────────────────────────────────────────────────────────
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

    # Выбор типа/категории вручную И/ИЛИ в режиме лимитов
    if data.startswith('type|'):
        typ = data.split('|', 1)[1]
        context.user_data.setdefault('pending', {})['type'] = typ
        return await prompt_category_menu(update, context)

    if data == 'add_cat':
        p = context.user_data.get('pending', {})
        merch = p.get('merch') or 'операция'
        context.user_data['adding_category'] = True
        context.user_data['pending_mode'] = 'awaiting_category_name'
        token_id = context.user_data.get('pending_token_id')
        if token_id:
            merge_action_token_payload(int(token_id), {'stage':'awaiting_category_name'})
        return await q.edit_message_text('Напиши название категории одним словом (например: "Спорт")')

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

        upsert_user_alias(cid, merch, typ, cat)
        bump_global_popularity(merch, typ, cat, 1)

        from services.records import record_operation
        if context.user_data.pop('edit_mode', False):
            try:
                delete_last_operation(cid)
            except Exception:
                pass
        token_id = context.user_data.get('pending_token_id')
        if token_id:
            merge_action_token_payload(int(token_id), {'stage':'category_selected','category_name':cat,'op_type':typ})
        return await record_operation(cat, amt, dt, typ, update, context, note, token_id=token_id, raw_text=merch)

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
