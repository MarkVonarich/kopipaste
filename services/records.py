# services/records.py — v2025.08.30-limits
__version__ = "2025.08.30-limits"

from typing import List, Tuple, Optional, Dict
import logging
from decimal import Decimal
from rapidfuzz import process

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from db.queries import (
    list_user_aliases, upsert_user_alias, insert_operation,
    get_user_budgets, get_user_currency
)
from cache.global_dict import bump_global_popularity, global_suggestions
from utils.text import norm_text, format_date_ru_with_weekday
from db.database import get_conn, pg_fetchall, pg_exec
from services.automatic_notifications import DeliveryPolicy, dispatch_automatic_notification
from services.limit_alerts import (
    EXCEEDED_BAND,
    build_category_limit_dedupe_key,
    build_category_limit_exceeded_dedupe_key,
    category_limit_alert_markup,
    render_category_limit_alert,
    safe_limit_threshold_event_properties,
)
from services.operations import RecordedOperation, record_financial_operation
from services.goals import build_salary_suggestion_text, format_money, salary_suggestion_goals
from services.product_events import ProductEvent, track_product_event
from services.user_profile import get_user_display_name
from services.user_time import user_local_date
from utils.money import format_money as format_money_value

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


def _period_bounds(period: str, user_id: int):
    """Вернёт (start_date, end_date_inclusive) в локальном времени пользователя."""
    from datetime import timedelta as _td
    today = user_local_date(user_id)
    if period == 'week':
        start = today - _td(days=today.weekday())
        return start, today
    # month
    first = today.replace(day=1)
    return first, today


def _has_previous_exceeded_alert(
    *,
    user_id: int,
    workspace_id: int | None,
    period: str,
    period_start,
    category_key: str,
) -> bool:
    workspace_part = workspace_id if workspace_id is not None else 0
    prefix = f"category_limit_exceeded:{user_id}:{workspace_part}:{period}:{period_start.isoformat()}:{category_key}:"
    try:
        rows = pg_fetchall(
            """
            SELECT 1
              FROM public.automatic_notifications
             WHERE user_id=%s
               AND notification_type='category_limit_warning'
               AND left(dedupe_key, length(%s)) = %s
             LIMIT 1
            """,
            (user_id, prefix, prefix),
        )
        return bool(rows)
    except Exception:
        return False


async def _check_category_limits_and_warn(
    chat_id: int,
    category: str,
    at_dt,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    workspace_id: int | None = None,
    operation_id: int | None = None,
    source: str = "operation_commit",
):
    """
    Проверяем лимиты (week/month) для категории и шлём предупреждение при переходе через 80/90/100/exceeded.
    Округление вниз: floor(spent*100/limit).
    """
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
        limit_amt = Decimal(str(lim_rows[0][0] or 0))
        if limit_amt <= 0:
            continue

        start, end = _period_bounds(period, chat_id)
        # считаем траты только по этой категории
        spent_rows = pg_fetchall("""
            SELECT COALESCE(SUM(amount),0)
              FROM public.operations
             WHERE chat_id=%s AND type='Расходы' AND category=%s
               AND op_date BETWEEN %s AND %s
        """, (chat_id, category, start, end))
        spent = Decimal(str(spent_rows[0][0] or 0)) if spent_rows else Decimal("0")
        alert = render_category_limit_alert(category=category, period=period, spent=spent, limit=limit_amt, currency=cur)
        if not alert:
            continue
        new_band = alert.threshold_band
        is_exceeded = new_band == EXCEEDED_BAND

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

        if is_exceeded and operation_id is None:
            continue

        if is_exceeded or new_band > last_band:
            state_band = 100 if is_exceeded else new_band
            pg_exec("""
                INSERT INTO public.category_limit_state (user_id, period, category, last_band, updated_at)
                VALUES (%s,%s,%s,%s, now())
                ON CONFLICT (user_id, period, category) DO UPDATE
                   SET last_band=GREATEST(public.category_limit_state.last_band, EXCLUDED.last_band),
                       updated_at=now()
            """, (chat_id, period, category, state_band))
            try:
                category_key = norm_text(category)
                if is_exceeded:
                    previous_exceeded = _has_previous_exceeded_alert(
                        user_id=chat_id,
                        workspace_id=workspace_id,
                        period=period,
                        period_start=start,
                        category_key=category_key,
                    )
                    dedupe_key = build_category_limit_exceeded_dedupe_key(
                        user_id=chat_id,
                        workspace_id=workspace_id,
                        period=period,
                        period_start=start,
                        category_key=category_key,
                        operation_id=int(operation_id),
                    )
                    alert = render_category_limit_alert(
                        category=category,
                        period=period,
                        spent=spent,
                        limit=limit_amt,
                        currency=cur,
                        intensified=previous_exceeded,
                    ) or alert
                else:
                    dedupe_key = build_category_limit_dedupe_key(
                        user_id=chat_id,
                        workspace_id=workspace_id,
                        period=period,
                        period_start=start,
                        category_key=category_key,
                        band=new_band,
                    )
                result = await dispatch_automatic_notification(
                    context,
                    user_id=chat_id,
                    chat_id=chat_id,
                    workspace_id=workspace_id,
                    notification_type="category_limit_warning",
                    dedupe_key=dedupe_key,
                    policy=DeliveryPolicy.DEFER,
                    text=alert.text,
                    reply_markup=category_limit_alert_markup(),
                    parse_mode=alert.parse_mode,
                    template_key="category_limit_warning",
                    payload={
                        "text": alert.text,
                        "parse_mode": alert.parse_mode,
                        "buttons": [[{"label": "Открыть лимиты", "callback_data": "lim_list"}], [{"label": "Изменить лимит", "callback_data": "lim_list"}], [{"label": "Отключить уведомления", "callback_data": "menu_notifications"}]],
                    },
                    disable_web_page_preview=True,
                    force_immediate=True,
                )
                if result.status == "duplicate":
                    continue
                track_product_event(ProductEvent(
                    event_name="limit_threshold_reached",
                    user_id=chat_id,
                    workspace_id=workspace_id,
                    status=alert.status,
                    currency=cur,
                    properties=safe_limit_threshold_event_properties(
                        band=new_band,
                        period=period,
                        status=alert.status,
                        currency=cur,
                        source=source,
                    ),
                ))
            except Exception as e:
                log.debug("warn send failed: %s", e)


