from __future__ import annotations

import hashlib
import json
import logging
import os
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency, get_user_locale
from services.budgeting import list_general_limits
from services.categories import list_managed_categories, normalized_category_key
from services.challenges import ChallengeCard, upsert_assignments
from services.goal_planning import (
    FREQUENCY_MONTHLY,
    FREQUENCY_NONE,
    FREQUENCY_TWICE_MONTHLY,
    FREQUENCY_WEEKLY,
    STRATEGY_CONTRIBUTION,
    STRATEGY_DEADLINE,
    STRATEGY_NONE,
    ScheduleConfig,
    calculate_contribution_first,
    calculate_deadline_first,
    progress_percent,
    remaining_amount,
)
from services.goals import (
    Goal,
    GoalError,
    add_goal_movement,
    create_goal_with_plan_tx,
    get_goal,
    list_goals,
    list_movements,
    set_goal_reminders,
    set_goal_status,
    update_goal_details,
    update_goal_plan,
)
from services.operations import (
    RecordedOperation,
    delete_financial_operation,
    insert_financial_operation_tx,
    record_financial_operation_post_commit,
    update_financial_operation,
)
from services.product_events import ProductEvent, track_product_event
from services.limit_alerts import alert_status_for_band, threshold_band
from services.miniapp_limits import (
    MiniAppLimitError,
    StoredLimit,
    create_or_update_general_limit,
    create_or_update_general_limit_tx,
    delete_limit as delete_stored_limit,
    replace_category_limit,
    replace_category_limit_tx,
)
from services.notification_preferences import (
    TOGGLE_FIELDS,
    get_notification_preferences,
    set_notification_timezone,
    set_quiet_hours,
    set_quiet_hours_time,
    toggle_notification_preference,
    toggle_quiet_hours,
)
from services.user_profile import ALLOWED_CURRENCIES, display_name_from_parts, get_user_preferred_name, set_user_currency, set_user_preferred_name
from services.user_time import TIMEZONE_CHOICES, user_local_date, user_timezone_name
from services.workspaces import WRITE_ROLES, WorkspaceContext, can_edit_operation, list_accessible_workspaces, rename_workspace, set_active_workspace
from utils.money import MoneyParseError, format_money, to_decimal_money

log = logging.getLogger(__name__)

READ_PAGE_LIMIT = 100
DEFAULT_PAGE_SIZE = 30
MAX_PERIOD_DAYS = 366
ALLOWED_THEMES = {"telegram", "light", "dark"}
OP_TYPES = {"expense": "Расходы", "income": "Доходы", "Расходы": "Расходы", "Доходы": "Доходы"}
READ_OPERATION_TYPES = {"all": None, "expense": "Расходы", "income": "Доходы", "Расходы": "Расходы", "Доходы": "Доходы"}
WRITE_RATE_WINDOW_SECONDS = 60
WRITE_RATE_LIMIT = 30
WRITE_RATE_RETENTION_BUCKETS = 24 * 60
IDEMPOTENCY_LEASE_SECONDS = 5 * 60
CHART_TOP_N = 5
RADAR_MAX_AXES = 6
GOAL_FREQUENCIES = {FREQUENCY_NONE, FREQUENCY_MONTHLY, FREQUENCY_TWICE_MONTHLY, FREQUENCY_WEEKLY}
GOAL_STRATEGIES = {STRATEGY_NONE, STRATEGY_DEADLINE, STRATEGY_CONTRIBUTION}
LIMIT_PERIODS = {"week", "month"}
NOTIFICATION_KEYS = set(TOGGLE_FIELDS)


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
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    telegram_username: str | None = None


