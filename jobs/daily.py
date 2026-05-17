from __future__ import annotations
"""
jobs/daily.py — персонализированные напоминания
Версия: 2025.08.22-ntf-06

— Morning/Evening: 1 раз в день для каждого вида, без повторов.
— Не шлём, если уже есть операции за локальные «сегодня».
— Журнал reminders_log с PK (user_id, kind, sent_on).
— Жёсткая автомиграция схемы: если остался старый PK (user_id, sent_on) — заменим.
"""

import logging
import random
from datetime import datetime, timedelta, timezone, date

from telegram.error import Forbidden, BadRequest
from psycopg2 import OperationalError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import get_conn
from db.queries import list_category_limits, get_smart_morning_limits_enabled
from settings import ENABLE_SMART_MORNING_LIMITS

log = logging.getLogger("finbot.daily")

# Кнопка: «Без операций сегодня»
INLINE_KB_NOOP = InlineKeyboardMarkup([[InlineKeyboardButton("Без операций сегодня", callback_data="noop_today")]])

# ---------------------------
# Шаблоны
# ---------------------------

MORNING_TEMPLATES = [
    {"id": 1,  "tag": "soft",  "text": "🔔 Дружеское напоминание: учёт за сегодня ещё пустой."},
    {"id": 10, "tag": "short", "text": "{name}, вжух и трата записалась 🌸"},
    {"id": 14, "tag": "pop",   "text": "{name}, умный человек в Telegram — сделать запись 👀"},
    {"id": 19, "tag": "duo",   "text": "Маленький шаг в учёте — большой плюс к контролю."},
    {"id": 24, "tag": "meme",  "text": "{name}, у Дюны — пряность добывать, у нас — привычка записывать 🌵"},
    {"id": 31, "tag": "meme",  "text": "{name}, первый, как формула, маленькая запись даст боту опыта 🏎️💨"},
    {"id": 2,  "tag": "duo",   "text": "{name}, одна запись — и день под контролем 🙂"},
    {"id": 3,  "tag": "short", "text": "{name}, 10 секунд: “продукты 250” — и готово."},
    {"id": 4,  "tag": "duo",   "text": "{name}, чем раньше отметишь — тем точнее статистика."},
    {"id": 5,  "tag": "soft",  "text": "Доброе утро, {name}. Одна строка — и финансы на месте."},
    {"id": 6,  "tag": "meme",  "text": "{name}, утро начинается с привычки — добавь запись ☕️"},
    {"id": 7,  "tag": "short", "text": "{name}, мини-задача: одна покупка → одна запись ✅"},
    {"id": 8,  "tag": "pop",   "text": "План на день, {name}: 1) кофе 2) запись 3) победа."},
    {"id": 9,  "tag": "duo",   "text": "{name}, дисциплина — это маленькие действия. Запишем?"},
    {"id": 11, "tag": "soft",  "text": "{name}, твой будущий я скажет спасибо за 1 запись сегодня."},
    {"id": 12, "tag": "short", "text": "{name}, «такси 340» — пример; твоя очередь 😉"},
    {"id": 13, "tag": "meme",  "text": "{name}, добавишь одну — боту будет легче считать 📊"},
    {"id": 15, "tag": "duo",   "text": "{name}, привычка > мотивация. Одна строка прямо сейчас."},
    {"id": 16, "tag": "soft",  "text": "Привет, {name}. Давай отметим утренние траты аккуратно."},
    {"id": 17, "tag": "short", "text": "{name}, заметка в учёте — минута дела."},
    {"id": 18, "tag": "meme",  "text": "{name}, пусть день начнётся с порядка 🧭"},
    {"id": 20, "tag": "pop",   "text": "{name}, бюджет любит ранних пташек 🐦"},
    {"id": 21, "tag": "duo",   "text": "{name}, укрепляем привычку: одна запись — и всё."},
    {"id": 22, "tag": "soft",  "text": "{name}, бережно напомню: отметить трату сейчас очень просто."},
    {"id": 23, "tag": "short", "text": "{name}, напиши сумму — я всё сохраню."},
    {"id": 25, "tag": "meme",  "text": "{name}, небольшая запись — большой контроль 💪"},
    {"id": 26, "tag": "duo",   "text": "{name}, сегодня без пропусков — хотя бы 1 строка!"},
    {"id": 27, "tag": "soft",  "text": "{name}, финучёт ждёт лишь одного твоего слова."},
    {"id": 28, "tag": "short", "text": "{name}, «обед 420» — и порядок."},
    {"id": 29, "tag": "pop",   "text": "{name}, твой день, твои цифры. Добавим?"},
    {"id": 30, "tag": "duo",   "text": "{name}, вместе доведём привычку до автоматизма 🔁"},
    {"id": 32, "tag": "soft",  "text": "{name}, как спалось? Пора отметить расходы за утро 🙂"},
    {"id": 33, "tag": "short", "text": "{name}, один жест — и бюджет в строю."},
]

