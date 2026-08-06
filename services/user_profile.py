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


def display_name_from_parts(
    preferred_name: str | None = None,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> str:
    saved = validate_preferred_name(preferred_name)
    if saved:
        return saved
    first = str(first_name or "").strip()
    last = str(last_name or "").strip()
    full = " ".join(part for part in (first, last) if part).strip()
    if full:
        return full
    handle = str(username or "").strip()
    if handle:
        return handle
    return "Пользователь"


def get_user_display_name(user_id: int, telegram_user: Any | None = None) -> str:
    saved = get_user_preferred_name(user_id)
    first = last = username = None
    if telegram_user is not None:
        first = getattr(telegram_user, "first_name", None)
        last = getattr(telegram_user, "last_name", None)
        username = getattr(telegram_user, "username", None)
        if not first and not last:
            full_name = getattr(telegram_user, "full_name", None)
            if full_name:
                first = str(full_name)
    return display_name_from_parts(saved, first_name=first, last_name=last, username=username)


def set_user_currency(user_id: int, currency: str) -> str:
    code = str(currency or "").strip().upper()
    if code not in ALLOWED_CURRENCIES:
        raise ValueError("invalid_currency")
    ensure_user(user_id)
    update_user_field(user_id, "currency", code)
    return get_user_currency(user_id)
