from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal

from psycopg2 import errors

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