EVENING_TEMPLATES = [
    {"id": 102, "tag": "short", "text": "{name}, ты — «продукты 500», я — окак 👀"},
    {"id": 113, "tag": "duo",   "text": "{name}, герой дня — тот, кто добавил одну строку в учёт, еуу ⭐️"},
    {"id": 126, "tag": "meme",  "text": "{name}, эмоция тотального слея от твоих записей 💅"},
    {"id": 131, "tag": "meme",  "text": "{name}, учёт финансов — не кринж, всё окей 👌"},
    {"id": 101, "tag": "soft",  "text": "{name}, давай закроем день аккуратно."},
    {"id": 103, "tag": "duo",   "text": "{name}, одна строка — и статистика не хромает."},
    {"id": 104, "tag": "meme",  "text": "{name}, закончим день красиво — добавь одну строку ✨"},
    {"id": 105, "tag": "short", "text": "{name}, 10 секунд сейчас сэкономят время завтра."},
    {"id": 106, "tag": "duo",   "text": "{name}, привычки строят будущее. Одна запись сегодня."},
    {"id": 107, "tag": "soft",  "text": "{name}, финальный штрих за сегодня — учёт."},
    {"id": 108, "tag": "short", "text": "{name}, один штрих к порядку — отметь сегодняшние расходы."},
    {"id": 109, "tag": "pop",   "text": "{name}, 60 секунд на бюджет сегодня — завтра скажешь «спасибо»."},
]

# ---------------------------
# Схема и вспомогалки
# ---------------------------

