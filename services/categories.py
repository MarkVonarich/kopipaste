from __future__ import annotations

import re
from dataclasses import dataclass

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from utils.text import norm_text


@dataclass(frozen=True)
class CategoryResult:
    name: str
    normalized_name: str
    created: bool
    category_id: int | None = None


def normalize_category_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    if not cleaned:
        raise ValueError("category name is empty")
    return cleaned[:64]


def normalized_category_key(value: str) -> str:
    return norm_text(normalize_category_name(value)).casefold()


def get_or_create_custom_category(
    *,
    workspace_id: int | None,
    user_id: int,
    op_type: str,
    name: str,
) -> CategoryResult:
    display_name = normalize_category_name(name)
    key = normalized_category_key(display_name)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name
                  FROM public.custom_categories
                 WHERE workspace_id IS NOT DISTINCT FROM %s
                   AND type=%s
                   AND normalized_name=%s
                   AND archived_at IS NULL
                 LIMIT 1
                """,
                (workspace_id, op_type, key),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return CategoryResult(name=row[1], normalized_name=key, created=False, category_id=int(row[0]))
            cur.execute(
                """
                INSERT INTO public.custom_categories (workspace_id, user_id, type, name, normalized_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (workspace_id, user_id, op_type, display_name, key),
            )
            category_id = int(cur.fetchone()[0])
        conn.commit()
        return CategoryResult(name=display_name, normalized_name=key, created=True, category_id=category_id)
    except errors.UndefinedTable:
        conn.rollback()
        return CategoryResult(name=display_name, normalized_name=key, created=False, category_id=None)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_custom_categories(workspace_id: int | None, op_type: str) -> list[dict]:
    try:
        rows = pg_fetchall(
            """
            SELECT id, name, icon, color
              FROM public.custom_categories
             WHERE workspace_id IS NOT DISTINCT FROM %s
               AND type=%s
               AND archived_at IS NULL
             ORDER BY name
            """,
            (workspace_id, op_type),
        )
    except errors.UndefinedTable:
        return []
    return [{"id": int(r[0]), "name": r[1], "icon": r[2], "color": r[3]} for r in rows]
