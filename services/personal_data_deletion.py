from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from psycopg2 import errors

from db.database import get_conn
from services.analytics_privacy import apply_history_deletion


PERSONAL_TABLES = {
    "operations": "user_id=%s OR chat_id=%s OR workspace_id = ANY(%s)",
    "user_reminders": "user_id=%s",
    "category_limits": "user_id=%s",
    "general_spending_limits": "owner_user_id=%s OR workspace_id = ANY(%s)",
    "limit_alert_deliveries": "user_id=%s OR workspace_id = ANY(%s)",
    "subscription_patterns": "user_id=%s OR workspace_id = ANY(%s)",
    "recurring_spend_patterns": "user_id=%s OR workspace_id = ANY(%s)",
    "operation_drafts": "actor_user_id=%s OR workspace_id = ANY(%s)",
    "financial_goals": "owner_user_id=%s OR workspace_id = ANY(%s)",
    "goal_drafts": "owner_user_id=%s OR workspace_id = ANY(%s)",
    "financial_activity_events": "user_id=%s OR workspace_id = ANY(%s)",
    "notification_events": "user_id=%s OR workspace_id = ANY(%s)",
    "automatic_notifications": "user_id=%s OR workspace_id = ANY(%s)",
    "notification_preferences": "user_id=%s",
    "custom_categories": "user_id=%s OR workspace_id = ANY(%s)",
    "user_aliases": "user_id=%s",
    "ml_observations": "user_id=%s",
    "action_tokens": "user_id=%s",
    "user_workspace_settings": "user_id=%s",
    "workspace_members": "user_id=%s",
    "budgets": "user_id=%s",
    "reminders_log": "user_id=%s",
}


@dataclass(frozen=True)
class DeletionResult:
    user_id: int
    counts: dict[str, int]
    anonymized_shared_operations: int = 0
    deleted: bool = False


@dataclass(frozen=True)
class HistoryDeletionPreview:
    user_id: int
    start_date: date | None
    end_date: date | None
    operation_count: int
    counts: dict[str, int]


