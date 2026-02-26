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
from datetime import datetime, timedelta, timezone

from telegram.error import Forbidden, BadRequest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import get_conn

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
    except Exception as e:
        log.exception("evening_reminder_job error: %s", e)
