from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn
from db.queries import get_user_currency, insert_operation
from services.activity import record_financial_activity
from services.workspaces import WorkspaceContext, can_add_operation, resolve_workspace

OperationSource = Literal["text", "voice", "ocr", "reminder", "import", "miniapp", "api"]


@dataclass(frozen=True)
class RecordedOperation:
    operation_id: int | None
    workspace_id: int | None
    actor_user_id: int
    user_id: int
    chat_id: int
    amount: int
    currency: str
    type: str
    category: str
    operation_date: date
    source: str
    comment: str

    def to_dict(self) -> dict:
        out = asdict(self)
        out["operation_date"] = self.operation_date.isoformat()
        return out


def _option_key(index: int) -> str:
    return f"c{index + 1}"


def category_options(categories: list[str]) -> dict[str, str]:
    return {_option_key(i): cat for i, cat in enumerate(categories[:8])}


def create_operation_draft(
    *,
    workspace: WorkspaceContext,
    amount: int,
    op_type: str,
    merchant: str,
    op_date: date | datetime,
    source: OperationSource,
    raw_text: str,
    categories: list[str],
    note: str | None = None,
) -> str:
    dt = op_date.date() if isinstance(op_date, datetime) else op_date
    payload = {
        "amount": int(amount),
        "type": op_type,
        "merchant": merchant,
        "op_date": dt.isoformat(),
        "source": source,
        "raw_text": raw_text,
        "note": note,
        "category_options": category_options(categories),
    }
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.operation_drafts (workspace_id, chat_id, actor_user_id, source, payload)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING draft_id
                """,
                (workspace.workspace_id, workspace.chat_id, workspace.actor_user_id, source, Json(payload)),
            )
            draft_id = str(cur.fetchone()[0])
        conn.commit()
        return draft_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_operation_draft(draft_id: str, actor_user_id: int | None = None) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='expired', updated_at=now()
                 WHERE draft_id=%s AND status='draft' AND expires_at < now()
                """,
                (draft_id,),
            )
            cur.execute(
                """
                SELECT draft_id, workspace_id, chat_id, actor_user_id, source, payload, status, expires_at
                  FROM public.operation_drafts
                 WHERE draft_id=%s
                 LIMIT 1
                """,
                (draft_id,),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if not row:
        return None
    if actor_user_id is not None and int(row[3]) != int(actor_user_id):
        return {
            "draft_id": row[0],
            "workspace_id": row[1],
            "chat_id": int(row[2]),
            "actor_user_id": int(row[3]),
            "source": row[4],
            "payload": row[5] or {},
            "status": "wrong_actor",
        }
    return {
        "draft_id": row[0],
        "workspace_id": row[1],
        "chat_id": int(row[2]),
        "actor_user_id": int(row[3]),
        "source": row[4],
        "payload": row[5] or {},
        "status": row[6],
    }


def mark_operation_draft_committed(draft_id: str, operation_id: int | None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='committed', committed_operation_id=%s, updated_at=now()
                 WHERE draft_id=%s AND status='draft'
                """,
                (operation_id, draft_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_financial_operation(
    *,
    chat_id: int,
    actor_user_id: int,
    op_date: date | datetime,
    op_type: str,
    category: str,
    amount: int,
    comment: str = "From Telegram",
    source: OperationSource = "text",
    chat_type: str = "private",
    workspace: WorkspaceContext | None = None,
    raw_text: str | None = None,
    metadata: dict | None = None,
) -> RecordedOperation:
    ctx = workspace or resolve_workspace(chat_id, actor_user_id, chat_type)
    if not can_add_operation(ctx):
        raise PermissionError("workspace is not configured or actor cannot add operations")

    dt = op_date.date() if isinstance(op_date, datetime) else op_date
    compatibility_user_id = actor_user_id if chat_type in {"group", "supergroup"} else chat_id
    operation_id = insert_operation(chat_id, dt, op_type, category, amount, comment)
    currency = get_user_currency(compatibility_user_id)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.operations
                   SET workspace_id=%s,
                       actor_user_id=%s,
                       user_id=%s,
                       source=%s,
                       currency=%s,
                       raw_text=COALESCE(%s, raw_text)
                 WHERE id=%s
                """,
                (ctx.workspace_id, actor_user_id, compatibility_user_id, source, currency, raw_text, operation_id),
            )
        conn.commit()
    except errors.UndefinedColumn:
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    try:
        record_financial_activity(
            user_id=actor_user_id,
            workspace_id=ctx.workspace_id,
            operation_id=operation_id,
            source=source,
            metadata=metadata or {"chat_id": chat_id},
        )
    except errors.UndefinedTable:
        pass

    return RecordedOperation(
        operation_id=operation_id,
        workspace_id=ctx.workspace_id,
        actor_user_id=actor_user_id,
        user_id=compatibility_user_id,
        chat_id=chat_id,
        amount=int(amount),
        currency=currency,
        type=op_type,
        category=category,
        operation_date=dt,
        source=source,
        comment=comment,
    )
