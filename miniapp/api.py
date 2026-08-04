from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency, get_user_locale
from services.categories import list_managed_categories, normalized_category_key
from services.goal_planning import progress_percent
from services.goals import list_goals
from services.operations import RecordedOperation, delete_financial_operation, record_financial_operation, update_financial_operation
from services.product_events import ProductEvent, track_product_event
from services.user_time import user_local_date, user_timezone_name
from services.workspaces import WRITE_ROLES, WorkspaceContext, can_edit_operation, list_accessible_workspaces
from utils.money import MoneyParseError, format_money, to_decimal_money

log = logging.getLogger(__name__)

READ_PAGE_LIMIT = 100
DEFAULT_PAGE_SIZE = 30
MAX_PERIOD_DAYS = 366
ALLOWED_THEMES = {"telegram", "light", "dark"}
OP_TYPES = {"expense": "Расходы", "income": "Доходы", "Расходы": "Расходы", "Доходы": "Доходы"}
WRITE_RATE_WINDOW_SECONDS = 60
WRITE_RATE_LIMIT = 30


class MiniAppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status = int(status)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MiniAppRequest:
    user_id: int
    request_id: str
    locale: str | None = None


def serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.01'))}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, RecordedOperation):
        return serialize(value.to_dict())
    if is_dataclass(value):
        return serialize(asdict(value))
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    return value


def success(data: Any = None, *, request_id: str) -> dict:
    return {"ok": True, "request_id": request_id, "data": serialize(data if data is not None else {})}


def error_envelope(exc: MiniAppError, *, request_id: str) -> dict:
    return {"ok": False, "request_id": request_id, "error": {"code": exc.code, "message": exc.message}}


