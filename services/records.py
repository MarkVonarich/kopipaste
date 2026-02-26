from utils.text import fmt_limit_warn
# services/records.py — v2025.08.30-limits
__version__ = "2025.08.30-limits"

from typing import List, Tuple, Optional, Dict
import logging
from rapidfuzz import process

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from db.queries import (
    list_user_aliases, upsert_user_alias, insert_operation,
    get_user_budgets, get_user_currency, get_user_tz
)
from cache.global_dict import bump_global_popularity, global_suggestions
from utils.text import norm_text, format_date_ru_with_weekday
from db.database import get_conn, pg_fetchall, pg_exec

log = logging.getLogger("finbot.records")


def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _is_bot_hint(s: str) -> bool:
    ss = (s or "").lower()
    return ("думаю" in ss) or ("выберите катег" in ss) or ("поменяйте тип" in ss)


def get_user_alias(user_id: int, merch: str) -> Optional[Tuple[str, str]]:
    merch_n = norm_text(merch)
    rows = list_user_aliases(user_id)
    if not rows:
        return None
    by_key: Dict[str, Tuple[str, str]] = {r[0]: (r[1], r[2]) for r in rows}
    if merch_n in by_key:
        return by_key[merch_n]
    best = process.extractOne(merch_n, list(by_key.keys()), score_cutoff=85)
    if best:
        return by_key[best[0]]
    return None


def guess_type_from_pairs(pairs: List[Tuple[str, str]]) -> str:
    score: Dict[str, int] = {}
    for _, t in pairs:
        score[t] = score.get(t, 0) + 1
    if not score:
        return "Расходы"
    return max(score, key=lambda k: (score[k], 1 if k == "Расходы" else 0))


def list_categories_for_type(user_id: int, typ: str) -> List[str]:
    from db.database import pg_fetchall
    cats: List[str] = []
    rows1 = pg_fetchall("""
        SELECT category, COUNT(*) c
          FROM public.operations
         WHERE chat_id=%s AND type=%s
         GROUP BY category
         ORDER BY c DESC
         LIMIT 12
    """, (user_id, typ))
    cats.extend([r[0] for r in rows1])
    rows2 = pg_fetchall("""
        SELECT category, SUM(COALESCE(popularity,0)) s
          FROM public.global_aliases
         WHERE type=%s
         GROUP BY category
         ORDER BY s DESC
         LIMIT 12
    """, (typ,))
    for c, _ in rows2:
        if c not in cats:
            cats.append(c)
    defaults = {
        "Расходы": ["Продукты", "Заведения", "Транспорт", "Связь", "Аптека", "Дом", "Одежда", "Развлечения"],
        "Доходы": ["Зарплата", "Подработка", "Подарки", "Проценты"],
        "Инвестиции": ["Покупка", "Продажа", "Дивиденды"],
        "Сбережения": ["Подушка", "Отпуск", "Покупка техники"],
        "Цель": ["Подушка", "Отпуск", "Покупка техники"],
    }
    for c in defaults.get(typ, []):
        if c not in cats:
            cats.append(c)
    return cats[:24]


def _period_bounds(period: str, tz_min: int):
    """Вернёт (start_date, end_date_inclusive) в локальном времени пользователя."""
    from datetime import datetime as _dt, timedelta as _td
    now_local = _dt.utcnow() + _td(minutes=tz_min)
    today = now_local.date()
    if period == 'week':
        start = today - _td(days=today.weekday())
        return start, today
    # month
    first = today.replace(day=1)
    return first, today


