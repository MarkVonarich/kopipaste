from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import os
import math
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from psycopg2 import errors
from psycopg2.extras import Json
from telegram import Bot

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency, get_user_locale
from services.budgeting import (
    create_category_budget_group,
    delete_category_budget_group,
    list_category_budget_groups,
    list_general_limits,
    set_category_budget_group_enabled,
    update_category_budget_group,
)
from services.categories import list_managed_categories, normalized_category_key
from services.categories import (
    CategoryReferenceCounts,
    category_reference_counts_many,
    delete_category_without_operations,
    get_or_create_custom_category,
    is_protected_category,
    rename_category,
    transfer_category,
)
from services.category_preferences import apply_category_preferences, get_category_preferences, set_category_preference
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
    delete_goal_permanently,
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
from services.planning import PlanningError, PlanningRequest, calculate_planning_estimate
from services.announcements import announcement_candidate, dismiss_announcement, report_ready_announcements, resolve_announcements
from services.reports import ReportBuildRequest, build_report, comparable_period, completed_report_period, report_ready_kinds
from services.home_preferences import get_home_preferences, home_widget_registry, reconcile_home_preferences, save_home_preferences
from services.experiments import exposure_properties
from services.forecasting import (
    ForecastPeriod,
    ForecastRepository,
    calculate_spendable,
    can_spend as calculate_can_spend,
    explain_forecast_change,
    record_forecast_feedback,
    unavailable as forecast_unavailable,
)
from services.shopping import (
    ShoppingError,
    clear_completed_shopping_items,
    create_shopping_item,
    delete_shopping_item,
    shopping_summary,
    update_shopping_item,
)
from services.export_xlsx import build_export_xlsx
from services.reminders import (
    ReminderError,
    create_reminder,
    delete_reminder as delete_user_reminder,
    get_reminder,
    list_reminders,
    record_reminder_tx,
    snooze_reminder,
    toggle_reminder,
    update_reminder,
)
from utils.text import norm_text
from services.limit_alerts import alert_status_for_band, threshold_band
from services.insights import PeriodRef, build_snapshot, insight_engine
from services.merchant_intelligence import (
    EMPTY_MERCHANT_KEY,
    comparable_baseline_periods,
    fold_merchant_rows,
    merchant_baseline,
    merchant_features,
    merchant_key_sql,
    normalize_merchant_key,
    raw_aliases_for_bucket,
)
from services.miniapp_limits import (
    MiniAppLimitError,
    StoredLimit,
    create_or_update_general_limit,
    create_or_update_general_limit_tx,
    delete_limit as delete_stored_limit,
    replace_category_limit,
    replace_category_limit_tx,
    set_general_limit_enabled,
)
from services.notification_preferences import (
    TOGGLE_FIELDS,
    get_vacation_mode,
    get_notification_preferences,
    grouped_notification_preferences,
    set_daily_notification_time,
    set_grouped_notification_preference,
    set_notification_timezone,
    set_quiet_hours,
    set_quiet_hours_time,
    set_vacation_mode,
    toggle_notification_preference,
    toggle_quiet_hours,
)
from services.analytics_privacy import apply_account_deletion
from services.personal_data_deletion import (
    delete_financial_history,
    delete_user_data,
    dry_run_delete_user_data,
    history_period_bounds,
    preview_delete_financial_history,
)
from services.user_profile import ALLOWED_CURRENCIES, display_name_from_parts, get_user_preferred_name, set_user_currency, set_user_preferred_name
from services.user_time import TIMEZONE_CHOICES, user_local_date, user_timezone_name
from services.workspaces import WRITE_ROLES, WorkspaceContext, can_edit_operation, list_accessible_workspaces, rename_workspace, set_active_workspace
from utils.money import MoneyParseError, format_money, to_decimal_money
from settings import TELEGRAM_TOKEN

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