async def send_operation_limit_alert(recorded: RecordedOperation | None, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not recorded or recorded.type != 'Расходы' or not recorded.operation_id:
        return
    await _check_category_limits_and_warn(
        recorded.user_id,
        recorded.category,
        recorded.operation_date,
        context,
        workspace_id=recorded.workspace_id,
        operation_id=recorded.operation_id,
        source=recorded.source or "operation_commit",
    )


async def record_operation(cat: str, amt, dt,
                           typ: str, update: Update,
                           context: ContextTypes.DEFAULT_TYPE,
                           note: Optional[str] = None,
                           merchant: Optional[str] = None):
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

    user = getattr(update, 'effective_user', None)
    actor_user_id = getattr(user, 'id', cid) or cid
    chat = getattr(update, 'effective_chat', None)
    chat_type = getattr(chat, 'type', 'private') or 'private'
    pending = context.user_data.get('pending') or {}
    source = pending.get('source') or context.user_data.get('operation_source') or 'text'
    operation_comment = (merchant or pending.get('merch') or note or '').strip()[:200]

    # Сохраняем операцию в БД через единый слой.
    recorded = record_financial_operation(
        chat_id=cid,
        actor_user_id=actor_user_id,
        op_date=dt.date(),
        op_type=typ,
        category=cat,
        amount=amt,
        comment=operation_comment,
        source=source,
        chat_type=chat_type,
        raw_text=orig_text if orig_text and not _is_bot_hint(orig_text) else None,
        metadata={'note': note} if note else None,
    )

    # Пишем raw_text в последнюю запись
    if orig_text and not _is_bot_hint(orig_text):
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute("""
                UPDATE public.operations
                   SET raw_text = %s
                 WHERE id = %s
            """, (orig_text, recorded.operation_id))
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
    edit_payload = f'op_edit|{recorded.operation_id}' if recorded.operation_id else 'op_edit'
    third = InlineKeyboardButton('✏️ Изменить', callback_data=edit_payload)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'),
        second,
        third
    ]])

    # Имя пользователя
    name = get_user_display_name(cid, user)

    # Текст ответа
    if typ == "Доходы":
        verb = "получил(а)"
    elif typ in ("Сбережения", "Цель"):
        verb = "отложил(а)"
    else:
        verb = "потратил(а)"

    cur_symbol = get_user_currency(cid)
    amount_text = format_money_value(amt, cur_symbol)
    line1 = f"{_md_escape(name)} {verb} *{amount_text}* на *{_md_escape(cat)}*"
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
            f"{name} {verb} {amount_text} на {cat}",
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

    try:
        if typ == 'Доходы' and recorded.operation_id:
            goals = salary_suggestion_goals(
                owner_user_id=actor_user_id,
                workspace_id=recorded.workspace_id,
                category=cat,
                currency=recorded.currency,
            )
            if goals:
                buttons = []
                for goal in goals[:5]:
                    amount = goal.planned_contribution_amount or goal.comfortable_amount
                    label = goal.display_name[:32]
                    if amount:
                        label = f"{label} · {format_money(amount, goal.currency)}"[:48]
                    buttons.append([InlineKeyboardButton(label, callback_data=f"goal|sal|a|{goal.id}|{recorded.operation_id}")])
                buttons.append([InlineKeyboardButton("Изменить сумму", callback_data=f"goal|sal|m|{goals[0].id}|{recorded.operation_id}")])
                buttons.append([InlineKeyboardButton("Напомнить позже", callback_data=f"goal|sal|s|{goals[0].id}|{recorded.operation_id}"), InlineKeyboardButton("Пропустить", callback_data=f"goal|sal|x|{goals[0].id}|{recorded.operation_id}")])
                await context.bot.send_message(chat_id=cid, text=build_salary_suggestion_text(goals), reply_markup=InlineKeyboardMarkup(buttons))
                from services.product_events import ProductEvent, track_product_event

                track_product_event(ProductEvent(
                    event_name="goal_income_suggestion_shown",
                    user_id=actor_user_id,
                    workspace_id=recorded.workspace_id,
                    status="shown",
                    currency=recorded.currency,
                    properties={"source": "income_operation"},
                ))
    except Exception as e:
        log.debug("goal salary suggestion skipped: %s", e)

    # Очистим batch_item_text, чтобы не «липло»
    context.user_data["batch_item_text"] = ""
    context.user_data.pop("operation_source", None)
    context.user_data.pop("pending", None)

    # После записи — проверяем лимиты по категории (только для Расходов)
    try:
        if typ == 'Расходы':
            await send_operation_limit_alert(recorded, context)
    except Exception as e:
        log.debug("limit-check failed: %s", e)

    # Если активен батч — продолжаем следующий элемент
    try:
        from routers.messages import continue_batch_if_needed
        await continue_batch_if_needed(update, context)
    except Exception as e:
        log.debug("continue_batch_if_needed skipped: %s", e)