def _ensure_tables():
    """Создаём и/или мигрируем reminders_log под PK (user_id, kind, sent_on)."""
    conn = get_conn(); cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.reminders_log (
            user_id  BIGINT NOT NULL,
            sent_on  DATE   NOT NULL DEFAULT CURRENT_DATE,
            kind     TEXT,
            tmpl_id  INT,
            tag      TEXT,
            sent_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # гарантируем колонки
    cur.execute("ALTER TABLE public.reminders_log ADD COLUMN IF NOT EXISTS sent_on DATE NOT NULL DEFAULT CURRENT_DATE")
    cur.execute("ALTER TABLE public.reminders_log ADD COLUMN IF NOT EXISTS kind TEXT")
    cur.execute("UPDATE public.reminders_log SET kind = COALESCE(kind,'legacy') WHERE kind IS NULL")
    cur.execute("ALTER TABLE public.reminders_log ALTER COLUMN kind SET NOT NULL")

    # если PK не тот — переопределим (жёстко)
    cur.execute("""
    DO $$
    DECLARE
      def text;
    BEGIN
      SELECT pg_get_constraintdef(c.oid)
        INTO def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
       WHERE n.nspname='public' AND t.relname='reminders_log' AND c.contype='p'
       LIMIT 1;

      IF def IS NULL THEN
        -- PK нет: просто добавим правильный
        EXECUTE 'ALTER TABLE public.reminders_log ADD CONSTRAINT reminders_log_pkey PRIMARY KEY (user_id, kind, sent_on)';
      ELSIF def NOT LIKE 'PRIMARY KEY (user_id, kind, sent_on)%' THEN
        -- PK есть, но старый: переопределим
        BEGIN
          EXECUTE 'ALTER TABLE public.reminders_log DROP CONSTRAINT reminders_log_pkey';
        EXCEPTION WHEN undefined_object THEN
          NULL;
        END;
        EXECUTE 'ALTER TABLE public.reminders_log ADD CONSTRAINT reminders_log_pkey PRIMARY KEY (user_id, kind, sent_on)';
      END IF;
    END$$;
    """)

    conn.commit(); conn.close()

def _user_tz_and_hour(user_id: int) -> tuple[int, int]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(tz_offset_min,180), COALESCE(reminder_hour,20)
          FROM public.users WHERE user_id=%s LIMIT 1
    """,(user_id,))
    row = cur.fetchone() or (180,20)
    conn.close()
    return int(row[0]), int(row[1])

def _local_now(user_id: int) -> datetime:
    off_min, _ = _user_tz_and_hour(user_id)
    return datetime.now(timezone.utc) + timedelta(minutes=off_min)

def _local_today(user_id: int):
    return _local_now(user_id).date()


def _period_from_local_date(user_id: int) -> date:
    return _local_now(user_id).date()


def previous_week_period(today: date) -> tuple[date, date]:
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    end = this_monday - timedelta(days=1)
    return start, end


def previous_month_period(today: date) -> tuple[date, date]:
    first_this_month = today.replace(day=1)
    end = first_this_month - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def format_money(amount: float | int | None) -> str:
    if amount is None:
        amount = 0
    value = float(amount)
    if abs(value - int(value)) < 0.005:
        s = f"{int(round(value)):,}".replace(",", " ")
    else:
        s = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} ₽"


def _report_key(kind: str, period_start: date, period_end: date) -> str:
    return f"{kind}:{period_start.isoformat()}:{period_end.isoformat()}"


def _report_already_sent(user_id: int, kind: str, period_start: date, period_end: date) -> bool:
    key = _report_key(kind, period_start, period_end)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM public.reminders_log WHERE user_id=%s AND kind=%s LIMIT 1", (user_id, key))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def _report_mark_sent(user_id: int, kind: str, period_start: date, period_end: date):
    key = _report_key(kind, period_start, period_end)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO public.reminders_log (user_id, sent_on, kind, tmpl_id, tag)
        VALUES (%s, %s, %s, NULL, %s)
        ON CONFLICT ON CONSTRAINT reminders_log_pkey DO NOTHING
    """, (user_id, _local_today(user_id), key, kind))
    conn.commit(); conn.close()


def _sum_by_type(user_id: int, start: date, end: date) -> tuple[float, float]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN type='Расходы' THEN amount END), 0),
          COALESCE(SUM(CASE WHEN type='Доходы' THEN amount END), 0)
        FROM public.operations
        WHERE user_id=%s
          AND op_date BETWEEN %s AND %s
          AND COALESCE(type,'') <> 'noop'
          AND COALESCE(category,'') <> 'Без операций'
    """, (user_id, start, end))
    row = cur.fetchone() or (0, 0)
    conn.close()
    return float(row[0] or 0), float(row[1] or 0)

def _has_ops_today(user_id: int) -> bool:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM public.operations
         WHERE chat_id=%s AND op_date=%s
    """,(user_id, _local_today(user_id)))
    n = cur.fetchone()[0]
    conn.close()
    return (n or 0) > 0

