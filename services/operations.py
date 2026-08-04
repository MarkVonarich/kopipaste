from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn
from db.queries import get_user_currency, insert_operation
from services.activity import record_financial_activity
from services.product_events import ProductEvent, track_product_event
from services.security_events import SecurityEvent, track_security_event
from services.workspaces import WorkspaceContext, can_add_operation, resolve_workspace
from utils.money import to_decimal_money

OperationSource = Literal["text", "voice", "ocr", "reminder", "import", "miniapp", "api"]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordedOperation:
    operation_id: int | None
    workspace_id: int | None
    actor_user_id: int
    user_id: int
    chat_id: int
    amount: Decimal
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
    amount: Decimal | int | str,
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
        "amount": str(to_decimal_money(amount, positive=True)),
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


def _record_activity_safely(*, actor_user_id: int, workspace_id: int | None, operation_id: int | None, source: str, metadata: dict | None) -> None:
    try:
        record_financial_activity(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            operation_id=operation_id,
            source=source,
            metadata=metadata or {},
        )
    except errors.UndefinedTable:
        pass
    except Exception as e:
        log.warning(
            "financial_activity_log_failed operation_id=%s workspace_id=%s source=%s reason=%s",
            operation_id,
            workspace_id,
            source,
            type(e).__name__,
        )


