# db/queries.py — v2025.08.30-limits
__version__ = "2025.08.30-limits"

from typing import Optional, Tuple, List
from .database import get_conn, pg_exec, pg_fetchall
from settings import WEEK_DEFAULT, MONTH_DEFAULT

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

def insert_operation(chat_id: int, op_date, typ: str, category: str, amount: int, comment: str = 'From Telegram'):
    if not isinstance(op_date, date):
        op_date = op_date.date()
    iso = op_date.isocalendar()
    week_start = op_date.fromordinal(op_date.toordinal() - (op_date.isoweekday() - 1))
    weekday = op_date.isoweekday()  # 1..7 (Mon..Sun)
    pg_exec("""
      INSERT INTO public.operations
        (chat_id, user_id, op_date, type, category, amount, comment, week_start, iso_year, iso_week, weekday)
      VALUES
        (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat_id, chat_id, op_date, typ, category, amount, comment, week_start, int(iso.year), int(iso.week), int(weekday)))

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
    with get_conn() as conn:
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
            return cur.rowcount > 0

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


def log_category_feedback(user_id: int, chat_id: int, raw_text: str, norm_text: str,
                          suggested_cat: str, chosen_cat: str, op_type: str, event_type: str):
    pg_exec("""
        INSERT INTO public.category_feedback
          (user_id, chat_id, raw_text, norm_text, suggested_cat, chosen_cat, op_type, event_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (user_id, chat_id, raw_text, norm_text, suggested_cat, chosen_cat, op_type, event_type))