def _recent_template_ids(user_id: int, kind: str, lookback_days: int = 14) -> set[int]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT tmpl_id
          FROM public.reminders_log
         WHERE user_id=%s AND kind=%s AND sent_on >= %s::date - (%s || ' days')::interval
           AND tmpl_id IS NOT NULL
    """, (user_id, kind, _local_today(user_id), lookback_days))
    ids = {r[0] for r in cur.fetchall() if r[0] is not None}
    conn.close()
    return ids

def _pick_template(pool: list[dict], banned: set[int]) -> dict:
    avail = [t for t in pool if t["id"] not in banned]
    return random.choice(avail or pool)

def _already_sent_today(user_id: int, kind: str) -> bool:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT 1
          FROM public.reminders_log
         WHERE user_id=%s AND kind=%s AND sent_on=%s
         LIMIT 1
    """,(user_id, kind, _local_today(user_id)))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def _log_sent(user_id: int, kind: str, tmpl_id: int|None, tag: str|None):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO public.reminders_log (user_id, sent_on, kind, tmpl_id, tag)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT reminders_log_pkey DO NOTHING
    """,(user_id, _local_today(user_id), kind, tmpl_id, tag))
    conn.commit(); conn.close()



def _is_too_many_clients(err: Exception) -> bool:
    return 'too many clients' in str(err).lower()

async def _user_name(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        return chat.first_name or chat.full_name or "друг"
    except Exception:
        return "друг"

# ---------------------------
# JOBS
# ---------------------------

async def day_nudge_job(context: ContextTypes.DEFAULT_TYPE):
    """Окно 06:00–12:00 локально. 1 раз/день. Пропуск, если были операции."""
    try:
        _ensure_tables()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM public.users")
        users = [r[0] for r in cur.fetchall()]
        conn.close()

        for uid in users:
            now_loc = _local_now(uid)
            if not (6 <= now_loc.hour < 12):
                continue
            if _already_sent_today(uid, "morning"):
                continue
            if _has_ops_today(uid):
                continue

            banned = _recent_template_ids(uid, "morning", 14)
            pick = _pick_template(MORNING_TEMPLATES, banned)
            name = await _user_name(context, uid)
            text = pick["text"].format(name=name)

            try:
                await context.bot.send_message(chat_id=uid, text=text)
                _log_sent(uid, "morning", pick.get("id"), pick.get("tag"))
            except (Forbidden, BadRequest) as e:
                log.info("morning: skip %s: %s", uid, e)
            except Exception as e:
                log.exception("morning: send error for %s: %s", uid, e)
    except OperationalError as e:
        if _is_too_many_clients(e):
            log.warning("day_nudge_job backoff: %s", e)
            return
        log.exception("day_nudge_job db error: %s", e)
    except Exception as e:
        log.exception("day_nudge_job error: %s", e)

async def evening_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Окно: reminder_hour..reminder_hour+2 локально. 1 раз/день. Пропуск, если были операции."""
    try:
        _ensure_tables()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM public.users")
        users = [r[0] for r in cur.fetchall()]
        conn.close()

        for uid in users:
            off_min, r_hour = _user_tz_and_hour(uid)
            now_loc = datetime.now(timezone.utc) + timedelta(minutes=off_min)
            if not (r_hour <= now_loc.hour <= r_hour + 2):
                continue
            if _already_sent_today(uid, "evening"):
                continue
            if _has_ops_today(uid):
                continue

            banned = _recent_template_ids(uid, "evening", 14)
            pick = _pick_template(EVENING_TEMPLATES, banned)
            name = await _user_name(context, uid)
            text = pick["text"].format(name=name)

            try:
                await context.bot.send_message(chat_id=uid, text=text, reply_markup=INLINE_KB_NOOP)
                _log_sent(uid, "evening", pick.get("id"), pick.get("tag"))
            except (Forbidden, BadRequest) as e:
                log.info("evening: skip %s: %s", uid, e)
            except Exception as e:
                log.exception("evening: send error for %s: %s", uid, e)
    except OperationalError as e:
        if _is_too_many_clients(e):
            log.warning("evening_reminder_job backoff: %s", e)
            return
        log.exception("evening_reminder_job db error: %s", e)
    except Exception as e:
        log.exception("evening_reminder_job error: %s", e)

def _top_expense_categories(user_id: int, start: date, end: date, limit_n: int) -> list[tuple[str, float]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT category, COALESCE(SUM(amount),0) AS total
        FROM public.operations
        WHERE user_id=%s
          AND op_date BETWEEN %s AND %s
          AND type='Расходы'
          AND COALESCE(type,'') <> 'noop'
          AND COALESCE(category,'') NOT IN ('', 'Без операций')
        GROUP BY category
        ORDER BY total DESC
        LIMIT %s
    """, (user_id, start, end, limit_n))
    rows = [(r[0], float(r[1] or 0)) for r in cur.fetchall()]
    conn.close()
    return rows


def _active_users(days: int) -> list[int]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT user_id
        FROM public.operations
        WHERE op_date >= CURRENT_DATE - (%s || ' days')::interval
    """, (days,))
    users = [int(r[0]) for r in cur.fetchall() if r[0] is not None]
    conn.close()
    return users


