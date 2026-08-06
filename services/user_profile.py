from __future__ import annotations

import re
from typing import Any

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from db.queries import ensure_user, get_user_currency, update_user_field
from services.currency import FX_CODES

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_PREFERRED_NAME_LEN = 50
ALLOWED_CURRENCIES = set(FX_CODES) | {"UZS", "TMT"}


def validate_preferred_name(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "\n" in text or "\r" in text or _CONTROL_RE.search(text):
        raise ValueError("invalid_preferred_name")
    if len(text) > MAX_PREFERRED_NAME_LEN:
        raise ValueError("invalid_preferred_name")
    return text


def get_user_preferred_name(user_id: int) -> str | None:
    try:
        rows = pg_fetchall("SELECT NULLIF(preferred_name, '') FROM public.users WHERE user_id=%s LIMIT 1", (user_id,))
    except errors.UndefinedColumn:
        return None
    return str(rows[0][0]) if rows and rows[0][0] else None


def set_user_preferred_name(user_id: int, value: str | None) -> str | None:
    name = validate_preferred_name(value)
    ensure_user(user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.users
                   SET preferred_name=%s
                 WHERE user_id=%s
                RETURNING preferred_name
                """,
                (name, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def get_user_display_name(user_id: int, telegram_user: Any | None = None) -> str:
    saved = get_user_preferred_name(user_id)
    if saved:
        return saved
    if telegram_user is not None:
        for attr in ("full_name", "first_name", "username"):
            value = getattr(telegram_user, attr, None)
            if value:
                return str(value)
    return "Пользователь"


def set_user_currency(user_id: int, currency: str) -> str:
    code = str(currency or "").strip().upper()
    if code not in ALLOWED_CURRENCIES:
        raise ValueError("invalid_currency")
    ensure_user(user_id)
    update_user_field(user_id, "currency", code)
    return get_user_currency(user_id)