@dataclass(frozen=True)
class HistoryDeletionResult:
    user_id: int
    start_date: date | None
    end_date: date | None
    operation_count: int
    counts: dict[str, int]
    deleted: bool = False


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def _table_columns(cur, name: str) -> set[str]:
    if not _table_exists(cur, name):
        return set()
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name=%s
        """,
        (name,),
    )
    return {r[0] for r in cur.fetchall()}


def _where_supported(columns: set[str], where: str) -> bool:
    required = {
        "user_id": "user_id=%s" in where,
        "chat_id": "chat_id=%s" in where,
        "workspace_id": "workspace_id = ANY(%s)" in where,
        "owner_user_id": "owner_user_id=%s" in where,
        "actor_user_id": "actor_user_id=%s" in where,
    }
    return all((not needed) or (column in columns) for column, needed in required.items())


def _personal_workspace_ids(cur, user_id: int) -> list[int]:
    if not _table_exists(cur, "workspaces"):
        return []
    cur.execute("SELECT id FROM public.workspaces WHERE kind='personal' AND owner_user_id=%s", (user_id,))
    return [int(r[0]) for r in cur.fetchall()]


def history_period_bounds(period: str, today: date | None = None) -> tuple[date | None, date | None]:
    today = today or date.today()
    if period == "today":
        return today, today
    if period == "last7":
        return today - timedelta(days=6), today
    if period == "this_month":
        return today.replace(day=1), today
    if period == "prev_month":
        first = today.replace(day=1)
        prev_end = first - timedelta(days=1)
        return prev_end.replace(day=1), prev_end
    if period == "this_year":
        return today.replace(month=1, day=1), today
    if period == "all":
        return None, None
    raise ValueError("unknown_period")


def _personal_operation_where(columns: set[str], workspace_ids: list[int]) -> str:
    clauses = []
    if "workspace_id" in columns and workspace_ids:
        clauses.append("workspace_id = ANY(%s)")
    legacy_clauses = []
    if "user_id" in columns:
        legacy_clauses.append("user_id=%s")
    if "chat_id" in columns:
        legacy_clauses.append("chat_id=%s")
    if "workspace_id" in columns:
        if legacy_clauses:
            clauses.append("(workspace_id IS NULL AND (" + " OR ".join(legacy_clauses) + "))")
    else:
        clauses.extend(legacy_clauses)
    return "(" + " OR ".join(clauses or ["FALSE"]) + ")"


def _operation_ids_for_period(cur, user_id: int, workspace_ids: list[int], start_date: date | None, end_date: date | None) -> list[int]:
    columns = _table_columns(cur, "operations")
    if not columns or "id" not in columns or "op_date" not in columns:
        return []
    where = _personal_operation_where(columns, workspace_ids)
    params = []
    if "workspace_id = ANY(%s)" in where:
        params.append(workspace_ids)
    if "user_id=%s" in where:
        params.append(user_id)
    if "chat_id=%s" in where:
        params.append(user_id)
    if start_date is not None:
        where += " AND op_date >= %s"
        params.append(start_date)
    if end_date is not None:
        where += " AND op_date <= %s"
        params.append(end_date)
    cur.execute(f"SELECT id FROM public.operations WHERE {where}", tuple(params))
    return [int(r[0]) for r in cur.fetchall()]


def preview_delete_financial_history(user_id: int, start_date: date | None, end_date: date | None) -> HistoryDeletionPreview:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            workspace_ids = _personal_workspace_ids(cur, user_id)
            operation_ids = _operation_ids_for_period(cur, user_id, workspace_ids, start_date, end_date)
            counts = {"operations": len(operation_ids)}
        conn.rollback()
        return HistoryDeletionPreview(user_id, start_date, end_date, len(operation_ids), counts)
    finally:
        conn.close()


def delete_financial_history(user_id: int, start_date: date | None, end_date: date | None) -> HistoryDeletionResult:
    conn = get_conn()
    counts: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            workspace_ids = _personal_workspace_ids(cur, user_id)
            operation_ids = _operation_ids_for_period(cur, user_id, workspace_ids, start_date, end_date)
            counts["operations"] = len(operation_ids)
            if not operation_ids:
                conn.rollback()
                return HistoryDeletionResult(user_id, start_date, end_date, 0, counts, deleted=True)
            try:
                counts["analytics_product_event_links"] = apply_history_deletion(operation_ids)
            except Exception:
                counts["analytics_product_event_links"] = 0

            related_tables = [
                ("financial_activity_events", "operation_id"),
                ("notification_events", "operation_id"),
                ("ml_observations", "operation_id"),
                ("operation_versions", "operation_id"),
                ("operations_history", "operation_id"),
            ]
            for table, column in related_tables:
                columns = _table_columns(cur, table)
                if column not in columns:
                    counts[table] = 0
                    continue
                cur.execute(f"DELETE FROM public.{table} WHERE {column}=ANY(%s)", (operation_ids,))
                counts[table] = int(cur.rowcount)

            draft_columns = _table_columns(cur, "operation_drafts")
            if {"actor_user_id", "payload"} <= draft_columns:
                scope_sql = ""
                params = [user_id]
                if "workspace_id" in draft_columns:
                    if workspace_ids:
                        scope_sql = "AND (workspace_id = ANY(%s) OR workspace_id IS NULL)"
                        params.append(workspace_ids)
                    else:
                        scope_sql = "AND workspace_id IS NULL"
                params.extend([start_date, start_date, end_date, end_date])
                cur.execute(
                    f"""
                    DELETE FROM public.operation_drafts
                     WHERE actor_user_id=%s
                       {scope_sql}
                       AND COALESCE(payload->>'op_date', payload->>'operation_date') IS NOT NULL
                       AND (%s::date IS NULL OR (COALESCE(payload->>'op_date', payload->>'operation_date'))::date >= %s)
                       AND (%s::date IS NULL OR (COALESCE(payload->>'op_date', payload->>'operation_date'))::date <= %s)
                    """,
                    tuple(params),
                )
                counts["operation_drafts"] = int(cur.rowcount)
            else:
                counts["operation_drafts"] = 0

            if start_date is None and end_date is None:
                goal_columns = _table_columns(cur, "financial_goals")
                if {"owner_user_id", "workspace_id"} <= goal_columns:
                    params = [user_id]
                    scope = "owner_user_id=%s"
                    if workspace_ids:
                        scope += " OR workspace_id = ANY(%s)"
                        params.append(workspace_ids)
                    cur.execute(f"DELETE FROM public.financial_goals WHERE {scope}", tuple(params))
                    counts["financial_goals"] = int(cur.rowcount)
                else:
                    counts["financial_goals"] = 0

            cur.execute("DELETE FROM public.operations WHERE id=ANY(%s)", (operation_ids,))
            counts["operations"] = int(cur.rowcount)
        conn.commit()
        return HistoryDeletionResult(user_id, start_date, end_date, counts["operations"], counts, deleted=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _count_for(cur, table: str, where: str, user_id: int, workspace_ids: list[int]) -> int:
    columns = _table_columns(cur, table)
    if not columns or not _where_supported(columns, where):
        return 0
    params = _params_for(where, user_id, workspace_ids)
    cur.execute(f"SELECT COUNT(*) FROM public.{table} WHERE {where}", params)
    return int(cur.fetchone()[0])


def _params_for(where: str, user_id: int, workspace_ids: list[int]) -> tuple:
    params = []
    for marker in where.split("%s")[:-1]:
        if "ANY(" in marker[-16:]:
            params.append(workspace_ids)
        else:
            params.append(user_id)
    return tuple(params)


def dry_run_delete_user_data(user_id: int) -> DeletionResult:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            workspace_ids = _personal_workspace_ids(cur, user_id)
            counts = {
                table: _count_for(cur, table, where, user_id, workspace_ids)
                for table, where in PERSONAL_TABLES.items()
            }
            if _table_exists(cur, "workspaces"):
                cur.execute("SELECT COUNT(*) FROM public.workspaces WHERE kind='personal' AND owner_user_id=%s", (user_id,))
                counts["workspaces"] = int(cur.fetchone()[0])
        conn.rollback()
        return DeletionResult(user_id=user_id, counts=counts)
    finally:
        conn.close()


def delete_user_data(user_id: int) -> DeletionResult:
    conn = get_conn()
    counts: dict[str, int] = {}
    anonymized = 0
    try:
        with conn.cursor() as cur:
            workspace_ids = _personal_workspace_ids(cur, user_id)
            if _table_exists(cur, "operations"):
                op_columns = _table_columns(cur, "operations")
                if {"actor_user_id", "workspace_id", "raw_text", "comment"} <= op_columns and _table_exists(cur, "workspace_members"):
                    cur.execute(
                        """
                        UPDATE public.operations o
                           SET actor_user_id=NULL,
                               raw_text=NULL,
                               comment=''
                          FROM public.workspace_members m
                         WHERE o.actor_user_id=%s
                           AND o.workspace_id=m.workspace_id
                           AND m.user_id<>%s
                           AND m.status='active'
                        """,
                        (user_id, user_id),
                    )
                    anonymized = cur.rowcount

            for table, where in PERSONAL_TABLES.items():
                columns = _table_columns(cur, table)
                if not columns or not _where_supported(columns, where):
                    counts[table] = 0
                    continue
                params = _params_for(where, user_id, workspace_ids)
                cur.execute(f"DELETE FROM public.{table} WHERE {where}", params)
                counts[table] = int(cur.rowcount)

            if _table_exists(cur, "workspaces"):
                cur.execute("DELETE FROM public.workspaces WHERE kind='personal' AND owner_user_id=%s", (user_id,))
                counts["workspaces"] = int(cur.rowcount)
            if _table_exists(cur, "users"):
                cur.execute("DELETE FROM public.users WHERE user_id=%s", (user_id,))
                counts["users"] = int(cur.rowcount)
        conn.commit()
        return DeletionResult(user_id=user_id, counts=counts, anonymized_shared_operations=anonymized, deleted=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def format_dry_run(result: DeletionResult) -> str:
    lines = [f"delete_data_dry_run user_id={result.user_id}", "row_counts:"]
    for table, count in sorted(result.counts.items()):
        lines.append(f"- {table}: {count}")
    return "\n".join(lines)
