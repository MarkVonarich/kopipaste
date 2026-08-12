from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import unicodedata

from db.database import get_conn, pg_fetchall


MAX_ITEM_TEXT = 200


class ShoppingError(ValueError):
    pass


@dataclass(frozen=True)
class ShoppingItem:
    id: int
    workspace_id: int
    text: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "text": self.text,
            "completed": self.completed_at is not None,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ShoppingSummary:
    items: list[ShoppingItem]
    active_count: int
    completed_count: int


def normalize_item_text(value: object) -> str:
    if not isinstance(value, str):
        raise ShoppingError("bad_item_text")
    raw = value
    if any(unicodedata.category(char).startswith("C") for char in raw):
        raise ShoppingError("bad_item_text")
    text = " ".join(raw.split())
    if not text or len(text) > MAX_ITEM_TEXT:
        raise ShoppingError("bad_item_text")
    return text


def _item(row) -> ShoppingItem:
    return ShoppingItem(int(row[0]), int(row[1]), str(row[2]), row[3], row[4], row[5])


def list_shopping_items(workspace_id: int, *, limit: int = 100) -> list[ShoppingItem]:
    rows = pg_fetchall(
        """
        SELECT id, workspace_id, item_text, completed_at, created_at, updated_at
          FROM public.shopping_items
         WHERE workspace_id=%s
         ORDER BY (completed_at IS NOT NULL), created_at DESC, id DESC
         LIMIT %s
        """,
        (int(workspace_id), max(1, min(int(limit), 200))),
    )
    return [_item(row) for row in rows]


def shopping_summary(workspace_id: int, *, preview_limit: int = 5) -> ShoppingSummary:
    rows = pg_fetchall(
        """
        SELECT COUNT(*) FILTER (WHERE completed_at IS NULL),
               COUNT(*) FILTER (WHERE completed_at IS NOT NULL)
          FROM public.shopping_items
         WHERE workspace_id=%s
        """,
        (int(workspace_id),),
    )
    active_count, completed_count = rows[0] if rows else (0, 0)
    return ShoppingSummary(
        items=list_shopping_items(workspace_id, limit=preview_limit),
        active_count=int(active_count or 0),
        completed_count=int(completed_count or 0),
    )


def create_shopping_item(workspace_id: int, actor_user_id: int, text: object) -> ShoppingItem:
    item_text = normalize_item_text(text)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.shopping_items (workspace_id, item_text, created_by, updated_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id, workspace_id, item_text, completed_at, created_at, updated_at
                """,
                (int(workspace_id), item_text, int(actor_user_id), int(actor_user_id)),
            )
            row = cur.fetchone()
        conn.commit()
        return _item(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_shopping_item(workspace_id: int, item_id: int, actor_user_id: int, *, text: object | None = None, completed: bool | None = None) -> ShoppingItem | None:
    if text is None and completed is None:
        raise ShoppingError("empty_update")
    fields = ["updated_by=%s", "updated_at=now()"]
    params: list[object] = [int(actor_user_id)]
    if text is not None:
        fields.append("item_text=%s")
        params.append(normalize_item_text(text))
    if completed is not None:
        fields.append("completed_at=CASE WHEN %s THEN COALESCE(completed_at, now()) ELSE NULL END")
        params.append(bool(completed))
    params.extend([int(item_id), int(workspace_id)])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE public.shopping_items SET {', '.join(fields)}
                 WHERE id=%s AND workspace_id=%s
                 RETURNING id, workspace_id, item_text, completed_at, created_at, updated_at
                """,
                tuple(params),
            )
            row = cur.fetchone()
        conn.commit()
        return _item(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_shopping_item(workspace_id: int, item_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.shopping_items WHERE id=%s AND workspace_id=%s", (int(item_id), int(workspace_id)))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_completed_shopping_items(workspace_id: int) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.shopping_items WHERE workspace_id=%s AND completed_at IS NOT NULL", (int(workspace_id),))
            count = int(cur.rowcount)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
