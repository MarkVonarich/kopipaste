from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import errors

from db.database import get_conn


PERSONAL_TABLES = {
    "operations": "user_id=%s OR chat_id=%s OR workspace_id = ANY(%s)",
    "user_reminders": "user_id=%s",
    "category_limits": "user_id=%s",
    "general_spending_limits": "owner_user_id=%s OR workspace_id = ANY(%s)",
    "limit_alert_deliveries": "user_id=%s OR workspace_id = ANY(%s)",
    "subscription_patterns": "user_id=%s OR workspace_id = ANY(%s)",
    "recurring_spend_patterns": "user_id=%s OR workspace_id = ANY(%s)",
    "operation_drafts": "actor_user_id=%s OR workspace_id = ANY(%s)",
    "financial_activity_events": "user_id=%s OR workspace_id = ANY(%s)",
    "notification_events": "user_id=%s OR workspace_id = ANY(%s)",
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