async def _check_category_limits_and_warn(chat_id: int, category: str, at_dt, context: ContextTypes.DEFAULT_TYPE):
    """
    Проверяем лимиты (week/month) для категории и шлём короткое предупреждение при переходе через 50/80/100%.
    Округление вниз: floor(spent*100/limit).
    """
    tz = get_user_tz(chat_id)
    cur = get_user_currency(chat_id)

    for period in ('week', 'month'):
        # есть ли лимит?
        lim_rows = pg_fetchall("""
            SELECT amount FROM public.category_limits
             WHERE user_id=%s AND period=%s AND category=%s
             LIMIT 1
        """, (chat_id, period, category))
        if not lim_rows:
            continue
        limit_amt = int(lim_rows[0][0]) or 0
        if limit_amt <= 0:
            continue

        start, end = _period_bounds(period, tz)
        # считаем траты только по этой категории
        spent_rows = pg_fetchall("""
            SELECT COALESCE(SUM(amount),0)
              FROM public.operations
             WHERE chat_id=%s AND type='Расходы' AND category=%s
               AND op_date BETWEEN %s AND %s
        """, (chat_id, category, start, end))
        spent = int(spent_rows[0][0]) if spent_rows else 0

        pct = (spent * 100) // limit_amt  # округление вниз
        new_band = 100 if pct >= 100 else 80 if pct >= 80 else 50 if pct >= 50 else 0

        # читаем состояние
        st_rows = pg_fetchall("""
            SELECT last_band, updated_at::date
              FROM public.category_limit_state
             WHERE user_id=%s AND period=%s AND category=%s
             LIMIT 1
        """, (chat_id, period, category))
        last_band, st_date = (0, None) if not st_rows else (int(st_rows[0][0]), st_rows[0][1])

        # если состояние старее текущего периода — сбрасываем
        if st_date is not None and start > st_date:
            last_band = 0

        if new_band > last_band and new_band in (50, 80, 100):
            # апдейтим state и шлём предупреждение
            pg_exec("""
                INSERT INTO public.category_limit_state (user_id, period, category, last_band, updated_at)
                VALUES (%s,%s,%s,%s, now())
                ON CONFLICT (user_id, period, category) DO UPDATE
                   SET last_band=EXCLUDED.last_band, updated_at=now()
            """, (chat_id, period, category, new_band))
            try:
                label = "неделя" if period == 'week' else "месяц"
                # короткая формулировка без цифр сумм
                text = f"⚠️ ЛИМИТ_ПО_СТРОКА {_md_escape(category)}» ({label}): {new_band}%."
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                log.debug("warn send failed: %s", e)