def insert_financial_operation_tx(
    cur,
    *,
    chat_id: int,
    actor_user_id: int,
    op_date: date | datetime,
    op_type: str,
    category: str,
    amount: Decimal | int | str,
    comment: str = "",
    source: OperationSource = "text",
    chat_type: str = "private",
    workspace: WorkspaceContext,
    raw_text: str | None = None,
) -> RecordedOperation:
    if not can_add_operation(workspace):
        track_security_event(SecurityEvent(
            event_name="permission_denied",
            user_id=actor_user_id,
            workspace_id=workspace.workspace_id,
            chat_type=chat_type,
            rule_key="operation_create",
            action_taken="denied",
            metadata={"handler": "insert_financial_operation_tx"},
        ))
        raise PermissionError("workspace is not configured or actor cannot add operations")

    dt = op_date.date() if isinstance(op_date, datetime) else op_date
    amount_dec = to_decimal_money(amount, positive=True)
    compatibility_user_id = actor_user_id if chat_type in {"group", "supergroup"} else chat_id
    currency = get_user_currency(compatibility_user_id)
    iso = dt.isocalendar()
    week_start = dt.fromordinal(dt.toordinal() - (dt.isoweekday() - 1))

    cur.execute(
        """
        INSERT INTO public.operations
          (chat_id, user_id, op_date, type, category, amount, comment,
           week_start, iso_year, iso_week, weekday,
           workspace_id, actor_user_id, source, currency, raw_text)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            chat_id, compatibility_user_id, dt, op_type, category, amount_dec, comment,
            week_start, int(iso.year), int(iso.week), int(dt.isoweekday()),
            workspace.workspace_id, actor_user_id, source, currency, raw_text,
        ),
    )
    operation_id = int(cur.fetchone()[0])
    return RecordedOperation(
        operation_id=operation_id,
        workspace_id=workspace.workspace_id,
        actor_user_id=actor_user_id,
        user_id=compatibility_user_id,
        chat_id=chat_id,
        amount=amount_dec,
        currency=currency,
        type=op_type,
        category=category,
        operation_date=dt,
        source=source,
        comment=comment,
    )


def record_financial_operation_post_commit(
    recorded: RecordedOperation,
    *,
    workspace_kind: str,
    metadata: dict | None = None,
) -> None:
    _record_activity_safely(
        actor_user_id=recorded.actor_user_id,
        workspace_id=recorded.workspace_id,
        operation_id=recorded.operation_id,
        source=recorded.source,
        metadata=metadata or {"chat_id": recorded.chat_id},
    )
    track_product_event(ProductEvent(
        event_name="operation_created",
        user_id=recorded.actor_user_id,
        workspace_id=recorded.workspace_id,
        workspace_kind=workspace_kind,
        source=recorded.source,
        currency=recorded.currency,
        status="success",
        entity_type="operation",
        entity_id=recorded.operation_id,
        properties={"operation_type": recorded.type, "category": recorded.category},
    ))


def commit_operation_draft(
    *,
    draft_id: str,
    actor_user_id: int,
    category: str,
    chat_id: int,
    workspace_id: int | None,
    chat_type: str = "group",
    metadata: dict | None = None,
) -> dict:
    ctx = resolve_workspace(chat_id, actor_user_id, chat_type)
    if not can_add_operation(ctx) or ctx.workspace_id != workspace_id:
        track_security_event(SecurityEvent(
            event_name="permission_denied",
            user_id=actor_user_id,
            workspace_id=workspace_id,
            chat_type=chat_type,
            rule_key="operation_draft_commit",
            action_taken="denied",
            metadata={"handler": "commit_operation_draft"},
        ))
        return {"status": "permission_denied"}

    compatibility_user_id = actor_user_id if chat_type in {"group", "supergroup"} else chat_id
    currency = get_user_currency(compatibility_user_id)
    conn = get_conn()
    recorded: RecordedOperation | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT draft_id, workspace_id, chat_id, actor_user_id, source, payload, status, expires_at, committed_operation_id
                  FROM public.operation_drafts
                 WHERE draft_id=%s
                 FOR UPDATE
                """,
                (draft_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return {"status": "not_found"}

            row_workspace_id = int(row[1]) if row[1] is not None else None
            row_chat_id = int(row[2])
            row_actor_user_id = int(row[3])
            payload = row[5] or {}
            status = row[6]
            committed_operation_id = int(row[8]) if row[8] is not None else None

            if row_actor_user_id != int(actor_user_id):
                conn.rollback()
                return {"status": "wrong_actor"}
            if row_chat_id != int(chat_id) or row_workspace_id != workspace_id:
                conn.rollback()
                return {"status": "scope_mismatch"}
            if status == "committed":
                conn.commit()
                return {
                    "status": "already_committed",
                    "operation_id": committed_operation_id,
                }
            if status != "draft":
                conn.rollback()
                return {"status": status or "expired"}

            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='expired', updated_at=now()
                 WHERE draft_id=%s AND status='draft' AND expires_at < now()
                 RETURNING 1
                """,
                (draft_id,),
            )
            if cur.fetchone():
                conn.commit()
                return {"status": "expired"}

            dt = date.fromisoformat(payload["op_date"])
            iso = dt.isocalendar()
            week_start = dt.fromordinal(dt.toordinal() - (dt.isoweekday() - 1))
            op_type = payload.get("type") or "Расходы"
            amount = to_decimal_money(payload.get("amount") or 0, positive=True)
            comment = payload.get("merchant") or "From group"
            source = payload.get("source") or row[4] or "text"
            raw_text = payload.get("raw_text")

            cur.execute(
                """
                INSERT INTO public.operations
                  (chat_id, user_id, op_date, type, category, amount, comment,
                   week_start, iso_year, iso_week, weekday,
                   workspace_id, actor_user_id, source, currency, raw_text)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    chat_id, compatibility_user_id, dt, op_type, category, amount, comment,
                    week_start, int(iso.year), int(iso.week), int(dt.isoweekday()),
                    workspace_id, actor_user_id, source, currency, raw_text,
                ),
            )
            operation_id = int(cur.fetchone()[0])
            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='committed', committed_operation_id=%s, updated_at=now()
                 WHERE draft_id=%s AND status='draft'
                """,
                (operation_id, draft_id),
            )
            recorded = RecordedOperation(
                operation_id=operation_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                user_id=compatibility_user_id,
                chat_id=chat_id,
                amount=amount,
                currency=currency,
                type=op_type,
                category=category,
                operation_date=dt,
                source=source,
                comment=comment,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _record_activity_safely(
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        operation_id=recorded.operation_id if recorded else None,
        source=recorded.source if recorded else "text",
        metadata=metadata or {"chat_id": chat_id, "draft_id": draft_id},
    )
    if recorded:
        track_product_event(ProductEvent(
            event_name="operation_created",
            user_id=actor_user_id,
            workspace_id=workspace_id,
            workspace_kind=ctx.kind,
            source=recorded.source,
            currency=recorded.currency,
            status="success",
            entity_type="operation",
            entity_id=recorded.operation_id,
            properties={"operation_type": recorded.type, "category": recorded.category},
        ))
    return {"status": "committed", "recorded": recorded, "operation_id": recorded.operation_id if recorded else None}


def cancel_operation_draft(
    *,
    draft_id: str,
    actor_user_id: int,
    chat_id: int,
    workspace_id: int | None,
) -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT draft_id, workspace_id, chat_id, actor_user_id, status, committed_operation_id
                  FROM public.operation_drafts
                 WHERE draft_id=%s
                 FOR UPDATE
                """,
                (draft_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return {"status": "not_found"}

            row_workspace_id = int(row[1]) if row[1] is not None else None
            row_chat_id = int(row[2])
            row_actor_user_id = int(row[3])
            status = row[4]
            committed_operation_id = int(row[5]) if row[5] is not None else None

            if row_actor_user_id != int(actor_user_id):
                conn.rollback()
                return {"status": "wrong_actor"}
            if row_chat_id != int(chat_id) or row_workspace_id != workspace_id:
                conn.rollback()
                return {"status": "scope_mismatch"}
            if status == "committed":
                conn.commit()
                return {"status": "already_committed", "operation_id": committed_operation_id}
            if status != "draft":
                conn.rollback()
                return {"status": status or "expired"}

            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='expired', updated_at=now()
                 WHERE draft_id=%s AND status='draft' AND expires_at < now()
                 RETURNING status
                """,
                (draft_id,),
            )
            expired = cur.fetchone()
            if expired:
                conn.commit()
                return {"status": expired[0]}

            cur.execute(
                """
                UPDATE public.operation_drafts
                   SET status='cancelled', updated_at=now()
                 WHERE draft_id=%s AND actor_user_id=%s AND chat_id=%s
                   AND workspace_id IS NOT DISTINCT FROM %s
                   AND status='draft'
                 RETURNING status
                """,
                (draft_id, actor_user_id, chat_id, workspace_id),
            )
            cancelled = cur.fetchone()
            if not cancelled:
                conn.rollback()
                return {"status": "conflict"}
        conn.commit()
        return {"status": cancelled[0]}
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
    amount: Decimal | int | str,
    comment: str = "",
    source: OperationSource = "text",
    chat_type: str = "private",
    workspace: WorkspaceContext | None = None,
    raw_text: str | None = None,
    metadata: dict | None = None,
) -> RecordedOperation:
    ctx = workspace or resolve_workspace(chat_id, actor_user_id, chat_type)
    if not can_add_operation(ctx):
        track_security_event(SecurityEvent(
            event_name="permission_denied",
            user_id=actor_user_id,
            workspace_id=ctx.workspace_id,
            chat_type=chat_type,
            rule_key="operation_create",
            action_taken="denied",
            metadata={"handler": "record_financial_operation"},
        ))
        raise PermissionError("workspace is not configured or actor cannot add operations")

    dt = op_date.date() if isinstance(op_date, datetime) else op_date
    amount_dec = to_decimal_money(amount, positive=True)
    compatibility_user_id = actor_user_id if chat_type in {"group", "supergroup"} else chat_id
    operation_id = insert_operation(chat_id, dt, op_type, category, amount_dec, comment)
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

    _record_activity_safely(
        actor_user_id=actor_user_id,
        workspace_id=ctx.workspace_id,
        operation_id=operation_id,
        source=source,
        metadata=metadata or {"chat_id": chat_id},
    )
    track_product_event(ProductEvent(
        event_name="operation_created",
        user_id=actor_user_id,
        workspace_id=ctx.workspace_id,
        workspace_kind=ctx.kind,
        source=source,
        currency=currency,
        status="success",
        entity_type="operation",
        entity_id=operation_id,
        properties={"operation_type": op_type, "category": category},
    ))

    return RecordedOperation(
        operation_id=operation_id,
        workspace_id=ctx.workspace_id,
        actor_user_id=actor_user_id,
        user_id=compatibility_user_id,
        chat_id=chat_id,
        amount=amount_dec,
        currency=currency,
        type=op_type,
        category=category,
        operation_date=dt,
        source=source,
        comment=comment,
    )


def update_financial_operation(
    *,
    actor_user_id: int,
    operation_id: int,
    amount: Decimal | int | str | None = None,
    category: str | None = None,
    op_date: date | datetime | None = None,
    op_type: str | None = None,
    comment: str | None = None,
    workspace_id: int | None = None,
    require_user_id: bool = True,
    source: OperationSource = "text",
    track_event: bool = True,
) -> dict | None:
    sets: list[str] = []
    vals: list = []
    changed_fields: list[str] = []
    if amount is not None:
        sets.append("amount=%s")
        vals.append(to_decimal_money(amount, positive=True))
        changed_fields.append("amount")
    if category is not None:
        sets.append("category=%s")
        vals.append(str(category).strip()[:64])
        changed_fields.append("category")
    if op_date is not None:
        sets.append("op_date=%s")
        vals.append(op_date.date() if isinstance(op_date, datetime) else op_date)
        changed_fields.append("op_date")
    if op_type is not None:
        sets.append("type=%s")
        vals.append(op_type)
        changed_fields.append("op_type")
    if comment is not None:
        sets.append("comment=%s")
        vals.append(str(comment)[:200])
        changed_fields.append("comment")
    if not sets:
        return None

    filters = ["id=%s"]
    filter_vals: list = [int(operation_id)]
    if require_user_id:
        filters.append("user_id=%s")
        filter_vals.append(int(actor_user_id))
    if workspace_id is not None:
        filters.append("workspace_id=%s")
        filter_vals.append(int(workspace_id))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE public.operations
                   SET {', '.join(sets)}, updated_at=now()
                 WHERE {' AND '.join(filters)}
                 RETURNING id, op_date, type, category, amount, COALESCE(currency,%s), COALESCE(comment,''), workspace_id, actor_user_id, created_at
                """,
                (*vals, *filter_vals, get_user_currency(actor_user_id)),
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
    out = {
        "id": int(row[0]),
        "op_date": row[1],
        "type": row[2],
        "category": row[3],
        "amount": to_decimal_money(row[4]),
        "currency": row[5],
        "comment": row[6],
        "workspace_id": row[7],
        "actor_user_id": row[8],
        "created_at": row[9],
    }
    if track_event:
        track_product_event(ProductEvent(
            event_name="operation_edited",
            user_id=actor_user_id,
            workspace_id=out["workspace_id"],
            source=source,
            status="success",
            entity_type="operation",
            entity_id=operation_id,
            properties={"changed_fields": changed_fields},
        ))
    return out


def delete_financial_operation(
    *,
    actor_user_id: int,
    operation_id: int | None = None,
    chat_id: int | None = None,
    workspace_id: int | None = None,
    require_user_id: bool = True,
    source: OperationSource = "text",
    track_event: bool = True,
) -> dict | None:
    filters: list[str] = []
    vals: list = []
    if operation_id is not None:
        filters.append("id=%s")
        vals.append(int(operation_id))
    elif chat_id is not None:
        filters.append("id = (SELECT id FROM public.operations WHERE chat_id=%s ORDER BY id DESC LIMIT 1)")
        vals.append(int(chat_id))
    else:
        raise ValueError("operation_id_or_chat_id_required")
    if require_user_id:
        filters.append("user_id=%s")
        vals.append(int(actor_user_id))
    if workspace_id is not None:
        filters.append("workspace_id=%s")
        vals.append(int(workspace_id))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM public.operations
                 WHERE {' AND '.join(filters)}
                 RETURNING id, op_date, type, category, amount, COALESCE(currency,%s), COALESCE(comment,''), workspace_id, actor_user_id, created_at
                """,
                (*vals, get_user_currency(actor_user_id)),
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
    out = {
        "id": int(row[0]),
        "op_date": row[1],
        "type": row[2],
        "category": row[3],
        "amount": to_decimal_money(row[4]),
        "currency": row[5],
        "comment": row[6],
        "workspace_id": row[7],
        "actor_user_id": row[8],
        "created_at": row[9],
    }
    if track_event:
        track_product_event(ProductEvent(
            event_name="operation_deleted",
            user_id=actor_user_id,
            workspace_id=out["workspace_id"],
            source=source,
            status="success",
            entity_type="operation",
            entity_id=out["id"],
        ))
    return out