@dataclass(frozen=True)
class TransactionFilters:
    workspace_ids: list[int | None]
    all_scope: bool
    start: date
    end: date
    period_key: str
    operation_type: str
    category: str | None
    where_sql: str
    params: tuple[Any, ...]


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
        self.version = os.getenv("MINIAPP_VERSION", "mvp-pr2")

    def request(
        self,
        user_id: int,
        *,
        request_id: str | None = None,
        locale: str | None = None,
        telegram_first_name: str | None = None,
        telegram_last_name: str | None = None,
        telegram_username: str | None = None,
    ) -> MiniAppRequest:
        return MiniAppRequest(
            user_id=int(user_id),
            request_id=request_id or str(uuid4()),
            locale=locale,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
            telegram_username=telegram_username,
        )

    def _display_name(self, req: MiniAppRequest, preferred_name: str | None = None) -> str:
        return display_name_from_parts(
            preferred_name,
            first_name=req.telegram_first_name,
            last_name=req.telegram_last_name,
            username=req.telegram_username,
        )

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
        if bucket % 10 == 0:
            self._cleanup_write_rate(bucket)

    def _cleanup_write_rate(self, current_bucket: int) -> None:
        cutoff = int(current_bucket) - WRITE_RATE_RETENTION_BUCKETS
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM public.miniapp_rate_limits WHERE bucket < %s", (cutoff,))
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
        except Exception as exc:
            conn.rollback()
            log.info("miniapp_rate_limit_cleanup_failed reason=%s", type(exc).__name__)
        finally:
            conn.close()

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
        if key == "last_30":
            key = "current_week"
        if key == "current_week":
            start = today - timedelta(days=today.weekday())
            end = today
        elif key == "current_month":
            start = today.replace(day=1)
            end = today
        elif key == "previous_month":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1)
            start = end.replace(day=1)
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

    def _operation_type_filter(self, params: dict[str, Any]) -> tuple[str, str | None]:
        raw = str(params.get("operation_type") or params.get("type") or "all")
        if raw not in READ_OPERATION_TYPES:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        if raw in {"Расходы", "Доходы"}:
            key = "expense" if raw == "Расходы" else "income"
        else:
            key = raw
        return key, READ_OPERATION_TYPES[raw]

    def _category_filter(self, params: dict[str, Any]) -> str | None:
        raw = str(params.get("category") or "").strip()
        if not raw or raw == "all":
            return None
        if len(raw) > 64:
            raise MiniAppError(400, "bad_category", "Invalid category.")
        return raw

    def _transaction_filters(self, req: MiniAppRequest, params: dict[str, Any], *, alias: str = "") -> TransactionFilters:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        start, end, period_key = self._period(req, params, None if all_scope else workspace_ids[0])
        operation_type, db_type = self._operation_type_filter(params)
        category = self._category_filter(params)
        where, wparams = self._workspace_filter_sql(workspace_ids, req.user_id, alias=alias)
        prefix = f"{alias}." if alias else ""
        filters = [
            where,
            f"{prefix}op_date BETWEEN %s AND %s",
            f"COALESCE({prefix}type,'') <> 'noop'",
            f"COALESCE({prefix}category,'') <> 'Без операций'",
        ]
        values: list[Any] = [*wparams, start, end]
        if db_type:
            filters.append(f"{prefix}type=%s")
            values.append(db_type)
        if category:
            filters.append(f"{prefix}category=%s")
            values.append(category)
        return TransactionFilters(
            workspace_ids=workspace_ids,
            all_scope=all_scope,
            start=start,
            end=end,
            period_key=period_key,
            operation_type=operation_type,
            category=category,
            where_sql=" AND ".join(filters),
            params=tuple(values),
        )

    def bootstrap(self, req: MiniAppRequest, params: dict[str, Any] | None = None) -> dict:
        workspaces = self._workspace_rows(req.user_id)
        theme = self._profile_theme(req.user_id)
        timezone_name, _reason = user_timezone_name(req.user_id)
        preferred_name = get_user_preferred_name(req.user_id)
        self._track(req, "mini_app_opened", properties={"surface": "telegram_webapp"})
        return success({
            "user": {"id": str(req.user_id), "locale": get_user_locale(req.user_id), "currency": get_user_currency(req.user_id), "timezone": timezone_name, "preferred_name": preferred_name, "display_name": self._display_name(req, preferred_name)},
            "workspaces": [{"workspace_id": "all", "name": "Все пространства", "kind": "all", "role": "viewer", "active": False, "read_only": True}, *workspaces],
            "periods": ["current_week", "current_month", "previous_month", "custom"],
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

    def _write_scope(self, req: MiniAppRequest, workspace_id: Any) -> WorkspaceContext:
        ctx = self._write_workspace(req, workspace_id)
        if ctx.workspace_id is None:
            return WorkspaceContext(None, req.user_id, req.user_id, "legacy_personal", "owner", "Личное", True)
        return ctx

    def _chart_grouping(self, start: date, end: date) -> str:
        days = (end - start).days + 1
        if days <= 45:
            return "day"
        if days <= 120:
            return "week"
        return "month"

    def _bucket_date(self, value: date, grouping: str) -> date:
        if grouping == "month":
            return value.replace(day=1)
        if grouping == "week":
            return value - timedelta(days=value.weekday())
        return value

    def _previous_period(self, start: date, end: date, period_key: str) -> tuple[date, date, str]:
        if period_key == "current_month":
            prev_end = start - timedelta(days=1)
            return prev_end.replace(day=1), prev_end, "previous_month"
        if period_key == "previous_month":
            prev_end = start - timedelta(days=1)
            return prev_end.replace(day=1), prev_end, "month_before_previous"
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        return prev_end - timedelta(days=length - 1), prev_end, "previous_equal_period"

    def _safe_goal_event(self, req: MiniAppRequest, event_name: str, *, workspace_id: int | None, action: str, result: str = "success") -> None:
        self._track(req, event_name, workspace_id=workspace_id, properties={"action": action, "result": result, "source": "mini_app"})

    def _goal_dict(self, goal: Goal) -> dict:
        remaining = remaining_amount(goal.target_amount, goal.current_balance)
        if goal.status == "achieved":
            next_action = "Цель выполнена"
        elif goal.status == "paused":
            next_action = "Цель приостановлена"
        elif goal.status == "archived":
            next_action = "Цель в архиве"
        elif goal.planned_contribution_amount and goal.next_contribution_date:
            next_action = f"Пополнить {format_money(goal.planned_contribution_amount, goal.currency)} до {goal.next_contribution_date.isoformat()}"
        elif goal.comfortable_amount:
            next_action = f"Комфортное пополнение {format_money(goal.comfortable_amount, goal.currency)}"
        elif goal.deadline:
            next_action = "Настройте периодичность пополнений"
        else:
            next_action = "План не настроен"
        return {
            "id": goal.id,
            "title": goal.display_name,
            "target": goal.target_amount,
            "current": goal.current_balance,
            "remaining": remaining,
            "percent": progress_percent(goal.target_amount, goal.current_balance),
            "currency": goal.currency,
            "status": goal.status,
            "deadline": goal.deadline,
            "strategy": goal.strategy,
            "frequency": goal.frequency,
            "comfortable_amount": goal.comfortable_amount,
            "planned_contribution_amount": goal.planned_contribution_amount,
            "schedule_config": goal.schedule_config,
            "projected_completion_date": goal.projected_completion_date,
            "next_contribution_date": goal.next_contribution_date,
            "reminders_enabled": goal.reminders_enabled,
            "next_action": next_action,
            "movement_count": goal.movement_count,
        }

    def _movement_dict(self, movement) -> dict:
        return {
            "id": movement.id,
            "goal_id": movement.goal_id,
            "movement_type": movement.movement_type,
            "amount": movement.amount,
            "balance_after": movement.balance_after,
            "occurred_at": movement.occurred_at,
            "source": movement.source,
        }

    def _schedule_from_body(self, body: dict[str, Any], frequency: str) -> dict[str, Any]:
        if frequency == FREQUENCY_MONTHLY:
            if body.get("day") in {None, ""}:
                raise MiniAppError(400, "schedule_required", "Choose a monthly day.")
            day = int(body.get("day"))
            if day < 1 or day > 28:
                raise MiniAppError(400, "bad_goal_schedule", "Monthly day must be 1-28.")
            return {"day": day}
        if frequency == FREQUENCY_TWICE_MONTHLY:
            days = body.get("days") if isinstance(body.get("days"), list) else []
            clean = sorted({int(day) for day in days if str(day).strip()})
            if len(clean) != 2:
                raise MiniAppError(400, "schedule_required", "Choose two monthly days.")
            if clean[0] == clean[1] or clean[0] < 1 or clean[1] > 28:
                raise MiniAppError(400, "bad_goal_schedule", "Twice-monthly days must be distinct and 1-28.")
            return {"days": clean}
        if frequency == FREQUENCY_WEEKLY:
            if body.get("weekday") in {None, ""}:
                raise MiniAppError(400, "schedule_required", "Choose a weekday.")
            weekday = int(body.get("weekday"))
            if weekday < 0 or weekday > 6:
                raise MiniAppError(400, "bad_goal_schedule", "Weekday must be 0-6.")
            return {"weekday": weekday}
        return {}

    def _schedule_config(self, schedule: dict[str, Any], frequency: str) -> ScheduleConfig:
        return ScheduleConfig(
            frequency=frequency,
            day=int(schedule.get("day")) if schedule.get("day") is not None else None,
            days=tuple(int(day) for day in schedule.get("days") or ()),
            weekday=int(schedule.get("weekday")) if schedule.get("weekday") is not None else None,
        )

    def _plan_preview(self, req: MiniAppRequest, body: dict[str, Any], goal: Goal | None = None) -> dict:
        strategy = str(body.get("strategy") or (goal.strategy if goal else STRATEGY_NONE))
        frequency = str(body.get("frequency") or (goal.frequency if goal else FREQUENCY_NONE))
        if strategy not in GOAL_STRATEGIES or frequency not in GOAL_FREQUENCIES:
            raise MiniAppError(400, "bad_goal_plan", "Invalid goal plan.")
        if bool(body.get("reminders_enabled")) and frequency == FREQUENCY_NONE:
            raise MiniAppError(400, "schedule_required", "Choose a reminder schedule.")
        target = to_decimal_money(body.get("target_amount") if body.get("target_amount") is not None else (goal.target_amount if goal else 0), positive=True)
        current = to_decimal_money(body.get("current_amount") if body.get("current_amount") is not None else (goal.current_balance if goal else 0))
        deadline = date.fromisoformat(str(body["deadline"])) if body.get("deadline") else (goal.deadline if goal else None)
        comfortable = to_decimal_money(body.get("comfortable_amount"), positive=True) if body.get("comfortable_amount") not in {None, ""} else None
        schedule = self._schedule_from_body(body, frequency)
        cfg = self._schedule_config(schedule, frequency)
        raw_workspace_id = body.get("workspace_id")
        preview_workspace_id = goal.workspace_id if goal else (int(raw_workspace_id) if raw_workspace_id not in {None, "", "all", "ALL"} else None)
        today = user_local_date(req.user_id, preview_workspace_id)
        if strategy == STRATEGY_DEADLINE:
            plan = calculate_deadline_first(target_amount=target, current_balance=current, deadline=deadline, schedule=cfg, today=today)
        elif strategy == STRATEGY_CONTRIBUTION and comfortable is not None:
            plan = calculate_contribution_first(target_amount=target, current_balance=current, comfortable_amount=comfortable, schedule=cfg, today=today)
        else:
            plan = calculate_contribution_first(target_amount=target, current_balance=current, comfortable_amount=Decimal("1.00"), schedule=ScheduleConfig(), today=today)
            plan = type(plan)(STRATEGY_NONE, FREQUENCY_NONE, remaining_amount(target, current), 0, feasible=True, reason="no_plan")
        return {
            "strategy": plan.strategy,
            "frequency": plan.frequency,
            "remaining_amount": plan.remaining_amount,
            "occurrence_count": plan.occurrence_count,
            "recommended_amount": plan.recommended_amount,
            "comfortable_amount": plan.comfortable_amount,
            "next_occurrence": plan.next_occurrence,
            "projected_completion_date": plan.projected_completion_date,
            "required_contributions": plan.required_contributions,
            "feasible": plan.feasible,
            "reason": plan.reason,
            "schedule_config": schedule,
            "preview_payload_hash": self._goal_preview_hash(req, body, goal),
        }

    def _goal_preview_hash(self, req: MiniAppRequest, body: dict[str, Any], goal: Goal | None = None, goal_id: int | None = None) -> str:
        strategy = str(body.get("strategy") or (goal.strategy if goal else STRATEGY_NONE))
        frequency = str(body.get("frequency") or (goal.frequency if goal else FREQUENCY_NONE))
        schedule = self._schedule_from_body(body, frequency)
        target = to_decimal_money(body.get("target_amount") if body.get("target_amount") is not None else (goal.target_amount if goal else 0), positive=True)
        current = to_decimal_money(body.get("current_amount") if body.get("current_amount") is not None else (goal.current_balance if goal else 0))
        comfortable = to_decimal_money(body.get("comfortable_amount"), positive=True) if body.get("comfortable_amount") not in {None, ""} else None
        deadline = str(body.get("deadline") or (goal.deadline.isoformat() if goal and goal.deadline else ""))
        raw_workspace_id = body.get("workspace_id")
        workspace_id = goal.workspace_id if goal else (int(raw_workspace_id) if raw_workspace_id not in {None, "", "all", "ALL"} else None)
        payload = {
            "user_id": int(req.user_id),
            "workspace_id": workspace_id,
            "goal_id": int(goal_id if goal_id is not None else (goal.id if goal else 0)),
            "target_amount": serialize(target),
            "current_amount": serialize(current),
            "deadline": deadline,
            "strategy": strategy,
            "frequency": frequency,
            "schedule_config": schedule,
            "comfortable_amount": serialize(comfortable) if comfortable is not None else None,
            "reminders_enabled": bool(body.get("reminders_enabled", goal.reminders_enabled if goal else False)),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _require_goal_preview_hash(self, req: MiniAppRequest, body: dict[str, Any], goal: Goal | None = None, goal_id: int | None = None) -> None:
        submitted = str(body.get("preview_payload_hash") or "").strip()
        expected = self._goal_preview_hash(req, body, goal, goal_id)
        if not submitted or submitted != expected:
            raise MiniAppError(409, "goal_preview_stale", "Goal preview is stale.")

    def _limit_period_dates(self, user_id: int, workspace_id: int | None, period: str) -> tuple[date, date]:
        today = user_local_date(user_id, workspace_id)
        if period == "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start, end

    def _limit_status(self, spent: Decimal, amount: Decimal) -> tuple[str, int]:
        band = threshold_band(spent, amount)
        if band is None:
            pct = int(min(999, (spent / amount * 100).to_integral_value())) if amount > 0 else 0
            if pct >= 50:
                return "half_used", pct
            return "normal", pct
        return alert_status_for_band(band), int(min(999, (spent / amount * 100).to_integral_value())) if amount > 0 else 0

    def _limit_projection_percent(self, *, spent: Decimal, amount: Decimal, period: str, today: date) -> int | None:
        if amount <= 0 or spent <= 0:
            return None
        if period == "week":
            period_start = today - timedelta(days=today.weekday())
            period_end = period_start + timedelta(days=6)
        else:
            period_start = today.replace(day=1)
            period_end = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        elapsed_days = (today - period_start).days + 1
        total_days = (period_end - period_start).days + 1
        if elapsed_days < 2 or total_days <= 0:
            return None
        projected_end = spent / Decimal(elapsed_days) * Decimal(total_days)
        return int((projected_end / amount * Decimal("100")).to_integral_value())

    def _limit_dict(
        self,
        *,
        user_id: int,
        kind: str,
        identifier: str,
        title: str,
        category: str | None,
        amount: Decimal,
        currency: str,
        period: str,
        workspace_id: int | None,
        alerts_enabled: bool = True,
    ) -> dict:
        spent = self._limit_spent(user_id, workspace_id, period, category)
        remaining = amount - spent
        status, percent = self._limit_status(spent, amount)
        return {
            "id": identifier,
            "kind": kind,
            "title": title,
            "category": category,
            "scope": "category" if category else "all_expenses",
            "amount": amount,
            "currency": currency,
            "spent": spent,
            "remaining": remaining,
            "percent": percent,
            "period": period,
            "status": status,
            "alerts_enabled": bool(alerts_enabled),
            "workspace_id": workspace_id,
            "icon": "category" if category else "wallet",
        }

    def _stored_limit_dict(self, req: MiniAppRequest, stored: StoredLimit) -> dict:
        return self._limit_dict(
            user_id=req.user_id,
            kind=stored.kind,
            identifier=stored.identifier,
            title=stored.title,
            category=stored.category,
            amount=stored.amount,
            currency=stored.currency,
            period=stored.period,
            workspace_id=stored.workspace_id,
            alerts_enabled=stored.alerts_enabled,
        )


    def overview(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        tx = self._transaction_filters(req, params)
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(currency, %s), type, COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
             GROUP BY COALESCE(currency, %s), type
            """,
            (get_user_currency(req.user_id), *tx.params, get_user_currency(req.user_id)),
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
        recent = self.operations(req, {**params, "limit": 3, "offset": 0})["data"]["items"][:3]
        info = None
        if not totals:
            info = {"kind": "welcome", "text": "Добавьте первую операцию, чтобы увидеть динамику периода."}
        elif aggregation_available:
            info = {"kind": "period", "text": "Показаны подтверждённые операции за выбранный период."}
        else:
            info = {"kind": "currencies", "text": "Валюты различаются, поэтому суммы сгруппированы без автоматической конвертации."}
        return success({
            "period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end},
            "filters": {"operation_type": tx.operation_type, "category": tx.category or "all"},
            "workspace_scope": "all" if tx.all_scope else tx.workspace_ids[0],
            "aggregation_available": aggregation_available,
            "totals_by_currency": totals,
            "recent_operations": recent,
            "info": info,
            "challenge": self._home_challenge(req),
            "focus": self._home_focus(req, params, tx),
            "insight": self._home_insight(req, tx, totals),
            "reminder": self._home_reminder(req),
        }, request_id=req.request_id)

    def _home_challenge(self, req: MiniAppRequest) -> dict | None:
        try:
            cards = upsert_assignments(req.user_id, "today")
        except Exception as exc:
            log.info("miniapp_home_challenge_unavailable reason=%s", type(exc).__name__)
            return None
        if not cards:
            return None
        active = [card for card in cards if not card.completed]
        card = active[0] if active else cards[0]
        return self._challenge_dict(card)

    def _challenge_dict(self, card: ChallengeCard) -> dict:
        return {
            "key": card.definition.key,
            "title": card.definition.title,
            "description": card.definition.description,
            "progress": min(card.progress, card.target),
            "target": card.target,
            "completed": bool(card.completed),
            "cta_label": card.definition.cta_label,
            "period_key": card.period_key,
            "period_end": card.period_end,
        }

    def _home_focus(self, req: MiniAppRequest, params: dict[str, Any], tx: TransactionFilters) -> dict | None:
        if tx.all_scope:
            return {"kind": "empty", "title": "Выберите пространство", "description": "Фокус доступен для одного пространства.", "read_only": True}
        candidates: list[dict] = []
        severity_rank = {"critical": 400, "high": 300, "medium": 200, "normal": 100}
        today = user_local_date(req.user_id, tx.workspace_ids[0])
        try:
            goals = self.goals(req, params)["data"].get("items", [])
            for goal in goals:
                percent = int(goal.get("percent") or 0)
                severity = "normal"
                reason = goal.get("next_action") or "Проверьте план цели."
                days_left: int | None = None
                if goal.get("deadline"):
                    try:
                        days_left = (date.fromisoformat(str(goal["deadline"])) - today).days
                    except ValueError:
                        days_left = None
                if days_left is not None and days_left < 0 and percent < 100:
                    severity, reason = "critical", "Срок цели уже прошёл."
                elif goal.get("next_contribution_date"):
                    try:
                        contribution_days = (date.fromisoformat(str(goal["next_contribution_date"])) - today).days
                    except ValueError:
                        contribution_days = 999
                    if contribution_days < 0:
                        severity, reason = "critical", "Плановый взнос просрочен."
                    elif contribution_days <= 2:
                        severity, reason = "high", "Ближайший взнос уже рядом."
                    elif contribution_days <= 7:
                        severity, reason = "medium", "Скоро плановый взнос."
                if goal.get("projected_completion_date") and goal.get("deadline") and goal.get("strategy") == STRATEGY_DEADLINE:
                    try:
                        if date.fromisoformat(str(goal["projected_completion_date"])) > date.fromisoformat(str(goal["deadline"])) and percent < 100:
                            severity, reason = ("high" if severity != "critical" else severity), "Текущий темп может не успеть к сроку."
                    except ValueError:
                        pass
                if days_left is not None and 0 <= days_left <= 14 and percent < 100 and severity == "normal":
                    severity, reason = "medium", "Дедлайн цели близко."
                score = severity_rank[severity] + min(percent, 100)
                candidates.append({
                    "score": score,
                    "severity": severity,
                    "kind": "goal",
                    "id": goal.get("id"),
                    "title": goal.get("title") or "Цель",
                    "description": reason,
                    "percent": percent,
                    "status": severity if severity != "normal" else goal.get("status") or "active",
                    "cta_label": "Открыть цели",
                    "target_mode": "goals",
                })
        except Exception as exc:
            log.info("miniapp_home_focus_goals_unavailable reason=%s", type(exc).__name__)
        try:
            limits = self.limits(req, params)["data"].get("items", [])
            for limit in limits:
                if tx.category and limit.get("category") and limit.get("category") != tx.category:
                    continue
                percent = int(limit.get("percent") or 0)
                projected_percent: int | None = None
                try:
                    projected_percent = self._limit_projection_percent(
                        spent=to_decimal_money(limit.get("spent") or 0),
                        amount=to_decimal_money(limit.get("amount") or 0),
                        period=str(limit.get("period") or "month"),
                        today=today,
                    )
                except Exception:
                    projected_percent = None
                if percent >= 100:
                    severity = "critical"
                    status = "exceeded"
                    description = "Лимит превышен. Проверьте расходы периода."
                elif percent >= 90 or (projected_percent is not None and projected_percent >= 115):
                    severity = "high"
                    status = "warning"
                    description = "При текущем темпе лимит может быть превышен." if percent < 90 else "Лимит почти исчерпан."
                elif percent >= 80 or (projected_percent is not None and projected_percent >= 100):
                    severity = "medium"
                    status = "risk"
                    description = "Темп расходов требует внимания."
                elif projected_percent is not None and projected_percent >= 90:
                    severity = "normal"
                    status = "attention"
                    description = "Прогноз близок к лимиту."
                elif percent >= 50:
                    severity = "normal"
                    status = limit.get("status") or "normal"
                    description = "Лимит в рабочем режиме."
                else:
                    severity = "normal"
                    status = limit.get("status") or "normal"
                    description = "Лимит в рабочем режиме."
                score = severity_rank[severity] + min(percent, 200)
                if tx.category and limit.get("category") == tx.category and severity != "normal":
                    score += 25
                candidates.append({
                    "score": score,
                    "severity": severity,
                    "kind": "limit",
                    "id": limit.get("id"),
                    "title": limit.get("title") or "Лимит",
                    "description": description,
                    "percent": percent,
                    "projected_percent": projected_percent if projected_percent is not None and projected_percent >= 90 else None,
                    "status": status,
                    "cta_label": "Открыть лимиты",
                    "target_mode": "limits",
                })
        except Exception as exc:
            log.info("miniapp_home_focus_limits_unavailable reason=%s", type(exc).__name__)
        if not candidates:
            return {"kind": "empty", "title": "Фокус свободен", "description": "Добавьте цель или лимит, чтобы видеть главный приоритет.", "target_mode": "goals"}
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["kind"]), str(item.get("id") or "")))
        item = dict(candidates[0])
        item.pop("score", None)
        return item

    def _home_insight(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        totals: dict[str, dict[str, Decimal | int]],
    ) -> dict:
        if not totals:
            return {"kind": "fallback", "tone": "neutral", "title": "Данных пока мало", "text": "Добавьте операции, и здесь появится сравнение периода."}
        if len(totals) > 1:
            return {"kind": "currency_mix", "tone": "neutral", "title": "Несколько валют", "text": "Сравнение не складывает разные валюты без конвертации."}
        currency = next(iter(totals))
        prev_start, prev_end, _prev_key = self._previous_period(tx.start, tx.end, tx.period_key)
        prev_params = dict(operation_type=tx.operation_type, category=tx.category or None)
        prev_tx = self._transaction_filters(
            req,
            {**prev_params, "workspace_id": "all" if tx.all_scope else tx.workspace_ids[0], "period": "custom", "start_date": prev_start.isoformat(), "end_date": prev_end.isoformat()},
        )
        try:
            rows = pg_fetchall(
                f"""
                SELECT type, COALESCE(SUM(amount),0)
                  FROM public.operations
                 WHERE {prev_tx.where_sql}
                   AND COALESCE(currency, %s)=%s
                 GROUP BY type
                """,
                (*prev_tx.params, get_user_currency(req.user_id), currency),
            )
        except Exception as exc:
            log.info("miniapp_home_insight_unavailable reason=%s", type(exc).__name__)
            return {"kind": "fallback", "tone": "neutral", "title": "Сравнение недоступно", "text": "Период показан без автоматического вывода."}
        previous = {"income": Decimal("0.00"), "expense": Decimal("0.00")}
        for typ, total in rows:
            if typ == "Доходы":
                previous["income"] = to_decimal_money(total)
            elif typ == "Расходы":
                previous["expense"] = to_decimal_money(total)
        current_expense = to_decimal_money(totals[currency].get("expense", 0))
        previous_expense = previous["expense"]
        if previous_expense <= 0:
            return {"kind": "previous_empty", "tone": "neutral", "title": "Есть текущий период", "text": "Для сравнения нужен предыдущий период с расходами."}
        delta = current_expense - previous_expense
        pct = int((abs(delta) / previous_expense * Decimal("100")).to_integral_value()) if previous_expense else 0
        if delta < 0:
            return {"kind": "expense_down", "tone": "positive", "title": "Расходы ниже", "text": f"На {pct}% меньше, чем в прошлом сопоставимом периоде.", "currency": currency}
        if delta > 0:
            return {"kind": "expense_up", "tone": "warning", "title": "Расходы выше", "text": f"На {pct}% больше, чем в прошлом сопоставимом периоде.", "currency": currency}
        return {"kind": "expense_flat", "tone": "neutral", "title": "Расходы без изменений", "text": "Период совпадает с прошлым по расходам.", "currency": currency}

    def operations(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        tx = self._transaction_filters(req, params, alias="o")
        limit = min(max(int(params.get("limit") or DEFAULT_PAGE_SIZE), 1), READ_PAGE_LIMIT)
        offset = max(int(params.get("offset") or 0), 0)
        filters = [tx.where_sql]
        values: list[Any] = [*tx.params]
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
        return success({"items": items, "has_more": len(rows) > limit, "limit": limit, "offset": offset, "period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end}}, request_id=req.request_id)

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

    def _operation_dict_from_recorded(self, recorded: RecordedOperation, *, workspace_name: str | None = None) -> dict:
        amount = to_decimal_money(recorded.amount)
        return {
            "id": recorded.operation_id,
            "op_date": recorded.operation_date,
            "type": recorded.type,
            "category": recorded.category,
            "amount": amount,
            "amount_text": format_money(amount, recorded.currency),
            "currency": recorded.currency,
            "description": recorded.comment,
            "workspace_id": recorded.workspace_id,
            "actor_user_id": str(recorded.actor_user_id),
            "created_at": None,
            "workspace_name": workspace_name,
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
        payload, recorded, created = self._create_operation_atomically(
            req=req,
            ctx=ctx,
            idempotency_key=self._namespaced_idempotency_key("operation:create", idem),
            request_hash=request_hash,
            op_date=op_date,
            op_type=op_type,
            category=category,
            amount=amount,
            description=description,
        )
        if created and recorded:
            try:
                record_financial_operation_post_commit(
                    recorded,
                    workspace_kind=ctx.kind,
                    metadata={"source": "miniapp"},
                )
            except Exception as exc:
                log.info("miniapp_operation_post_commit_failed operation_id=%s reason=%s", recorded.operation_id, type(exc).__name__)
            self._track(req, "mini_app_transaction_created", workspace_id=ctx.workspace_id, properties={"operation_type": op_type})
        return success(payload, request_id=req.request_id)

    def _create_operation_atomically(
        self,
        *,
        req: MiniAppRequest,
        ctx: WorkspaceContext,
        idempotency_key: str,
        request_hash: str,
        op_date: date,
        op_type: str,
        category: str,
        amount: Decimal,
        description: str,
    ) -> tuple[dict, RecordedOperation | None, bool]:
        conn = get_conn()
        recorded: RecordedOperation | None = None
        try:
            with conn.cursor() as cur:
                claim = self._claim_idempotency_tx(cur, req.user_id, idempotency_key, request_hash)
                status = claim["status"]
                if status == "completed":
                    conn.commit()
                    return claim["response"], None, False
                if status == "reconcile_completed":
                    payload = self._reconstruct_operation_payload_tx(cur, req, ctx, claim["operation_id"])
                    self._complete_idempotency_tx(cur, req.user_id, idempotency_key, request_hash, payload, operation_id=claim["operation_id"])
                    conn.commit()
                    return payload, None, False
                if status != "claimed":
                    conn.rollback()
                    raise MiniAppError(claim["http_status"], claim["status"], claim["message"])

                recorded = insert_financial_operation_tx(
                    cur,
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
                )
                payload = {"operation": self._operation_dict_from_recorded(recorded, workspace_name=ctx.name)}
                self._complete_idempotency_tx(cur, req.user_id, idempotency_key, request_hash, payload, operation_id=recorded.operation_id)
            conn.commit()
            return payload, recorded, True
        except errors.UndefinedTable as exc:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
        tx = self._transaction_filters(req, params)
        overview = self.overview(req, params)["data"]
        available_currencies = sorted(str(currency) for currency in overview["totals_by_currency"].keys())
        requested_currency = str(params.get("currency") or "").strip().upper()
        prev_start, prev_end, _prev_key = self._previous_period(tx.start, tx.end, tx.period_key)
        radar_type = self._chart_op_type(params, "radar_type", tx.operation_type)
        radar_available_currencies = self._radar_currencies(req, tx, prev_start, prev_end, radar_type)
        if requested_currency and requested_currency not in set(available_currencies) | set(radar_available_currencies):
            raise MiniAppError(400, "bad_currency", "Currency is not available for this scope.")
        if requested_currency and requested_currency in available_currencies:
            if requested_currency not in available_currencies:
                raise MiniAppError(400, "bad_currency", "Currency is not available for this scope.")
            chart_currencies = [requested_currency]
        else:
            chart_currencies = available_currencies
        aggregation_available = len(available_currencies) <= 1
        category_type = self._chart_op_type(params, "category_type", tx.operation_type)
        grouping = self._validated_grouping(str(params.get("grouping") or "auto"), tx.start, tx.end)
        structure = self._category_structure(req, tx, category_type, currencies=chart_currencies)
        dynamics = self._time_dynamics(req, tx, grouping=grouping, currencies=chart_currencies)
        radar = self._radar(
            req,
            tx,
            prev_start,
            prev_end,
            _prev_key,
            radar_type,
            currency=requested_currency or (radar_available_currencies[0] if len(radar_available_currencies) == 1 else None),
            available_currencies=radar_available_currencies,
        )
        activity = self._activity_calendar(req, tx)
        currency_groups = {
            currency: {
                "summary": {
                    **overview["totals_by_currency"][currency],
                    "result": to_decimal_money(overview["totals_by_currency"][currency]["income"]) - to_decimal_money(overview["totals_by_currency"][currency]["expense"]),
                },
                "category_structure": structure["currency_groups"].get(currency, {"currency": currency, "total": Decimal("0.00"), "items": []}),
                "time_dynamics": dynamics["currency_groups"].get(currency, {"currency": currency, "datasets": []}),
            }
            for currency in available_currencies
        }
        return success({
            "period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end},
            "filters": {"operation_type": tx.operation_type, "category": tx.category or "all"},
            "overview": overview,
            "aggregation_available": aggregation_available,
            "available_currencies": available_currencies,
            "radar_available_currencies": radar_available_currencies,
            "selected_currency": requested_currency or None,
            "currency_groups": currency_groups,
            "summary": {
                "aggregation_available": aggregation_available,
                "available_currencies": available_currencies,
                "currency_groups": {
                    currency: {
                        **values,
                        "result": to_decimal_money(values["income"]) - to_decimal_money(values["expense"]),
                    }
                    for currency, values in overview["totals_by_currency"].items()
                },
                "totals_by_currency": overview["totals_by_currency"],
                "result_by_currency": {
                    currency: to_decimal_money(values["income"]) - to_decimal_money(values["expense"])
                    for currency, values in overview["totals_by_currency"].items()
                },
            },
            "category_structure": structure,
            "time_dynamics": dynamics,
            "radar": radar,
            "activity_calendar": activity,
            "top_expense_categories": structure["items"],
        }, request_id=req.request_id)

    def _chart_op_type(self, params: dict[str, Any], key: str, global_operation_type: str) -> str:
        if global_operation_type in {"expense", "income"}:
            return "Расходы" if global_operation_type == "expense" else "Доходы"
        raw = str(params.get(key) or "expense")
        if raw not in OP_TYPES:
            raise MiniAppError(400, "bad_type", "Invalid chart operation type.")
        return OP_TYPES[raw]

    def _validated_grouping(self, grouping: str, start: date, end: date) -> str:
        if not grouping or grouping == "auto":
            return self._chart_grouping(start, end)
        if grouping not in {"day", "week", "month"}:
            raise MiniAppError(400, "bad_grouping", "Invalid grouping.")
        return grouping

    def _radar_currencies(self, req: MiniAppRequest, tx: TransactionFilters, prev_start: date, prev_end: date, op_type: str) -> list[str]:
        date_sql = tx.where_sql.replace("op_date BETWEEN %s AND %s", "((op_date BETWEEN %s AND %s) OR (op_date BETWEEN %s AND %s))")
        values = list(tx.params)
        period_index = len(tx.params) - (2 + (1 if tx.operation_type != "all" else 0) + (1 if tx.category else 0))
        values[period_index:period_index + 2] = [tx.start, tx.end, prev_start, prev_end]
        rows = pg_fetchall(
            f"""
            SELECT DISTINCT COALESCE(currency, %s)
              FROM public.operations
             WHERE {date_sql}
               AND type=%s
             ORDER BY 1
            """,
            (get_user_currency(req.user_id), *values, op_type),
        )
        return sorted(str(row[0]) for row in rows if row and row[0])

    def _category_structure(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, *, currencies: list[str] | None = None) -> dict:
        rows = pg_fetchall(
            f"""
            SELECT category, COALESCE(currency, %s), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql} AND type=%s
             GROUP BY category, COALESCE(currency, %s)
             ORDER BY COALESCE(SUM(amount),0) DESC, category
            """,
            (get_user_currency(req.user_id), *tx.params, op_type, get_user_currency(req.user_id)),
        )
        by_currency: dict[str, list[tuple[str, Decimal, int]]] = defaultdict(list)
        for category, currency, total, count in rows:
            if currencies and str(currency) not in currencies:
                continue
            by_currency[str(currency)].append((str(category), to_decimal_money(total), int(count or 0)))
        items = []
        groups: dict[str, dict[str, Any]] = {}
        for currency, values in by_currency.items():
            currency_total = sum((row[1] for row in values), Decimal("0.00"))
            top = values[:CHART_TOP_N]
            other_total = sum((row[1] for row in values[CHART_TOP_N:]), Decimal("0.00"))
            other_count = sum((row[2] for row in values[CHART_TOP_N:]), 0)
            group_items = []
            for category, total, count in top:
                share = int((total / currency_total * Decimal("100")).to_integral_value()) if currency_total > 0 else 0
                item = {"category": category, "currency": currency, "total": total, "count": count, "share": share}
                items.append(item)
                group_items.append(item)
            if other_total > 0:
                share = int((other_total / currency_total * Decimal("100")).to_integral_value()) if currency_total > 0 else 0
                item = {"category": "Прочее", "currency": currency, "total": other_total, "count": other_count, "share": share}
                items.append(item)
                group_items.append(item)
            groups[currency] = {"currency": currency, "total": currency_total, "items": group_items}
        return {"type": "income" if op_type == "Доходы" else "expense", "top_n": CHART_TOP_N, "currency_groups": groups, "items": items}

    def _time_dynamics(self, req: MiniAppRequest, tx: TransactionFilters, *, grouping: str, currencies: list[str] | None = None) -> dict:
        rows = pg_fetchall(
            f"""
            SELECT op_date, type, COALESCE(currency, %s), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
             GROUP BY op_date, type, COALESCE(currency, %s)
             ORDER BY op_date
            """,
            (get_user_currency(req.user_id), *tx.params, get_user_currency(req.user_id)),
        )
        buckets: dict[tuple[date, str], dict[str, Any]] = {}
        for op_date, typ, currency, total, count in rows:
            if currencies and str(currency) not in currencies:
                continue
            bucket = self._bucket_date(op_date, grouping)
            item = buckets.setdefault((bucket, str(currency)), {"date": bucket, "currency": str(currency), "income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            if typ == "Доходы":
                item["income"] += to_decimal_money(total)
            elif typ == "Расходы":
                item["expense"] += to_decimal_money(total)
            item["count"] += int(count or 0)
        items = [buckets[key] for key in sorted(buckets)]
        groups: dict[str, dict[str, Any]] = {}
        for currency in sorted({item["currency"] for item in items}):
            currency_items = [item for item in items if item["currency"] == currency]
            groups[currency] = {
                "currency": currency,
                "datasets": [
                    {"kind": "expense", "items": [{"date": item["date"], "amount": item["expense"], "count": item["count"]} for item in currency_items]},
                    {"kind": "income", "items": [{"date": item["date"], "amount": item["income"], "count": item["count"]} for item in currency_items]},
                ],
            }
        return {"grouping": grouping, "currency_groups": groups, "items": items}

    def _nice_scale(self, maximum: Decimal) -> dict[str, Any]:
        if maximum <= 0:
            return {"max": Decimal("0.00"), "step": Decimal("0.00"), "ticks": [Decimal("0.00")]}
        raw_step = float(maximum) / 4.0
        magnitude = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
        for family in (1, 2, 2.5, 5, 10):
            step = Decimal(str(family * magnitude))
            scale_max = (maximum / step).to_integral_value(rounding="ROUND_CEILING") * step
            tick_count = int(scale_max / step) + 1
            if 4 <= tick_count <= 6:
                return {"max": scale_max, "step": step, "ticks": [step * i for i in range(tick_count)]}
        step = Decimal(str(10 * magnitude))
        scale_max = (maximum / step).to_integral_value(rounding="ROUND_CEILING") * step
        return {"max": scale_max, "step": step, "ticks": [step * i for i in range(int(scale_max / step) + 1)]}

    def _radar(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        prev_start: date,
        prev_end: date,
        prev_key: str,
        op_type: str,
        *,
        currency: str | None,
        available_currencies: list[str],
    ) -> dict:
        if currency and currency not in available_currencies:
            raise MiniAppError(400, "bad_currency", "Currency is not available for radar.")
        base = {
            "type": "income" if op_type == "Доходы" else "expense",
            "currency": currency,
            "current_period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end},
            "previous_period": {"key": prev_key, "start_date": prev_start, "end_date": prev_end},
            "metric": "absolute_amount",
            "max_axes": RADAR_MAX_AXES,
            "scale": self._nice_scale(Decimal("0.00")),
        }
        if tx.category:
            return {
                **base,
                "aggregation_available": True,
                "radar_available_currencies": available_currencies,
                "insufficient_data": True,
                "reason": "category_filter",
                "explanation": "Для Radar нужно несколько категорий. Сбросьте фильтр категории, чтобы сравнить структуру.",
                "axes": [],
            }
        if not available_currencies:
            return {
                **base,
                "currency": None,
                "aggregation_available": True,
                "radar_available_currencies": [],
                "insufficient_data": True,
                "reason": "insufficient_data",
                "explanation": "Недостаточно данных для radar за оба периода.",
                "axes": [],
            }
        if not currency and len(available_currencies) > 1:
            return {
                **base,
                "currency": None,
                "aggregation_available": False,
                "radar_available_currencies": available_currencies,
                "insufficient_data": True,
                "reason": "mixed_currencies",
                "explanation": "Radar недоступен для нескольких валют без выбора конкретной валюты.",
                "axes": [],
            }
        currency_filter = "AND COALESCE(currency, %s)=%s" if currency else ""
        currency_params: tuple[Any, ...] = (get_user_currency(req.user_id), currency) if currency else ()
        where, wparams = self._workspace_filter_sql(tx.workspace_ids, req.user_id)
        rows = pg_fetchall(
            f"""
            SELECT CASE WHEN op_date BETWEEN %s AND %s THEN 'current' ELSE 'previous' END AS bucket,
                   category, COALESCE(SUM(amount),0)
              FROM public.operations
             WHERE {where}
               AND type=%s
               {currency_filter}
               AND ((op_date BETWEEN %s AND %s) OR (op_date BETWEEN %s AND %s))
               AND COALESCE(category,'') <> 'Без операций'
             GROUP BY bucket, category
            """,
            (tx.start, tx.end, *wparams, op_type, *currency_params, tx.start, tx.end, prev_start, prev_end),
        )
        values: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"current": Decimal("0.00"), "previous": Decimal("0.00")})
        for bucket, category, total in rows:
            amount = to_decimal_money(total)
            values[str(category)][str(bucket)] += amount
        ranked = sorted(values.items(), key=lambda item: item[1]["current"] + item[1]["previous"], reverse=True)[:RADAR_MAX_AXES]
        max_amount = max((max(vals["current"], vals["previous"]) for _category, vals in ranked), default=Decimal("0.00"))
        insufficient = len(ranked) < 2 or max_amount <= 0
        scale = self._nice_scale(max_amount)
        axes = [
            {
                "category": category,
                "current_amount": vals["current"],
                "previous_amount": vals["previous"],
            }
            for category, vals in ranked
        ]
        return {
            **base,
            "aggregation_available": True,
            "radar_available_currencies": available_currencies,
            "scale": scale,
            "insufficient_data": insufficient,
            "reason": "insufficient_data" if insufficient else None,
            "explanation": "Radar сравнивает абсолютные суммы категорий в выбранной валюте.",
            "axes": [] if insufficient else axes,
        }

    def _home_reminder(self, req: MiniAppRequest) -> dict:
        local_today = user_local_date(req.user_id)
        try:
            completed_rows = pg_fetchall(
                """
                SELECT r.id, r.title, r.category, r.amount, r.currency, e.event_date,
                       r.event_date, r.repeat_rule, r.is_active, e.created_at
                  FROM public.user_reminder_events e
                  JOIN public.user_reminders r ON r.id=e.reminder_id AND r.user_id=e.user_id
                 WHERE e.user_id=%s
                   AND e.event_type='recorded'
                   AND (e.created_at AT TIME ZONE %s)::date=%s
                 ORDER BY e.created_at DESC, e.id DESC
                 LIMIT 1
                """,
                (req.user_id, user_timezone_name(req.user_id)[0], local_today),
            )
        except (errors.UndefinedTable, errors.UndefinedColumn):
            completed_rows = []
        if completed_rows:
            rid, title, category, amount, currency, event_date, next_event_date, repeat_rule, is_active, _created_at = completed_rows[0]
            recurring_next = next_event_date if is_active and repeat_rule != "none" and next_event_date != event_date else None
            return {
                "state": "completed_today",
                "id": int(rid),
                "title": str(title),
                "event_date": event_date,
                "amount_text": format_money(to_decimal_money(amount), currency),
                "category": str(category),
                "next_event_date": recurring_next,
                "status_text": "Оплачено сегодня" if recurring_next else "Завершено сегодня",
                "overdue_days": 0,
                "repeat_rule": repeat_rule,
            }
        try:
            overdue_rows = pg_fetchall(
                """
                SELECT id, title, category, amount, currency, event_date, repeat_rule
                  FROM public.user_reminders
                 WHERE user_id=%s AND is_active=TRUE AND event_date < %s
                 ORDER BY event_date, id
                 LIMIT 1
                """,
                (req.user_id, local_today),
            )
        except (errors.UndefinedTable, errors.UndefinedColumn):
            overdue_rows = []
        if overdue_rows:
            rid, title, category, amount, currency, event_date, repeat_rule = overdue_rows[0]
            overdue_days = (local_today - event_date).days
            return {
                "state": "overdue",
                "id": int(rid),
                "title": str(title),
                "event_date": event_date,
                "amount_text": format_money(to_decimal_money(amount), currency),
                "category": str(category),
                "next_event_date": None,
                "status_text": f"Просрочено на {overdue_days} дн.",
                "overdue_days": overdue_days,
                "repeat_rule": repeat_rule,
            }
        try:
            upcoming_rows = pg_fetchall(
                """
                SELECT id, title, category, amount, currency, event_date, repeat_rule
                  FROM public.user_reminders
                 WHERE user_id=%s AND is_active=TRUE AND event_date >= %s
                 ORDER BY event_date, id
                 LIMIT 1
                """,
                (req.user_id, local_today),
            )
        except (errors.UndefinedTable, errors.UndefinedColumn):
            upcoming_rows = []
        if upcoming_rows:
            rid, title, category, amount, currency, event_date, repeat_rule = upcoming_rows[0]
            days = (event_date - local_today).days
            if days == 0:
                status = "Сегодня"
            elif days == 1:
                status = "Завтра"
            else:
                status = f"Через {days} дн."
            return {
                "state": "upcoming",
                "id": int(rid),
                "title": str(title),
                "event_date": event_date,
                "amount_text": format_money(to_decimal_money(amount), currency),
                "category": str(category),
                "next_event_date": None,
                "status_text": status,
                "overdue_days": 0,
                "repeat_rule": repeat_rule,
            }
        return {
            "state": "empty",
            "id": None,
            "title": "Нет запланированных событий",
            "event_date": None,
            "amount_text": None,
            "category": None,
            "next_event_date": None,
            "status_text": "Добавьте напоминание в боте.",
            "overdue_days": 0,
            "repeat_rule": None,
        }

    def _activity_calendar(self, req: MiniAppRequest, tx: TransactionFilters) -> dict:
        rows = pg_fetchall(
            f"""
            SELECT op_date, COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
             GROUP BY op_date
             ORDER BY op_date
            """,
            tx.params,
        )
        counts = {row[0]: int(row[1] or 0) for row in rows}
        days = []
        current = tx.start
        max_count = 0
        while current <= tx.end:
            count = counts.get(current, 0)
            max_count = max(max_count, count)
            days.append({"date": current, "count": count})
            current += timedelta(days=1)
        return {"start_date": tx.start, "end_date": tx.end, "max_count": max_count, "days": days}

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
        return success({"read_only": False, "goals": self._goals(req, workspace_id), "limits": self._limits(req, workspace_id), "all_scope_note": None}, request_id=req.request_id)

    def _goals(self, req: MiniAppRequest, workspace_id: int | None, *, status_group: str = "active") -> list[dict]:
        goals = list_goals(req.user_id, workspace_id, status_group=status_group, limit=50)
        if status_group == "active":
            goals = [*goals, *list_goals(req.user_id, workspace_id, status_group="completed", limit=20)]
        return [self._goal_dict(goal) for goal in goals]

    def goals(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство для целей."}, request_id=req.request_id)
        return success({"items": self._goals(req, workspace_ids[0], status_group=str(params.get("status_group") or "active")), "read_only": False}, request_id=req.request_id)

    def goal_detail(self, req: MiniAppRequest, goal_id: int, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            raise MiniAppError(400, "concrete_workspace_required", "Choose one workspace for this action.")
        goal = get_goal(int(goal_id), req.user_id, workspace_ids[0])
        if not goal:
            raise MiniAppError(404, "goal_not_found", "Goal was not found.")
        return success({"goal": self._goal_dict(goal), "movements": [self._movement_dict(m) for m in list_movements(goal.id, req.user_id, workspace_ids[0], limit=20)]}, request_id=req.request_id)

    def create_goal(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        idem = str(body.get("idempotency_key") or "").strip()[:120]
        if not idem:
            raise MiniAppError(400, "idempotency_required", "Idempotency key is required.")
        ctx = self._write_scope(req, body.get("workspace_id"))
        self._require_goal_preview_hash(req, body, None, None)
        response, created = self._run_idempotent_create(
            req,
            "goal:create",
            idem,
            body,
            lambda cur: self._create_goal_tx(cur, req, ctx, body),
        )
        if created:
            self._safe_goal_event(req, "mini_app_goal_created", workspace_id=ctx.workspace_id, action="create")
        return success(response, request_id=req.request_id)

    def _create_goal_tx(self, cur, req: MiniAppRequest, ctx: WorkspaceContext, body: dict[str, Any]) -> dict:
        try:
            strategy = str(body.get("strategy") or STRATEGY_NONE)
            frequency = str(body.get("frequency") or FREQUENCY_NONE)
            goal = create_goal_with_plan_tx(
                cur,
                owner_user_id=req.user_id,
                workspace_id=ctx.workspace_id,
                display_name=str(body.get("title") or body.get("display_name") or "").strip(),
                target_amount=body.get("target_amount"),
                currency=str(body.get("currency") or get_user_currency(req.user_id)),
                deadline=date.fromisoformat(str(body["deadline"])) if body.get("deadline") else None,
                initial_amount=body.get("current_amount") or body.get("initial_amount") or "0",
                strategy=strategy,
                frequency=frequency,
                comfortable_amount=body.get("comfortable_amount") or None,
                schedule_config=self._schedule_from_body(body, frequency),
                reminders_enabled=bool(body.get("reminders_enabled", False)),
            )
        except (GoalError, ValueError, TypeError, MoneyParseError) as exc:
            raise MiniAppError(400, "bad_goal", "Invalid goal fields.") from exc
        return {"goal": self._goal_dict(goal), "plan_preview": self._plan_preview(req, body, goal)}

    def update_goal(self, req: MiniAppRequest, goal_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        try:
            goal = get_goal(int(goal_id), req.user_id, ctx.workspace_id)
            if not goal:
                raise MiniAppError(404, "goal_not_found", "Goal was not found.")
            self._require_goal_preview_hash(req, body, goal, int(goal_id))
            if any(key in body for key in {"title", "display_name", "target_amount", "deadline"}):
                goal = update_goal_details(
                    goal_id=goal.id,
                    owner_user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    display_name=str(body.get("title") or body.get("display_name")).strip() if body.get("title") or body.get("display_name") else None,
                    target_amount=body.get("target_amount") or None,
                    deadline=date.fromisoformat(str(body["deadline"])) if body.get("deadline") else (None if "deadline" in body else ...),
                )
            if any(key in body for key in {"strategy", "frequency", "comfortable_amount", "reminders_enabled", "day", "days", "weekday"}):
                strategy = str(body.get("strategy") or goal.strategy)
                frequency = str(body.get("frequency") or goal.frequency or FREQUENCY_NONE)
                goal = update_goal_plan(
                    goal_id=goal.id,
                    owner_user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    strategy=strategy,
                    frequency=frequency,
                    deadline=date.fromisoformat(str(body["deadline"])) if body.get("deadline") else goal.deadline,
                    comfortable_amount=body.get("comfortable_amount") if body.get("comfortable_amount") not in {None, ""} else goal.comfortable_amount,
                    schedule_config=self._schedule_from_body(body, frequency) or goal.schedule_config,
                    reminders_enabled=body.get("reminders_enabled") if "reminders_enabled" in body else None,
                )
        except MiniAppError:
            raise
        except (GoalError, ValueError, TypeError, MoneyParseError) as exc:
            raise MiniAppError(400, "bad_goal", "Invalid goal fields.") from exc
        self._safe_goal_event(req, "mini_app_goal_plan_changed", workspace_id=ctx.workspace_id, action="update")
        return success({"goal": self._goal_dict(goal), "plan_preview": self._plan_preview(req, body, goal)}, request_id=req.request_id)

    def goal_plan_preview(self, req: MiniAppRequest, body: dict[str, Any], goal_id: int | None = None) -> dict:
        workspace_id = body.get("workspace_id")
        ctx = self._write_scope(req, workspace_id)
        goal = None
        if goal_id is not None:
            goal = get_goal(int(goal_id), req.user_id, ctx.workspace_id)
            if not goal:
                raise MiniAppError(404, "goal_not_found", "Goal was not found.")
        return success({"plan_preview": self._plan_preview(req, body, goal)}, request_id=req.request_id)

    def goal_contribution(self, req: MiniAppRequest, goal_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        idem = str(body.get("idempotency_key") or "").strip()[:180]
        if not idem:
            raise MiniAppError(400, "idempotency_required", "Idempotency key is required.")
        ctx = self._write_scope(req, body.get("workspace_id"))
        try:
            goal, movement, created = add_goal_movement(
                goal_id=int(goal_id),
                owner_user_id=req.user_id,
                workspace_id=ctx.workspace_id,
                actor_user_id=req.user_id,
                movement_type=str(body.get("movement_type") or "contribution"),
                amount=body.get("amount"),
                new_balance=body.get("new_balance"),
                source="miniapp",
                idempotency_key=idem,
            )
        except GoalError as exc:
            raise MiniAppError(400, "bad_goal_movement", "Invalid goal movement.") from exc
        if created:
            self._safe_goal_event(req, "mini_app_goal_contribution_added", workspace_id=ctx.workspace_id, action=str(body.get("movement_type") or "contribution"))
        return success({"goal": self._goal_dict(goal), "movement": self._movement_dict(movement) if movement else None, "created": created}, request_id=req.request_id)

    def goal_reminders(self, req: MiniAppRequest, goal_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        goal = set_goal_reminders(int(goal_id), req.user_id, ctx.workspace_id, bool(body.get("enabled")))
        self._safe_goal_event(req, "mini_app_goal_plan_changed", workspace_id=ctx.workspace_id, action="reminders")
        return success({"goal": self._goal_dict(goal)}, request_id=req.request_id)

    def goal_status(self, req: MiniAppRequest, goal_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        status = str(body.get("status") or "")
        if status not in {"active", "paused", "archived", "achieved"}:
            raise MiniAppError(400, "bad_goal_status", "Invalid goal status.")
        goal = set_goal_status(int(goal_id), req.user_id, ctx.workspace_id, status)
        self._safe_goal_event(req, "mini_app_goal_plan_changed", workspace_id=ctx.workspace_id, action=status)
        return success({"goal": self._goal_dict(goal)}, request_id=req.request_id)

    def _limits(self, req: MiniAppRequest, workspace_id: int | None) -> list[dict]:
        items: list[dict] = []
        if workspace_id is None:
            rows = pg_fetchall(
                """
                SELECT period, category, amount, currency
                  FROM public.category_limits
                 WHERE user_id=%s AND workspace_id IS NULL
                 ORDER BY period, category
                """,
                (req.user_id,),
            )
        else:
            rows = pg_fetchall(
                """
                SELECT period, category, amount, currency
                  FROM public.category_limits
                 WHERE user_id=%s AND workspace_id=%s
                 ORDER BY period, category
                """,
                (req.user_id, workspace_id),
            )
        for period, category, amount_raw, currency in rows:
            items.append(self._limit_dict(
                user_id=req.user_id,
                kind="category",
                identifier=f"category:{period}:{category}",
                title=str(category),
                category=str(category),
                amount=to_decimal_money(amount_raw),
                currency=currency,
                period=period,
                workspace_id=workspace_id,
                alerts_enabled=True,
            ))
        try:
            general_limits = list_general_limits(req.user_id, workspace_id)
        except Exception as exc:
            log.info("miniapp_general_limits_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            general_limits = []
        for item in general_limits:
            if item.get("period_type") not in LIMIT_PERIODS or not item.get("enabled", True):
                continue
            items.append(self._limit_dict(
                user_id=req.user_id,
                kind="general",
                identifier=f"general:{item['id']}",
                title=item["name"],
                category=None,
                amount=item["amount"],
                currency=item["currency"],
                period=item["period_type"],
                workspace_id=item["workspace_id"],
                alerts_enabled=bool(item.get("alerts_enabled", True)),
            ))
        return items

    def limits(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство для лимитов."}, request_id=req.request_id)
        return success({"items": self._limits(req, workspace_ids[0]), "read_only": False}, request_id=req.request_id)

    def create_limit(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        idem = str(body.get("idempotency_key") or "").strip()[:120]
        if not idem:
            raise MiniAppError(400, "idempotency_required", "Idempotency key is required.")
        ctx = self._write_scope(req, body.get("workspace_id"))
        response, created = self._run_idempotent_create(
            req,
            "limit:create",
            idem,
            body,
            lambda cur: self._create_limit_tx(cur, req, ctx, body),
        )
        if created:
            self._track(req, "mini_app_budget_limit_created", workspace_id=ctx.workspace_id, properties={"period_kind": response["limit"]["period"], "action": "create", "source": "mini_app"})
        return success(response, request_id=req.request_id)

    def _create_limit_tx(self, cur, req: MiniAppRequest, ctx: WorkspaceContext, body: dict[str, Any]) -> dict:
        period = str(body.get("period") or "month")
        if period not in LIMIT_PERIODS:
            raise MiniAppError(400, "bad_limit_period", "Only week and month limits are supported.")
        amount = to_decimal_money(body.get("amount"), positive=True)
        scope = str(body.get("scope") or "category")
        currency = str(body.get("currency") or get_user_currency(req.user_id))[:8]
        try:
            if scope == "all_expenses":
                stored = create_or_update_general_limit_tx(cur, user_id=req.user_id, workspace_id=ctx.workspace_id, name=str(body.get("title") or "Все расходы")[:80], amount=amount, period=period, currency=currency, alerts_enabled=bool(body.get("alerts_enabled", True)))
            else:
                category = self._validate_category(req, ctx.workspace_id, "Расходы", str(body.get("category") or ""))
                stored = replace_category_limit_tx(cur, user_id=req.user_id, workspace_id=ctx.workspace_id, old_period=None, old_category=None, period=period, category=category, amount=amount, currency=currency)
        except MiniAppLimitError as exc:
            raise MiniAppError(400, exc.code, "Limit could not be created.") from exc
        return {"limit": self._stored_limit_dict(req, stored)}

    def update_limit(self, req: MiniAppRequest, limit_id: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        decoded = unquote(limit_id)
        period = str(body.get("period") or "month")
        if period not in LIMIT_PERIODS:
            raise MiniAppError(400, "bad_limit_period", "Only week and month limits are supported.")
        amount = to_decimal_money(body.get("amount"), positive=True)
        currency = str(body.get("currency") or get_user_currency(req.user_id))[:8]
        if decoded.startswith("general:"):
            raw_id = int(decoded.split(":", 1)[1])
            try:
                stored = create_or_update_general_limit(user_id=req.user_id, workspace_id=ctx.workspace_id, limit_id=raw_id, name=str(body.get("title") or "Все расходы")[:80], amount=amount, period=period, currency=currency, alerts_enabled=bool(body.get("alerts_enabled", True)))
            except MiniAppLimitError as exc:
                raise MiniAppError(404 if exc.code == "limit_not_found" else 400, exc.code, "Limit could not be updated.") from exc
            lookup = f"general:{raw_id}"
        else:
            _kind, old_period, old_category = decoded.split(":", 2)
            category = self._validate_category(req, ctx.workspace_id, "Расходы", str(body.get("category") or old_category))
            try:
                stored = replace_category_limit(
                    user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    old_period=old_period,
                    old_category=old_category,
                    period=period,
                    category=category,
                    amount=amount,
                    currency=currency,
                    require_existing=True,
                )
            except MiniAppLimitError as exc:
                raise MiniAppError(404 if exc.code == "limit_not_found" else 400, exc.code, "Limit could not be updated.") from exc
            lookup = f"category:{period}:{category}"
        self._track(req, "mini_app_budget_limit_updated", workspace_id=ctx.workspace_id, properties={"period_kind": period, "action": "update", "source": "mini_app"})
        return success({"limit": self._stored_limit_dict(req, stored), "id": lookup}, request_id=req.request_id)

    def delete_limit(self, req: MiniAppRequest, limit_id: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        decoded = unquote(limit_id)
        try:
            deleted = delete_stored_limit(user_id=req.user_id, workspace_id=ctx.workspace_id, limit_id=decoded)
        except (MiniAppLimitError, ValueError) as exc:
            raise MiniAppError(400, "bad_limit_id", "Invalid limit id.") from exc
        if not deleted:
            raise MiniAppError(404, "limit_not_found", "Limit was not found.")
        self._track(req, "mini_app_budget_limit_deleted", workspace_id=ctx.workspace_id, properties={"action": "delete", "source": "mini_app"})
        return success({"deleted": True, "limit_id": decoded}, request_id=req.request_id)

    def _limit_spent(self, user_id: int, workspace_id: int | None, period: str, category: str | None, today: date | None = None) -> Decimal:
        today = today or user_local_date(user_id, workspace_id)
        if period == "week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        else:
            start = today.replace(day=1)
            nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = nxt - timedelta(days=1)
        where, params = self._workspace_filter_sql([workspace_id], user_id)
        category_filter = "AND category=%s" if category else ""
        category_params = (category,) if category else ()
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(SUM(amount), 0)
              FROM public.operations
             WHERE {where}
               AND type='Расходы'
               {category_filter}
               AND op_date BETWEEN %s AND %s
               AND COALESCE(type,'') <> 'noop'
               AND COALESCE(category,'') <> 'Без операций'
            """,
            (*params, *category_params, start, end),
        )
        return to_decimal_money(rows[0][0] if rows else 0)

    def profile(self, req: MiniAppRequest) -> dict:
        timezone_name, _reason = user_timezone_name(req.user_id)
        try:
            notifications = get_notification_preferences(req.user_id)
        except Exception as exc:
            log.info("miniapp_notification_preferences_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            notifications = {}
        try:
            categories = {
                "expense": self._managed_categories(req, None, "Расходы")[:20],
                "income": self._managed_categories(req, None, "Доходы")[:20],
            }
        except Exception as exc:
            log.info("miniapp_profile_categories_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            categories = {"expense": [], "income": []}
        preferred_name = get_user_preferred_name(req.user_id)
        return success({
            "theme": self._profile_theme(req.user_id),
            "preferred_name": preferred_name,
            "display_name": self._display_name(req, preferred_name),
            "currency": get_user_currency(req.user_id),
            "available_currencies": sorted(ALLOWED_CURRENCIES),
            "timezone": timezone_name,
            "timezone_options": [{"label": label, "value": value} for label, value in TIMEZONE_CHOICES],
            "workspaces": self._workspace_rows(req.user_id),
            "categories": categories,
            "notifications": notifications,
            "premium": self._premium_info(),
            "export": self._export_info(req),
            "help_url": os.getenv("MINIAPP_HELP_URL", "https://t.me/chiracredible"),
            "links": {
                "privacy": os.getenv("MINIAPP_PRIVACY_URL") or None,
                "terms": os.getenv("MINIAPP_TERMS_URL") or None,
            },
            "version": self.version,
        }, request_id=req.request_id)

    def profile_categories(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство для категорий."}, request_id=req.request_id)
        op_type = OP_TYPES.get(str(params.get("type") or "expense")) or "Расходы"
        return success({"items": self._managed_categories(req, workspace_ids[0], op_type), "read_only": False}, request_id=req.request_id)

    def notification_preferences(self, req: MiniAppRequest) -> dict:
        return success(get_notification_preferences(req.user_id), request_id=req.request_id)

    def set_profile_preferred_name(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            preferred_name = set_user_preferred_name(req.user_id, body.get("preferred_name"))
        except ValueError as exc:
            raise MiniAppError(400, "bad_preferred_name", "Invalid preferred name.") from exc
        self._track(req, "mini_app_profile_setting_changed", properties={"setting": "preferred_name", "result": "success", "source": "mini_app"})
        return success({"preferred_name": preferred_name, "display_name": self._display_name(req, preferred_name)}, request_id=req.request_id)

    def set_profile_currency(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            currency = set_user_currency(req.user_id, str(body.get("currency") or ""))
        except ValueError as exc:
            raise MiniAppError(400, "bad_currency", "Invalid currency.") from exc
        self._track(req, "mini_app_profile_setting_changed", properties={"setting": "currency", "result": "success", "source": "mini_app"})
        return success({"currency": currency}, request_id=req.request_id)

    def set_profile_timezone(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            notifications = set_notification_timezone(req.user_id, str(body.get("timezone") or body.get("value") or ""))
        except ValueError as exc:
            raise MiniAppError(400, "bad_timezone", "Invalid timezone.") from exc
        self._track(req, "mini_app_profile_setting_changed", properties={"setting": "timezone", "result": "success", "source": "mini_app"})
        return success({"timezone": notifications.get("timezone"), "notifications": notifications}, request_id=req.request_id)

    def set_profile_active_workspace(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            workspace_id = int(body.get("workspace_id"))
        except (TypeError, ValueError) as exc:
            raise MiniAppError(400, "bad_workspace", "Invalid workspace.") from exc
        if not set_active_workspace(req.user_id, workspace_id):
            raise MiniAppError(403, "workspace_access_denied", "Workspace is not available.")
        self._track(req, "mini_app_profile_setting_changed", workspace_id=workspace_id, properties={"setting": "active_workspace", "result": "success", "source": "mini_app"})
        return success({"workspaces": self._workspace_rows(req.user_id), "active_workspace_id": workspace_id}, request_id=req.request_id)

    def update_workspace(self, req: MiniAppRequest, workspace_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            row = rename_workspace(req.user_id, int(workspace_id), str(body.get("name") or ""))
        except PermissionError as exc:
            raise MiniAppError(403, "workspace_rename_denied", "You cannot rename this workspace.") from exc
        except ValueError as exc:
            raise MiniAppError(400, "bad_workspace_name", "Invalid workspace name.") from exc
        if not row:
            raise MiniAppError(404, "workspace_not_found", "Workspace was not found.")
        self._track(req, "mini_app_profile_setting_changed", workspace_id=int(workspace_id), properties={"setting": "workspace_name", "result": "success", "source": "mini_app"})
        return success({"workspace": row, "workspaces": self._workspace_rows(req.user_id)}, request_id=req.request_id)

    def update_notification_preferences(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        action = str(body.get("action") or "toggle")
        key = str(body.get("key") or "")
        try:
            if action == "toggle":
                if key not in NOTIFICATION_KEYS:
                    raise MiniAppError(400, "bad_notification_key", "Unknown notification setting.")
                value = toggle_notification_preference(req.user_id, key)
                self._track(req, "mini_app_notification_setting_changed", properties={"action": key, "result": "enabled" if value else "disabled", "source": "mini_app"})
            elif action == "quiet_toggle":
                value = toggle_quiet_hours(req.user_id)
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "quiet_hours", "result": "enabled" if value else "disabled", "source": "mini_app"})
            elif action == "quiet_time":
                set_quiet_hours_time(req.user_id, key, str(body.get("value") or ""))
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "quiet_hours_time", "result": "success", "source": "mini_app"})
            elif action == "quiet_hours_update":
                set_quiet_hours(
                    req.user_id,
                    enabled=bool(body.get("enabled")),
                    start=str(body.get("start") or body.get("quiet_hours_start") or "22:30"),
                    end=str(body.get("end") or body.get("quiet_hours_end") or "08:00"),
                )
                self._track(req, "mini_app_profile_setting_changed", properties={"setting": "quiet_hours", "result": "success", "source": "mini_app"})
            elif action == "timezone":
                set_notification_timezone(req.user_id, str(body.get("value") or ""))
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "timezone", "result": "success", "source": "mini_app"})
            else:
                raise MiniAppError(400, "bad_notification_action", "Unknown notification action.")
        except ValueError as exc:
            raise MiniAppError(400, "bad_notification_value", "Invalid notification setting.") from exc
        return success(get_notification_preferences(req.user_id), request_id=req.request_id)

    def _premium_info(self) -> dict:
        return {
            "available": False,
            "title": "Premium",
            "status": "info_only",
            "description": "Premium-раздел подготовлен для будущих возможностей. Оплата и ограничения в MVP не включены.",
            "features": [],
        }

    def premium(self, req: MiniAppRequest) -> dict:
        self._track(req, "mini_app_premium_opened", properties={"source": "mini_app", "action": "open"})
        return success(self._premium_info(), request_id=req.request_id)

    def _export_info(self, req: MiniAppRequest) -> dict:
        return {
            "available": True,
            "presets": ["today", "7", "14", "month", "previous_month", "year"],
            "status": "ready",
            "privacy_note": "Экспорт использует существующий Telegram flow; Mini App не хранит отдельные файлы.",
        }

    def export_entry(self, req: MiniAppRequest, body: dict[str, Any] | None = None) -> dict:
        self._track(req, "mini_app_export_opened", properties={"source": "mini_app", "action": "open"})
        return success(self._export_info(req), request_id=req.request_id)

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
            "mini_app_global_filter_opened",
            "mini_app_global_filter_applied",
            "mini_app_activity_calendar_opened",
            "mini_app_analytics_details_toggled",
            "mini_app_analytics_grouping_changed",
            "mini_app_home_reminder_opened",
            "mini_app_transaction_add_opened",
            "mini_app_analytics_chart_filter_changed",
            "mini_app_premium_opened",
            "mini_app_export_opened",
            "mini_app_home_challenge_opened",
            "mini_app_home_focus_opened",
            "mini_app_home_insight_opened",
            "mini_app_profile_section_opened",
            "mini_app_profile_setting_changed",
        }
        if event not in allowed:
            raise MiniAppError(400, "bad_event", "Invalid analytics event.")
        props = {
            k: v
            for k, v in (body.get("properties") or {}).items()
            if k in {"tab", "period", "scope", "action", "chart_type", "filter_kind", "period_kind", "operation_type", "has_category_filter", "grouping", "result", "source", "kind", "setting", "section"}
        }
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

    def _namespaced_idempotency_key(self, action: str, key: str) -> str:
        return f"{action}:{str(key).strip()}"[:180]

    def _run_idempotent_create(
        self,
        req: MiniAppRequest,
        action: str,
        idempotency_key: str,
        body: dict[str, Any],
        creator_tx,
    ) -> tuple[dict, bool]:
        key = self._namespaced_idempotency_key(action, idempotency_key)
        request_hash = self._request_hash(body)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                claim = self._claim_idempotency_tx(cur, req.user_id, key, request_hash)
                status = claim["status"]
                if status == "completed":
                    conn.commit()
                    return claim["response"], False
                if status != "claimed":
                    conn.rollback()
                    raise MiniAppError(claim["http_status"], claim["status"], claim["message"])
                response = creator_tx(cur)
                self._complete_idempotency_tx(cur, req.user_id, key, request_hash, response, operation_id=None)
            conn.commit()
            return response, True
        except errors.UndefinedTable as exc:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _claim_idempotency_tx(self, cur, user_id: int, key: str, request_hash: str) -> dict:
        cur.execute(
            """
            INSERT INTO public.miniapp_idempotency_keys
              (user_id, idempotency_key, request_hash, status, lease_expires_at, attempt_count, updated_at)
            VALUES (%s, %s, %s, 'pending', now() + (%s || ' seconds')::interval, 1, now())
            ON CONFLICT (user_id, idempotency_key) DO NOTHING
            RETURNING status
            """,
            (user_id, key, request_hash, IDEMPOTENCY_LEASE_SECONDS),
        )
        if cur.fetchone():
            return {"status": "claimed"}

        cur.execute(
            """
            SELECT request_hash, status, response_json, operation_id,
                   lease_expires_at, lease_expires_at IS NULL OR lease_expires_at <= now()
              FROM public.miniapp_idempotency_keys
             WHERE user_id=%s AND idempotency_key=%s
             FOR UPDATE
            """,
            (user_id, key),
        )
        row = cur.fetchone()
        if not row:
            raise MiniAppError(503, "idempotency_unavailable", "Could not verify request status.")
        existing_hash, status, response, operation_id, _lease_expires_at, lease_expired = row
        if existing_hash != request_hash:
            return {"status": "idempotency_conflict", "http_status": 409, "message": "This idempotency key was used for a different request."}
        if status == "completed":
            if response:
                return {"status": "completed", "response": response}
            if operation_id is not None:
                return {"status": "reconcile_completed", "operation_id": int(operation_id)}
            return {"status": "completed", "response": {}}
        if status == "pending" and operation_id is not None:
            return {"status": "reconcile_completed", "operation_id": int(operation_id)}
        if status == "pending" and not lease_expired:
            return {"status": "idempotency_pending", "http_status": 409, "message": "Request is already being processed."}

        cur.execute(
            """
            UPDATE public.miniapp_idempotency_keys
               SET status='pending',
                   lease_expires_at=now() + (%s || ' seconds')::interval,
                   attempt_count=COALESCE(attempt_count, 0) + 1,
                   last_error_code=NULL,
                   updated_at=now()
             WHERE user_id=%s AND idempotency_key=%s AND request_hash=%s
            """,
            (IDEMPOTENCY_LEASE_SECONDS, user_id, key, request_hash),
        )
        return {"status": "claimed"}

    def _complete_idempotency_tx(self, cur, user_id: int, key: str, request_hash: str, response: dict, *, operation_id: int | None) -> None:
        cur.execute(
            """
            UPDATE public.miniapp_idempotency_keys
               SET operation_id=%s,
                   status='completed',
                   response_json=%s,
                   lease_expires_at=NULL,
                   last_error_code=NULL,
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

    def _reconstruct_operation_payload_tx(self, cur, req: MiniAppRequest, ctx: WorkspaceContext, operation_id: int | None) -> dict:
        if operation_id is None:
            return {}
        cur.execute(
            """
            SELECT o.id, o.op_date, o.type, o.category, o.amount, COALESCE(o.currency, %s),
                   COALESCE(o.comment,''), o.workspace_id, o.actor_user_id, o.created_at,
                   COALESCE(w.name, 'Личное')
              FROM public.operations o
              LEFT JOIN public.workspaces w ON w.id=o.workspace_id
             WHERE o.id=%s
               AND o.workspace_id IS NOT DISTINCT FROM %s
               AND (o.workspace_id IS NOT NULL OR o.user_id=%s)
             LIMIT 1
            """,
            (get_user_currency(req.user_id), int(operation_id), ctx.workspace_id, req.user_id),
        )
        row = cur.fetchone()
        if not row:
            raise MiniAppError(503, "idempotency_unavailable", "Could not restore request status.")
        return {"operation": self._operation_dict(row)}