async def record_operation(cat: str, amt: int, dt,
                           typ: str, update: Update,
                           context: ContextTypes.DEFAULT_TYPE,
                           note: Optional[str] = None):
    """
    Финальная фиксация операции + ответ пользователю.
    Важные моменты:
      • raw_text берём из user_data['batch_item_text'] (если есть) либо из сообщения пользователя.
      • reply_to направляем ТОЛЬКО на исходное пользовательское сообщение, не на служебные меню.
      • После успешной отправки ответа — если активен батч, продолжаем следующий элемент.
    """
    cid = update.effective_chat.id

    # Закрываем служебные меню
    for key in ('type_menu_id', 'cat_menu_id', 'suggest_msg_id'):
        mid = context.user_data.pop(key, None)
        if mid:
            try:
                await context.bot.delete_message(cid, mid)
            except Exception:
                pass

    # ── исходный текст операции и reply_to ──
    orig_text: str = ""
    reply_to_msg_id: Optional[int] = None

    batch_piece = context.user_data.get("batch_item_text")
    if batch_piece:
        orig_text = str(batch_piece)

    if getattr(update, "callback_query", None):
        cq_msg = update.callback_query.message
        rtm = getattr(cq_msg, "reply_to_message", None)
        if rtm and getattr(rtm, "text", None):
            from_user = getattr(rtm, "from_user", None)
            if not (from_user and getattr(from_user, "is_bot", False)):
                reply_to_msg_id = rtm.message_id
                if not orig_text:
                    orig_text = rtm.text

    if reply_to_msg_id is None and getattr(update, "effective_message", None):
        em = update.effective_message
        if getattr(em, "text", None):
            from_user = getattr(em, "from_user", None)
            if not (from_user and getattr(from_user, "is_bot", False)):
                reply_to_msg_id = em.message_id
                if not orig_text:
                    orig_text = em.text

    if not orig_text:
        ut = context.user_data.get("last_user_text", "")
        if ut and not _is_bot_hint(ut):
            orig_text = ut

    # Сохраняем операцию в БД
    insert_operation(cid, dt.date(), typ, cat, amt, 'From Telegram')

    # Пишем raw_text в последнюю запись
    if orig_text and not _is_bot_hint(orig_text):
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute("""
                UPDATE public.operations
                   SET raw_text = %s
                 WHERE id = (
                     SELECT id FROM public.operations
                      WHERE chat_id=%s
                      ORDER BY id DESC
                      LIMIT 1
                 )
            """, (orig_text, cid))
            conn.commit(); conn.close()
        except Exception as e:
            log.warning("raw_text UPDATE failed: %s", e)

    # Кнопки
    if typ == 'Расходы':
        second = InlineKeyboardButton('💰 Остаток', callback_data='status')
    elif typ == 'Доходы':
        second = InlineKeyboardButton('💵 Доходы', callback_data='income_status')
    elif typ == 'Инвестиции':
        second = InlineKeyboardButton('📊 Инвестиции (месяц)', callback_data='inv_status')
    else:
        second = InlineKeyboardButton('🎯 Прогресс цели', callback_data=f'goal_status|{cat}')
    third = InlineKeyboardButton('✏️ Изменить', callback_data='op_edit')
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'),
        second,
        third
    ]])

    # Имя пользователя
    user = getattr(update, 'effective_user', None)
    name = (getattr(user, 'full_name', None)
            or getattr(user, 'first_name', None)
            or getattr(user, 'username', None)
            or "Пользователь")

    # Текст ответа
    if typ == "Доходы":
        verb = "получил(а)"
    elif typ in ("Сбережения", "Цель"):
        verb = "отложил(а)"
    else:
        verb = "потратил(а)"

    cur_symbol = get_user_currency(cid)
    line1 = f"{_md_escape(name)} {verb} *{amt} {cur_symbol}* на *{_md_escape(cat)}*"
    line2 = format_date_ru_with_weekday(dt.date())

    parts = [line1, line2]
    if batch_piece:
        parts.append(f"_{_md_escape(batch_piece)}_")
    elif orig_text and not _is_bot_hint(orig_text):
        parts.append(f"_{_md_escape(orig_text)}_")
    if note and not _is_bot_hint(note):
        parts.append(f"_{_md_escape(note)}_")

    final_text = "\n".join(parts)

    kwargs = dict(chat_id=cid, text=final_text, parse_mode='Markdown', reply_markup=kb)
    if reply_to_msg_id:
        kwargs["reply_to_message_id"] = reply_to_msg_id

    try:
        await context.bot.send_message(**kwargs)
    except Exception as e:
        log.warning("final confirmation send failed (markdown), fallback plain text: %s", e)
        plain_parts = [
            f"{name} {verb} {amt} {cur_symbol} на {cat}",
            line2,
        ]
        if batch_piece:
            plain_parts.append(batch_piece)
        elif orig_text and not _is_bot_hint(orig_text):
            plain_parts.append(orig_text)
        if note and not _is_bot_hint(note):
            plain_parts.append(note)
        await context.bot.send_message(
            chat_id=cid,
            text="\n".join(plain_parts),
            reply_markup=kb,
            reply_to_message_id=reply_to_msg_id if reply_to_msg_id else None,
        )

    # Очистим batch_item_text, чтобы не «липло»
    context.user_data["batch_item_text"] = ""

    # После записи — проверяем лимиты по категории (только для Расходов)
    try:
        if typ == 'Расходы':
            await _check_category_limits_and_warn(cid, cat, dt, context)
    except Exception as e:
        log.debug("limit-check failed: %s", e)

    # Если активен батч — продолжаем следующий элемент
    try:
        from routers.messages import continue_batch_if_needed
        await continue_batch_if_needed(update, context)
    except Exception as e:
        log.debug("continue_batch_if_needed skipped: %s", e)