class MiniAppAPI:
    def __init__(self) -> None:
        self.version = os.getenv("MINIAPP_VERSION", "mvp-pr1")

    def request(self, user_id: int, *, request_id: str | None = None, locale: str | None = None) -> MiniAppRequest:
        return MiniAppRequest(user_id=int(user_id), request_id=request_id or str(uuid4()), locale=locale)

    def _track(self, req: MiniAppRequest, event_name: str, *, workspace_id: int | None = None, status: str = "success", properties: dict | None = None) -> None:
        try:
            track_product_event(ProductEvent(
                event_name=event_name,
                user_id=req.user_id,
                workspace_id=workspace_id,
                source="miniapp",
                platform="telegram_miniapp",
                status=status,
                properties=properties or {},
            ))
        except Exception as exc:
            log.info("miniapp_product_event_failed event=%s reason=%s", event_name, type(exc).__name__)

    def _check_write_rate(self, req: MiniAppRequest) -> None:
        bucket = int(datetime.utcnow().timestamp() // WRITE_RATE_WINDOW_SECONDS)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.miniapp_rate_limits (user_id, bucket, write_count, updated_at)
                    VALUES (%s, %s, 1, now())
                    ON CONFLICT (user_id, bucket) DO UPDATE
                       SET write_count=public.miniapp_rate_limits.write_count + 1,
                           updated_at=now()
                    RETURNING write_count
                    """,
                    (req.user_id, bucket),
                )
                count = int(cur.fetchone()[0])
            conn.commit()
        except errors.UndefinedTable as exc:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if count > WRITE_RATE_LIMIT:
            raise MiniAppError(429, "rate_limited", "Too many write requests.")

    def _workspace_rows(self, user_id: int) -> list[dict]:
        rows = list_accessible_workspaces(user_id)
        return [
            {
                "workspace_id": row.get("workspace_id"),
                "name": "Личное" if row.get("kind") in {"personal", "legacy_personal"} else row.get("name"),
                "kind": row.get("kind"),
                "role": row.get("role"),
                "active": bool(row.get("active")),
                "read_only": row.get("role") not in WRITE_ROLES,
            }
            for row in rows
        ]

    def _workspace_detail(self, req: MiniAppRequest, workspace_id: int | None) -> WorkspaceContext:
        for row in self._workspace_rows(req.user_id):
            if row["workspace_id"] == workspace_id:
                detail = pg_fetchall(
                    """
                    SELECT id, name, kind, COALESCE(telegram_chat_id, owner_user_id), COALESCE(owner_user_id, %s)
                      FROM public.workspaces
                     WHERE id=%s AND archived_at IS NULL
                     LIMIT 1
                    """,
                    (req.user_id, workspace_id),
                )
                if detail:
                    _id, name, kind, chat_id, _owner = detail[0]
                    return WorkspaceContext(int(_id), int(chat_id), req.user_id, kind, row["role"], name, True)
                if workspace_id is None:
                    return WorkspaceContext(None, req.user_id, req.user_id, row["kind"] or "legacy_personal", row["role"] or "owner", row["name"] or "Личное", True)
        raise MiniAppError(403, "workspace_access_denied", "Workspace is not available.")

    def _read_scope(self, req: MiniAppRequest, workspace_id: str | int | None) -> tuple[list[int | None], bool]:
        rows = self._workspace_rows(req.user_id)
        if workspace_id in {"all", "ALL"}:
            return [row["workspace_id"] for row in rows], True
        if workspace_id in {None, ""}:
            active = next((row for row in rows if row["active"]), rows[0] if rows else None)
            if not active:
                raise MiniAppError(403, "workspace_access_denied", "Workspace is not available.")
            return [active["workspace_id"]], False
        try:
            wid = int(workspace_id)
        except (TypeError, ValueError) as exc:
            raise MiniAppError(400, "bad_workspace", "Invalid workspace.") from exc
        if not any(row["workspace_id"] == wid for row in rows):
            raise MiniAppError(403, "workspace_access_denied", "Workspace is not available.")
        return [wid], False

    def _write_workspace(self, req: MiniAppRequest, workspace_id: Any) -> WorkspaceContext:
        if workspace_id in {None, "", "all", "ALL"}:
            raise MiniAppError(400, "concrete_workspace_required", "Choose one workspace for this action.")
        ctx = self._workspace_detail(req, int(workspace_id))
        if ctx.role not in WRITE_ROLES:
            raise MiniAppError(403, "workspace_read_only", "This workspace is read-only.")
        return ctx

    def _operation_write_context(self, req: MiniAppRequest, workspace_id: int | None) -> WorkspaceContext:
        if workspace_id is None:
            return WorkspaceContext(None, req.user_id, req.user_id, "legacy_personal", "owner", "Личное", True)
        return self._write_workspace(req, workspace_id)

    def _managed_categories(self, req: MiniAppRequest, workspace_id: int | None, op_type: str) -> list[dict]:
        items = list_managed_categories(user_id=req.user_id, workspace_id=workspace_id, op_type=op_type, limit=100)
        return [
            {
                "name": item.name,
                "normalized_name": item.normalized_name,
                "type": item.op_type,
                "source": item.source,
                "operation_count": item.operation_count,
                "has_budget": item.has_budget,
            }
            for item in items
        ]

    def _validate_category(self, req: MiniAppRequest, workspace_id: int | None, op_type: str, category: str) -> str:
        name = str(category or "").strip()[:64]
        if not name:
            raise MiniAppError(400, "category_required", "Choose a category.")
        key = normalized_category_key(name)
        allowed = {item["normalized_name"] for item in self._managed_categories(req, workspace_id, op_type)}
        if key not in allowed:
            raise MiniAppError(400, "category_not_available", "Choose an available category.")
        return name

    def categories(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        op_type = OP_TYPES.get(str(params.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство, чтобы увидеть категории."}, request_id=req.request_id)
        return success({"items": self._managed_categories(req, workspace_ids[0], op_type), "read_only": False}, request_id=req.request_id)

    def _period(self, req: MiniAppRequest, params: dict[str, Any], workspace_id: int | None = None) -> tuple[date, date, str]:
        today = user_local_date(req.user_id, workspace_id)
        key = params.get("period") or "current_month"
        if key == "current_month":
            start = today.replace(day=1)
            end = today
        elif key == "previous_month":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1)
            start = end.replace(day=1)
        elif key == "last_30":
            end = today
            start = today - timedelta(days=29)
        elif key == "custom":
            try:
                start = date.fromisoformat(str(params["start_date"]))
                end = date.fromisoformat(str(params["end_date"]))
            except Exception as exc:
                raise MiniAppError(400, "bad_period", "Invalid period.") from exc
        else:
            raise MiniAppError(400, "bad_period", "Invalid period.")
        if start > end or (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise MiniAppError(400, "bad_period", "Invalid period.")
        return start, end, key

    def bootstrap(self, req: MiniAppRequest, params: dict[str, Any] | None = None) -> dict:
        workspaces = self._workspace_rows(req.user_id)
        theme = self._profile_theme(req.user_id)
        timezone_name, _reason = user_timezone_name(req.user_id)
        self._track(req, "mini_app_opened", properties={"surface": "telegram_webapp"})
        return success({
            "user": {"id": str(req.user_id), "locale": get_user_locale(req.user_id), "currency": get_user_currency(req.user_id), "timezone": timezone_name},
            "workspaces": [{"workspace_id": "all", "name": "Все пространства", "kind": "all", "role": "viewer", "active": False, "read_only": True}, *workspaces],
            "periods": ["current_month", "previous_month", "last_30", "custom"],
            "theme": theme,
            "version": self.version,
        }, request_id=req.request_id)

    def workspaces(self, req: MiniAppRequest) -> dict:
        return success({"items": [{"workspace_id": "all", "name": "Все пространства", "kind": "all", "role": "viewer", "read_only": True}, *self._workspace_rows(req.user_id)]}, request_id=req.request_id)

    def _workspace_filter_sql(self, ids: list[int | None], user_id: int, *, alias: str = "") -> tuple[str, tuple]:
        prefix = f"{alias}." if alias else ""
        concrete = [int(v) for v in ids if v is not None]
        include_null = any(v is None for v in ids)
        if concrete and include_null:
            return f"({prefix}workspace_id = ANY(%s) OR ({prefix}workspace_id IS NULL AND {prefix}user_id=%s))", (concrete, int(user_id))
        if concrete:
            return f"{prefix}workspace_id = ANY(%s)", (concrete,)
        return f"{prefix}workspace_id IS NULL AND {prefix}user_id=%s", (int(user_id),)

    def overview(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        start, end, period_key = self._period(req, params, None if all_scope else workspace_ids[0])
        where, wparams = self._workspace_filter_sql(workspace_ids, req.user_id)
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(currency, %s), type, COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {where}
               AND op_date BETWEEN %s AND %s
               AND COALESCE(type,'') <> 'noop'
               AND COALESCE(category,'') <> 'Без операций'
             GROUP BY COALESCE(currency, %s), type
            """,
            (get_user_currency(req.user_id), *wparams, start, end, get_user_currency(req.user_id)),
        )
        totals: dict[str, dict[str, Decimal | int]] = {}
        for currency, typ, total, count in rows:
            bucket = totals.setdefault(currency, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            if typ == "Доходы":
                bucket["income"] = to_decimal_money(total)
            elif typ == "Расходы":
                bucket["expense"] = to_decimal_money(total)
            bucket["count"] = int(bucket["count"]) + int(count or 0)
        aggregation_available = len(totals) <= 1
        recent = self.operations(req, {**params, "limit": 7, "offset": 0})["data"]["items"]
        info = None
        if not totals:
            info = {"kind": "welcome", "text": "Добавьте первую операцию, чтобы увидеть динамику периода."}
        elif aggregation_available:
            info = {"kind": "period", "text": "Показаны подтверждённые операции за выбранный период."}
        else:
            info = {"kind": "currencies", "text": "Валюты различаются, поэтому суммы сгруппированы без автоматической конвертации."}
        return success({
            "period": {"key": period_key, "start_date": start, "end_date": end},
            "workspace_scope": "all" if all_scope else workspace_ids[0],
            "aggregation_available": aggregation_available,
            "totals_by_currency": totals,
            "recent_operations": recent,
            "info": info,
        }, request_id=req.request_id)

    def operations(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        start, end, period_key = self._period(req, params, None if all_scope else workspace_ids[0])
        limit = min(max(int(params.get("limit") or DEFAULT_PAGE_SIZE), 1), READ_PAGE_LIMIT)
        offset = max(int(params.get("offset") or 0), 0)
        where, wparams = self._workspace_filter_sql(workspace_ids, req.user_id, alias="o")
        filters = [where, "o.op_date BETWEEN %s AND %s", "COALESCE(o.type,'') <> 'noop'", "COALESCE(o.category,'') <> 'Без операций'"]
        values: list[Any] = [*wparams, start, end]
        typ = params.get("type")
        if typ:
            if typ not in OP_TYPES:
                raise MiniAppError(400, "bad_type", "Invalid operation type.")
            filters.append("o.type=%s")
            values.append(OP_TYPES[typ])
        if params.get("category"):
            filters.append("o.category=%s")
            values.append(str(params["category"])[:64])
        if params.get("member_user_id"):
            filters.append("o.actor_user_id=%s")
            values.append(int(params["member_user_id"]))
        if params.get("search"):
            filters.append("(o.comment ILIKE %s OR o.category ILIKE %s)")
            q = f"%{str(params['search']).strip()[:80]}%"
            values.extend([q, q])
        sql = f"""
            SELECT o.id, o.op_date, o.type, o.category, o.amount, COALESCE(o.currency, %s),
                   COALESCE(o.comment,''), o.workspace_id, o.actor_user_id, o.created_at,
                   COALESCE(w.name, 'Личное')
              FROM public.operations o
              LEFT JOIN public.workspaces w ON w.id=o.workspace_id
             WHERE {' AND '.join(filters)}
             ORDER BY o.op_date DESC, o.id DESC
             LIMIT %s OFFSET %s
        """
        rows = pg_fetchall(sql, (get_user_currency(req.user_id), *values, limit + 1, offset))
        items = [self._operation_dict(r, include_workspace=all_scope) for r in rows[:limit]]
        return success({"items": items, "has_more": len(rows) > limit, "limit": limit, "offset": offset, "period": {"key": period_key, "start_date": start, "end_date": end}}, request_id=req.request_id)

    def _operation_dict(self, row: tuple, *, include_workspace: bool = True) -> dict:
        amount = to_decimal_money(row[4] or 0)
        return {
            "id": int(row[0]),
            "op_date": row[1],
            "type": row[2],
            "category": row[3],
            "amount": amount,
            "amount_text": format_money(amount, row[5]),
            "currency": row[5],
            "description": row[6],
            "workspace_id": row[7],
            "actor_user_id": str(row[8]) if row[8] is not None else None,
            "created_at": row[9],
            "workspace_name": row[10] if include_workspace else None,
        }

    def operation_detail(self, req: MiniAppRequest, operation_id: int) -> dict:
        row = self._operation_row(req, operation_id)
        return success(self._operation_dict(row), request_id=req.request_id)

    def _operation_row(self, req: MiniAppRequest, operation_id: int) -> tuple:
        rows = pg_fetchall(
            """
            SELECT o.id, o.op_date, o.type, o.category, o.amount, COALESCE(o.currency, %s),
                   COALESCE(o.comment,''), o.workspace_id, o.actor_user_id, o.created_at,
                   COALESCE(w.name, 'Личное')
              FROM public.operations o
              LEFT JOIN public.workspaces w ON w.id=o.workspace_id
             WHERE o.id=%s AND (o.workspace_id IS NOT NULL OR o.user_id=%s)
             LIMIT 1
            """,
            (get_user_currency(req.user_id), int(operation_id), req.user_id),
        )
        if not rows:
            raise MiniAppError(404, "operation_not_found", "Operation was not found.")
        row = rows[0]
        self._workspace_detail(req, row[7])
        return row

    def create_operation(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        idem = str(body.get("idempotency_key") or "").strip()[:120]
        if not idem:
            raise MiniAppError(400, "idempotency_required", "Idempotency key is required.")
        ctx = self._write_workspace(req, body.get("workspace_id"))
        try:
            amount = to_decimal_money(body.get("amount"), positive=True)
            op_date = date.fromisoformat(str(body.get("op_date")))
        except (MoneyParseError, ValueError) as exc:
            raise MiniAppError(400, "bad_operation", "Invalid operation fields.") from exc
        op_type = OP_TYPES.get(str(body.get("type") or ""))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        category = str(body.get("category") or "").strip()[:64]
        description = str(body.get("description") or "").strip()[:200]
        if not category or not description:
            raise MiniAppError(400, "bad_operation", "Description and category are required.")
        category = self._validate_category(req, ctx.workspace_id, op_type, category)
        request_hash = self._request_hash(body)
        claim = self._idempotency_claim(req.user_id, idem, request_hash)
        if claim["status"] == "completed":
            return success(claim["response"], request_id=req.request_id)
        if claim["status"] != "claimed":
            raise MiniAppError(claim["http_status"], claim["status"], claim["message"])
        try:
            recorded = record_financial_operation(
                chat_id=ctx.chat_id,
                actor_user_id=req.user_id,
                op_date=op_date,
                op_type=op_type,
                category=category,
                amount=amount,
                comment=description,
                source="miniapp",
                chat_type="group" if ctx.kind == "group" else "private",
                workspace=ctx,
                raw_text=None,
                metadata={"source": "miniapp"},
            )
            operation = self._operation_dict(self._operation_row(req, recorded.operation_id)) if recorded.operation_id else recorded.to_dict()
            payload = {"operation": operation}
            self._idempotency_complete(req.user_id, idem, request_hash, payload, operation_id=recorded.operation_id)
        except Exception:
            self._idempotency_fail(req.user_id, idem, request_hash)
            raise
        self._track(req, "mini_app_transaction_created", workspace_id=ctx.workspace_id, properties={"operation_type": op_type})
        return success(payload, request_id=req.request_id)

    def update_operation(self, req: MiniAppRequest, operation_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        row = self._operation_row(req, operation_id)
        ctx = self._operation_write_context(req, row[7])
        if not can_edit_operation(ctx, row[8]):
            raise MiniAppError(403, "operation_access_denied", "Operation cannot be edited.")
        fields: dict[str, Any] = {}
        if "amount" in body:
            fields["amount"] = to_decimal_money(body["amount"], positive=True)
        if "type" in body:
            op_type = OP_TYPES.get(str(body["type"]))
            if not op_type:
                raise MiniAppError(400, "bad_type", "Invalid operation type.")
            fields["op_type"] = op_type
        if "category" in body:
            category = str(body["category"]).strip()[:64]
            row_type = fields.get("op_type") or row[2]
            fields["category"] = self._validate_category(req, ctx.workspace_id, row_type, category)
        if "description" in body:
            fields["comment"] = str(body["description"]).strip()[:200]
        if "op_date" in body:
            fields["op_date"] = date.fromisoformat(str(body["op_date"]))
        if not fields:
            raise MiniAppError(400, "bad_operation", "No editable fields provided.")
        updated = update_financial_operation(
            actor_user_id=req.user_id,
            operation_id=int(operation_id),
            workspace_id=ctx.workspace_id,
            require_user_id=ctx.workspace_id is None,
            source="miniapp",
            **fields,
        )
        if not updated:
            raise MiniAppError(404, "operation_not_found", "Operation was not found.")
        self._track(req, "mini_app_transaction_edited", workspace_id=ctx.workspace_id, properties={"changed_fields": sorted(fields)})
        return success({"operation": updated}, request_id=req.request_id)

    def delete_operation(self, req: MiniAppRequest, operation_id: int, body: dict[str, Any] | None = None) -> dict:
        self._check_write_rate(req)
        row = self._operation_row(req, operation_id)
        ctx = self._operation_write_context(req, row[7])
        if not can_edit_operation(ctx, row[8]):
            raise MiniAppError(403, "operation_access_denied", "Operation cannot be deleted.")
        deleted = delete_financial_operation(
            actor_user_id=req.user_id,
            operation_id=int(operation_id),
            workspace_id=ctx.workspace_id,
            require_user_id=ctx.workspace_id is None,
            source="miniapp",
        )
        if not deleted:
            raise MiniAppError(404, "operation_not_found", "Operation was not found.")
        self._track(req, "mini_app_transaction_deleted", workspace_id=ctx.workspace_id)
        return success({"deleted": True, "operation_id": int(operation_id)}, request_id=req.request_id)

    def analytics(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        start, end, period_key = self._period(req, params, None if all_scope else workspace_ids[0])
        overview = self.overview(req, params)["data"]
        where, wparams = self._workspace_filter_sql(workspace_ids, req.user_id)
        rows = pg_fetchall(
            f"""
            SELECT category, COALESCE(currency, %s), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {where} AND type='Расходы' AND op_date BETWEEN %s AND %s
             GROUP BY category, COALESCE(currency, %s)
             ORDER BY COALESCE(SUM(amount),0) DESC, category
             LIMIT 5
            """,
            (get_user_currency(req.user_id), *wparams, start, end, get_user_currency(req.user_id)),
        )
        return success({"period": {"key": period_key, "start_date": start, "end_date": end}, "overview": overview, "top_expense_categories": [{"category": r[0], "currency": r[1], "total": to_decimal_money(r[2]), "count": int(r[3])} for r in rows]}, request_id=req.request_id)

    def plans(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            return success({
                "read_only": True,
                "goals": [],
                "limits": [],
                "all_scope_note": "Выберите одно пространство, чтобы увидеть цели и лимиты без смешивания данных.",
            }, request_id=req.request_id)
        workspace_id = workspace_ids[0]
        goals = [
            {
                "id": goal.id,
                "title": goal.display_name,
                "target": goal.target_amount,
                "current": goal.current_balance,
                "percent": progress_percent(goal.target_amount, goal.current_balance),
                "currency": goal.currency,
                "status": goal.status,
                "deadline": goal.deadline,
            }
            for goal in list_goals(req.user_id, workspace_id, statuses=("active", "achieved", "paused"), limit=20)
        ]
        limits = []
        rows = pg_fetchall("SELECT period, category, amount, currency FROM public.category_limits WHERE user_id=%s ORDER BY period, category", (req.user_id,))
        for period, category, amount_raw, currency in rows:
            amount = to_decimal_money(amount_raw)
            spent = self._limit_spent(req.user_id, workspace_id, period, category)
            remaining = amount - spent
            percent = int(min(999, (spent / amount * 100).to_integral_value())) if amount > 0 else 0
            limits.append({
                "period": period,
                "category": category,
                "amount": amount,
                "currency": currency,
                "spent": spent,
                "remaining": remaining,
                "percent": percent,
                "status": "exceeded" if spent > amount else "near" if percent >= 80 else "ok",
            })
        return success({"read_only": True, "goals": goals, "limits": limits, "all_scope_note": None}, request_id=req.request_id)

    def _limit_spent(self, user_id: int, workspace_id: int | None, period: str, category: str, today: date | None = None) -> Decimal:
        today = today or user_local_date(user_id, workspace_id)
        if period == "week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        else:
            start = today.replace(day=1)
            nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = nxt - timedelta(days=1)
        where, params = self._workspace_filter_sql([workspace_id], user_id)
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(SUM(amount), 0)
              FROM public.operations
             WHERE {where}
               AND type='Расходы'
               AND category=%s
               AND op_date BETWEEN %s AND %s
               AND COALESCE(type,'') <> 'noop'
               AND COALESCE(category,'') <> 'Без операций'
            """,
            (*params, category, start, end),
        )
        return to_decimal_money(rows[0][0] if rows else 0)

    def profile(self, req: MiniAppRequest) -> dict:
        timezone_name, _reason = user_timezone_name(req.user_id)
        return success({
            "theme": self._profile_theme(req.user_id),
            "currency": get_user_currency(req.user_id),
            "timezone": timezone_name,
            "workspaces": self._workspace_rows(req.user_id),
            "help_url": os.getenv("MINIAPP_HELP_URL", "https://t.me/chiracredible"),
            "links": {
                "privacy": os.getenv("MINIAPP_PRIVACY_URL") or None,
                "terms": os.getenv("MINIAPP_TERMS_URL") or None,
            },
            "version": self.version,
        }, request_id=req.request_id)

    def set_theme(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        theme = str(body.get("theme") or "telegram")
        if theme not in ALLOWED_THEMES:
            raise MiniAppError(400, "bad_theme", "Invalid theme.")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.miniapp_user_preferences (user_id, theme, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET theme=EXCLUDED.theme, updated_at=now()
                    """,
                    (req.user_id, theme),
                )
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._track(req, "mini_app_theme_changed", properties={"theme": theme})
        return success({"theme": theme}, request_id=req.request_id)

    def track_ui_event(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        event = str(body.get("event") or "")
        allowed = {
            "mini_app_tab_opened",
            "mini_app_workspace_changed",
            "mini_app_period_changed",
            "mini_app_transaction_add_opened",
        }
        if event not in allowed:
            raise MiniAppError(400, "bad_event", "Invalid analytics event.")
        props = {k: v for k, v in (body.get("properties") or {}).items() if k in {"tab", "period", "scope", "action"}}
        self._track(req, event, properties=props)
        return success({"tracked": True}, request_id=req.request_id)

    def _profile_theme(self, user_id: int) -> str:
        try:
            rows = pg_fetchall("SELECT theme FROM public.miniapp_user_preferences WHERE user_id=%s LIMIT 1", (user_id,))
        except errors.UndefinedTable:
            return "telegram"
        return rows[0][0] if rows and rows[0][0] in ALLOWED_THEMES else "telegram"

    def _request_hash(self, request_body: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(serialize(request_body), sort_keys=True).encode("utf-8")).hexdigest()

    def _idempotency_claim(self, user_id: int, key: str, request_hash: str) -> dict:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.miniapp_idempotency_keys
                      (user_id, idempotency_key, request_hash, status, updated_at)
                    VALUES (%s, %s, %s, 'pending', now())
                    ON CONFLICT (user_id, idempotency_key) DO NOTHING
                    RETURNING status
                    """,
                    (user_id, key, request_hash),
                )
                inserted = cur.fetchone()
                if inserted:
                    conn.commit()
                    return {"status": "claimed"}
                cur.execute(
                    """
                    SELECT request_hash, status, response_json
                      FROM public.miniapp_idempotency_keys
                     WHERE user_id=%s AND idempotency_key=%s
                     LIMIT 1
                    """,
                    (user_id, key),
                )
                row = cur.fetchone()
            conn.commit()
        except errors.UndefinedTable as exc:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if not row:
            raise MiniAppError(503, "idempotency_unavailable", "Could not verify request status.")
        existing_hash, status, response = row
        if existing_hash != request_hash:
            return {"status": "idempotency_conflict", "http_status": 409, "message": "This idempotency key was used for a different request."}
        if status == "completed":
            return {"status": "completed", "response": response or {}}
        if status == "failed":
            return {"status": "idempotency_failed", "http_status": 503, "message": "Previous request failed. Open the form again and retry."}
        return {"status": "idempotency_pending", "http_status": 409, "message": "Request is already being processed."}

    def _idempotency_complete(self, user_id: int, key: str, request_hash: str, response: dict, *, operation_id: int | None) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.miniapp_idempotency_keys
                       SET operation_id=%s,
                           status='completed',
                           response_json=%s,
                           updated_at=now()
                     WHERE user_id=%s
                       AND idempotency_key=%s
                       AND request_hash=%s
                       AND status='pending'
                    """,
                    (operation_id, Json(serialize(response)), user_id, key, request_hash),
                )
                if cur.rowcount != 1:
                    raise MiniAppError(503, "idempotency_unavailable", "Could not persist request status.")
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _idempotency_fail(self, user_id: int, key: str, request_hash: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.miniapp_idempotency_keys
                       SET status='failed', updated_at=now()
                     WHERE user_id=%s
                       AND idempotency_key=%s
                       AND request_hash=%s
                       AND status='pending'
                    """,
                    (user_id, key, request_hash),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.info("miniapp_idempotency_fail_mark_failed user=%s reason=%s", user_id, type(exc).__name__)
        finally:
            conn.close()
