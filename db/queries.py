# db/queries.py — v2025.08.30-limits
__version__ = "2025.08.30-limits"

from typing import Optional, Tuple, List
from datetime import date, timedelta
from psycopg2.extras import Json
from .database import get_conn, pg_exec, pg_fetchall
from settings import WEEK_DEFAULT, MONTH_DEFAULT
from services.ml_prep import normalize_alias_text
import math


def set_smart_morning_limits_enabled(user_id: int, enabled: bool):
    kind = 'smart_morning_opt_in' if enabled else 'smart_morning_opt_out'
    pg_exec(
        """
        INSERT INTO public.reminders_log (user_id, sent_on, kind, tmpl_id, tag)
        VALUES (%s, CURRENT_DATE, %s, NULL, 'settings_toggle')
        """,
        (user_id, kind)
    )


def get_smart_morning_limits_enabled(user_id: int) -> bool:
    rows = pg_fetchall(
        """
        SELECT kind
        FROM public.reminders_log
        WHERE user_id=%s
          AND kind IN ('smart_morning_opt_in', 'smart_morning_opt_out')
        ORDER BY sent_at DESC
        LIMIT 1
        """,
        (user_id,)
    )
    if not rows:
        return False
    return rows[0][0] == 'smart_morning_opt_in'


def set_quick_suggestions_enabled(user_id: int, enabled: bool):
    kind = 'quick_suggestions_opt_in' if enabled else 'quick_suggestions_opt_out'
    pg_exec(
        """
        INSERT INTO public.reminders_log (user_id, sent_on, kind, tmpl_id, tag)
        VALUES (%s, CURRENT_DATE, %s, NULL, 'settings_toggle')
        """,
        (user_id, kind)
    )


def get_quick_suggestions_enabled(user_id: int) -> bool:
    rows = pg_fetchall(
        """
        SELECT kind
        FROM public.reminders_log
        WHERE user_id=%s
          AND kind IN ('quick_suggestions_opt_in', 'quick_suggestions_opt_out')
        ORDER BY sent_at DESC
        LIMIT 1
        """,
        (user_id,)
    )
    if not rows:
        return True
    return rows[0][0] == 'quick_suggestions_opt_in'