def notification_read_model(user_id: int) -> dict:
    try:
        return grouped_notification_preferences(user_id)
    except Exception:
        prefs = get_notification_preferences(user_id)
        return {
            **prefs,
            "daily_notifications": {
                "enabled": bool(prefs.get("morning_enabled", True) or prefs.get("evening_enabled", True)),
                "morning_time": prefs.get("morning_time") or "08:30",
                "evening_time": prefs.get("evening_time") or "20:30",
            },
            "plans_control": {"enabled": bool(
                prefs.get("limit_alerts_enabled", True)
                or prefs.get("budget_alerts_enabled", True)
                or prefs.get("goal_notifications_enabled", False)
                or prefs.get("subscription_alerts_enabled", True)
                or prefs.get("recurring_spend_alerts_enabled", True)
            )},
            "reports": {"enabled": bool(prefs.get("weekly_reports_enabled", True) or prefs.get("monthly_reports_enabled", True))},
            "quiet_hours": {"enabled": bool(prefs.get("quiet_hours_enabled")), "start": prefs.get("quiet_hours_start") or "22:30", "end": prefs.get("quiet_hours_end") or "08:00"},
            "timezone": prefs.get("timezone") or "Europe/Moscow",
        }


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

    def _managed_categories(
        self,
        req: MiniAppRequest,
        workspace_id: int | None,
        op_type: str,
        *,
        include_references: bool = False,
        include_irrelevant: bool = False,
        preserve_key: str | None = None,
    ) -> list[dict]:
        items = list_managed_categories(user_id=req.user_id, workspace_id=workspace_id, op_type=op_type, limit=100)
        reference_counts = {}
        if include_references:
            reference_counts = category_reference_counts_many(
                user_id=req.user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                category_keys=[item.normalized_name for item in items],
            )
        result = []
        for item in items:
            data = {
                "name": item.name,
                "normalized_name": item.normalized_name,
                "token": item.normalized_name,
                "type": item.op_type,
                "source": item.source,
                "operation_count": item.operation_count,
                "has_budget": item.has_budget,
                "protected": is_protected_category(item.name),
            }
            if include_references:
                counts = reference_counts.get(item.normalized_name) or CategoryReferenceCounts()
                data["references"] = {**counts.as_dict(), "total": counts.total}
            result.append(data)
        try:
            preferences = get_category_preferences(req.user_id, workspace_id, op_type, [item["normalized_name"] for item in result])
        except Exception as exc:
            log.info("miniapp_category_preferences_unavailable reason=%s", type(exc).__name__)
            preferences = {}
        return apply_category_preferences(result, preferences, include_irrelevant=include_irrelevant, preserve_key=preserve_key)

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
        return success({"items": self._managed_categories(req, workspace_ids[0], op_type, preserve_key=params.get("current_category")), "read_only": False}, request_id=req.request_id)

    def managed_categories(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        op_type = OP_TYPES.get(str(params.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство, чтобы управлять категориями."}, request_id=req.request_id)
        return success({"items": self._managed_categories(req, workspace_ids[0], op_type, include_references=True, include_irrelevant=True), "read_only": False}, request_id=req.request_id)

    def _category_by_token(self, req: MiniAppRequest, workspace_id: int | None, op_type: str, token: str) -> dict:
        token = unquote(str(token or "")).strip()
        for item in self._managed_categories(req, workspace_id, op_type, include_references=False, include_irrelevant=True):
            if item["token"] == token:
                return item
        raise MiniAppError(404, "category_not_found", "Category was not found.")

    def create_category(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        op_type = OP_TYPES.get(str(body.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        try:
            result = get_or_create_custom_category(workspace_id=ctx.workspace_id, user_id=req.user_id, op_type=op_type, name=str(body.get("name") or ""))
        except ValueError as exc:
            raise MiniAppError(400, "bad_category_name", "Invalid category name.") from exc
        self._track(req, "mini_app_category_created", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "type": "income" if op_type == "Доходы" else "expense"})
        return success({"category": self._category_by_token(req, ctx.workspace_id, op_type, result.normalized_name), "created": result.created}, request_id=req.request_id)

    def update_category_preference(self, req: MiniAppRequest, token: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        op_type = OP_TYPES.get(str(body.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        current = self._category_by_token(req, ctx.workspace_id, op_type, token)
        priority = body.get("priority")
        relevant = body.get("relevant")
        try:
            preference = set_category_preference(
                req.user_id,
                ctx.workspace_id,
                op_type,
                current["normalized_name"],
                priority=str(priority or ""),
                relevant=relevant,
            )
        except ValueError as exc:
            raise MiniAppError(400, "bad_category_preference", "Проверьте настройки категории.") from exc
        self._track(req, "category_preference_changed", workspace_id=ctx.workspace_id, properties={"priority": preference.priority, "relevant": preference.relevant, "operation_type": "income" if op_type == "Доходы" else "expense"})
        return success({"category": self._category_by_token(req, ctx.workspace_id, op_type, current["normalized_name"])}, request_id=req.request_id)

    def update_category(self, req: MiniAppRequest, token: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        op_type = OP_TYPES.get(str(body.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        current = self._category_by_token(req, ctx.workspace_id, op_type, token)
        if current.get("protected"):
            raise MiniAppError(400, "category_protected", "Protected categories cannot be renamed.")
        try:
            result = rename_category(
                user_id=req.user_id,
                workspace_id=ctx.workspace_id,
                op_type=op_type,
                source=current["name"],
                destination=str(body.get("name") or ""),
                shared_workspace=ctx.kind not in {"personal", "legacy_personal"},
            )
        except ValueError as exc:
            raise MiniAppError(400, "category_rename_failed", "Category could not be renamed.") from exc
        self._track(req, "mini_app_category_renamed", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "type": "income" if op_type == "Доходы" else "expense"})
        return success({"category": self._category_by_token(req, ctx.workspace_id, op_type, normalized_category_key(result.destination)), "result": "renamed"}, request_id=req.request_id)

    def delete_category(self, req: MiniAppRequest, token: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        op_type = OP_TYPES.get(str(body.get("type") or "expense"))
        if not op_type:
            raise MiniAppError(400, "bad_type", "Invalid operation type.")
        current = self._category_by_token(req, ctx.workspace_id, op_type, token)
        if current.get("protected"):
            raise MiniAppError(400, "category_protected", "Protected categories cannot be deleted.")
        destination = str(body.get("transfer_to") or "").strip()
        try:
            if destination:
                destination_key = normalized_category_key(destination)
                available = {item["normalized_name"]: item["name"] for item in self._managed_categories(req, ctx.workspace_id, op_type)}
                if destination_key not in available:
                    raise ValueError("destination_not_found")
                destination = available[destination_key]
                result = transfer_category(
                    user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    op_type=op_type,
                    source=current["name"],
                    destination=destination,
                    archive_source=True,
                    budget_resolution="transfer_source",
                    shared_workspace=ctx.kind not in {"personal", "legacy_personal"},
                )
                deleted = True
                counts = result.counts.as_dict()
            else:
                result = delete_category_without_operations(
                    user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    op_type=op_type,
                    category=current["name"],
                    shared_workspace=ctx.kind not in {"personal", "legacy_personal"},
                )
                deleted = bool(result.changed)
                counts = result.counts.as_dict()
        except ValueError as exc:
            reason = str(exc)
            code, message = {
                "protected_category": ("category_protected", "Protected categories cannot be deleted."),
                "category_has_operations": ("category_transfer_required", "Choose a replacement category before deleting this category."),
                "category_has_references": ("category_transfer_required", "Choose a replacement category before deleting this category."),
                "destination_not_found": ("category_destination_not_found", "Choose an available replacement category."),
                "same_category": ("category_same_destination", "Choose a different replacement category."),
            }.get(reason, ("category_delete_failed", "Category could not be deleted safely."))
            raise MiniAppError(400, code, message) from exc
        self._track(req, "mini_app_category_deleted", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "type": "income" if op_type == "Доходы" else "expense", "mode": "transfer" if destination else "empty"})
        return success({"deleted": deleted, "references": counts}, request_id=req.request_id)

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
        return comparable_period(start, end, period_key)

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
        current = goal.current_balance if goal else to_decimal_money(body.get("current_amount") or 0)
        deadline = self._goal_deadline_from_body(body, goal)
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
        current = goal.current_balance if goal else to_decimal_money(body.get("current_amount") or 0)
        comfortable = to_decimal_money(body.get("comfortable_amount"), positive=True) if body.get("comfortable_amount") not in {None, ""} else None
        parsed_deadline = self._goal_deadline_from_body(body, goal)
        deadline = parsed_deadline.isoformat() if parsed_deadline else ""
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

    def _goal_deadline_from_body(self, body: dict[str, Any], goal: Goal | None = None) -> date | None:
        if "deadline" in body:
            raw = str(body.get("deadline") or "").strip()
            try:
                return date.fromisoformat(raw) if raw else None
            except ValueError as exc:
                raise MiniAppError(400, "bad_goal", "Invalid goal deadline.") from exc
        return goal.deadline if goal else None

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

    def _validated_currency(self, value: Any, *, fallback: str | None = None) -> str:
        code = str(value or fallback or "RUB").strip().upper()[:8]
        if code not in ALLOWED_CURRENCIES:
            raise MiniAppError(400, "bad_currency", "Invalid currency.")
        return code

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
        spent = self._limit_spent(user_id, workspace_id, period, category, currency=currency)
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
        data = self._limit_dict(
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
        if stored.kind == "general":
            data["enabled"] = stored.enabled
        return data

    def _general_limit_dict(self, req: MiniAppRequest, item: dict) -> dict:
        period = str(item.get("period_type") or "month")
        return self._limit_dict(
            user_id=req.user_id,
            kind="general",
            identifier=f"general:{item['id']}",
            title=str(item.get("name") or "Общий лимит"),
            category=None,
            amount=to_decimal_money(item.get("amount") or 0),
            currency=str(item.get("currency") or get_user_currency(req.user_id)),
            period=period,
            workspace_id=item.get("workspace_id"),
            alerts_enabled=bool(item.get("alerts_enabled", True)),
        ) | {"enabled": bool(item.get("enabled", True))}

    def _category_budget_dict(self, req: MiniAppRequest, item: dict) -> dict:
        period = str(item.get("period_type") or "month")
        categories = [str(category) for category in item.get("categories") or []]
        amount = to_decimal_money(item.get("amount") or 0)
        currency = str(item.get("currency") or get_user_currency(req.user_id))
        spent = self._category_budget_spent(req.user_id, item.get("workspace_id"), period, categories, currency)
        remaining = amount - spent
        status, percent = self._limit_status(spent, amount)
        return {
            "id": int(item["id"]),
            "kind": "category_budget",
            "title": str(item.get("name") or "Бюджет категорий"),
            "amount": amount,
            "currency": currency,
            "spent": spent,
            "remaining": remaining,
            "percent": percent,
            "period": period,
            "status": status,
            "categories": categories,
            "enabled": bool(item.get("enabled", True)),
            "alerts_enabled": bool(item.get("alerts_enabled", True)),
            "workspace_id": item.get("workspace_id"),
        }

    def _category_budget_spent(self, user_id: int, workspace_id: int | None, period: str, categories: list[str], currency: str) -> Decimal:
        if not categories:
            return Decimal("0.00")
        today = user_local_date(user_id, workspace_id)
        if period == "week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        else:
            start = today.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        where, params = self._workspace_filter_sql([workspace_id], user_id)
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(SUM(amount), 0)
              FROM public.operations
             WHERE {where}
               AND type='Расходы'
               AND category = ANY(%s)
               AND op_date BETWEEN %s AND %s
               AND COALESCE(currency, %s) = %s
               AND COALESCE(category,'') <> 'Без операций'
            """,
            (*params, categories, start, end, get_user_currency(user_id), currency),
        )
        return to_decimal_money(rows[0][0] if rows else 0)

    def _activity_label(self, tx: TransactionFilters) -> str:
        if tx.category:
            return f"Активность · {tx.category}"
        if tx.operation_type == "expense":
            return "Активность · Расходы"
        if tx.operation_type == "income":
            return "Активность · Доходы"
        return "Активность · Все операции"

    def _forecast_context(self, req: MiniAppRequest, params: dict[str, Any]) -> tuple[TransactionFilters, int | None, str, str, ForecastPeriod]:
        tx = self._transaction_filters(req, params)
        if tx.all_scope:
            raise MiniAppError(400, "forecast_workspace_required", "Выберите пространство для прогноза.")
        workspace_id = tx.workspace_ids[0]
        workspace = next((item for item in self._workspace_rows(req.user_id) if item["workspace_id"] == workspace_id), None)
        workspace_kind = str((workspace or {}).get("kind") or "legacy_personal")
        requested_currency = str(params.get("currency") or "").strip().upper()
        currency = self._validated_currency(requested_currency or get_user_currency(req.user_id))
        today = user_local_date(req.user_id, workspace_id)
        if tx.end < today:
            raise MiniAppError(400, "forecast_period_completed", "Период уже завершён.")
        if tx.start > today:
            raise MiniAppError(400, "forecast_period_future", "Период ещё не начался.")
        period = ForecastPeriod(tx.period_key, tx.start, tx.end, today)
        return tx, workspace_id, workspace_kind, currency, period

    def _forecast_data(self, req: MiniAppRequest, params: dict[str, Any], *, persist: bool = True) -> tuple[Any, Any]:
        _tx, workspace_id, workspace_kind, currency, period = self._forecast_context(req, params)
        repository = ForecastRepository()
        inputs = repository.load_inputs(
            user_id=req.user_id,
            workspace_id=workspace_id,
            workspace_kind=workspace_kind,
            currency=currency,
            period=period,
            default_currency=get_user_currency(req.user_id),
            timezone_name=user_timezone_name(req.user_id, workspace_id)[0],
        )
        forecast = calculate_spendable(inputs)
        if persist:
            repository.persist_prediction(inputs, forecast)
        return inputs, forecast

    def _overview_spendable(self, req: MiniAppRequest, params: dict[str, Any], tx: TransactionFilters) -> dict[str, Any]:
        if tx.all_scope:
            return forecast_unavailable("workspace_all").as_dict()
        today = user_local_date(req.user_id, tx.workspace_ids[0])
        if tx.end < today:
            return forecast_unavailable("period_completed").as_dict()
        try:
            _inputs, forecast = self._forecast_data(req, params)
            change = explain_forecast_change(forecast, ForecastRepository().previous_prediction(_inputs, forecast))
            recurring_commitments = [item for item in _inputs.commitments if item.source == "recurring"]
            return {
                "available": True,
                "amount": forecast.amount,
                "currency": forecast.currency,
                "approximate": forecast.approximate,
                "period_label": forecast.period_label,
                "quality_label": forecast.quality_label,
                "quality_tier": forecast.quality_tier,
                "risk_state": forecast.risk_state,
                "current_result": forecast.current_result,
                "known_commitments": forecast.known_commitments,
                "known_commitment_count": forecast.known_commitment_count,
                "goal_reserve": forecast.goal_reserve,
                "variable_reserve": forecast.variable_reserve,
                "variable_q50": forecast.variable_q50,
                "variable_q80": forecast.variable_q80,
                "variable_q90": forecast.variable_q90,
                "general_budget_remaining": forecast.general_budget_remaining,
                "general_budget_current_remaining": forecast.general_budget_current_remaining,
                "general_budget_projected_remaining": forecast.general_budget_projected_remaining,
                "reason_codes": [str(item.get("code")) for item in forecast.reasons if item.get("code")],
                "recurring_commitment_count": len(recurring_commitments),
                "recurring_commitments": sum((item.amount for item in recurring_commitments), Decimal("0.00")),
                "expected_end_result": forecast.expected_end_result,
                "change": change,
                "fingerprint": forecast.fingerprint,
                "feedback": forecast.feedback,
                "experiment": {
                    "enabled": True,
                    "variant": exposure_properties("spendable-explanation-v1", req.user_id, "home_spendable", forecast.quality_tier)["variant"],
                },
            }
        except MiniAppError as exc:
            if exc.code == "forecast_period_future":
                return forecast_unavailable("period_future").as_dict()
            raise
        except Exception as exc:
            log.info("miniapp_forecast_overview_unavailable reason=%s", type(exc).__name__)
            return {"available": False, "code": "temporarily_unavailable", "title": "Прогноз пока недоступен", "description": "Попробуйте обновить экран позже."}

    def spendable_forecast(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        try:
            _inputs, forecast = self._forecast_data(req, params)
        except MiniAppError:
            raise
        self._track(req, "spendable_opened", workspace_id=_inputs.workspace_id, properties={
            "surface": "forecast_detail",
            "quality_tier": forecast.quality_tier,
            "risk_bucket": forecast.risk_state,
            "model_family": forecast.model_family,
            "model_version": forecast.model_version,
        })
        return success(forecast.as_dict(), request_id=req.request_id)

    def forecast_can_spend(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        if "purchase_date" in body:
            raise MiniAppError(400, "unsupported_forecast_purchase_date", "Дата покупки пока не используется в расчёте.")
        params = {
            "workspace_id": body.get("workspace_id"),
            "period": body.get("period") or "current_month",
            "start_date": body.get("start_date"),
            "end_date": body.get("end_date"),
            "currency": body.get("currency"),
            "operation_type": "all",
            "category": "all",
        }
        try:
            amount = to_decimal_money(body.get("amount"), positive=True)
            inputs, forecast = self._forecast_data(req, params, persist=False)
            category = str(body.get("category") or "").strip() or None
            if category:
                category = self._validate_category(req, inputs.workspace_id, "Расходы", category)
            result = calculate_can_spend(inputs, forecast, amount, category)
        except MoneyParseError as exc:
            raise MiniAppError(400, "bad_forecast_amount", "Проверьте сумму покупки.") from exc
        self._track(req, "spendable_can_spend_checked", workspace_id=inputs.workspace_id, properties={
            "surface": "forecast_detail",
            "quality_tier": forecast.quality_tier,
            "risk_bucket": result["risk_state_after"],
            "verdict": result["verdict"],
        })
        return success(result, request_id=req.request_id)

    def forecast_feedback(self, req: MiniAppRequest, fingerprint: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        workspace_ids, all_scope = self._read_scope(req, body.get("workspace_id"))
        if all_scope:
            raise MiniAppError(400, "forecast_workspace_required", "Выберите пространство для прогноза.")
        feedback_type = str(body.get("feedback_type") or "")
        try:
            created = record_forecast_feedback(req.user_id, workspace_ids[0], fingerprint, feedback_type)
        except ValueError as exc:
            raise MiniAppError(400, "bad_forecast_feedback", "Проверьте оценку прогноза.") from exc
        if created:
            self._track(req, "spendable_feedback", workspace_id=workspace_ids[0], properties={"surface": "home_spendable", "verdict": feedback_type})
        return success({"recorded": created, "feedback_type": feedback_type}, request_id=req.request_id)

    def forecast_exposure(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, body.get("workspace_id"))
        if all_scope:
            raise MiniAppError(400, "forecast_workspace_required", "Выберите пространство для прогноза.")
        surface = str(body.get("surface") or "")
        quality_tier = str(body.get("quality_tier") or "known_only")
        if quality_tier not in {"known_only", "limited", "personal", "strong", "calibrated"}:
            raise MiniAppError(400, "bad_forecast_exposure", "Invalid forecast exposure.")
        try:
            properties = exposure_properties("spendable-explanation-v1", req.user_id, surface, quality_tier)
        except ValueError as exc:
            raise MiniAppError(400, "bad_forecast_exposure", "Invalid forecast exposure.") from exc
        self._track(req, "experiment_exposure", workspace_id=workspace_ids[0], properties=properties)
        return success({"recorded": True}, request_id=req.request_id)

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
        result_comparison = None
        if not params.get("_skip_comparison"):
            try:
                prev_start, prev_end, prev_key = self._previous_period(tx.start, tx.end, tx.period_key)
                previous = self._totals_for_tx(req, self._tx_for_period(req, tx, prev_start, prev_end, prev_key))
                selected = str(params.get("currency") or get_user_currency(req.user_id)).strip().upper()
                result_comparison = self._overview_metrics(totals, previous, currencies=[selected]).get(selected, {}).get("result")
            except Exception as exc:
                log.info("miniapp_home_comparison_unavailable reason=%s", type(exc).__name__)
        recent = self.operations(req, {**params, "limit": 3, "offset": 0})["data"]["items"][:3]
        info = None
        if not totals:
            info = {"kind": "welcome", "text": "Добавьте первую операцию, чтобы увидеть динамику периода."}
        elif aggregation_available:
            info = {"kind": "period", "text": "Показаны подтверждённые операции за выбранный период."}
        else:
            info = {"kind": "currencies", "text": "Валюты различаются, поэтому суммы сгруппированы без автоматической конвертации."}
        challenges = []
        focus_items = self._home_focus_items(req, params, tx)
        if tx.all_scope:
            scope_marker = focus_items[0]
            goal_items = [{**scope_marker, "description": "Цели доступны для одного пространства.", "target_mode": "goals"}]
            limit_items = [{**scope_marker, "description": "Лимиты доступны для одного пространства.", "target_mode": "limits"}]
        else:
            goal_items = [item for item in focus_items if item.get("kind") in {"goal", "empty"}]
            limit_items = [item for item in focus_items if item.get("kind") == "limit"]
        reminders = self._home_reminders(req)
        spendable = (
            {"available": False, "code": "not_requested"}
            if params.get("_skip_forecast")
            else self._overview_spendable(req, params, tx)
        )
        insights = [] if params.get("_skip_insights") else self._home_insights(req, tx, totals, focus_items, spendable)
        try:
            home_preferences = get_home_preferences(req.user_id)
        except Exception as exc:
            log.info("miniapp_home_preferences_unavailable reason=%s", type(exc).__name__)
            home_preferences = reconcile_home_preferences(None, None)
        shopping = {"items": [], "active_count": 0, "completed_count": 0, "read_only": True, "available": False}
        if not tx.all_scope and tx.workspace_ids[0] is not None:
            try:
                workspace_row = next((row for row in self._workspace_rows(req.user_id) if row["workspace_id"] == tx.workspace_ids[0]), None)
                summary = shopping_summary(int(tx.workspace_ids[0]), preview_limit=5)
                shopping = {
                    "items": [item.as_dict() for item in summary.items],
                    "active_count": summary.active_count,
                    "completed_count": summary.completed_count,
                    "read_only": not bool(workspace_row and workspace_row.get("role") in WRITE_ROLES),
                    "available": True,
                }
            except Exception as exc:
                log.info("miniapp_home_shopping_unavailable reason=%s", type(exc).__name__)
        announcements = []
        if not params.get("_skip_announcements"):
            try:
                announcement_today = user_local_date(req.user_id, None if tx.all_scope else tx.workspace_ids[0])
                workspace_where, workspace_params = self._workspace_filter_sql(tx.workspace_ids, req.user_id)
                ready = report_ready_kinds(
                    workspace_where,
                    workspace_params,
                    today=announcement_today,
                    operation_type=tx.operation_type,
                    category=tx.category,
                )
                announcements = resolve_announcements(
                    req.user_id,
                    today=announcement_today,
                    extra_candidates=report_ready_announcements(ready, today=announcement_today),
                )
            except Exception as exc:
                log.info("miniapp_announcements_unavailable reason=%s", type(exc).__name__)
        return success({
            "period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end},
            "filters": {"operation_type": tx.operation_type, "category": tx.category or "all"},
            "workspace_scope": "all" if tx.all_scope else tx.workspace_ids[0],
            "aggregation_available": aggregation_available,
            "totals_by_currency": totals,
            "result_comparison": result_comparison,
            "recent_operations": recent,
            "info": info,
            "challenges": challenges,
            "challenge": challenges[0] if challenges else None,
            "focus_items": focus_items,
            "goal_items": goal_items,
            "limit_items": limit_items,
            "focus": focus_items[0] if focus_items else {"kind": "empty", "title": "Фокус свободен", "description": "Добавьте цель или лимит, чтобы видеть главный приоритет.", "target_mode": "goals"},
            "insights": insights,
            "insight": insights[0] if insights else None,
            "reminders": reminders,
            "reminder": reminders[0] if reminders else self._home_reminder(req),
            "activity": self._activity_calendar(req, tx),
            "home_widgets": home_widget_registry(),
            "home_preferences": home_preferences,
            "shopping": shopping,
            "announcements": announcements,
            "spendable": spendable,
        }, request_id=req.request_id)

    def _home_challenge(self, req: MiniAppRequest) -> dict | None:
        cards = self._home_challenges(req)
        return cards[0] if cards else None

    def _home_challenges(self, req: MiniAppRequest) -> list[dict]:
        all_cards: list[ChallengeCard] = []
        for section in ("today", "week", "month"):
            try:
                cards = upsert_assignments(req.user_id, section)
            except Exception as exc:
                log.info("miniapp_home_challenge_unavailable section=%s reason=%s", section, type(exc).__name__)
                cards = []
            if not cards:
                continue
            active = [card for card in cards if not card.completed]
            all_cards.append(active[0] if active else cards[0])
        return [self._challenge_dict(card) for card in all_cards]

    def _home_challenge_legacy(self, req: MiniAppRequest) -> dict | None:
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
            "period_type": card.definition.period_type,
            "period_key": card.period_key,
            "period_end": card.period_end,
        }

    def _home_focus(self, req: MiniAppRequest, params: dict[str, Any], tx: TransactionFilters) -> dict | None:
        items = self._home_focus_items(req, params, tx)
        return items[0] if items else None

    def _home_focus_items(self, req: MiniAppRequest, params: dict[str, Any], tx: TransactionFilters) -> list[dict]:
        if tx.all_scope:
            return [{"kind": "empty", "title": "Выберите пространство", "description": "Фокус доступен для одного пространства.", "read_only": True}]
        candidates: list[dict] = []
        severity_rank = {"critical": 400, "high": 300, "medium": 200, "normal": 100}
        today = user_local_date(req.user_id, tx.workspace_ids[0])
        try:
            selected_category_key = normalized_category_key(tx.category) if tx.category else None
        except ValueError:
            selected_category_key = None
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
            if not tx.all_scope:
                try:
                    budget_rows = list_category_budget_groups(req.user_id, tx.workspace_ids[0])
                    limits = [
                        *limits,
                        *[
                            self._category_budget_dict(req, item)
                            for item in budget_rows
                            if item.get("period_type") in LIMIT_PERIODS and item.get("enabled", True)
                        ],
                    ]
                except Exception as exc:
                    log.info("miniapp_home_focus_category_budgets_unavailable reason=%s", type(exc).__name__)
            for limit in limits:
                if not limit.get("enabled", True):
                    continue
                limit_category_key = None
                if limit.get("category"):
                    try:
                        limit_category_key = normalized_category_key(str(limit["category"]))
                    except ValueError:
                        continue
                if tx.category and limit.get("kind") == "category_budget":
                    budget_keys = set()
                    for category_name in limit.get("categories") or []:
                        try:
                            budget_keys.add(normalized_category_key(str(category_name)))
                        except ValueError:
                            continue
                    if selected_category_key not in budget_keys:
                        continue
                elif tx.category and limit.get("category"):
                    if selected_category_key and limit_category_key:
                        if limit_category_key != selected_category_key:
                            continue
                    elif str(limit["category"]) != tx.category:
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
                if selected_category_key and limit_category_key == selected_category_key and severity != "normal":
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
                    "amount": limit.get("amount"),
                    "spent": limit.get("spent"),
                    "currency": limit.get("currency"),
                    "period": limit.get("period"),
                    "category": limit.get("category"),
                    "budget_kind": limit.get("kind"),
                    "enabled": limit.get("enabled", True),
                    "status": status,
                    "cta_label": "Открыть лимиты",
                    "target_mode": "limits",
                })
        except Exception as exc:
            log.info("miniapp_home_focus_limits_unavailable reason=%s", type(exc).__name__)
        if not candidates:
            return []
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["kind"]), str(item.get("id") or "")))
        items = []
        for candidate in candidates:
            item = dict(candidate)
            item.pop("score", None)
            items.append(item)
        return items

    def _insight_rows(self, req: MiniAppRequest, tx: TransactionFilters) -> list[tuple[Any, Any, Any, Any, Any]]:
        return pg_fetchall(
            f"""
            SELECT COALESCE(category, 'Прочее'),
                   NULLIF(TRIM(COALESCE(comment,'')), ''),
                   COALESCE(currency, %s),
                   COALESCE(SUM(amount),0),
                   COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type='Расходы'
             GROUP BY COALESCE(category, 'Прочее'),
                      NULLIF(TRIM(COALESCE(comment,'')), ''),
                      COALESCE(currency, %s)
            """,
            (get_user_currency(req.user_id), *tx.params, get_user_currency(req.user_id)),
        )

    def _home_insights(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        totals: dict[str, dict[str, Decimal | int]],
        focus_items: list[dict],
        spendable: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not totals or tx.all_scope or tx.operation_type == "income":
            return []
        forecast_currency = str((spendable or {}).get("currency") or "").upper()
        preferred_currency = forecast_currency or str(get_user_currency(req.user_id) or "").upper()
        if preferred_currency in totals:
            currency = preferred_currency
        elif len(totals) == 1:
            currency = next(iter(totals))
        else:
            return []
        prev_start, prev_end, _prev_key = self._previous_period(tx.start, tx.end, tx.period_key)
        prev_tx = self._tx_for_period(req, tx, prev_start, prev_end, _prev_key)
        try:
            current_rows = self._insight_rows(req, tx)
            previous_rows = self._insight_rows(req, prev_tx)
            limits = [item for item in focus_items if item.get("kind") == "limit"]
            workspace_row = next(
                (item for item in self._workspace_rows(req.user_id) if item.get("workspace_id") == tx.workspace_ids[0]),
                None,
            )
            snapshot = build_snapshot(
                user_id=req.user_id,
                workspace_id=tx.workspace_ids[0],
                workspace_kind="personal" if tx.workspace_ids[0] is None else "workspace",
                currency=currency,
                period=PeriodRef(tx.period_key, tx.start, tx.end),
                comparison_period=PeriodRef(_prev_key, prev_start, prev_end),
                current_rows=current_rows,
                previous_rows=previous_rows,
                limits=limits,
                scope_category=tx.category,
                can_write=bool(workspace_row and workspace_row.get("role") in WRITE_ROLES),
                forecast=spendable,
            )
            return insight_engine.generate(
                snapshot,
                today=user_local_date(req.user_id, tx.workspace_ids[0]),
            )
        except Exception as exc:
            log.info("miniapp_home_insights_unavailable reason=%s", type(exc).__name__)
            return []

    def _insight_workspace(self, req: MiniAppRequest, workspace_id: Any) -> int | None:
        workspace_ids, all_scope = self._read_scope(req, workspace_id)
        if all_scope or len(workspace_ids) != 1:
            raise MiniAppError(400, "concrete_workspace_required", "Choose one workspace.")
        return workspace_ids[0]

    def insight_impression(self, req: MiniAppRequest, insight_id: str, body: dict[str, Any]) -> dict:
        fingerprint = str(insight_id or "").strip().lower()
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise MiniAppError(400, "bad_insight_id", "Invalid insight.")
        workspace_id = self._insight_workspace(req, body.get("workspace_id"))
        state = insight_engine.impression(req.user_id, workspace_id, fingerprint)
        if not state:
            raise MiniAppError(404, "insight_not_found", "Insight was not found.")
        self._track(
            req,
            "insight_impression",
            workspace_id=workspace_id,
            properties={"detector_type": state.detector_type, "surface": "home"},
        )
        return success({"recorded": True}, request_id=req.request_id)

    def insight_feedback(self, req: MiniAppRequest, insight_id: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        fingerprint = str(insight_id or "").strip().lower()
        feedback_type = str(body.get("feedback_type") or "").strip().lower()
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise MiniAppError(400, "bad_insight_id", "Invalid insight.")
        if feedback_type not in {"useful", "not_useful"}:
            raise MiniAppError(400, "bad_insight_feedback", "Invalid feedback.")
        workspace_id = self._insight_workspace(req, body.get("workspace_id"))
        state = insight_engine.feedback(req.user_id, workspace_id, fingerprint, feedback_type)
        if not state:
            raise MiniAppError(404, "insight_not_found", "Insight was not found.")
        self._track(
            req,
            "insight_feedback",
            workspace_id=workspace_id,
            properties={"detector_type": state.detector_type, "feedback_type": feedback_type, "surface": "detail"},
        )
        return success({
            "recorded": True,
            "feedback_type": feedback_type,
            "suppressed_until": state.suppression_until,
        }, request_id=req.request_id)

    def operations(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        category_key = str(params.get("category_key") or "").strip()
        scope_category = str(params.get("scope_category") or "").strip()
        tx_params = (
            {**params, "category": scope_category}
            if scope_category
            else {**params, "category": "all"} if category_key else params
        )
        tx = self._transaction_filters(req, tx_params, alias="o")
        limit = min(max(int(params.get("limit") or DEFAULT_PAGE_SIZE), 1), READ_PAGE_LIMIT)
        offset = max(int(params.get("offset") or 0), 0)
        filters = [tx.where_sql]
        values: list[Any] = [*tx.params]
        currency = str(params.get("currency") or "").strip().upper()
        if currency:
            if currency not in ALLOWED_CURRENCIES:
                raise MiniAppError(400, "bad_currency", "Invalid currency.")
            filters.append("COALESCE(o.currency, %s)=%s")
            values.extend([get_user_currency(req.user_id), currency])
        if category_key:
            if len(category_key) > 80:
                raise MiniAppError(400, "bad_category", "Invalid category.")
            filters.append(f"{self._category_key_sql('o.category')}=%s")
            try:
                values.append(normalized_category_key(category_key))
            except ValueError as exc:
                raise MiniAppError(400, "bad_category", "Invalid category.") from exc
        merchant_key = str(params.get("merchant_key") or "").strip()
        if merchant_key:
            normalized_merchant = normalize_merchant_key(merchant_key)
            if not normalized_merchant or normalized_merchant == EMPTY_MERCHANT_KEY or len(normalized_merchant) > 120:
                raise MiniAppError(400, "bad_merchant", "Invalid merchant.")
            filters.append(f"{merchant_key_sql('o.comment')}=%s")
            values.append(normalized_merchant)
        merchant = str(params.get("merchant") or "").strip()
        if merchant:
            if len(merchant) > 120:
                raise MiniAppError(400, "bad_merchant", "Invalid merchant.")
            filters.append("TRIM(COALESCE(o.comment,''))=%s")
            values.append(merchant)
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
            if row_type == row[2] and normalized_category_key(category) == normalized_category_key(str(row[3] or "")):
                fields["category"] = category
            else:
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
        overview = self.overview(req, {**params, "_skip_insights": True, "_skip_announcements": True, "_skip_forecast": True})["data"]
        available_currencies = sorted(str(currency) for currency in overview["totals_by_currency"].keys())
        requested_currency = str(params.get("currency") or "").strip().upper()
        prev_start, prev_end, _prev_key = self._previous_period(tx.start, tx.end, tx.period_key)
        prev_tx = self._tx_for_period(req, tx, prev_start, prev_end, _prev_key)
        radar_type = self._chart_op_type(params, "radar_type", tx.operation_type)
        radar_available_currencies = self._radar_currencies(req, tx, prev_start, prev_end, radar_type)
        if requested_currency and requested_currency not in ALLOWED_CURRENCIES:
            raise MiniAppError(400, "bad_currency", "Invalid currency.")
        selected_currency = requested_currency if requested_currency in available_currencies else None
        if selected_currency:
            chart_currencies = [requested_currency]
        else:
            chart_currencies = available_currencies
        aggregation_available = len(chart_currencies) <= 1
        category_type = self._chart_op_type(params, "category_type", tx.operation_type)
        grouping = self._validated_grouping(str(params.get("grouping") or "auto"), tx.start, tx.end)
        structure = self._dimension_structure(req, tx, prev_tx, category_type, dimension="category", currencies=chart_currencies)
        merchant_structure = self._dimension_structure(req, tx, prev_tx, category_type, dimension="merchant", currencies=chart_currencies)
        contribution = self._change_contribution(req, tx, prev_tx, category_type, currencies=chart_currencies)
        dynamics = self._time_dynamics(req, tx, grouping=grouping, currencies=chart_currencies)
        radar = self._radar(
            req,
            tx,
            prev_start,
            prev_end,
            _prev_key,
            radar_type,
            currency=selected_currency or (requested_currency if requested_currency in radar_available_currencies else None) or (radar_available_currencies[0] if len(radar_available_currencies) == 1 else None),
            available_currencies=radar_available_currencies,
        )
        activity = self._activity_calendar(req, tx)
        previous_totals = self._totals_for_tx(req, prev_tx, currencies=chart_currencies)
        overview_metrics = self._overview_metrics(overview["totals_by_currency"], previous_totals, currencies=chart_currencies or None)
        detail_type = self._chart_op_type(params, "detail_operation_type", tx.operation_type) if params.get("detail_kind") else category_type
        selected_detail = self._analytics_detail(req, tx, prev_tx, params, detail_type)
        search = self._analytics_search(req, tx, params, category_type, currencies=chart_currencies)
        response_currencies = chart_currencies if selected_currency else available_currencies
        summary_totals = {
            currency: overview["totals_by_currency"].get(currency, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            for currency in response_currencies
        }
        currency_groups = {
            currency: {
                "summary": {
                    **overview["totals_by_currency"].get(currency, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0}),
                    "result": to_decimal_money(overview["totals_by_currency"].get(currency, {}).get("income") or 0) - to_decimal_money(overview["totals_by_currency"].get(currency, {}).get("expense") or 0),
                },
                "category_structure": structure["currency_groups"].get(currency, {"currency": currency, "total": Decimal("0.00"), "items": []}),
                "merchant_structure": merchant_structure["currency_groups"].get(currency, {"currency": currency, "total": Decimal("0.00"), "items": []}),
                "time_dynamics": dynamics["currency_groups"].get(currency, {"currency": currency, "datasets": []}),
            }
            for currency in response_currencies
        }
        return success({
            "period": {"key": tx.period_key, "start_date": tx.start, "end_date": tx.end},
            "previous_period": {"key": _prev_key, "start_date": prev_start, "end_date": prev_end},
            "filters": {"operation_type": tx.operation_type, "category": tx.category or "all"},
            "overview": overview,
            "aggregation_available": aggregation_available,
            "available_currencies": available_currencies,
            "radar_available_currencies": radar_available_currencies,
            "selected_currency": selected_currency,
            "currency_groups": currency_groups,
            "summary": {
                "aggregation_available": aggregation_available,
                "available_currencies": available_currencies,
                "currency_groups": {
                    currency: {
                        **values,
                        "result": to_decimal_money(values["income"]) - to_decimal_money(values["expense"]),
                    }
                    for currency, values in summary_totals.items()
                },
                "totals_by_currency": summary_totals,
                "result_by_currency": {
                    currency: to_decimal_money(values["income"]) - to_decimal_money(values["expense"])
                    for currency, values in summary_totals.items()
                },
            },
            "overview_metrics": overview_metrics,
            "category_structure": structure,
            "merchant_structure": merchant_structure,
            "change_contribution": contribution,
            "time_dynamics": dynamics,
            "radar": radar,
            "activity_calendar": activity,
            "search": search,
            "selected_detail": selected_detail,
            "top_expense_categories": structure["items"],
        }, request_id=req.request_id)

    def report(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        report_kind = str(params.get("report_kind") or "selected")
        if report_kind not in {"selected", "completed_week", "completed_month"}:
            raise MiniAppError(400, "bad_report_kind", "Invalid report kind.")
        report_params = dict(params)
        if report_kind != "selected":
            workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
            today = user_local_date(req.user_id, None if all_scope else workspace_ids[0])
            start, end, _period_key = completed_report_period(report_kind, today)
            if report_kind == "completed_month":
                report_params.update({"period": "previous_month"})
            else:
                report_params.update({"period": "custom", "start_date": start.isoformat(), "end_date": end.isoformat()})
        analytics = self.analytics(req, report_params)["data"]
        tx = self._transaction_filters(req, report_params)
        requested_currency = str(params.get("currency") or "").strip().upper() or None
        if requested_currency and requested_currency not in ALLOWED_CURRENCIES:
            raise MiniAppError(400, "bad_currency", "Invalid currency.")
        if tx.all_scope:
            workspace_name, workspace_type, read_only, workspace_scope = "Все пространства", "all", True, "all"
        elif tx.workspace_ids[0] is None:
            workspace_name, workspace_type, read_only, workspace_scope = "Личное", "legacy_personal", False, None
        else:
            row = next((item for item in self._workspace_rows(req.user_id) if item["workspace_id"] == tx.workspace_ids[0]), None)
            workspace_name = str((row or {}).get("name") or "Пространство")
            workspace_type = str((row or {}).get("kind") or "shared")
            read_only = bool(row and row.get("read_only"))
            workspace_scope = tx.workspace_ids[0]
        report = build_report(
            analytics,
            ReportBuildRequest(
                report_kind=report_kind,
                workspace_scope=workspace_scope,
                workspace_name=workspace_name,
                workspace_type=workspace_type,
                read_only=read_only,
                selected_currency=requested_currency,
                fallback_currency=get_user_currency(req.user_id),
            ),
        )
        self._track(
            req,
            "report_opened",
            workspace_id=None if tx.all_scope else tx.workspace_ids[0],
            properties={
                "report_kind": report_kind,
                "period_kind": report["period"]["key"],
                "workspace_type": workspace_type,
                "operation_type": tx.operation_type,
                "result": report["data_state"],
                "source": "mini_app",
                "currency": report["selected_currency"],
            },
        )
        return success({"report": report}, request_id=req.request_id)

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

    def _category_key_sql(self, column: str) -> str:
        return f"REPLACE(LOWER(TRIM(REGEXP_REPLACE(COALESCE({column},''), '\\s+', ' ', 'g'))), 'ё', 'е')"

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
        by_currency: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for category, currency, total, count in rows:
            currency_code = str(currency)
            if currencies and currency_code not in currencies:
                continue
            display_name = str(category or "Прочее")
            try:
                category_key = normalized_category_key(display_name)
            except ValueError:
                category_key = normalized_category_key("Прочее")
            bucket = by_currency[currency_code].setdefault(
                category_key,
                {"category": display_name, "total": Decimal("0.00"), "count": 0},
            )
            bucket["total"] += to_decimal_money(total)
            bucket["count"] += int(count or 0)
        items = []
        groups: dict[str, dict[str, Any]] = {}
        for currency, grouped in by_currency.items():
            values = sorted(
                ((str(row["category"]), row["total"], row["count"]) for row in grouped.values()),
                key=lambda row: (-row[1], row[0]),
            )
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
                item = {"category": "Остальные", "currency": currency, "total": other_total, "count": other_count, "share": share}
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
            item = buckets.setdefault((bucket, str(currency)), {"date": bucket, "currency": str(currency), "income": Decimal("0.00"), "expense": Decimal("0.00"), "result": Decimal("0.00"), "count": 0})
            if typ == "Доходы":
                item["income"] += to_decimal_money(total)
            elif typ == "Расходы":
                item["expense"] += to_decimal_money(total)
            item["result"] = item["income"] - item["expense"]
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
                    {"kind": "result", "items": [{"date": item["date"], "amount": item["result"], "count": item["count"]} for item in currency_items]},
                ],
            }
        return {"grouping": grouping, "currency_groups": groups, "items": items}

    def _tx_for_period(self, req: MiniAppRequest, tx: TransactionFilters, start: date, end: date, period_key: str) -> TransactionFilters:
        return self._transaction_filters(req, {
            "workspace_id": "all" if tx.all_scope else tx.workspace_ids[0],
            "period": "custom",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "operation_type": tx.operation_type,
            "category": tx.category or "all",
        })

    def _totals_for_tx(self, req: MiniAppRequest, tx: TransactionFilters, *, currencies: list[str] | None = None) -> dict[str, dict[str, Decimal | int]]:
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(currency, %s), type, COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
             GROUP BY COALESCE(currency, %s), type
            """,
            (get_user_currency(req.user_id), *tx.params, get_user_currency(req.user_id)),
        )
        allowed = set(currencies or [])
        totals: dict[str, dict[str, Decimal | int]] = {}
        for currency, typ, total, count in rows:
            currency_code = str(currency)
            if allowed and currency_code not in allowed:
                continue
            bucket = totals.setdefault(currency_code, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            if typ == "Доходы":
                bucket["income"] = to_decimal_money(total)
            elif typ == "Расходы":
                bucket["expense"] = to_decimal_money(total)
            bucket["count"] = int(bucket["count"]) + int(count or 0)
        return totals

    def _metric_comparison(self, current: Decimal, previous: Decimal, *, sign_change_on_cross_zero: bool = False) -> dict[str, Any]:
        delta = current - previous
        pct = None
        state = "ok"
        if previous == 0:
            state = "zero_baseline" if current != 0 else "empty_previous"
        elif sign_change_on_cross_zero and ((previous < 0 < current) or (previous > 0 > current)):
            state = "sign_change"
        else:
            pct = (delta / abs(previous) * Decimal("100")).quantize(Decimal("0.01"))
        return {"current": current, "previous": previous, "delta": delta, "pct": pct, "state": state}

    def _overview_metrics(
        self,
        current: dict[str, dict[str, Decimal | int]],
        previous: dict[str, dict[str, Decimal | int]],
        *,
        currencies: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        selected = set(currencies or [])
        currency_codes = sorted((set(current) | set(previous)) if not selected else selected)
        result: dict[str, dict[str, Any]] = {}
        for currency in currency_codes:
            cur = current.get(currency, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            prev = previous.get(currency, {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
            cur_income = to_decimal_money(cur.get("income") or 0)
            cur_expense = to_decimal_money(cur.get("expense") or 0)
            prev_income = to_decimal_money(prev.get("income") or 0)
            prev_expense = to_decimal_money(prev.get("expense") or 0)
            result[currency] = {
                "income": self._metric_comparison(cur_income, prev_income),
                "expense": self._metric_comparison(cur_expense, prev_expense),
                "result": self._metric_comparison(cur_income - cur_expense, prev_income - prev_expense, sign_change_on_cross_zero=True),
                "count": int(cur.get("count") or 0),
                "previous_count": int(prev.get("count") or 0),
            }
        return result

    def _dimension_rows(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        op_type: str,
        *,
        dimension: str,
        currencies: list[str] | None = None,
    ) -> list[tuple[str, str, Decimal, int]]:
        if dimension == "merchant":
            select_expr = "NULLIF(TRIM(COALESCE(comment,'')), '')"
            group_expr = "NULLIF(TRIM(COALESCE(comment,'')), '')"
        else:
            select_expr = "category"
            group_expr = "category"
        rows = pg_fetchall(
            f"""
            SELECT {select_expr}, COALESCE(currency, %s), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql} AND type=%s
             GROUP BY {group_expr}, COALESCE(currency, %s)
             ORDER BY COALESCE(SUM(amount),0) DESC, {group_expr}
            """,
            (get_user_currency(req.user_id), *tx.params, op_type, get_user_currency(req.user_id)),
        )
        allowed = set(currencies or [])
        result = []
        for raw_name, currency, total, count in rows:
            currency_code = str(currency)
            if allowed and currency_code not in allowed:
                continue
            result.append((str(raw_name or ""), currency_code, to_decimal_money(total), int(count or 0)))
        return result

    def _fold_dimension(self, rows: list[tuple[str, str, Decimal, int]], *, dimension: str) -> dict[str, dict[str, dict[str, Any]]]:
        if dimension == "merchant":
            return fold_merchant_rows(rows)
        grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for name, currency, total, count in rows:
            raw_name = name.strip()
            display_name = raw_name or ("Без описания" if dimension == "merchant" else "Прочее")
            try:
                key = normalized_category_key(display_name)
            except ValueError:
                key = normalized_category_key("Прочее")
            drillable = True
            fallback = False
            bucket = grouped[currency].setdefault(
                key,
                {
                    "key": key,
                    "name": display_name,
                    "total": Decimal("0.00"),
                    "count": 0,
                    "synthetic": False,
                    "drillable": drillable,
                    "fallback": fallback,
                },
            )
            bucket["total"] += total
            bucket["count"] += count
        return grouped

    def _dimension_structure(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        prev_tx: TransactionFilters,
        op_type: str,
        *,
        dimension: str,
        currencies: list[str] | None = None,
        top_n: int | None = CHART_TOP_N,
    ) -> dict:
        current = self._fold_dimension(self._dimension_rows(req, tx, op_type, dimension=dimension, currencies=currencies), dimension=dimension)
        previous = self._fold_dimension(self._dimension_rows(req, prev_tx, op_type, dimension=dimension, currencies=currencies), dimension=dimension)
        groups: dict[str, dict[str, Any]] = {}
        flat_items = []
        for currency in sorted(set(current) | set(previous)):
            keys = set(current.get(currency, {})) | set(previous.get(currency, {}))
            values = []
            for key in keys:
                prev = previous.get(currency, {}).get(key, {"name": key, "total": Decimal("0.00"), "count": 0})
                cur = current.get(currency, {}).get(key, {"name": prev["name"], "total": Decimal("0.00"), "count": 0})
                values.append({
                    "key": key,
                    "category" if dimension == "category" else "merchant": cur["name"],
                    "currency": currency,
                    "total": cur["total"],
                    "previous_total": prev["total"],
                    "delta": cur["total"] - prev["total"],
                    "count": cur["count"],
                    "previous_count": prev["count"],
                    "synthetic": bool(cur.get("synthetic") or prev.get("synthetic")),
                    "drillable": bool(cur.get("drillable", True) and prev.get("drillable", True)),
                    "fallback": bool(cur.get("fallback") or prev.get("fallback")),
                    **({"source": cur.get("source") or prev.get("source") or "deterministic", "raw_aliases": raw_aliases_for_bucket(cur) or raw_aliases_for_bucket(prev)} if dimension == "merchant" else {}),
                })
            values.sort(key=lambda item: (-to_decimal_money(item["total"]), str(item.get("category") or item.get("merchant"))))
            currency_total = sum((to_decimal_money(item["total"]) for item in values), Decimal("0.00"))
            group_items = []
            selected_values = values if top_n is None else values[:top_n]
            for item in selected_values:
                share = int((to_decimal_money(item["total"]) / currency_total * Decimal("100")).to_integral_value()) if currency_total > 0 else 0
                rendered = {**item, "share": share}
                group_items.append(rendered)
                flat_items.append(rendered)
            if top_n is not None:
                remainder = values[top_n:]
                if remainder:
                    other_current = sum((to_decimal_money(item["total"]) for item in remainder), Decimal("0.00"))
                    other_previous = sum((to_decimal_money(item["previous_total"]) for item in remainder), Decimal("0.00"))
                    other_count = sum((int(item.get("count") or 0) for item in remainder), 0)
                    other_previous_count = sum((int(item.get("previous_count") or 0) for item in remainder), 0)
                    rendered = {
                        "key": f"__synthetic_other_{dimension}__",
                        "category" if dimension == "category" else "merchant": "Остальные",
                        "currency": currency,
                        "total": other_current,
                        "previous_total": other_previous,
                        "delta": other_current - other_previous,
                        "count": other_count,
                        "previous_count": other_previous_count,
                        "share": int((other_current / currency_total * Decimal("100")).to_integral_value()) if currency_total > 0 else 0,
                        "synthetic": True,
                        "drillable": False,
                        "fallback": False,
                    }
                    group_items.append(rendered)
                    flat_items.append(rendered)
            groups[currency] = {"currency": currency, "total": currency_total, "items": group_items}
        return {
            "type": "income" if op_type == "Доходы" else "expense",
            "dimension": dimension,
            "top_n": top_n or len(flat_items),
            "currency_groups": groups,
            "items": flat_items,
        }

    def _change_contribution(
        self,
        req: MiniAppRequest,
        tx: TransactionFilters,
        prev_tx: TransactionFilters,
        op_type: str,
        *,
        currencies: list[str] | None = None,
    ) -> dict:
        structure = self._dimension_structure(req, tx, prev_tx, op_type, dimension="category", currencies=currencies, top_n=None)
        groups = {}
        for currency, group in structure["currency_groups"].items():
            all_items = sorted(group["items"], key=lambda item: (-abs(to_decimal_money(item["delta"])), str(item["category"])))
            items = all_items[:CHART_TOP_N]
            remainder = all_items[CHART_TOP_N:]
            total_current = sum((to_decimal_money(item["total"]) for item in all_items), Decimal("0.00"))
            total_previous = sum((to_decimal_money(item["previous_total"]) for item in all_items), Decimal("0.00"))
            total_delta = total_current - total_previous
            if remainder:
                other_current = sum((to_decimal_money(item["total"]) for item in remainder), Decimal("0.00"))
                other_previous = sum((to_decimal_money(item["previous_total"]) for item in remainder), Decimal("0.00"))
                other_delta = other_current - other_previous
                other_count = sum((int(item.get("count") or 0) for item in remainder), 0)
                other_previous_count = sum((int(item.get("previous_count") or 0) for item in remainder), 0)
                if other_current != 0 or other_previous != 0 or other_delta != 0 or other_count or other_previous_count:
                    items.append({
                        "key": "__synthetic_other_contribution__",
                        "category": "Остальные",
                        "currency": currency,
                        "total": other_current,
                        "previous_total": other_previous,
                        "delta": other_delta,
                        "count": other_count,
                        "previous_count": other_previous_count,
                        "share": int((other_current / total_current * Decimal("100")).to_integral_value()) if total_current > 0 else 0,
                        "synthetic": True,
                        "drillable": False,
                        "fallback": False,
                    })
            groups[currency] = {
                "currency": currency,
                "type": "income" if op_type == "Доходы" else "expense",
                "current_total": total_current,
                "previous_total": total_previous,
                "total_delta": total_delta,
                "items": items,
                "reconciles": sum((to_decimal_money(item["delta"]) for item in items), Decimal("0.00")) == total_delta,
            }
        return {"type": "income" if op_type == "Доходы" else "expense", "currency_groups": groups, "items": [item for group in groups.values() for item in group["items"]]}

    def _analytics_operations_scope(self, tx: TransactionFilters, op_type: str, currency: str, *, category_key: str | None = None, merchant: str | None = None, merchant_key: str | None = None) -> dict:
        return {
            "workspace_id": "all" if tx.all_scope else tx.workspace_ids[0],
            "period": tx.period_key,
            "start_date": tx.start.isoformat(),
            "end_date": tx.end.isoformat(),
            "operation_type": "income" if op_type == "Доходы" else "expense",
            "category": "all" if category_key else tx.category or "all",
            "scope_category": tx.category if category_key else None,
            "currency": currency,
            "category_key": category_key,
            "merchant": merchant,
            "merchant_key": merchant_key,
        }

    def _detail_operation_rows(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, *, category_key: str | None = None, merchant: str | None = None, merchant_key: str | None = None, limit: int = 8) -> list[dict]:
        params = self._analytics_operations_scope(tx, op_type, currency, category_key=category_key, merchant=merchant, merchant_key=merchant_key)
        response = self.operations(req, {**params, "limit": limit, "offset": 0})
        return response["data"]["items"]

    def _category_merchant_breakdown(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, category_key: str) -> dict:
        category_expr = self._category_key_sql("category")
        rows = pg_fetchall(
            f"""
            SELECT NULLIF(TRIM(COALESCE(comment,'')), ''), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               AND COALESCE(currency, %s)=%s
               AND {category_expr}=%s
             GROUP BY NULLIF(TRIM(COALESCE(comment,'')), '')
             ORDER BY COALESCE(SUM(amount),0) DESC, NULLIF(TRIM(COALESCE(comment,'')), '')
            """,
            (*tx.params, op_type, get_user_currency(req.user_id), currency, category_key),
        )
        total = sum((to_decimal_money(row[1]) for row in rows), Decimal("0.00"))
        total_count = sum((int(row[2] or 0) for row in rows), 0)
        grouped = fold_merchant_rows((str(row[0] or ""), currency, to_decimal_money(row[1]), int(row[2] or 0)) for row in rows)
        values = sorted(
            grouped.get(currency, {}).values(),
            key=lambda item: (-to_decimal_money(item["total"]), str(item["name"])),
        )
        items = []
        top_rows = values[:CHART_TOP_N]
        for merchant in top_rows:
            item_total = to_decimal_money(merchant["total"])
            share = int((item_total / total * Decimal("100")).to_integral_value()) if total > 0 else 0
            items.append({
                "key": merchant["key"],
                "merchant": merchant["name"],
                "currency": currency,
                "total": item_total,
                "count": int(merchant.get("count") or 0),
                "share": share,
                "synthetic": False,
                "drillable": bool(merchant.get("drillable", True)),
                "fallback": bool(merchant.get("fallback")),
                "source": merchant.get("source") or "deterministic",
                "raw_aliases": raw_aliases_for_bucket(merchant),
            })
        other_rows = values[CHART_TOP_N:]
        if other_rows:
            other_total = sum((to_decimal_money(row["total"]) for row in other_rows), Decimal("0.00"))
            other_count = sum((int(row.get("count") or 0) for row in other_rows), 0)
            items.append({
                "key": "__synthetic_other_merchant__",
                "merchant": "Остальные",
                "currency": currency,
                "total": other_total,
                "count": other_count,
                "share": int((other_total / total * Decimal("100")).to_integral_value()) if total > 0 else 0,
                "synthetic": True,
                "drillable": False,
                "fallback": False,
            })
        return {"currency": currency, "total": total, "count": total_count, "items": items}

    def _detail_summary(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, *, category_key: str | None = None, merchant: str | None = None, merchant_key: str | None = None) -> dict[str, Any]:
        filters = [tx.where_sql, "type=%s", "COALESCE(currency, %s)=%s"]
        values: list[Any] = [*tx.params, op_type, get_user_currency(req.user_id), currency]
        if category_key:
            filters.append(f"{self._category_key_sql('category')}=%s")
            values.append(category_key)
        if merchant_key:
            normalized_merchant = normalize_merchant_key(merchant_key)
            if not normalized_merchant:
                raise MiniAppError(400, "bad_merchant", "Invalid merchant.")
            filters.append(f"{merchant_key_sql('comment')}=%s")
            values.append(normalized_merchant)
        if merchant:
            filters.append("TRIM(COALESCE(comment,''))=%s")
            values.append(merchant)
        rows = pg_fetchall(
            f"""
            SELECT COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {' AND '.join(filters)}
            """,
            tuple(values),
        )
        total = to_decimal_money(rows[0][0] if rows else 0)
        count = int(rows[0][1] if rows else 0)
        return {
            "total": total,
            "operation_count": count,
            "average_check": (total / Decimal(count)).quantize(Decimal("0.01")) if count else Decimal("0.00"),
        }

    def _merchant_context(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, merchant_key: str, *, category_key: str | None = None) -> dict[str, Any]:
        normalized_merchant = normalize_merchant_key(merchant_key)
        if not normalized_merchant:
            raise MiniAppError(400, "bad_merchant", "Invalid merchant.")
        category_expr = self._category_key_sql("category")
        merchant_expr = merchant_key_sql("comment")
        category_filter = f"AND {category_expr}=%s" if category_key else ""
        category_values = (category_key,) if category_key else ()
        rows = pg_fetchall(
            f"""
            SELECT {category_expr} AS category_key,
                   MIN(TRIM(COALESCE(category, 'Прочее'))),
                   COALESCE(SUM(amount),0),
                   COALESCE(SUM(CASE WHEN {merchant_expr}=%s THEN amount ELSE 0 END),0),
                   COUNT(*) FILTER (WHERE {merchant_expr}=%s)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               AND COALESCE(currency, %s)=%s
               {category_filter}
             GROUP BY {category_expr}
             ORDER BY COALESCE(SUM(CASE WHEN {merchant_expr}=%s THEN amount ELSE 0 END),0) DESC,
                      COALESCE(SUM(amount),0) DESC,
                      MIN(TRIM(COALESCE(category, 'Прочее')))
            """,
            (
                normalized_merchant,
                normalized_merchant,
                *tx.params,
                op_type,
                get_user_currency(req.user_id),
                currency,
                *category_values,
                normalized_merchant,
            ),
        )
        scope_total = sum((to_decimal_money(row[2]) for row in rows), Decimal("0.00"))
        category_items = []
        for category_key, category, category_total, merchant_total, merchant_count in rows:
            merchant_amount = to_decimal_money(merchant_total)
            if merchant_amount <= 0 and int(merchant_count or 0) <= 0:
                continue
            category_amount = to_decimal_money(category_total)
            category_items.append({
                "category_key": str(category_key or ""),
                "category": str(category or "Прочее"),
                "category_total": category_amount,
                "merchant_total": merchant_amount,
                "merchant_count": int(merchant_count or 0),
                "merchant_share_of_category": merchant_features(
                    current_total=merchant_amount,
                    current_count=int(merchant_count or 0),
                    category_total=category_amount,
                    scope_total=scope_total,
                )["merchant_share_of_category"],
            })
        primary = category_items[0] if category_items else None
        return {"scope_total": scope_total, "categories": category_items, "primary_category": primary}

    def _merchant_identity_snapshot(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, merchant_key: str, *, category_key: str | None = None) -> dict[str, Any]:
        normalized_merchant = normalize_merchant_key(merchant_key)
        category_filter = f"AND {self._category_key_sql('category')}=%s" if category_key else ""
        category_values = (category_key,) if category_key else ()
        rows = pg_fetchall(
            f"""
            SELECT NULLIF(TRIM(COALESCE(comment,'')), ''), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               AND COALESCE(currency, %s)=%s
               AND {merchant_key_sql('comment')}=%s
               {category_filter}
             GROUP BY NULLIF(TRIM(COALESCE(comment,'')), '')
             ORDER BY COUNT(*) DESC, COALESCE(SUM(amount),0) DESC, NULLIF(TRIM(COALESCE(comment,'')), '')
            """,
            (*tx.params, op_type, get_user_currency(req.user_id), currency, normalized_merchant, *category_values),
        )
        grouped = fold_merchant_rows((str(row[0] or ""), currency, to_decimal_money(row[1]), int(row[2] or 0)) for row in rows)
        bucket = grouped.get(currency, {}).get(normalized_merchant)
        if not bucket:
            return {"merchant_key": normalized_merchant, "display_name": normalized_merchant, "raw_aliases": []}
        return {"merchant_key": normalized_merchant, "display_name": bucket["name"], "raw_aliases": raw_aliases_for_bucket(bucket)}

    def _merchant_baseline(self, req: MiniAppRequest, tx: TransactionFilters, op_type: str, currency: str, merchant_key: str, *, category_key: str | None = None) -> dict[str, Any]:
        normalized_merchant = normalize_merchant_key(merchant_key)
        periods = comparable_baseline_periods(tx.start, tx.end, tx.period_key)
        if not periods:
            return merchant_baseline([])
        earliest = periods[-1][0]
        latest = periods[0][1]
        date_case = " ".join(
            f"WHEN op_date BETWEEN %s AND %s THEN {idx}"
            for idx, (_start, _end) in enumerate(periods)
        )
        date_values = [value for period in periods for value in period]
        category_filter = f"AND {self._category_key_sql('category')}=%s" if category_key else ""
        category_values = (category_key,) if category_key else ()
        rows = pg_fetchall(
            f"""
            SELECT bucket, COALESCE(SUM(amount),0), COUNT(*)
              FROM (
                    SELECT CASE {date_case} END AS bucket, amount
                      FROM public.operations
                     WHERE {tx.where_sql.replace('op_date BETWEEN %s AND %s', 'op_date BETWEEN %s AND %s')}
                       AND type=%s
                       AND COALESCE(currency, %s)=%s
                       AND {merchant_key_sql('comment')}=%s
                       {category_filter}
                   ) scoped
             WHERE bucket IS NOT NULL
             GROUP BY bucket
             ORDER BY bucket
            """,
            (
                *date_values,
                *self._replace_tx_period(tx, earliest, latest),
                op_type,
                get_user_currency(req.user_id),
                currency,
                normalized_merchant,
                *category_values,
            ),
        )
        by_bucket = {int(row[0]): (to_decimal_money(row[1]), int(row[2] or 0)) for row in rows}
        period_rows = []
        for idx, (period_start, period_end) in enumerate(periods):
            total, count = by_bucket.get(idx, (Decimal("0.00"), 0))
            period_rows.append((period_start, period_end, total, count))
        return merchant_baseline(period_rows)

    def _replace_tx_period(self, tx: TransactionFilters, start: date, end: date) -> tuple[Any, ...]:
        values = list(tx.params)
        period_index = len(tx.params) - (2 + (1 if tx.operation_type != "all" else 0) + (1 if tx.category else 0))
        values[period_index:period_index + 2] = [start, end]
        return tuple(values)

    def _analytics_detail(self, req: MiniAppRequest, tx: TransactionFilters, prev_tx: TransactionFilters, params: dict[str, Any], op_type: str) -> dict | None:
        kind = str(params.get("detail_kind") or "").strip()
        if kind not in {"category", "merchant"}:
            return None
        value = str(params.get("detail_value") or "").strip()
        currency = str(params.get("detail_currency") or params.get("currency") or "").strip().upper()
        if not value or not currency:
            return None
        if currency not in ALLOWED_CURRENCIES:
            raise MiniAppError(400, "bad_currency", "Invalid currency.")
        if kind == "category":
            try:
                category_key = normalized_category_key(value)
            except ValueError as exc:
                raise MiniAppError(400, "bad_category", "Invalid category.") from exc
            operation_items = self._detail_operation_rows(req, tx, op_type, currency, category_key=category_key)
            merchant_breakdown = self._category_merchant_breakdown(req, tx, op_type, currency, category_key)
            current_summary = self._detail_summary(req, tx, op_type, currency, category_key=category_key)
            previous_summary = self._detail_summary(req, prev_tx, op_type, currency, category_key=category_key)
            comparison = self._metric_comparison(current_summary["total"], previous_summary["total"])
            return {
                "kind": "category",
                "title": value,
                "currency": currency,
                "operation_type": "income" if op_type == "Доходы" else "expense",
                "category_key": category_key,
                "merchant_breakdown": merchant_breakdown,
                "operations": operation_items,
                "operation_count": current_summary["operation_count"],
                "previous_operation_count": previous_summary["operation_count"],
                "total": current_summary["total"],
                "previous_total": previous_summary["total"],
                "delta": comparison["delta"],
                "pct": comparison["pct"],
                "state": comparison["state"],
                "visible_total": current_summary["total"],
                "operation_scope": self._analytics_operations_scope(tx, op_type, currency, category_key=category_key),
            }
        merchant_key = normalize_merchant_key(value)
        if not merchant_key or merchant_key == EMPTY_MERCHANT_KEY:
            raise MiniAppError(400, "bad_merchant", "Invalid merchant.")
        raw_detail_category_key = str(params.get("detail_category_key") or "").strip()
        try:
            detail_category_key = normalized_category_key(raw_detail_category_key) if raw_detail_category_key else None
        except ValueError as exc:
            raise MiniAppError(400, "bad_category", "Invalid category.") from exc
        operation_items = self._detail_operation_rows(
            req, tx, op_type, currency,
            category_key=detail_category_key,
            merchant_key=merchant_key,
        )
        current_summary = self._detail_summary(
            req, tx, op_type, currency,
            category_key=detail_category_key,
            merchant_key=merchant_key,
        )
        previous_summary = self._detail_summary(
            req, prev_tx, op_type, currency,
            category_key=detail_category_key,
            merchant_key=merchant_key,
        )
        comparison = self._metric_comparison(current_summary["total"], previous_summary["total"])
        context = self._merchant_context(req, tx, op_type, currency, merchant_key, category_key=detail_category_key)
        identity = self._merchant_identity_snapshot(req, tx, op_type, currency, merchant_key, category_key=detail_category_key)
        primary_category = context["primary_category"]
        feature_set = merchant_features(
            current_total=current_summary["total"],
            current_count=current_summary["operation_count"],
            previous_total=previous_summary["total"],
            previous_count=previous_summary["operation_count"],
            category_total=primary_category["category_total"] if primary_category else None,
            scope_total=context["scope_total"],
        )
        baseline = self._merchant_baseline(req, tx, op_type, currency, merchant_key, category_key=detail_category_key)
        return {
            "kind": "merchant",
            "title": identity["display_name"],
            "currency": currency,
            "operation_type": "income" if op_type == "Доходы" else "expense",
            "merchant_key": merchant_key,
            "category_key": detail_category_key,
            "total": current_summary["total"],
            "previous_total": previous_summary["total"],
            "delta": comparison["delta"],
            "pct": comparison["pct"],
            "state": comparison["state"],
            "operation_count": current_summary["operation_count"],
            "previous_operation_count": previous_summary["operation_count"],
            "average_check": current_summary["average_check"],
            "previous_average_check": previous_summary["average_check"],
            "frequency_delta": feature_set["frequency_delta"],
            "frequency_pct": feature_set["frequency_pct"],
            "average_check_delta": feature_set["average_check_delta"],
            "average_check_pct": feature_set["average_check_pct"],
            "merchant_share_of_total": feature_set["merchant_share_of_total"],
            "merchant_share_of_category": feature_set["merchant_share_of_category"],
            "primary_category": primary_category,
            "baseline": baseline,
            "raw_aliases": identity["raw_aliases"],
            "operations": operation_items,
            "operation_scope": self._analytics_operations_scope(
                tx,
                op_type,
                currency,
                category_key=detail_category_key,
                merchant_key=merchant_key,
            ),
        }

    def _analytics_search(self, req: MiniAppRequest, tx: TransactionFilters, params: dict[str, Any], op_type: str, *, currencies: list[str] | None = None) -> dict:
        query = str(params.get("analytics_search") or params.get("q") or "").strip()
        if len(query) < 2:
            return {"query": query, "items": []}
        q = f"%{query[:80]}%"
        normalized_q = f"%{norm_text(query[:80])}%"
        merchant_query_key = normalize_merchant_key(query[:80])
        merchant_normalized_q = f"%{merchant_query_key}%" if merchant_query_key else ""
        currency_filter = ""
        currency_values: list[Any] = []
        selected_currency = str(params.get("currency") or "").strip().upper()
        if selected_currency:
            currency_filter = "AND COALESCE(currency, %s)=%s"
            currency_values.extend([get_user_currency(req.user_id), selected_currency])
        category_expr = self._category_key_sql("category")
        allowed = set(currencies or [])
        category_rows = pg_fetchall(
            f"""
            SELECT {category_expr} AS category_key, MIN(TRIM(COALESCE(category, 'Прочее'))),
                   COALESCE(currency, %s), COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               {currency_filter}
               AND {category_expr} LIKE %s
             GROUP BY {category_expr}, COALESCE(currency, %s)
             ORDER BY COUNT(*) DESC, COALESCE(SUM(amount),0) DESC
             LIMIT 4
            """,
            (get_user_currency(req.user_id), *tx.params, op_type, *currency_values, normalized_q, get_user_currency(req.user_id)),
        )
        merchant_rows = pg_fetchall(
            f"""
            SELECT {merchant_key_sql('comment')} AS merchant_key,
                   NULLIF(TRIM(COALESCE(comment,'')), ''),
                   COALESCE(currency, %s),
                   COALESCE(SUM(amount),0), COUNT(*)
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               {currency_filter}
               AND ({merchant_key_sql('comment')} LIKE %s OR comment ILIKE %s)
               AND {merchant_key_sql('comment')} <> ''
             GROUP BY {merchant_key_sql('comment')}, NULLIF(TRIM(COALESCE(comment,'')), ''), COALESCE(currency, %s)
             ORDER BY {merchant_key_sql('comment')}, COUNT(*) DESC, COALESCE(SUM(amount),0) DESC
            """,
            (get_user_currency(req.user_id), *tx.params, op_type, *currency_values, merchant_normalized_q, q, get_user_currency(req.user_id)),
        )
        operation_rows = pg_fetchall(
            f"""
            SELECT id, op_date, category, amount, COALESCE(currency, %s), COALESCE(comment,'')
              FROM public.operations
             WHERE {tx.where_sql}
               AND type=%s
               {currency_filter}
               AND (category ILIKE %s OR comment ILIKE %s)
             ORDER BY op_date DESC, id DESC
             LIMIT 8
            """,
            (get_user_currency(req.user_id), *tx.params, op_type, *currency_values, q, q),
        )
        items = []
        for category_key, category, currency, total, count in category_rows:
            currency_code = str(currency)
            if allowed and currency_code not in allowed:
                continue
            category_name = str(category or "Прочее")
            items.append({
                "kind": "category",
                "title": category_name,
                "subtitle": f"{int(count or 0)} операций",
                "currency": currency_code,
                "amount": to_decimal_money(total),
                "params": {"detail_kind": "category", "detail_value": str(category_key or category_name), "detail_currency": currency_code},
            })
        grouped_merchants = fold_merchant_rows((str(merchant or ""), str(currency), to_decimal_money(total), int(count or 0)) for _key, merchant, currency, total, count in merchant_rows)
        merchant_items = []
        for currency_code, grouped in grouped_merchants.items():
            if allowed and currency_code not in allowed:
                continue
            for merchant in grouped.values():
                if not merchant.get("drillable", True):
                    continue
                merchant_items.append((currency_code, merchant))
        merchant_items.sort(key=lambda item: (-to_decimal_money(item[1]["total"]), -int(item[1].get("count") or 0), str(item[1]["name"])))
        for currency_code, merchant in merchant_items[:4]:
            items.append({
                "kind": "merchant",
                "title": merchant["name"],
                "subtitle": f"{int(merchant.get('count') or 0)} операций",
                "currency": currency_code,
                "amount": to_decimal_money(merchant["total"]),
                "params": {
                    "detail_kind": "merchant",
                    "detail_value": merchant["key"],
                    "detail_currency": currency_code,
                    "merchant_key": merchant["key"],
                },
            })
        for operation_id, _op_date, category, amount, currency, merchant in operation_rows:
            currency_code = str(currency)
            if allowed and currency_code not in allowed:
                continue
            category_name = str(category or "Прочее")
            merchant_name = str(merchant or "").strip()
            items.append({
                "kind": "operation",
                "title": merchant_name or category_name,
                "subtitle": category_name,
                "currency": currency_code,
                "amount": to_decimal_money(amount),
                "operation_id": int(operation_id),
            })
        return {"query": query, "items": items[:8]}

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
        reminders = self._home_reminders(req)
        if reminders:
            return reminders[0]
        return {
            "state": "empty",
            "id": None,
            "title": "Нет запланированных событий",
            "event_date": None,
            "amount_text": None,
            "category": None,
            "next_event_date": None,
            "status_text": "Добавьте напоминание в Планах.",
            "overdue_days": 0,
            "repeat_rule": None,
        }

    def _home_reminders(self, req: MiniAppRequest) -> list[dict]:
        local_today = user_local_date(req.user_id)
        try:
            reminders = list_reminders(req.user_id, active_only=True, today=local_today)
        except Exception as exc:
            log.info("miniapp_home_reminders_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            reminders = []
        rank = {"overdue": 0, "today": 1, "upcoming": 2}
        active = [item for item in reminders if item.get("status") in rank]
        active.sort(key=lambda item: (rank.get(str(item.get("status")), 9), item.get("event_date") or date.max, int(item.get("id") or 0)))
        result = []
        for item in active:
            event_date = item["event_date"]
            days = (event_date - local_today).days
            if item["status"] == "overdue":
                status = f"Нужно было оплатить {event_date.isoformat()}"
            elif days == 0:
                status = "Сегодня"
            elif days == 1:
                status = "Завтра"
            else:
                status = f"Через {days} дн."
            result.append({
                "state": "overdue" if item["status"] == "overdue" else "upcoming",
                "id": int(item["id"]),
                "title": str(item["title"]),
                "event_date": event_date,
                "amount_text": format_money(to_decimal_money(item["amount"]), item["currency"]),
                "category": str(item["category"]),
                "next_event_date": None,
                "status_text": status,
                "overdue_days": max(0, -days),
                "repeat_rule": item["repeat_rule"],
            })
        return result

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
        counts = {}
        for row in rows:
            if not isinstance(row[0], date):
                continue
            try:
                counts[row[0]] = int(row[1] or 0)
            except (TypeError, ValueError):
                continue
        days = []
        current = tx.start
        max_count = 0
        active_days = 0
        operations_count = 0
        while current <= tx.end:
            count = counts.get(current, 0)
            max_count = max(max_count, count)
            if count > 0:
                active_days += 1
                operations_count += count
            days.append({"date": current, "count": count})
            current += timedelta(days=1)
        today = user_local_date(req.user_id, None if tx.all_scope else tx.workspace_ids[0])
        streak_cursor = min(today, tx.end)
        if tx.start <= today <= tx.end and counts.get(today, 0) <= 0:
            streak_cursor = today - timedelta(days=1)
        current_streak = 0
        while streak_cursor >= tx.start and counts.get(streak_cursor, 0) > 0:
            current_streak += 1
            streak_cursor -= timedelta(days=1)
        return {
            "start_date": tx.start,
            "end_date": tx.end,
            "max_count": max_count,
            "days": days,
            "current_streak": current_streak,
            "active_days": active_days,
            "days_in_period": (tx.end - tx.start).days + 1,
            "operations_count": operations_count,
            "label": self._activity_label(tx),
        }

    def plans(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        def safe_reminders(workspace_id: int | None = None) -> list[dict]:
            try:
                return list_reminders(req.user_id, active_only=False, today=user_local_date(req.user_id, workspace_id))
            except Exception as exc:
                log.info("miniapp_plans_reminders_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
                return []
        if all_scope:
            return success({
                "read_only": True,
                "goals": [],
                "archived_goals": [],
                "limits": [],
                "general_limits": [],
                "category_budgets": [],
                "reminders": safe_reminders(),
                "all_scope_note": "Выберите одно пространство, чтобы увидеть цели и лимиты без смешивания данных.",
            }, request_id=req.request_id)
        workspace_id = workspace_ids[0]
        try:
            general_limit_rows = list_general_limits(req.user_id, workspace_id)
        except Exception as exc:
            log.info("miniapp_plans_general_limits_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            general_limit_rows = []
        try:
            category_budget_rows = list_category_budget_groups(req.user_id, workspace_id)
        except Exception as exc:
            log.info("miniapp_plans_category_budgets_unavailable user=%s reason=%s", req.user_id, type(exc).__name__)
            category_budget_rows = []
        general_limits = [self._general_limit_dict(req, item) for item in general_limit_rows if item.get("period_type") in LIMIT_PERIODS]
        category_budgets = [self._category_budget_dict(req, item) for item in category_budget_rows if item.get("period_type") in LIMIT_PERIODS]
        return success({
            "read_only": False,
            "goals": self._goals(req, workspace_id),
            "archived_goals": self._goals(req, workspace_id, status_group="archive"),
            "limits": [item for item in self._limits(req, workspace_id) if item.get("kind") == "category"],
            "general_limits": general_limits,
            "category_budgets": category_budgets,
            "reminders": safe_reminders(workspace_id),
            "all_scope_note": None,
        }, request_id=req.request_id)

    def planning_estimate(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, body.get("workspace_id"))
        if all_scope:
            raise MiniAppError(400, "concrete_workspace_required", "Выберите пространство для расчёта.")
        workspace_id = workspace_ids[0]
        ctx = self._workspace_detail(req, workspace_id)
        kind = str(body.get("kind") or "")
        period = str(body.get("period") or "month")
        goal_id = int(body["editing_entity_id"]) if kind == "goal" and str(body.get("editing_entity_id") or "").isdigit() else None
        goal = get_goal(goal_id, req.user_id, workspace_id) if goal_id is not None else None
        if goal_id is not None and goal is None:
            raise MiniAppError(404, "goal_not_found", "Цель не найдена.")
        default_currency = self._validated_currency(get_user_currency(req.user_id))
        currency = self._validated_currency(body.get("currency"), fallback=goal.currency if goal else default_currency)
        categories: list[str] = []
        if kind in {"category_limit", "category_budget"}:
            raw_categories = body.get("categories") if kind == "category_budget" else [body.get("category") or body.get("category_key")]
            if not isinstance(raw_categories, list):
                raise MiniAppError(400, "categories_required", "Выберите категории для расчёта.")
            allowed = {item["normalized_name"]: item["name"] for item in self._managed_categories(req, workspace_id, "Расходы")}
            for raw in raw_categories:
                try:
                    key = normalized_category_key(str(raw or ""))
                except ValueError as exc:
                    raise MiniAppError(400, "categories_required", "Выберите категории для расчёта.") from exc
                if key not in allowed:
                    raise MiniAppError(400, "category_not_available", "Выберите категорию из списка.")
                if key not in {normalized_category_key(item) for item in categories}:
                    categories.append(allowed[key])
            if not categories:
                raise MiniAppError(400, "categories_required", "Выберите категории для расчёта.")
        try:
            target = None
            current = Decimal("0.00")
            deadline = None
            frequency = FREQUENCY_NONE
            schedule: dict[str, Any] = {}
            if kind == "goal":
                target = to_decimal_money(body.get("target_amount") if body.get("target_amount") is not None else (goal.target_amount if goal else None), positive=True)
                current = goal.current_balance if goal else to_decimal_money(body.get("current_amount") or 0)
                deadline = self._goal_deadline_from_body(body, goal)
                frequency = str(body.get("frequency") or (goal.frequency if goal else FREQUENCY_NONE))
                if frequency not in GOAL_FREQUENCIES:
                    raise PlanningError("bad_goal_schedule")
                schedule = self._schedule_from_body(body, frequency)
            raw_editing_id = str(body.get("editing_entity_id") or "") or None
            if kind == "category_budget" and raw_editing_id and raw_editing_id.isdigit():
                raw_editing_id = f"budget:{raw_editing_id}"
            planning_request = PlanningRequest(
                user_id=req.user_id,
                workspace_id=workspace_id,
                kind=kind,
                currency=currency,
                default_currency=default_currency,
                period=period,
                categories=tuple(categories),
                editing_entity_id=raw_editing_id,
                target_amount=target,
                current_amount=current,
                deadline=deadline,
                frequency=frequency,
                schedule_config=schedule,
                editing_goal_id=goal_id,
            )
            estimate = calculate_planning_estimate(
                planning_request,
                today=user_local_date(req.user_id, workspace_id),
            )
        except (PlanningError, MoneyParseError, ValueError, TypeError) as exc:
            code = exc.code if isinstance(exc, PlanningError) else "bad_planning_request"
            raise MiniAppError(400, code, "Проверьте параметры расчёта.") from exc
        read_only = ctx.role not in WRITE_ROLES
        estimate["read_only"] = read_only
        estimate["can_apply"] = not read_only and estimate.get("recommendation") is not None
        self._track(
            req,
            "smart_planning_calculated",
            workspace_id=workspace_id,
            properties={
                "planning_kind": kind,
                "period_kind": "month" if kind == "goal" else period,
                "history_confidence": estimate["history_confidence"],
                "source": "mini_app",
                "workspace_type": ctx.kind,
            },
        )
        return success({"estimate": estimate}, request_id=req.request_id)

    def _reminder_dict(self, item: dict[str, Any] | None) -> dict | None:
        if not item:
            return None
        amount = to_decimal_money(item.get("amount") or 0)
        currency = str(item.get("currency") or "RUB")
        return {
            "id": int(item["id"]),
            "title": str(item.get("title") or ""),
            "amount": amount,
            "amount_text": format_money(amount, currency),
            "currency": currency,
            "category": str(item.get("category") or "Прочее"),
            "rem_type": str(item.get("rem_type") or "Расходы"),
            "event_date": item.get("event_date"),
            "status": str(item.get("status") or "upcoming"),
            "repeat_rule": str(item.get("repeat_rule") or "none"),
            "repeat_interval_days": item.get("repeat_interval_days"),
            "notify_days_before": int(item.get("notify_days_before") or 0),
            "next_event_date": item.get("next_event_date"),
            "is_active": bool(item.get("is_active")),
        }

    def _reminder_error(self, exc: ReminderError) -> MiniAppError:
        status = 404 if exc.code == "reminder_not_found" else 403 if exc.code == "reminder_access_denied" else 400
        messages = {
            "reminder_not_found": "Напоминание не найдено.",
            "reminder_inactive": "Напоминание выключено.",
            "reminder_already_recorded": "Это напоминание уже записано.",
            "reminder_invalid_date": "Проверьте дату напоминания.",
            "reminder_invalid_repeat": "Проверьте повтор напоминания.",
            "reminder_access_denied": "Нет доступа к напоминанию.",
            "reminder_stale_occurrence": "Напоминание уже изменилось. Обновите экран.",
            "reminder_title_required": "Введите название напоминания.",
            "reminder_category_required": "Выберите категорию.",
        }
        return MiniAppError(status, exc.code, messages.get(exc.code, "Не получилось обновить напоминание."))

    def _reminder_body(self, req: MiniAppRequest, body: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        payload = dict(body)
        if "currency" in payload:
            payload["currency"] = self._validated_currency(payload.get("currency"))
        if "rem_type" in payload or not partial:
            payload["rem_type"] = OP_TYPES.get(str(payload.get("rem_type") or payload.get("type") or "expense")) or str(payload.get("rem_type") or "")
        if "category" in payload and payload.get("workspace_id") not in {None, "", "all", "ALL"}:
            workspace_id = int(payload.get("workspace_id"))
            op_type = str(payload.get("rem_type") or "Расходы")
            payload["category"] = self._validate_category(req, workspace_id, op_type, str(payload.get("category") or ""))
        return payload

    def reminders(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        today = user_local_date(req.user_id)
        self._track(req, "mini_app_plans_reminders_opened", properties={"source": "mini_app", "action": "open"})
        return success({"items": [self._reminder_dict(item) for item in list_reminders(req.user_id, active_only=False, today=today)]}, request_id=req.request_id)

    def reminder_detail(self, req: MiniAppRequest, reminder_id: int) -> dict:
        item = get_reminder(req.user_id, int(reminder_id), today=user_local_date(req.user_id))
        if not item:
            raise MiniAppError(404, "reminder_not_found", "Напоминание не найдено.")
        self._track(req, "mini_app_reminder_opened", properties={"source": "mini_app", "reminder_state": str(item.get("status") or "unknown")})
        return success({"reminder": self._reminder_dict(item)}, request_id=req.request_id)

    def create_reminder(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            item = create_reminder(req.user_id, self._reminder_body(req, body))
        except ReminderError as exc:
            raise self._reminder_error(exc) from exc
        self._track(req, "mini_app_reminder_created", properties={"source": "mini_app", "result": "success", "reminder_state": str(item.get("status") or "upcoming")})
        return success({"reminder": self._reminder_dict(item)}, request_id=req.request_id)

    def update_reminder(self, req: MiniAppRequest, reminder_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            item = update_reminder(req.user_id, int(reminder_id), **self._reminder_body(req, body, partial=True))
        except ReminderError as exc:
            raise self._reminder_error(exc) from exc
        self._track(req, "mini_app_reminder_updated", properties={"source": "mini_app", "result": "success", "reminder_state": str(item.get("status") or "upcoming")})
        return success({"reminder": self._reminder_dict(item)}, request_id=req.request_id)

    def delete_reminder(self, req: MiniAppRequest, reminder_id: int) -> dict:
        self._check_write_rate(req)
        deleted = delete_user_reminder(req.user_id, int(reminder_id))
        if not deleted:
            raise MiniAppError(404, "reminder_not_found", "Напоминание не найдено.")
        self._track(req, "mini_app_reminder_updated", properties={"source": "mini_app", "result": "deleted", "action": "delete"})
        return success({"deleted": True, "reminder_id": int(reminder_id)}, request_id=req.request_id)

    def toggle_reminder(self, req: MiniAppRequest, reminder_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            item = toggle_reminder(req.user_id, int(reminder_id), body.get("enabled") if "enabled" in body else None)
        except ReminderError as exc:
            raise self._reminder_error(exc) from exc
        self._track(req, "mini_app_reminder_updated", properties={"source": "mini_app", "result": "enabled" if item.get("is_active") else "disabled", "action": "toggle"})
        return success({"reminder": self._reminder_dict(item)}, request_id=req.request_id)

    def snooze_reminder(self, req: MiniAppRequest, reminder_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            item = snooze_reminder(req.user_id, int(reminder_id), days=int(body.get("days") or 1))
        except ReminderError as exc:
            raise self._reminder_error(exc) from exc
        self._track(req, "mini_app_reminder_snoozed", properties={"source": "mini_app", "result": "success", "reminder_state": str(item.get("status") or "upcoming")})
        return success({"reminder": self._reminder_dict(item)}, request_id=req.request_id)

    def record_reminder(self, req: MiniAppRequest, reminder_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        idem = str(body.get("idempotency_key") or "").strip()[:120]
        if not idem:
            raise MiniAppError(400, "idempotency_required", "Idempotency key is required.")
        ctx = self._write_scope(req, body.get("workspace_id"))
        expected = None
        if body.get("event_date"):
            try:
                expected = date.fromisoformat(str(body.get("event_date")))
            except ValueError as exc:
                raise MiniAppError(400, "reminder_invalid_date", "Проверьте дату напоминания.") from exc
        payload, recorded, created = self._record_reminder_atomically(req, ctx, int(reminder_id), idem, body, expected)
        if created and recorded:
            try:
                record_financial_operation_post_commit(recorded, workspace_kind=ctx.kind, metadata={"source": "reminder"})
            except Exception as exc:
                log.info("miniapp_reminder_post_commit_failed operation_id=%s reason=%s", recorded.operation_id, type(exc).__name__)
            self._track(req, "mini_app_reminder_recorded", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "result": "success", "reminder_state": "recorded"})
        return success(payload, request_id=req.request_id)

    def _record_reminder_atomically(
        self,
        req: MiniAppRequest,
        ctx: WorkspaceContext,
        reminder_id: int,
        idem: str,
        body: dict[str, Any],
        expected_event_date: date | None,
    ) -> tuple[dict, RecordedOperation | None, bool]:
        key = self._namespaced_idempotency_key("reminder:record", idem)
        request_hash = self._request_hash(body)
        conn = get_conn()
        recorded: RecordedOperation | None = None
        try:
            with conn.cursor() as cur:
                claim = self._claim_idempotency_tx(cur, req.user_id, key, request_hash)
                if claim["status"] == "completed":
                    conn.commit()
                    return claim["response"], None, False
                if claim["status"] != "claimed":
                    conn.rollback()
                    raise MiniAppError(claim["http_status"], claim["status"], claim["message"])
                try:
                    result = record_reminder_tx(
                        cur,
                        user_id=req.user_id,
                        reminder_id=reminder_id,
                        workspace=ctx,
                        chat_type="group" if ctx.kind == "group" else "private",
                        expected_event_date=expected_event_date,
                    )
                except ReminderError as exc:
                    raise self._reminder_error(exc) from exc
                recorded = result.operation
                operation = self._operation_dict_from_recorded(recorded, workspace_name=ctx.name) if recorded else None
                response = {"result": result.status, "reminder": self._reminder_dict(result.reminder), "operation": operation}
                self._complete_idempotency_tx(cur, req.user_id, key, request_hash, response, operation_id=recorded.operation_id if recorded else None)
            conn.commit()
            return response, recorded, bool(recorded)
        except errors.UndefinedTable as exc:
            conn.rollback()
            raise MiniAppError(503, "miniapp_not_configured", "Mini App storage is not configured.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _category_budget_body(self, req: MiniAppRequest, ctx: WorkspaceContext, body: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        period = str(body.get("period") or body.get("period_type") or "month")
        if period not in LIMIT_PERIODS:
            raise MiniAppError(400, "bad_budget_period", "Проверьте период бюджета.")
        raw_categories = body.get("categories") or []
        if not isinstance(raw_categories, list):
            raise MiniAppError(400, "budget_invalid_categories", "Выберите категории из списка.")
        allowed = {item["normalized_name"]: item["name"] for item in self._managed_categories(req, ctx.workspace_id, "Расходы")}
        selected = []
        for category in raw_categories:
            name = str(category or "").strip()[:64]
            key = normalized_category_key(name)
            if key not in allowed:
                raise MiniAppError(400, "budget_invalid_categories", "Выберите категории из списка.")
            selected.append(allowed[key])
        if not selected:
            raise MiniAppError(400, "budget_invalid_categories", "Выберите хотя бы одну категорию.")
        return {
            "name": str(body.get("title") or body.get("name") or "Бюджет категорий").strip()[:120],
            "amount": to_decimal_money(body.get("amount"), positive=True),
            "currency": self._validated_currency(body.get("currency"), fallback=str((existing or {}).get("currency") or get_user_currency(req.user_id))),
            "period_type": period,
            "categories": selected,
            "enabled": bool(body["enabled"]) if "enabled" in body else bool((existing or {}).get("enabled", True)),
            "alerts_enabled": bool(body["alerts_enabled"]) if "alerts_enabled" in body else bool((existing or {}).get("alerts_enabled", True)),
        }

    def create_category_budget(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        payload = self._category_budget_body(req, ctx, body)
        group_id = create_category_budget_group(user_id=req.user_id, workspace_id=ctx.workspace_id, **payload)
        item = next((row for row in list_category_budget_groups(req.user_id, ctx.workspace_id) if int(row["id"]) == int(group_id)), None)
        if not item:
            raise MiniAppError(404, "budget_not_found", "Бюджет не найден.")
        self._track(req, "mini_app_budget_created", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "budget_kind": "category_group", "period_kind": payload["period_type"], "result": "success"})
        return success({"budget": self._category_budget_dict(req, item)}, request_id=req.request_id)

    def update_category_budget(self, req: MiniAppRequest, group_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        if "toggle" in body:
            try:
                item = set_category_budget_group_enabled(user_id=req.user_id, workspace_id=ctx.workspace_id, group_id=int(group_id), enabled=bool(body.get("enabled")) if "enabled" in body else None, alerts_enabled=bool(body.get("alerts_enabled")) if "alerts_enabled" in body else None)
            except LookupError as exc:
                raise MiniAppError(404, "budget_not_found", "Бюджет не найден.") from exc
        else:
            current = next((row for row in list_category_budget_groups(req.user_id, ctx.workspace_id) if int(row["id"]) == int(group_id)), None)
            if not current:
                raise MiniAppError(404, "budget_not_found", "Бюджет не найден.")
            payload = self._category_budget_body(req, ctx, body, existing=current)
            try:
                update_category_budget_group(user_id=req.user_id, workspace_id=ctx.workspace_id, group_id=int(group_id), **payload)
            except LookupError as exc:
                raise MiniAppError(404, "budget_not_found", "Бюджет не найден.") from exc
            item = next((row for row in list_category_budget_groups(req.user_id, ctx.workspace_id) if int(row["id"]) == int(group_id)), None)
            if not item:
                raise MiniAppError(404, "budget_not_found", "Бюджет не найден.")
        self._track(req, "mini_app_budget_created", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "budget_kind": "category_group", "action": "update", "result": "success"})
        return success({"budget": self._category_budget_dict(req, item)}, request_id=req.request_id)

    def delete_category_budget(self, req: MiniAppRequest, group_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        if not delete_category_budget_group(user_id=req.user_id, workspace_id=ctx.workspace_id, group_id=int(group_id)):
            raise MiniAppError(404, "budget_not_found", "Бюджет не найден.")
        self._track(req, "mini_app_budget_created", workspace_id=ctx.workspace_id, properties={"source": "mini_app", "budget_kind": "category_group", "action": "delete", "result": "success"})
        return success({"deleted": True, "budget_id": int(group_id)}, request_id=req.request_id)

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
                    deadline=self._goal_deadline_from_body(body, goal) if "deadline" in body else ...,
                )
            if any(key in body for key in {"strategy", "frequency", "comfortable_amount", "reminders_enabled", "day", "days", "weekday"}):
                strategy = str(body.get("strategy") or goal.strategy)
                frequency = str(body.get("frequency") or goal.frequency or FREQUENCY_NONE)
                schedule_changed = any(key in body for key in {"frequency", "day", "days", "weekday"})
                goal = update_goal_plan(
                    goal_id=goal.id,
                    owner_user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    strategy=strategy,
                    frequency=frequency,
                    deadline=self._goal_deadline_from_body(body, goal),
                    comfortable_amount=(body.get("comfortable_amount") or None) if "comfortable_amount" in body else goal.comfortable_amount,
                    schedule_config=self._schedule_from_body(body, frequency) if schedule_changed else goal.schedule_config,
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
        try:
            goal = set_goal_status(int(goal_id), req.user_id, ctx.workspace_id, status)
        except GoalError as exc:
            raise MiniAppError(404, "goal_not_found", "Goal was not found.") from exc
        self._safe_goal_event(req, "mini_app_goal_plan_changed", workspace_id=ctx.workspace_id, action=status)
        return success({"goal": self._goal_dict(goal)}, request_id=req.request_id)

    def delete_goal(self, req: MiniAppRequest, goal_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        goal = get_goal(int(goal_id), req.user_id, ctx.workspace_id)
        if not goal:
            raise MiniAppError(404, "goal_not_found", "Goal was not found.")
        if goal.status != "archived":
            raise MiniAppError(409, "goal_not_archived", "Archive the goal before deleting it permanently.")
        try:
            movement_count = delete_goal_permanently(int(goal_id), req.user_id, ctx.workspace_id)
        except GoalError as exc:
            if exc.code == "goal_not_archived":
                raise MiniAppError(409, "goal_not_archived", "Archive the goal before deleting it permanently.") from exc
            raise MiniAppError(404, "goal_not_found", "Goal was not found.") from exc
        self._safe_goal_event(req, "mini_app_goal_plan_changed", workspace_id=ctx.workspace_id, action="delete")
        return success({"deleted": True, "goal_id": int(goal_id), "deleted_movement_count": movement_count}, request_id=req.request_id)

    def _limits(self, req: MiniAppRequest, workspace_id: int | None) -> list[dict]:
        items: list[dict] = []
        if workspace_id is None:
            rows = pg_fetchall(
                """
                SELECT period, category, amount, currency, COALESCE(display_name, category), alerts_enabled
                  FROM public.category_limits
                 WHERE user_id=%s AND workspace_id IS NULL
                 ORDER BY period, category
                """,
                (req.user_id,),
            )
        else:
            rows = pg_fetchall(
                """
                SELECT period, category, amount, currency, COALESCE(display_name, category), alerts_enabled
                  FROM public.category_limits
                 WHERE user_id=%s AND workspace_id=%s
                 ORDER BY period, category
                """,
                (req.user_id, workspace_id),
            )
        for row in rows:
            period, category, amount_raw, currency = row[:4]
            display_name = row[4] if len(row) > 4 else category
            alerts_enabled = row[5] if len(row) > 5 else True
            items.append(self._limit_dict(
                user_id=req.user_id,
                kind="category",
                identifier=f"category:{period}:{category}",
                title=str(display_name or category),
                category=str(category),
                amount=to_decimal_money(amount_raw),
                currency=currency,
                period=period,
                workspace_id=workspace_id,
                alerts_enabled=bool(alerts_enabled),
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
        currency = self._validated_currency(body.get("currency"), fallback=get_user_currency(req.user_id))
        try:
            if scope == "all_expenses":
                stored = create_or_update_general_limit_tx(cur, user_id=req.user_id, workspace_id=ctx.workspace_id, name=str(body.get("title") or "Все расходы")[:80], amount=amount, period=period, currency=currency, alerts_enabled=bool(body.get("alerts_enabled", True)))
            else:
                category = self._validate_category(req, ctx.workspace_id, "Расходы", str(body.get("category") or ""))
                stored = replace_category_limit_tx(cur, user_id=req.user_id, workspace_id=ctx.workspace_id, old_period=None, old_category=None, period=period, category=category, amount=amount, currency=currency, title=str(body.get("title") or category), alerts_enabled=bool(body.get("alerts_enabled", True)))
        except MiniAppLimitError as exc:
            status = 409 if exc.code == "limit_conflict" else 400
            raise MiniAppError(status, exc.code, "Limit could not be created.") from exc
        return {"limit": self._stored_limit_dict(req, stored)}

    def update_limit(self, req: MiniAppRequest, limit_id: str, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        decoded = unquote(limit_id)
        if "toggle" in body and decoded.startswith("general:"):
            raw_id = int(decoded.split(":", 1)[1])
            try:
                stored = set_general_limit_enabled(
                    user_id=req.user_id,
                    workspace_id=ctx.workspace_id,
                    limit_id=raw_id,
                    enabled=bool(body.get("enabled")) if "enabled" in body else None,
                    alerts_enabled=bool(body.get("alerts_enabled")) if "alerts_enabled" in body else None,
                )
            except MiniAppLimitError as exc:
                raise MiniAppError(404 if exc.code == "limit_not_found" else 400, exc.code, "Limit could not be updated.") from exc
            self._track(req, "mini_app_budget_limit_updated", workspace_id=ctx.workspace_id, properties={"action": "toggle", "source": "mini_app"})
            return success({"limit": self._stored_limit_dict(req, stored), "id": decoded}, request_id=req.request_id)
        category_old_period = decoded.split(":", 2)[1] if decoded.startswith("category:") else None
        period = str(body.get("period") or category_old_period or "month")
        if period not in LIMIT_PERIODS:
            raise MiniAppError(400, "bad_limit_period", "Only week and month limits are supported.")
        amount = to_decimal_money(body.get("amount"), positive=True)
        currency = self._validated_currency(body.get("currency"), fallback=get_user_currency(req.user_id)) if body.get("currency") else None
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
                    title=str(body.get("title") or "").strip() or None,
                    alerts_enabled=bool(body["alerts_enabled"]) if "alerts_enabled" in body else None,
                    require_existing=True,
                )
            except MiniAppLimitError as exc:
                status = 404 if exc.code == "limit_not_found" else 409 if exc.code == "limit_conflict" else 400
                raise MiniAppError(status, exc.code, "A limit already exists for this category and period." if exc.code == "limit_conflict" else "Limit could not be updated.") from exc
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

    def _limit_spent(self, user_id: int, workspace_id: int | None, period: str, category: str | None, today: date | None = None, *, currency: str) -> Decimal:
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
               AND COALESCE(currency, %s) = %s
               AND COALESCE(type,'') <> 'noop'
               AND COALESCE(category,'') <> 'Без операций'
            """,
            (*params, *category_params, start, end, get_user_currency(user_id), currency),
        )
        return to_decimal_money(rows[0][0] if rows else 0)

    def home_preferences(self, req: MiniAppRequest) -> dict:
        return success({"widgets": home_widget_registry(), **get_home_preferences(req.user_id)}, request_id=req.request_id)

    def update_home_preferences(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        try:
            prefs = save_home_preferences(req.user_id, body.get("order"), body.get("enabled"))
        except ValueError as exc:
            raise MiniAppError(400, "bad_home_preferences", "Invalid Home preferences.") from exc
        self._track(req, "mini_app_home_preferences_saved", properties={"result": "success", "total": len(prefs["enabled"]), "source": "mini_app"})
        return success({"widgets": home_widget_registry(), **prefs}, request_id=req.request_id)

    def shopping_items(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope or workspace_ids[0] is None:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство для списка покупок."}, request_id=req.request_id)
        workspace_id = int(workspace_ids[0])
        row = next((item for item in self._workspace_rows(req.user_id) if item["workspace_id"] == workspace_id), None)
        summary = shopping_summary(workspace_id, preview_limit=100)
        return success({
            "items": [item.as_dict() for item in summary.items],
            "active_count": summary.active_count,
            "completed_count": summary.completed_count,
            "read_only": not bool(row and row.get("role") in WRITE_ROLES),
        }, request_id=req.request_id)

    def create_shopping_item(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        try:
            item = create_shopping_item(int(ctx.workspace_id), req.user_id, body.get("text"))
        except ShoppingError as exc:
            raise MiniAppError(400, str(exc), "Проверьте название покупки.") from exc
        self._track(req, "mini_app_shopping_item_created", workspace_id=ctx.workspace_id, properties={"result": "success", "source": "mini_app"})
        return success({"item": item.as_dict()}, request_id=req.request_id)

    def update_shopping_item(self, req: MiniAppRequest, item_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        if "completed" in body and not isinstance(body["completed"], bool):
            raise MiniAppError(400, "bad_shopping_state", "Проверьте статус покупки.")
        try:
            item = update_shopping_item(
                int(ctx.workspace_id),
                int(item_id),
                req.user_id,
                text=body.get("text") if "text" in body else None,
                completed=body["completed"] if "completed" in body else None,
            )
        except ShoppingError as exc:
            raise MiniAppError(400, str(exc), "Проверьте изменение покупки.") from exc
        if item is None:
            raise MiniAppError(404, "shopping_item_not_found", "Покупка не найдена.")
        action = "completed" if body.get("completed") is True else "restored" if body.get("completed") is False else "edited"
        self._track(req, "mini_app_shopping_item_updated", workspace_id=ctx.workspace_id, properties={"action": action, "result": "success", "source": "mini_app"})
        return success({"item": item.as_dict()}, request_id=req.request_id)

    def delete_shopping_item(self, req: MiniAppRequest, item_id: int, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        if not delete_shopping_item(int(ctx.workspace_id), int(item_id)):
            raise MiniAppError(404, "shopping_item_not_found", "Покупка не найдена.")
        self._track(req, "mini_app_shopping_item_deleted", workspace_id=ctx.workspace_id, properties={"result": "success", "source": "mini_app"})
        return success({"deleted": True, "item_id": int(item_id)}, request_id=req.request_id)

    def clear_completed_shopping_items(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        ctx = self._write_scope(req, body.get("workspace_id"))
        count = clear_completed_shopping_items(int(ctx.workspace_id))
        self._track(req, "mini_app_shopping_completed_cleared", workspace_id=ctx.workspace_id, properties={"result": "success", "total": count, "source": "mini_app"})
        return success({"deleted": count}, request_id=req.request_id)

    def dismiss_announcement(self, req: MiniAppRequest, candidate_id: str, body: dict[str, Any] | None = None) -> dict:
        self._check_write_rate(req)
        workspace_id = None
        if body and "workspace_id" in body:
            workspace_ids, all_scope = self._read_scope(req, body.get("workspace_id"))
            workspace_id = None if all_scope else workspace_ids[0]
        announcement_today = user_local_date(req.user_id, workspace_id)
        candidate = announcement_candidate(candidate_id, announcement_today)
        if not dismiss_announcement(req.user_id, candidate_id, announcement_today):
            raise MiniAppError(404, "announcement_not_found", "Объявление не найдено.")
        properties = {"result": "success", "source": "mini_app"}
        if candidate is not None:
            properties.update({"update_key": candidate.id, "update_kind": candidate.kind})
        self._track(req, "mini_app_announcement_dismissed", properties=properties)
        return success({"dismissed": True, "candidate_id": candidate_id}, request_id=req.request_id)

    def profile(self, req: MiniAppRequest) -> dict:
        timezone_name, _reason = user_timezone_name(req.user_id)
        try:
            notifications = notification_read_model(req.user_id)
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
        try:
            home_preferences = get_home_preferences(req.user_id)
        except Exception as exc:
            log.info("miniapp_home_preferences_unavailable reason=%s", type(exc).__name__)
            home_preferences = reconcile_home_preferences(None, None)
        try:
            vacation_mode = get_vacation_mode(req.user_id)
        except Exception as exc:
            log.info("miniapp_vacation_mode_unavailable reason=%s", type(exc).__name__)
            vacation_mode = {"enabled": False, "active": False, "status": "disabled", "start_date": None, "end_date": None}
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
            "vacation_mode": vacation_mode,
            "home_preferences": {"widgets": home_widget_registry(), **home_preferences},
            "premium": self._premium_info(),
            "export": self._export_info(req),
            "help_url": os.getenv("MINIAPP_HELP_URL", "https://t.me/chiracredible"),
            "links": {
                "privacy": os.getenv("MINIAPP_PRIVACY_URL") or None,
                "terms": os.getenv("MINIAPP_TERMS_URL") or None,
            },
            "version": self.version,
        }, request_id=req.request_id)

    def profile_behaviour(self, req: MiniAppRequest) -> dict:
        return success({"vacation_mode": get_vacation_mode(req.user_id)}, request_id=req.request_id)

    def set_profile_vacation(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        if "enabled" not in body or not isinstance(body.get("enabled"), bool):
            raise MiniAppError(400, "bad_vacation_enabled", "Проверьте состояние режима отпуска.")
        try:
            vacation = set_vacation_mode(
                req.user_id,
                enabled=body["enabled"],
                start_date=body.get("start_date"),
                end_date=body.get("end_date"),
            )
        except ValueError as exc:
            code = str(exc)
            message = {
                "vacation_dates_required": "Укажите дату начала и окончания.",
                "invalid_vacation_range": "Дата окончания не может быть раньше даты начала.",
                "invalid_vacation_date": "Проверьте даты режима отпуска.",
            }.get(code, "Проверьте настройки режима отпуска.")
            raise MiniAppError(400, code, message) from exc
        self._track(req, "vacation_mode_changed", properties={"enabled": vacation["enabled"], "vacation_state": vacation["status"]})
        return success({"vacation_mode": vacation}, request_id=req.request_id)

    @staticmethod
    def _history_summary(counts: dict[str, int]) -> dict[str, int]:
        return {
            "operations": int(counts.get("operations", 0)),
            "drafts": int(counts.get("operation_drafts", 0)),
            "goals": int(counts.get("financial_goals", 0)),
            "related_records": sum(int(counts.get(key, 0)) for key in ("financial_activity_events", "notification_events", "ml_observations", "operation_versions", "operations_history")),
        }

    @staticmethod
    def _account_summary(counts: dict[str, int]) -> dict[str, int]:
        financial_keys = {"operations", "category_limits", "general_spending_limits", "financial_goals", "goal_drafts", "budgets", "user_reminders"}
        preference_keys = {"notification_preferences", "user_home_preferences", "user_category_preferences", "user_aliases", "user_announcement_state"}
        return {
            "financial_records": sum(int(counts.get(key, 0)) for key in financial_keys),
            "preferences": sum(int(counts.get(key, 0)) for key in preference_keys),
            "personal_workspaces": int(counts.get("workspaces", 0)),
        }

    def profile_privacy(self, req: MiniAppRequest) -> dict:
        return success({
            "history_periods": ["today", "last7", "this_month", "prev_month", "this_year", "all"],
            "export": self._export_info(req),
            "shared_workspace_note": "Личные данные и личное пространство будут удалены. Данные других участников общих пространств сохранятся, а необходимая общая атрибуция будет обезличена.",
        }, request_id=req.request_id)

    def preview_profile_history_deletion(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        period = str(body.get("period") or "")
        try:
            start_date, end_date = history_period_bounds(period, user_local_date(req.user_id))
        except ValueError as exc:
            raise MiniAppError(400, "unsupported_history_period", "Выберите доступный период.") from exc
        preview = preview_delete_financial_history(req.user_id, start_date, end_date)
        return success({"period": period, "start_date": start_date, "end_date": end_date, "summary": self._history_summary(preview.counts)}, request_id=req.request_id)

    def delete_profile_history(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        if body.get("confirmed") is not True:
            raise MiniAppError(400, "history_confirmation_required", "Подтвердите удаление финансовой истории.")
        period = str(body.get("period") or "")
        try:
            start_date, end_date = history_period_bounds(period, user_local_date(req.user_id))
        except ValueError as exc:
            raise MiniAppError(400, "unsupported_history_period", "Выберите доступный период.") from exc
        result = delete_financial_history(req.user_id, start_date, end_date)
        return success({"deleted": result.deleted, "period": period, "summary": self._history_summary(result.counts)}, request_id=req.request_id)

    def preview_profile_account_deletion(self, req: MiniAppRequest) -> dict:
        self._check_write_rate(req)
        preview = dry_run_delete_user_data(req.user_id)
        return success({"summary": self._account_summary(preview.counts), "confirmation_text": "УДАЛИТЬ", "shared_workspace_note": "Личные данные и личное пространство будут удалены. Данные других участников общих пространств сохранятся; необходимая общая атрибуция будет обезличена."}, request_id=req.request_id)

    def delete_profile_account(self, req: MiniAppRequest, body: dict[str, Any]) -> dict:
        self._check_write_rate(req)
        if body.get("confirmed") is not True or body.get("confirmation_text") != "УДАЛИТЬ":
            raise MiniAppError(400, "account_confirmation_required", "Введите УДАЛИТЬ и подтвердите удаление.")
        apply_account_deletion(req.user_id, strict=True)
        result = delete_user_data(req.user_id)
        return success({"deleted": result.deleted, "terminal": True, "message": "Данные удалены. Вы можете закрыть КопиPaste."}, request_id=req.request_id)

    def profile_categories(self, req: MiniAppRequest, params: dict[str, Any]) -> dict:
        workspace_ids, all_scope = self._read_scope(req, params.get("workspace_id"))
        if all_scope:
            return success({"items": [], "read_only": True, "note": "Выберите одно пространство для категорий."}, request_id=req.request_id)
        op_type = OP_TYPES.get(str(params.get("type") or "expense")) or "Расходы"
        return success({"items": self._managed_categories(req, workspace_ids[0], op_type), "read_only": False}, request_id=req.request_id)

    def notification_preferences(self, req: MiniAppRequest) -> dict:
        return success(notification_read_model(req.user_id), request_id=req.request_id)

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
                if key in {"daily", "plans", "reports"}:
                    current = notification_read_model(req.user_id)
                    read_key = "daily_notifications" if key == "daily" else "plans_control" if key == "plans" else "reports"
                    value = not bool((current.get(read_key) or {}).get("enabled"))
                    set_grouped_notification_preference(req.user_id, key, value)
                    self._track(req, "mini_app_notification_setting_changed", properties={"action": key, "result": "enabled" if value else "disabled", "source": "mini_app"})
                elif key == "challenges":
                    self._track(req, "mini_app_notification_setting_changed", properties={"action": "challenges", "result": "retired", "source": "mini_app"})
                elif key not in NOTIFICATION_KEYS:
                    raise MiniAppError(400, "bad_notification_key", "Unknown notification setting.")
                else:
                    value = toggle_notification_preference(req.user_id, key)
                    self._track(req, "mini_app_notification_setting_changed", properties={"action": key, "result": "enabled" if value else "disabled", "source": "mini_app"})
            elif action == "quiet_toggle":
                value = toggle_quiet_hours(req.user_id)
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "quiet_hours", "result": "enabled" if value else "disabled", "source": "mini_app"})
            elif action == "quiet_time":
                set_quiet_hours_time(req.user_id, key, str(body.get("value") or ""))
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "quiet_hours_time", "result": "success", "source": "mini_app"})
            elif action == "daily_time":
                if key == "morning":
                    raise MiniAppError(400, "retired_notification_time", "Morning notifications are disabled.")
                set_daily_notification_time(req.user_id, key, str(body.get("value") or ""))
                self._track(req, "mini_app_notification_setting_changed", properties={"action": "daily_time", "result": "success", "source": "mini_app"})
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
        return success(notification_read_model(req.user_id), request_id=req.request_id)

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
            "presets": ["today", "7", "14", "month", "previous_month", "year", "previous_year", "custom"],
            "status": "ready",
            "privacy_note": "XLSX формируется по выбранному периоду и отправляется в чат с КопиPaste.",
        }

    def export_entry(self, req: MiniAppRequest, body: dict[str, Any] | None = None) -> dict:
        body = body or {}
        action = str(body.get("action") or "open")
        if action == "open":
            self._track(req, "mini_app_export_opened", properties={"source": "mini_app", "action": "open"})
            return success(self._export_info(req), request_id=req.request_id)
        dfrom, dto, preset = self._export_period(req, body)
        rows = self._export_rows(req, body, dfrom, dto)
        preview = self._export_preview_payload(rows, dfrom, dto, preset)
        if action == "preview":
            return success(preview, request_id=req.request_id)
        if action != "send":
            raise MiniAppError(400, "bad_export_action", "Unknown export action.")
        if not TELEGRAM_TOKEN:
            raise MiniAppError(503, "export_send_unavailable", "Telegram delivery is unavailable.")
        filename = f"kopipaste_export_{dfrom.isoformat()}_{dto.isoformat()}.xlsx"
        fd, path = tempfile.mkstemp(prefix="kopipaste_miniapp_export_", suffix=".xlsx")
        os.close(fd)
        try:
            build_export_xlsx(path, rows, dfrom, dto, get_user_locale(req.user_id), fallback_currency=get_user_currency(req.user_id))
            async def _send() -> None:
                bot = Bot(TELEGRAM_TOKEN)
                with open(path, "rb") as fh:
                    await bot.send_document(chat_id=req.user_id, document=fh, filename=filename, caption=f"📤 Экспорт готов\nПериод: {dfrom:%d.%m.%Y}–{dto:%d.%m.%Y}\nОпераций: {len(rows)}")
            asyncio.run(_send())
        except Exception as exc:
            log.warning("miniapp_export_send_failed user=%s reason=%s", req.user_id, type(exc).__name__)
            raise MiniAppError(502, "export_send_failed", "Export could not be sent.") from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        self._track(req, "mini_app_export_sent", properties={"source": "mini_app", "preset": preset, "result": "success"})
        return success({**preview, "result": "sent", "filename": filename}, request_id=req.request_id)

    def _export_period(self, req: MiniAppRequest, body: dict[str, Any]) -> tuple[date, date, str]:
        today = user_local_date(req.user_id)
        preset = str(body.get("preset") or "month")
        if preset == "today":
            return today, today, preset
        if preset == "7":
            return today - timedelta(days=6), today, preset
        if preset == "14":
            return today - timedelta(days=13), today, preset
        if preset == "month":
            return today.replace(day=1), today, preset
        if preset == "previous_month":
            first = today.replace(day=1)
            end = first - timedelta(days=1)
            return end.replace(day=1), end, preset
        if preset == "year":
            return today.replace(month=1, day=1), today, preset
        if preset == "previous_year":
            year = today.year - 1
            return date(year, 1, 1), date(year, 12, 31), preset
        if preset == "custom":
            try:
                start = date.fromisoformat(str(body.get("start_date") or ""))
                end = date.fromisoformat(str(body.get("end_date") or ""))
            except Exception as exc:
                raise MiniAppError(400, "bad_export_period", "Invalid export period.") from exc
            if start > end or (end - start).days + 1 > MAX_PERIOD_DAYS:
                raise MiniAppError(400, "bad_export_period", "Invalid export period.")
            return start, end, preset
        raise MiniAppError(400, "bad_export_preset", "Invalid export preset.")

    def _export_rows(self, req: MiniAppRequest, body: dict[str, Any], start: date, end: date) -> list[dict]:
        tx = self._transaction_filters(req, {
            "workspace_id": body.get("workspace_id", body.get("workspace_scope", None)),
            "period": "custom",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "operation_type": body.get("operation_type") or "all",
            "category": body.get("category") or "all",
        }, alias="o")
        rows = pg_fetchall(
            f"""
            SELECT o.id, o.op_date, o.type, o.category, o.amount, COALESCE(o.comment,''), COALESCE(to_jsonb(o)->>'source', 'miniapp'), COALESCE(o.currency, %s)
              FROM public.operations o
             WHERE {tx.where_sql}
             ORDER BY o.op_date, o.id
            """,
            (get_user_currency(req.user_id), *tx.params),
        )
        return [{"id": r[0], "op_date": r[1], "type": r[2], "category": r[3], "amount": to_decimal_money(r[4]), "comment": r[5], "source": r[6], "currency": r[7]} for r in rows]

    def _export_preview_payload(self, rows: list[dict], start: date, end: date, preset: str) -> dict:
        totals: dict[str, dict[str, Decimal | int]] = defaultdict(lambda: {"income": Decimal("0.00"), "expense": Decimal("0.00"), "count": 0})
        # Legacy export rows do not store currency per row in the builder query; preserve count and avoid cross-currency summing.
        for row in rows:
            currency = str(row.get("currency") or "RUB")
            if row["type"] == "Доходы":
                totals[currency]["income"] = to_decimal_money(totals[currency]["income"]) + to_decimal_money(row["amount"])
            elif row["type"] == "Расходы":
                totals[currency]["expense"] = to_decimal_money(totals[currency]["expense"]) + to_decimal_money(row["amount"])
            totals[currency]["count"] = int(totals[currency]["count"]) + 1
        return {"preset": preset, "period": {"start_date": start, "end_date": end}, "count": len(rows), "totals_by_currency": dict(totals)}

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
            "mini_app_activity_opened",
            "mini_app_analytics_details_toggled",
            "mini_app_analytics_grouping_changed",
            "mini_app_budget_opened",
            "mini_app_plans_reminders_opened",
            "mini_app_reminder_opened",
            "mini_app_reminder_created",
            "mini_app_reminder_recorded",
            "mini_app_reminder_snoozed",
            "mini_app_reminder_updated",
            "mini_app_home_reminder_opened",
            "mini_app_transaction_add_opened",
            "mini_app_analytics_chart_filter_changed",
            "mini_app_premium_opened",
            "mini_app_export_opened",
            "mini_app_home_challenge_opened",
            "mini_app_home_focus_opened",
            "mini_app_home_insight_opened",
            "insight_opened",
            "insight_action_clicked",
            "mini_app_profile_section_opened",
            "mini_app_profile_setting_changed",
            "mini_app_challenge_carousel_changed",
            "mini_app_focus_carousel_changed",
            "mini_app_reminder_carousel_changed",
            "mini_app_home_preferences_saved",
            "mini_app_shopping_opened",
            "mini_app_shopping_item_created",
            "mini_app_shopping_item_updated",
            "mini_app_shopping_item_deleted",
            "mini_app_shopping_completed_cleared",
            "mini_app_announcement_opened",
            "mini_app_announcement_dismissed",
            "mini_app_announcement_carousel_changed",
            "mini_app_home_customization_opened",
            "mini_app_announcement_impression",
            "smart_planning_opened",
            "smart_planning_applied",
            "smart_planning_warning_seen",
            "report_drilldown_opened",
            "report_export_requested",
        }
        if event not in allowed:
            raise MiniAppError(400, "bad_event", "Invalid analytics event.")
        props = {
            k: v
            for k, v in (body.get("properties") or {}).items()
            if k in {"tab", "period", "scope", "action", "action_type", "chart_type", "filter_kind", "period_kind", "operation_type", "has_category_filter", "grouping", "result", "source", "surface", "kind", "report_kind", "currency", "detector_type", "setting", "section", "reminder_state", "budget_kind", "direction", "position", "total", "widget_key", "update_key", "update_kind", "workspace_type", "planning_kind", "history_confidence", "warning_kind"}
        }
        if event in {"report_drilldown_opened", "report_export_requested"}:
            raw = body.get("properties") or {}
            props = {"source": "mini_app"}
            if raw.get("report_kind") in {"selected", "completed_week", "completed_month"}:
                props["report_kind"] = raw["report_kind"]
            if raw.get("currency") in ALLOWED_CURRENCIES:
                props["currency"] = raw["currency"]
            if event == "report_drilldown_opened" and raw.get("kind") in {"category", "merchant", "observation"}:
                props["kind"] = raw["kind"]
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
