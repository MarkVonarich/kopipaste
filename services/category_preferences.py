from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from psycopg2 import errors

from db.database import get_conn
PRIORITIES = {"normal", "high"}
OPERATION_TYPES = {"Расходы", "Доходы"}


@dataclass(frozen=True)
class CategoryPreference:
    category_key: str
    priority: str = "normal"
    relevant: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"category_key": self.category_key, "priority": self.priority, "relevant": self.relevant}


def _category_key(value: str) -> str:
    from services.categories import normalized_category_key

    return normalized_category_key(value)


def _validate_operation_type(operation_type: str) -> str:
    value = str(operation_type or "")
    if value not in OPERATION_TYPES:
        raise ValueError("invalid_operation_type")
    return value


def get_category_preferences(
    user_id: int,
    workspace_id: int | None,
    operation_type: str,
    category_keys: Iterable[str] | None = None,
) -> dict[str, CategoryPreference]:
    op_type = _validate_operation_type(operation_type)
    keys = [_category_key(value) for value in category_keys or () if str(value or "").strip()]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT category_key, priority, relevant
                  FROM public.user_category_preferences
                 WHERE user_id=%s
                   AND workspace_id IS NOT DISTINCT FROM %s
                   AND operation_type=%s
            """
            params: list[Any] = [int(user_id), workspace_id, op_type]
            if keys:
                sql += " AND category_key = ANY(%s)"
                params.append(keys)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.rollback()
    except errors.UndefinedTable:
        conn.rollback()
        return {}
    finally:
        conn.close()
    return {
        str(row[0]): CategoryPreference(str(row[0]), str(row[1] or "normal"), bool(row[2]))
        for row in rows
    }


def set_category_preference(
    user_id: int,
    workspace_id: int | None,
    operation_type: str,
    category: str,
    *,
    priority: str,
    relevant: bool,
) -> CategoryPreference:
    op_type = _validate_operation_type(operation_type)
    priority_value = str(priority or "")
    if priority_value not in PRIORITIES:
        raise ValueError("invalid_priority")
    if not isinstance(relevant, bool):
        raise ValueError("invalid_relevance")
    key = _category_key(category)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_category_preferences
                    (user_id, workspace_id, category_key, operation_type, priority, relevant)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, workspace_scope_key, operation_type, category_key)
                DO UPDATE SET priority=EXCLUDED.priority,
                              relevant=EXCLUDED.relevant,
                              updated_at=now()
                RETURNING category_key, priority, relevant
                """,
                (int(user_id), workspace_id, key, op_type, priority_value, relevant),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return CategoryPreference(str(row[0]), str(row[1]), bool(row[2]))


def apply_category_preferences(
    items: list[dict[str, Any]],
    preferences: dict[str, CategoryPreference],
    *,
    include_irrelevant: bool,
    preserve_key: str | None = None,
) -> list[dict[str, Any]]:
    preserve = _category_key(preserve_key) if preserve_key else None
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        key = _category_key(str(item.get("normalized_name") or item.get("name") or ""))
        pref = preferences.get(key) or CategoryPreference(key)
        if not include_irrelevant and not pref.relevant and key != preserve:
            continue
        result.append({**item, "priority": pref.priority, "relevant": pref.relevant, "_preference_order": index})
    result.sort(key=lambda item: (0 if item["priority"] == "high" else 1, int(item["_preference_order"])))
    for item in result:
        item.pop("_preference_order", None)
    return result


def apply_suggestion_preferences(
    suggestions: list[dict[str, Any]],
    preferences: dict[str, CategoryPreference],
    *,
    preserve_source_order: bool,
) -> list[dict[str, Any]]:
    visible = [
        item for item in suggestions
        if (preferences.get(_category_key(str(item.get("cat") or ""))) or CategoryPreference("")).relevant
    ]
    if preserve_source_order:
        return visible
    ordered = sorted(
        enumerate(visible),
        key=lambda pair: (
            0 if (preferences.get(_category_key(str(pair[1].get("cat") or ""))) or CategoryPreference("")).priority == "high" else 1,
            pair[0],
        ),
    )
    return [item for _index, item in ordered]


def migrate_category_preferences_cur(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    operation_type: str,
    source_key: str,
    destination_key: str | None,
    shared_workspace: bool,
) -> int:
    if source_key == destination_key:
        return 0
    user_scope = "" if shared_workspace and workspace_id is not None else " AND p.user_id=%s"
    scope_params: list[Any] = [workspace_id, operation_type, source_key]
    if user_scope:
        scope_params.append(int(user_id))
    if destination_key:
        cur.execute(
            f"""
            DELETE FROM public.user_category_preferences p
             WHERE p.workspace_id IS NOT DISTINCT FROM %s
               AND p.operation_type=%s
               AND p.category_key=%s
               {user_scope}
               AND EXISTS (
                   SELECT 1 FROM public.user_category_preferences d
                    WHERE d.user_id=p.user_id
                      AND d.workspace_id IS NOT DISTINCT FROM p.workspace_id
                      AND d.operation_type=p.operation_type
                      AND d.category_key=%s
               )
            """,
            (*scope_params, destination_key),
        )
        changed = int(cur.rowcount or 0)
        cur.execute(
            f"""
            UPDATE public.user_category_preferences p
               SET category_key=%s, updated_at=now()
             WHERE p.workspace_id IS NOT DISTINCT FROM %s
               AND p.operation_type=%s
               AND p.category_key=%s
               {user_scope}
            """,
            (destination_key, *scope_params),
        )
        return changed + int(cur.rowcount or 0)
    cur.execute(
        f"""
        DELETE FROM public.user_category_preferences p
         WHERE p.workspace_id IS NOT DISTINCT FROM %s
           AND p.operation_type=%s
           AND p.category_key=%s
           {user_scope}
        """,
        tuple(scope_params),
    )
    return int(cur.rowcount or 0)