def ensure_user(user_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.users (user_id, locale, currency, tz_offset_min, reminder_hour, plan, ml_consent)
                VALUES (%s, 'ru', 'RUB', 180, 20, 'free', false)
                ON CONFLICT (user_id) DO NOTHING
            """, (user_id,))
            created = (cur.rowcount == 1)
        conn.commit()
        return created
    finally:
        conn.close()

def update_user_field(user_id: int, field: str, value):
    pg_exec(f"UPDATE public.users SET {field}=%s WHERE user_id=%s", (value, user_id))

def get_user_currency(user_id: int) -> str:
    rows = pg_fetchall("SELECT currency FROM public.users WHERE user_id=%s", (user_id,))
    return (rows[0][0] if rows else "RUB") or "RUB"

def get_user_locale(user_id: int) -> str:
    rows = pg_fetchall("SELECT COALESCE(locale,'ru') FROM public.users WHERE user_id=%s", (user_id,))
    return (rows[0][0] if rows else "ru") or "ru"

def get_user_tz(user_id: int) -> int:
    rows = pg_fetchall("SELECT COALESCE(tz_offset_min,180) FROM public.users WHERE user_id=%s", (user_id,))
    return int(rows[0][0] if rows else 180)

def get_user_reminder_hour(user_id: int) -> int:
    rows = pg_fetchall("SELECT COALESCE(reminder_hour,20) FROM public.users WHERE user_id=%s", (user_id,))
    return int(rows[0][0] if rows else 20)

def get_user_budgets(user_id: int) -> Tuple[Optional[int], Optional[int]]:
    rows = pg_fetchall("SELECT week_limit, month_limit FROM public.budgets WHERE user_id=%s", (user_id,))
    if rows:
        wl, ml = rows[0]
        return wl if wl is not None else WEEK_DEFAULT, ml if ml is not None else MONTH_DEFAULT
    return WEEK_DEFAULT, MONTH_DEFAULT

def set_budget(user_id: int, week: Optional[int]=None, month: Optional[int]=None):
    wl, ml = get_user_budgets(user_id)
    wl = week if week is not None else wl
    ml = month if month is not None else ml
    pg_exec("""
        INSERT INTO public.budgets (user_id, week_limit, month_limit, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
           SET week_limit=EXCLUDED.week_limit,
               month_limit=EXCLUDED.month_limit,
               updated_at=now()
    """, (user_id, wl, ml))

def has_ops_today(cur, chat_id: int, local_date) -> bool:
    cur.execute("""
        SELECT 1 FROM public.operations
         WHERE chat_id = %s
           AND op_date >= %s::date
           AND op_date < (%s::date + INTERVAL '1 day')
         LIMIT 1
    """, (chat_id, local_date, local_date))
    return cur.fetchone() is not None

def insert_operation(chat_id: int, op_date, typ: str, category: str, amount: int, comment: str = ''):
    if not isinstance(op_date, date):
        op_date = op_date.date()
    iso = op_date.isocalendar()
    week_start = op_date.fromordinal(op_date.toordinal() - (op_date.isoweekday() - 1))
    weekday = op_date.isoweekday()  # 1..7 (Mon..Sun)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
              INSERT INTO public.operations
                (chat_id, user_id, op_date, type, category, amount, comment, week_start, iso_year, iso_week, weekday)
              VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id
            """, (chat_id, chat_id, op_date, typ, category, amount, comment, week_start, int(iso.year), int(iso.week), int(weekday)))
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def delete_last_operation(chat_id: int):
    pg_exec("""
        DELETE FROM public.operations
         WHERE id = (
           SELECT id FROM public.operations
            WHERE chat_id=%s
            ORDER BY id DESC
            LIMIT 1
         )
    """, (chat_id,))

def sum_amount(chat_id: int, typ: str, start_date, end_date) -> int:
    rows = pg_fetchall("""
        SELECT COALESCE(SUM(amount),0) FROM public.operations
         WHERE chat_id=%s AND type=%s AND op_date BETWEEN %s AND %s
    """, (chat_id, typ, start_date, end_date))
    return int(rows[0][0] if rows else 0)

def list_user_aliases(user_id: int):
    return pg_fetchall("""
        SELECT norm_text, type, category
          FROM public.user_aliases
         WHERE user_id=%s
    """, (user_id,))

def upsert_user_alias(user_id: int, norm_text: str, typ: str, category: str):
    pg_exec("""
        INSERT INTO public.user_aliases (user_id, norm_text, type, category, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (user_id, norm_text) DO UPDATE
           SET type=EXCLUDED.type,
               category=EXCLUDED.category,
               updated_at=now()
    """, (user_id, norm_text, typ, category))

def load_global_alias_rows():
    return pg_fetchall("""
        SELECT norm_text, category, type, COALESCE(popularity,0)
          FROM public.global_aliases
    """)

def bump_global_alias(norm_text: str, typ: str, category: str, inc: int = 1):
    pg_exec("""
        INSERT INTO public.global_aliases (norm_text, type, category, popularity, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (norm_text, type, category) DO UPDATE
           SET popularity = public.global_aliases.popularity + EXCLUDED.popularity,
               updated_at = now()
    """, (norm_text, typ, category, inc))


def cleanup_action_tokens(ttl_minutes: int = 10, hard_delete_days: int = 7) -> dict:
    """Expire stale draft tokens and delete old finished/expired tokens."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.action_tokens
                   SET status = 'expired'
                 WHERE status = 'draft'
                   AND COALESCE(expires_at, created_at + (%s || ' minutes')::interval) < now()
                """,
                (int(ttl_minutes),),
            )
            expired = cur.rowcount

            cur.execute(
                """
                DELETE FROM public.action_tokens
                 WHERE status IN ('committed', 'cancelled', 'expired')
                   AND created_at < now() - (%s || ' days')::interval
                """,
                (int(hard_delete_days),),
            )
            deleted = cur.rowcount
        conn.commit()
        return {"expired": int(expired), "deleted": int(deleted)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def get_user_top_categories(user_id: int, op_type: str = 'Расходы', lookback_ops: int = 50, top_n: int = 2) -> List[str]:
    rows = pg_fetchall("""
        WITH recent AS (
            SELECT category
              FROM public.operations
             WHERE user_id=%s
               AND type=%s
               AND category IS NOT NULL
             ORDER BY id DESC
             LIMIT %s
        )
        SELECT category, COUNT(*) c
          FROM recent
         GROUP BY category
         ORDER BY c DESC, category ASC
         LIMIT %s
    """, (user_id, op_type, lookback_ops, top_n))
    return [r[0] for r in rows if r and r[0]]



def get_recent_choices_for_text(user_id: int, normalized_text: str, days: int = 90, limit: int = 200):
    pref = (normalized_text or '').strip()
    rows = pg_fetchall("""
        SELECT chosen_category, COUNT(*) c
          FROM public.ml_observations
         WHERE user_id=%s
           AND action='pick_cat'
           AND chosen_category IS NOT NULL
           AND created_at >= now() - (%s || ' days')::interval
           AND (normalized_text=%s OR normalized_text LIKE %s)
         GROUP BY chosen_category
         ORDER BY c DESC, chosen_category ASC
         LIMIT %s
    """, (user_id, int(days), pref, f"{pref}%", int(limit)))
    return [(r[0], int(r[1])) for r in rows if r and r[0]]


def get_ml_stats(user_id: int, days: int = 30):
    rows = pg_fetchall("""
        SELECT
          COUNT(*)::int AS picks,
          COUNT(*) FILTER (
            WHERE chosen_category = (suggested_top2->0->>'cat')
          )::int AS top1_hit,
          COUNT(*) FILTER (
            WHERE chosen_category IN (
              suggested_top2->0->>'cat',
              suggested_top2->1->>'cat'
            )
          )::int AS top2_hit,
          COUNT(*) FILTER (WHERE COALESCE(meta->>'source','baseline')='baseline')::int AS baseline_picks,
          COUNT(*) FILTER (
            WHERE COALESCE(meta->>'source','baseline')='baseline'
              AND chosen_category = (suggested_top2->0->>'cat')
          )::int AS baseline_top1_hit,
          COUNT(*) FILTER (
            WHERE COALESCE(meta->>'source','baseline')='baseline'
              AND chosen_category IN (suggested_top2->0->>'cat', suggested_top2->1->>'cat')
          )::int AS baseline_top2_hit,
          COUNT(*) FILTER (WHERE COALESCE(meta->>'source','baseline')='model')::int AS model_picks,
          COUNT(*) FILTER (
            WHERE COALESCE(meta->>'source','baseline')='model'
              AND chosen_category = (suggested_top2->0->>'cat')
          )::int AS model_top1_hit,
          COUNT(*) FILTER (
            WHERE COALESCE(meta->>'source','baseline')='model'
              AND chosen_category IN (suggested_top2->0->>'cat', suggested_top2->1->>'cat')
          )::int AS model_top2_hit
        FROM public.ml_observations
        WHERE user_id=%s
          AND action='pick_cat'
          AND chosen_category IS NOT NULL
          AND suggested_top2 IS NOT NULL
          AND created_at >= now() - (%s || ' days')::interval
    """, (user_id, int(days)))
    if not rows:
        return {}
    keys = [
        'picks','top1_hit','top2_hit',
        'baseline_picks','baseline_top1_hit','baseline_top2_hit',
        'model_picks','model_top1_hit','model_top2_hit'
    ]
    vals = rows[0]
    return {k: int(v or 0) for k, v in zip(keys, vals)}



def get_ml_training_rows(days: int = 180, op_type: str = 'Расходы', limit: int = 20000):
    rows = pg_fetchall("""
        SELECT normalized_text, chosen_category, chosen_type
          FROM public.ml_observations
         WHERE action='pick_cat'
           AND normalized_text IS NOT NULL
           AND normalized_text<>''
           AND chosen_category IS NOT NULL
           AND chosen_category<>''
           AND created_at >= now() - (%s || ' days')::interval
           AND COALESCE(chosen_type, 'Расходы')=%s
         ORDER BY created_at DESC
         LIMIT %s
    """, (int(days), op_type, int(limit)))
    return rows

def get_last_operation(user_id: int):
    """Return last operation for user as dict with keys: id, op_date, type, category, amount."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, op_date, type, category, amount FROM operations WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "op_date": row[1], "type": row[2], "category": row[3], "amount": row[4]}

def update_last_operation_category(user_id: int, new_category: str) -> bool:
    """Update category of the last operation for user. Returns True if updated."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM operations WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return False
            op_id = row[0]
            cur.execute(
                "UPDATE operations SET category=%s WHERE id=%s",
                (new_category, op_id)
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_last_operation_fields(user_id: int, *, amount: int | None = None, category: str | None = None, op_date=None, op_type: str | None = None, comment: str | None = None) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM operations WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            op_id = row[0]
            sets = []
            vals = []
            if amount is not None:
                sets.append("amount=%s"); vals.append(int(amount))
            if category is not None:
                sets.append("category=%s"); vals.append(category)
            if op_date is not None:
                sets.append("op_date=%s"); vals.append(op_date)
            if op_type is not None:
                sets.append("type=%s"); vals.append(op_type)
            if comment is not None:
                sets.append("comment=%s"); vals.append(comment)
            if sets:
                vals.append(op_id)
                cur.execute(f"UPDATE operations SET {', '.join(sets)} WHERE id=%s", tuple(vals))
            cur.execute("SELECT id, op_date, type, category, amount, COALESCE(comment,'') FROM operations WHERE id=%s", (op_id,))
            r = cur.fetchone()
            out = {"id": r[0], "op_date": r[1], "type": r[2], "category": r[3], "amount": int(r[4]), "comment": r[5]}
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_operation_fields_by_id(user_id: int, operation_id: int, *, amount: int | None = None, category: str | None = None, op_date=None, op_type: str | None = None, comment: str | None = None) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM operations WHERE user_id=%s AND id=%s LIMIT 1", (user_id, operation_id))
            if not cur.fetchone():
                return None
            sets = []
            vals = []
            if amount is not None:
                sets.append("amount=%s"); vals.append(int(amount))
            if category is not None:
                sets.append("category=%s"); vals.append(category)
            if op_date is not None:
                sets.append("op_date=%s"); vals.append(op_date)
            if op_type is not None:
                sets.append("type=%s"); vals.append(op_type)
            if comment is not None:
                sets.append("comment=%s"); vals.append(comment)
            if sets:
                sets.append("updated_at=now()")
                vals.append(operation_id)
                cur.execute(f"UPDATE operations SET {', '.join(sets)} WHERE id=%s", tuple(vals))
            cur.execute("SELECT id, op_date, type, category, amount, COALESCE(comment,'') FROM operations WHERE id=%s", (operation_id,))
            r = cur.fetchone()
            out = {"id": r[0], "op_date": r[1], "type": r[2], "category": r[3], "amount": int(r[4]), "comment": r[5]}
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reminders_list(user_id: int, active_only: bool = True):
    sql = """SELECT id, title, rem_type, category, amount, currency, event_date, repeat_rule, repeat_interval_days, notify_days_before, is_active
             FROM public.user_reminders WHERE user_id=%s"""
    params = [user_id]
    if active_only:
        sql += " AND is_active=TRUE"
    sql += " ORDER BY event_date, id"
    rows = pg_fetchall(sql, tuple(params))
    return [dict(id=r[0], title=r[1], rem_type=r[2], category=r[3], amount=float(r[4]), currency=r[5], event_date=r[6], repeat_rule=r[7], repeat_interval_days=r[8], notify_days_before=int(r[9]), is_active=bool(r[10])) for r in rows]


def reminder_get(user_id: int, rid: int):
    rows = pg_fetchall("""SELECT id, title, rem_type, category, amount, currency, event_date, repeat_rule, repeat_interval_days, notify_days_before, is_active
                          FROM public.user_reminders WHERE user_id=%s AND id=%s LIMIT 1""", (user_id, rid))
    if not rows:
        return None
    r = rows[0]
    return dict(id=r[0], title=r[1], rem_type=r[2], category=r[3], amount=float(r[4]), currency=r[5], event_date=r[6], repeat_rule=r[7], repeat_interval_days=r[8], notify_days_before=int(r[9]), is_active=bool(r[10]))


def reminder_insert(user_id: int, payload: dict) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO public.user_reminders
                (user_id, title, rem_type, category, amount, currency, event_date, repeat_rule, repeat_interval_days, notify_days_before, is_active, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,now())
                RETURNING id""", (user_id, payload['title'], payload['rem_type'], payload['category'], payload['amount'], payload.get('currency', 'RUB'), payload['event_date'], payload['repeat_rule'], payload.get('repeat_interval_days'), payload['notify_days_before']))
            rid = int(cur.fetchone()[0])
        conn.commit()
        return rid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reminder_update(user_id: int, rid: int, **fields):
    if not fields:
        return
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=%s"); vals.append(v)
    sets.append("updated_at=now()")
    vals.extend([user_id, rid])
    pg_exec(f"UPDATE public.user_reminders SET {', '.join(sets)} WHERE user_id=%s AND id=%s", tuple(vals))


def reminder_delete(user_id: int, rid: int):
    pg_exec("DELETE FROM public.user_reminders WHERE user_id=%s AND id=%s", (user_id, rid))

# ─────────────────────────────
# Лимиты по категориям
# ─────────────────────────────

def set_category_limit(user_id: int, period: str, category: str, amount: int, currency: Optional[str] = None):
    if currency is None:
        currency = get_user_currency(user_id)
    pg_exec("""
        INSERT INTO public.category_limits (user_id, period, category, amount, currency, updated_at)
        VALUES (%s,%s,%s,%s,%s, now())
        ON CONFLICT (user_id, period, category) DO UPDATE
           SET amount=EXCLUDED.amount,
               currency=EXCLUDED.currency,
               updated_at=now()
    """, (user_id, period, category, amount, currency))

def get_category_limit(user_id: int, period: str, category: str) -> Optional[Tuple[int, str]]:
    rows = pg_fetchall("""
        SELECT amount, currency
          FROM public.category_limits
         WHERE user_id=%s AND period=%s AND category=%s
         LIMIT 1
    """, (user_id, period, category))
    return (int(rows[0][0]), rows[0][1]) if rows else None

def list_category_limits(user_id: int, period: Optional[str] = None) -> List[Tuple[str,int,str,str]]:
    """
    Возвращает список кортежей: (period, amount, currency, category), отсортированный по period, category.
    """
    if period:
        rows = pg_fetchall("""
            SELECT period, amount, currency, category
              FROM public.category_limits
             WHERE user_id=%s AND period=%s
             ORDER BY category
        """, (user_id, period))
    else:
        rows = pg_fetchall("""
            SELECT period, amount, currency, category
              FROM public.category_limits
             WHERE user_id=%s
             ORDER BY period, category
        """, (user_id,))
    return [(r[0], int(r[1]), r[2], r[3]) for r in rows]

def delete_category_limit(user_id: int, period: str, category: str):
    pg_exec("""
        DELETE FROM public.category_limits
         WHERE user_id=%s AND period=%s AND category=%s
    """, (user_id, period, category))



def list_user_limits(user_id: int):
    rows = pg_fetchall("""
        SELECT period, category, amount, currency
          FROM public.category_limits
         WHERE user_id=%s
         ORDER BY period, category
    """, (user_id,))
    return [
        {
            'period': r[0],
            'category': r[1],
            'amount': int(r[2]),
            'currency': r[3],
        }
        for r in rows
    ]


def get_limit_by_key(user_id: int, period: str, category: str):
    row = pg_fetchall("""
        SELECT period, category, amount, currency
          FROM public.category_limits
         WHERE user_id=%s AND period=%s AND category=%s
         LIMIT 1
    """, (user_id, period, category))
    if not row:
        return None
    r = row[0]
    return {'period': r[0], 'category': r[1], 'amount': int(r[2]), 'currency': r[3]}


def update_limit_amount(user_id: int, period: str, category: str, amount: int):
    pg_exec("""
        UPDATE public.category_limits
           SET amount=%s, updated_at=now()
         WHERE user_id=%s AND period=%s AND category=%s
    """, (int(amount), user_id, period, category))
    return get_limit_by_key(user_id, period, category)


def get_limit_spent(user_id: int, period: str, category: str, today: Optional[date] = None) -> int:
    today = today or date.today()
    if period == 'week':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        start = today.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = nxt - timedelta(days=1)
    rows = pg_fetchall("""
        SELECT COALESCE(SUM(amount), 0)
        FROM public.operations
        WHERE user_id=%s
          AND type='Расходы'
          AND category=%s
          AND op_date BETWEEN %s AND %s
          AND COALESCE(type,'') <> 'noop'
          AND COALESCE(category,'') <> 'Без операций'
    """, (user_id, category, start, end))
    return int(rows[0][0] if rows else 0)


def adjust_limit_amount(user_id: int, period: str, category: str, delta: int):
    row = get_limit_by_key(user_id, period, category)
    if not row:
        return {'status': 'not_found'}
    old_amount = int(row['amount'])
    new_amount = old_amount + int(delta)
    if new_amount <= 0:
        return {'status': 'too_small', 'old_amount': old_amount, 'new_amount': new_amount}
    if new_amount >= 1_000_000_000:
        return {'status': 'too_big', 'old_amount': old_amount, 'new_amount': new_amount}
    updated = update_limit_amount(user_id, period, category, new_amount)
    return {'status': 'ok', 'old_amount': old_amount, 'new_amount': new_amount, 'limit': updated}


def update_limit_period(user_id: int, old_period: str, category: str, new_period: str):
    current = get_limit_by_key(user_id, old_period, category)
    if not current:
        return {'status': 'not_found'}
    target = get_limit_by_key(user_id, new_period, category)
    if target:
        return {'status': 'conflict', 'current': current, 'target': target}
    pg_exec("""
        UPDATE public.category_limits
           SET period=%s, updated_at=now()
         WHERE user_id=%s AND period=%s AND category=%s
    """, (new_period, user_id, old_period, category))
    return {'status': 'ok', 'limit': get_limit_by_key(user_id, new_period, category)}


def resolve_limit_conflict_replace(user_id: int, old_period: str, new_period: str, category: str):
    current = get_limit_by_key(user_id, old_period, category)
    target = get_limit_by_key(user_id, new_period, category)
    if not current or not target:
        return {'status': 'not_found'}
    # переносим сумму из текущего в целевой и удаляем исходный
    pg_exec("""
        UPDATE public.category_limits
           SET amount=%s, currency=%s, updated_at=now()
         WHERE user_id=%s AND period=%s AND category=%s
    """, (current['amount'], current['currency'], user_id, new_period, category))
    pg_exec("""
        DELETE FROM public.category_limits
         WHERE user_id=%s AND period=%s AND category=%s
    """, (user_id, old_period, category))
    return {'status': 'ok', 'limit': get_limit_by_key(user_id, new_period, category)}


def delete_limit_by_key(user_id: int, period: str, category: str):
    pg_exec("""
        DELETE FROM public.category_limits
         WHERE user_id=%s AND period=%s AND category=%s
    """, (user_id, period, category))

def get_limit_state(user_id: int, period: str, category: str) -> Tuple[int, Optional[str]]:
    rows = pg_fetchall("""
        SELECT last_band, to_char(updated_at, 'YYYY-MM-DD')
          FROM public.category_limit_state
         WHERE user_id=%s AND period=%s AND category=%s
         LIMIT 1
    """, (user_id, period, category))
    if not rows:
        return 0, None
    return int(rows[0][0]), rows[0][1]

def set_limit_state(user_id: int, period: str, category: str, band: int):
    pg_exec("""
        INSERT INTO public.category_limit_state (user_id, period, category, last_band, updated_at)
        VALUES (%s,%s,%s,%s, now())
        ON CONFLICT (user_id, period, category) DO UPDATE
           SET last_band=EXCLUDED.last_band,
               updated_at=now()
    """, (user_id, period, category, band))
# --- compat shims for older routers/messages imports ---
def get_local_alias(user_id: int, text: str):
    """
    Возвращает (type, category) для нормализованного текста пользователя из user_aliases,
    либо None если не найдено.
    """
    try:
        from utils.text import norm_text as _norm_text
    except Exception:
        def _norm_text(s): return (s or '').strip().lower()
    nt = _norm_text(text)
    rows = pg_fetchall("""
        SELECT type, category
          FROM public.user_aliases
         WHERE user_id=%s AND norm_text=%s
         LIMIT 1
    """, (user_id, nt))
    return (rows[0][0], rows[0][1]) if rows else None


def get_personal_category_suggestion(user_id: int, alias_norm: str, op_type: str = 'Расходы'):
    rows = pg_fetchall("""
        SELECT type, category
        FROM public.user_aliases
        WHERE user_id=%s AND norm_text=%s AND type=%s
        LIMIT 1
    """, (user_id, alias_norm, op_type))
    if rows:
        return {'type': rows[0][0], 'category': rows[0][1], 'reason': 'personal_exact'}
    rows = pg_fetchall("""
        SELECT type, category, COUNT(*) c
        FROM public.user_aliases
        WHERE user_id=%s AND type=%s AND (norm_text LIKE %s OR %s LIKE norm_text || '%%')
        GROUP BY type, category
        ORDER BY c DESC
        LIMIT 1
    """, (user_id, op_type, f"{alias_norm}%", alias_norm))
    if rows:
        return {'type': rows[0][0], 'category': rows[0][1], 'reason': 'personal_fuzzy'}
    return None

def get_global_alias(text: str):
    """
    Возвращает (type, category) из global_aliases по нормализованному тексту,
    берём самый популярный вариант, либо None.
    """
    try:
        from utils.text import norm_text as _norm_text
    except Exception:
        def _norm_text(s): return (s or '').strip().lower()
    nt = _norm_text(text)
    rows = pg_fetchall("""
        SELECT type, category
          FROM public.global_aliases
         WHERE norm_text=%s
         ORDER BY COALESCE(popularity,0) DESC
         LIMIT 1
    """, (nt,))
    return (rows[0][0], rows[0][1]) if rows else None


def get_global_alias_exact(alias_norm: str, op_type: str = 'Расходы'):
    rows = pg_fetchall("""
        SELECT type, category, COALESCE(popularity,0)::int AS popularity
        FROM public.global_aliases
        WHERE norm_text=%s AND type=%s
        ORDER BY COALESCE(popularity,0) DESC
        LIMIT 1
    """, (alias_norm, op_type))
    if not rows:
        return None
    return {'type': rows[0][0], 'category': rows[0][1], 'popularity': int(rows[0][2] or 0)}


def get_global_category_suggestion(alias_norm: str, op_type: str = 'Расходы'):
    rows = pg_fetchall("""
        SELECT chosen_cat,
               COUNT(*)::int AS positive_votes,
               COUNT(DISTINCT user_id)::int AS distinct_users
        FROM public.category_feedback
        WHERE norm_text=%s
          AND op_type=%s
          AND event_type IN ('accept', 'decline')
          AND chosen_cat IS NOT NULL
          AND chosen_cat<>''
        GROUP BY chosen_cat
        ORDER BY positive_votes DESC
    """, (alias_norm, op_type))
    if not rows:
        return None
    total_votes = sum(int(r[1] or 0) for r in rows)
    winner = rows[0]
    cat = winner[0]
    positive_votes = int(winner[1] or 0)
    distinct_users = int(winner[2] or 0)
    dominance_ratio = (positive_votes / total_votes) if total_votes else 0.0
    log_boost = min(1.3, 1.0 + math.log1p(distinct_users) / 10.0 + math.log1p(total_votes) / 20.0)
    confidence = min(0.99, dominance_ratio * log_boost)
    level = 'low'
    if distinct_users >= 3 and positive_votes >= 3 and dominance_ratio >= 0.75:
        level = 'high'
    elif positive_votes >= 2 and dominance_ratio >= 0.60:
        level = 'medium'
    return {
        'category': cat,
        'type': op_type,
        'confidence': confidence,
        'dominance_ratio': dominance_ratio,
        'votes_count': total_votes,
        'positive_votes': positive_votes,
        'distinct_users': distinct_users,
        'level': level,
    }


def record_category_confirmation(user_id: int, raw_text: str, alias_norm: str, category: str, op_type: str, source: str):
    norm = alias_norm or normalize_alias_text(raw_text)
    upsert_user_alias(user_id, norm, op_type, category)
    bump_global_alias(norm, op_type, category, 1)
    log_category_feedback(user_id, user_id, raw_text, norm, category, category, op_type, source)


def log_category_feedback(user_id: int, chat_id: int, raw_text: str, norm_text: str,
                          suggested_cat: str, chosen_cat: str, op_type: str, event_type: str):
    pg_exec("""
        INSERT INTO public.category_feedback
          (user_id, chat_id, raw_text, norm_text, suggested_cat, chosen_cat, op_type, event_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, chat_id, raw_text, norm_text, suggested_cat, chosen_cat, op_type, event_type))


def insert_ml_observation(
    user_id: int,
    chat_id: int,
    raw_text: str,
    normalized_text: str,
    detected_type: str,
    action: str,
    suggested_top2=None,
    chosen_category: str | None = None,
    chosen_type: str | None = None,
    confidence_top1=None,
    meta=None,
):
    pg_exec("""
        INSERT INTO public.ml_observations
          (user_id, chat_id, raw_text, normalized_text, detected_type, suggested_top2,
           chosen_category, chosen_type, action, confidence_top1, meta)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        user_id,
        chat_id,
        raw_text or '',
        normalized_text or '',
        detected_type or 'Расходы',
        Json(suggested_top2) if suggested_top2 is not None else None,
        chosen_category,
        chosen_type,
        action,
        confidence_top1,
        Json(meta) if meta is not None else None,
    ))


def update_ml_observation_choice(observation_id: int, chosen_category: str | None = None, chosen_type: str | None = None):
    pg_exec("""
        UPDATE public.ml_observations
           SET chosen_category=COALESCE(%s, chosen_category),
               chosen_type=COALESCE(%s, chosen_type)
         WHERE id=%s
    """, (chosen_category, chosen_type, observation_id))