def build_weekly_report_text(user_id: int, period_start: date, period_end: date) -> str:
    exp, inc = _sum_by_type(user_id, period_start, period_end)
    title = f"📊 Итоги недели: {period_start:%d.%m}–{period_end:%d.%m}"
    if exp == 0 and inc == 0:
        return f"{title}\n\nЗа прошлую неделю записей не было.\n\nМожно начать с малого: просто напиши боту:\nкофе 250"
    prev_start = period_start - timedelta(days=7)
    prev_end = period_end - timedelta(days=7)
    prev_exp, _ = _sum_by_type(user_id, prev_start, prev_end)
    if prev_exp > 0:
        delta = (exp - prev_exp) / prev_exp * 100
        dyn = f"Расходы на {abs(delta):.0f}% {'выше' if delta > 0 else 'меньше'}, чем неделей ранее."
    else:
        dyn = "Неделей ранее расходов не было."
    tops = _top_expense_categories(user_id, period_start, period_end, 3)
    tops_text = "\n".join([f"{i+1}. {c} — {format_money(v)}" for i, (c, v) in enumerate(tops)]) or "—"
    balance = inc - exp
    return (
        f"{title}\n\n"
        f"Расходы: {format_money(exp)}\n"
        f"Доходы: {format_money(inc)}\n"
        f"Баланс: {'+' if balance >= 0 else ''}{format_money(balance)}\n\n"
        f"Топ расходов:\n{tops_text}\n\n"
        f"Динамика:\n{dyn}"
    )


def build_monthly_report_text(user_id: int, period_start: date, period_end: date) -> str:
    exp, inc = _sum_by_type(user_id, period_start, period_end)
    title = f"📊 Итоги месяца: {period_start:%m.%Y}"
    if exp == 0 and inc == 0:
        return f"{title}\n\nЗа месяц записей не было.\nКогда вернёшься к учёту, я снова соберу отчёт автоматически."
    prev_end = period_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    prev_exp, _ = _sum_by_type(user_id, prev_start, prev_end)
    if prev_exp > 0:
        delta = (exp - prev_exp) / prev_exp * 100
        dyn = f"Расходы на {abs(delta):.0f}% {'выше' if delta > 0 else 'меньше'}, чем в прошлом месяце."
    else:
        dyn = "Месяцем ранее расходов не было."
    tops = _top_expense_categories(user_id, period_start, period_end, 5)
    tops_text = "\n".join([f"{i+1}. {c} — {format_money(v)}" for i, (c, v) in enumerate(tops)]) or "—"
    balance = inc - exp
    return (
        f"{title}\n\n"
        f"Расходы: {format_money(exp)}\n"
        f"Доходы: {format_money(inc)}\n"
        f"Баланс: {'+' if balance >= 0 else ''}{format_money(balance)}\n\n"
        f"Топ расходов:\n{tops_text}\n\n"
        f"Динамика:\n{dyn}"
    )


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        _ensure_tables()
        for uid in _active_users(14):
            now_loc = _local_now(uid)
            today = now_loc.date()
            if today.weekday() != 0 or now_loc.hour != 12:
                continue
            start, end = previous_week_period(today)
            if _report_already_sent(uid, "weekly_report", start, end):
                continue
            text = build_weekly_report_text(uid, start, end)
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                _report_mark_sent(uid, "weekly_report", start, end)
            except (Forbidden, BadRequest) as e:
                log.info("weekly report: skip %s: %s", uid, e)
            except Exception as e:
                log.exception("weekly report: send error for %s: %s", uid, e)
    except OperationalError as e:
        if _is_too_many_clients(e):
            log.warning("weekly_report_job backoff: %s", e)
            return
        log.exception("weekly_report_job db error: %s", e)


async def monthly_report_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        _ensure_tables()
        for uid in _active_users(45):
            now_loc = _local_now(uid)
            today = now_loc.date()
            if today.day != 1 or now_loc.hour != 10:
                continue
            start, end = previous_month_period(today)
            if _report_already_sent(uid, "monthly_report", start, end):
                continue
            text = build_monthly_report_text(uid, start, end)
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                _report_mark_sent(uid, "monthly_report", start, end)
            except (Forbidden, BadRequest) as e:
                log.info("monthly report: skip %s: %s", uid, e)
            except Exception as e:
                log.exception("monthly report: send error for %s: %s", uid, e)
    except OperationalError as e:
        if _is_too_many_clients(e):
            log.warning("monthly_report_job backoff: %s", e)
            return
        log.exception("monthly_report_job db error: %s", e)


def _has_recent_activity(user_id: int, days: int) -> bool:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM public.operations WHERE user_id=%s AND op_date >= CURRENT_DATE - (%s || ' days')::interval LIMIT 1", (user_id, days))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def _current_period_bounds(local_today: date, period: str) -> tuple[date, date, int, int]:
    if period == 'week':
        start = local_today - timedelta(days=local_today.weekday())
        end = start + timedelta(days=6)
    else:
        start = local_today.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = nxt - timedelta(days=1)
    total = (end - start).days + 1
    elapsed = (local_today - start).days + 1
    return start, end, elapsed, total


def _spent_in_period(user_id: int, category: str, start: date, end: date) -> float:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
      SELECT COALESCE(SUM(amount),0)
      FROM public.operations
      WHERE user_id=%s AND type='Расходы' AND category=%s AND op_date BETWEEN %s AND %s
        AND COALESCE(type,'') <> 'noop' AND COALESCE(category,'') <> 'Без операций'
    """, (user_id, category, start, end))
    v = float((cur.fetchone() or [0])[0] or 0)
    conn.close()
    return v


def _build_smart_morning_text(user_id: int, local_today: date):
    limits = list_category_limits(user_id)
    if not limits:
        return None
    picks = []
    for period, amount, currency, category in limits:
        if period not in ('week', 'month') or not amount:
            continue
        start, end, elapsed, total = _current_period_bounds(local_today, period)
        spent = _spent_in_period(user_id, category, start, end)
        remaining = amount - spent
        days_left = max(1, (end - local_today).days + 1)
        safe_today = max(0.0, remaining / days_left)
        spent_ratio = spent / amount if amount else 0.0
        expected_progress = elapsed / total if total else 1.0
        if (remaining <= 0) or (spent_ratio >= 0.8 and days_left >= 2) or (safe_today <= 500) or (spent_ratio >= expected_progress + 0.25):
            picks.append((category, period, remaining, safe_today, spent_ratio))
    if not picks:
        return None
    category, period, remaining, safe_today, spent_ratio = sorted(picks, key=lambda x: (x[2], x[3]))[0]
    hdr = "🌤 Утренний ориентир" if remaining <= 0 else "🌤 Лимиты на сегодня"
    if remaining <= 0:
        line1 = f"{category}: лимит уже превышен на {format_money(abs(remaining))}."
    else:
        line1 = f"{category}: безопасно ≈ {format_money(safe_today)} в день."
    line2 = f"Использовано {spent_ratio*100:.0f}% {'недельного' if period=='week' else 'месячного'} лимита."
    return f"{hdr}\n\n{line1}\n{line2}\n\nКоротко: сегодня лучше держать эту категорию спокойно."


async def smart_morning_limit_job(context: ContextTypes.DEFAULT_TYPE):
    if not ENABLE_SMART_MORNING_LIMITS:
        return
    try:
        _ensure_tables()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM public.users")
        users = [r[0] for r in cur.fetchall()]
        conn.close()
        for uid in users:
            if not get_smart_morning_limits_enabled(uid):
                continue
            now_loc = _local_now(uid)
            if not (9 <= now_loc.hour <= 11):
                continue
            if _already_sent_today(uid, 'smart_morning_limit'):
                continue
            if not _has_recent_activity(uid, 14):
                continue
            text = _build_smart_morning_text(uid, now_loc.date())
            if not text:
                continue
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                _log_sent(uid, 'smart_morning_limit', None, 'limits')
            except (Forbidden, BadRequest) as e:
                log.info("smart morning: skip %s: %s", uid, e)
            except Exception as e:
                log.exception("smart morning: send error for %s: %s", uid, e)
    except OperationalError as e:
        if _is_too_many_clients(e):
            log.warning("smart_morning_limit_job backoff: %s", e)
            return
        log.exception("smart_morning_limit_job db error: %s", e)
    except Exception as e:
        log.exception("smart_morning_limit_job error: %s", e)
