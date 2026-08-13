from __future__ import annotations

import re
from dataclasses import dataclass, field

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from services.product_events import ProductEvent, track_product_event
from utils.text import norm_text


@dataclass(frozen=True)
class CategoryResult:
    name: str
    normalized_name: str
    created: bool
    category_id: int | None = None


PROTECTED_CATEGORY_NAMES = {"", "Без операций"}
EXPENSE_CATEGORY_TYPE = "Расходы"


@dataclass(frozen=True)
class ManagedCategory:
    name: str
    normalized_name: str
    op_type: str
    category_id: int | None = None
    source: str = "operation"
    operation_count: int = 0
    has_budget: bool = False


@dataclass(frozen=True)
class CategoryReferenceCounts:
    operations: int = 0
    drafts: int = 0
    category_limits: int = 0
    category_limit_states: int = 0
    category_budget_groups: int = 0
    reminders: int = 0
    aliases: int = 0
    ml_observations: int = 0
    subscription_patterns: int = 0
    recurring_spend_patterns: int = 0

    @property
    def total(self) -> int:
        return sum(int(v) for v in self.__dict__.values())

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class CategoryTransferResult:
    source: str
    destination: str
    op_type: str
    counts: CategoryReferenceCounts
    archived_category_id: int | None = None
    deleted_budget_count: int = 0
    transferred_budget_count: int = 0
    skipped_destination_budget_count: int = 0
    changed: bool = False


@dataclass(frozen=True)
class CategoryRenameResult:
    source: str
    destination: str
    op_type: str
    counts: CategoryReferenceCounts
    category_id: int | None = None
    changed: bool = False


@dataclass(frozen=True)
class CategoryDeleteResult:
    source: str
    op_type: str
    counts: CategoryReferenceCounts
    archived_category_id: int | None = None
    deleted_operation_count: int = 0
    changed: bool = False


def normalize_category_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    if not cleaned:
        raise ValueError("category name is empty")
    return cleaned[:64]


def normalized_category_key(value: str) -> str:
    return norm_text(normalize_category_name(value)).casefold()


def category_key_sql(value_sql: str) -> str:
    """Return the PostgreSQL equivalent of normalized_category_key()."""
    return (
        "replace(lower(regexp_replace(btrim(COALESCE("
        f"{value_sql}, '')), '[[:space:]]+', ' ', 'g')), 'ё', 'е')"
    )


def is_protected_category(name: str) -> bool:
    if not (name or "").strip():
        return True
    return normalize_category_name(name) in PROTECTED_CATEGORY_NAMES


def _is_expense_category_type(op_type: str) -> bool:
    return op_type == EXPENSE_CATEGORY_TYPE


def _scope_clause(workspace_id: int | None, *, alias: str = "") -> tuple[str, tuple]:
    prefix = f"{alias}." if alias else ""
    if workspace_id is None:
        return f"{prefix}workspace_id IS NULL", ()
    return f"{prefix}workspace_id=%s", (workspace_id,)


def _is_shared_scope(workspace_id: int | None, shared_workspace: bool | None) -> bool:
    return workspace_id is not None if shared_workspace is None else bool(shared_workspace and workspace_id is not None)


def _legacy_personal_clause(
    user_id: int,
    workspace_id: int | None,
    *,
    alias: str = "",
    shared_workspace: bool | None = None,
) -> tuple[str, tuple]:
    prefix = f"{alias}." if alias else ""
    if workspace_id is None:
        return f"({prefix}workspace_id IS NULL AND ({prefix}user_id=%s OR {prefix}chat_id=%s))", (user_id, user_id)
    if not _is_shared_scope(workspace_id, shared_workspace):
        return f"({prefix}workspace_id=%s AND ({prefix}user_id=%s OR {prefix}chat_id=%s))", (workspace_id, user_id, user_id)
    return f"{prefix}workspace_id=%s", (workspace_id,)


def _workspace_filter(columns: set[str], workspace_id: int | None, *, alias: str = "") -> tuple[str, list]:
    if "workspace_id" not in columns:
        return "", []
    prefix = f"{alias}." if alias else ""
    if workspace_id is None:
        return f"AND {prefix}workspace_id IS NULL", []
    return f"AND {prefix}workspace_id=%s", [workspace_id]


def _type_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _type_filter(columns: set[str], op_type: str, candidates: tuple[str, ...], *, alias: str = "") -> tuple[str, list]:
    column = _type_column(columns, candidates)
    if not column:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"AND {prefix}{column}=%s", [op_type]


def _owned_reference_scope(
    columns: set[str],
    user_id: int,
    workspace_id: int | None,
    *,
    owner_column: str,
    alias: str = "",
    shared_workspace: bool | None = None,
) -> tuple[list[str], list]:
    prefix = f"{alias}." if alias else ""
    if "workspace_id" in columns:
        if workspace_id is None:
            return [f"{prefix}workspace_id IS NULL", f"{prefix}{owner_column}=%s"], [user_id]
        if _is_shared_scope(workspace_id, shared_workspace):
            return [f"{prefix}workspace_id=%s"], [workspace_id]
        return [f"{prefix}workspace_id=%s", f"{prefix}{owner_column}=%s"], [workspace_id, user_id]
    return [f"{prefix}{owner_column}=%s"], [user_id]


def _personalization_scope(
    columns: set[str],
    user_id: int,
    workspace_id: int | None,
    *,
    owner_column: str = "user_id",
    shared_workspace: bool | None = None,
) -> tuple[list[str], list] | None:
    if workspace_id is not None and "workspace_id" not in columns:
        return None
    return _owned_reference_scope(
        columns,
        user_id,
        workspace_id,
        owner_column=owner_column,
        shared_workspace=shared_workspace,
    )


def _category_limit_scope(
    columns: set[str],
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    *,
    shared_workspace: bool | None = None,
) -> tuple[list[str], list] | None:
    type_sql, type_params = _type_filter(columns, op_type, ("type", "op_type"))
    if not type_sql and not _is_expense_category_type(op_type):
        return None
    filters, params = _owned_reference_scope(
        columns,
        user_id,
        workspace_id,
        owner_column="user_id",
        shared_workspace=shared_workspace,
    )
    if type_sql:
        filters.append(type_sql.removeprefix("AND "))
        params.extend(type_params)
    return filters, params


def _reminder_scope(
    columns: set[str],
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    *,
    shared_workspace: bool | None = None,
) -> tuple[list[str], list]:
    filters, params = _owned_reference_scope(
        columns,
        user_id,
        workspace_id,
        owner_column="user_id",
        shared_workspace=shared_workspace,
    )
    type_sql, type_params = _type_filter(columns, op_type, ("rem_type", "type", "op_type"))
    if type_sql:
        filters.append(type_sql.removeprefix("AND "))
        params.extend(type_params)
    return filters, params


def _table_columns(cur, name: str) -> set[str]:
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    if not cur.fetchone()[0]:
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


def _count_rows(cur, table: str, where: str, params: tuple) -> int:
    relation = table if " " in table else f"public.{table}"
    cur.execute(f"SELECT COUNT(*) FROM {relation} WHERE {where}", params)
    return int(cur.fetchone()[0] or 0)


def _category_exists(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    name: str,
    shared_workspace: bool | None = None,
) -> bool:
    key = normalized_category_key(name)
    columns = _table_columns(cur, "custom_categories")
    if columns:
        scope, scope_params = _scope_clause(workspace_id)
        user_filter = "AND (user_id=%s OR %s::boolean)"
        cur.execute(
            f"""
            SELECT 1
              FROM public.custom_categories
             WHERE {scope}
               AND type=%s
               AND normalized_name=%s
               AND archived_at IS NULL
               {user_filter}
             LIMIT 1
            """,
            (*scope_params, op_type, key, user_id, _is_shared_scope(workspace_id, shared_workspace)),
        )
        if cur.fetchone():
            return True
    op_columns = _table_columns(cur, "operations")
    if {"category", "type"} <= op_columns:
        scope, params = _legacy_personal_clause(user_id, workspace_id, shared_workspace=shared_workspace)
        category_key = category_key_sql("category")
        cur.execute(
            f"""
            SELECT 1
              FROM public.operations
             WHERE {scope}
               AND type=%s
               AND {category_key}=%s
               AND COALESCE(category,'') NOT IN ('', 'Без операций')
             LIMIT 1
            """,
            (*params, op_type, key),
        )
        return cur.fetchone() is not None
    return False


def _ensure_category_exists(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    name: str,
    shared_workspace: bool | None = None,
) -> None:
    if not _category_exists(
        cur,
        user_id=user_id,
        workspace_id=workspace_id,
        op_type=op_type,
        name=name,
        shared_workspace=shared_workspace,
    ):
        raise ValueError("category_not_found")


def _update_draft_category(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    source_key: str,
    destination_name: str | None,
    shared_workspace: bool | None = None,
) -> int:
    draft_columns = _table_columns(cur, "operation_drafts")
    if not {"payload", "actor_user_id"} <= draft_columns:
        return 0
    filters, params = _owned_reference_scope(
        draft_columns,
        user_id,
        workspace_id,
        owner_column="actor_user_id",
        shared_workspace=shared_workspace,
    )
    op_type_filter = "AND COALESCE(payload->>'type', payload->>'op_type', %s)=%s"
    params.extend([op_type, op_type, source_key])
    category_key = category_key_sql("COALESCE(payload->>'category', payload->>'merchant', '')")
    if destination_name is None:
        cur.execute(
            f"""
            DELETE FROM public.operation_drafts
             WHERE {' AND '.join(filters)}
               {op_type_filter}
               AND {category_key}=%s
            """,
            tuple(params),
        )
    else:
        cur.execute(
            f"""
            UPDATE public.operation_drafts
                   SET payload=jsonb_set(payload, '{{category}}', to_jsonb(%s::text), true),
                   updated_at=now()
             WHERE {' AND '.join(filters)}
               {op_type_filter}
               AND {category_key}=%s
            """,
            (destination_name, *params),
        )
    return int(cur.rowcount or 0)


def list_managed_categories(*, user_id: int, workspace_id: int | None, op_type: str = "Расходы", limit: int = 100) -> list[ManagedCategory]:
    found: dict[str, ManagedCategory] = {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            custom_columns = _table_columns(cur, "custom_categories")
            if custom_columns:
                scope, params = _scope_clause(workspace_id)
                cur.execute(
                    f"""
                    SELECT id, name, normalized_name
                      FROM public.custom_categories
                     WHERE {scope}
                       AND type=%s
                       AND archived_at IS NULL
                       AND (user_id=%s OR %s::bigint IS NOT NULL)
                     ORDER BY name
                     LIMIT %s
                    """,
                    (*params, op_type, user_id, workspace_id, int(limit)),
                )
                for category_id, name, normalized in cur.fetchall():
                    found[str(normalized)] = ManagedCategory(
                        category_id=int(category_id),
                        name=name,
                        normalized_name=str(normalized),
                        op_type=op_type,
                        source="custom",
                    )

            op_columns = _table_columns(cur, "operations")
            if {"category", "type"} <= op_columns:
                scope, params = _legacy_personal_clause(user_id, workspace_id)
                category_key = category_key_sql("category")
                cur.execute(
                    f"""
                    SELECT MIN(category), {category_key}, COUNT(*)::int
                      FROM public.operations
                     WHERE {scope}
                       AND type=%s
                       AND COALESCE(category,'') NOT IN ('', 'Без операций')
                     GROUP BY {category_key}
                     ORDER BY COUNT(*) DESC, MIN(category) ASC
                     LIMIT %s
                    """,
                    (*params, op_type, int(limit)),
                )
                for name, normalized, op_count in cur.fetchall():
                    normalized = str(normalized)
                    current = found.get(normalized)
                    found[normalized] = ManagedCategory(
                        category_id=current.category_id if current else None,
                        name=current.name if current else name,
                        normalized_name=normalized,
                        op_type=op_type,
                        source=current.source if current else "operation",
                        operation_count=int(op_count or 0),
                    )

            limit_columns = _table_columns(cur, "category_limits")
            if {"category", "user_id"} <= limit_columns:
                scope = _category_limit_scope(limit_columns, user_id, workspace_id, op_type)
                if not scope:
                    conn.rollback()
                    return sorted(found.values(), key=lambda item: (item.name.casefold(), item.source))
                filters, params = scope
                category_key = category_key_sql("category")
                cur.execute(
                    f"""
                    SELECT {category_key}, COUNT(*)::int
                      FROM public.category_limits
                     WHERE {' AND '.join(filters)}
                     GROUP BY {category_key}
                    """,
                    tuple(params),
                )
                budgeted = {str(r[0]) for r in cur.fetchall() if r[0]}
                for key, item in list(found.items()):
                    if key in budgeted:
                        found[key] = ManagedCategory(**{**item.__dict__, "has_budget": True})
        conn.rollback()
    finally:
        conn.close()
    return sorted(found.values(), key=lambda item: (item.name.casefold(), item.source))


def category_reference_counts(*, user_id: int, workspace_id: int | None, op_type: str, category: str) -> CategoryReferenceCounts:
    key = normalized_category_key(category)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            counts = _category_reference_counts_cur(cur, user_id=user_id, workspace_id=workspace_id, op_type=op_type, category_key=key)
        conn.rollback()
        return counts
    finally:
        conn.close()


def _count_keys(values: list[str]) -> list[str]:
    keys = []
    seen = set()
    for value in values:
        try:
            key = normalized_category_key(value)
        except ValueError:
            continue
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _merge_count_rows(target: dict[str, dict[str, int]], field: str, rows) -> None:
    for key, count in rows:
        if key is None:
            continue
        bucket = target.setdefault(str(key), {})
        bucket[field] = int(count or 0)


def category_reference_counts_many(*, user_id: int, workspace_id: int | None, op_type: str, category_keys: list[str]) -> dict[str, CategoryReferenceCounts]:
    keys = _count_keys(category_keys)
    values: dict[str, dict[str, int]] = {key: {} for key in keys}
    if not keys:
        return {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            op_columns = _table_columns(cur, "operations")
            if {"category", "type"} <= op_columns:
                scope, params = _legacy_personal_clause(user_id, workspace_id)
                category_key = category_key_sql("category")
                cur.execute(
                    f"""
                    SELECT {category_key}, COUNT(*)::int
                      FROM public.operations
                     WHERE {scope}
                       AND type=%s
                       AND {category_key}=ANY(%s)
                     GROUP BY {category_key}
                    """,
                    (*params, op_type, keys),
                )
                _merge_count_rows(values, "operations", cur.fetchall())

            draft_columns = _table_columns(cur, "operation_drafts")
            if {"payload", "actor_user_id"} <= draft_columns:
                filters, params = _owned_reference_scope(
                    draft_columns,
                    user_id,
                    workspace_id,
                    owner_column="actor_user_id",
                )
                category_key = category_key_sql("COALESCE(payload->>'category', payload->>'merchant', '')")
                cur.execute(
                    f"""
                    SELECT {category_key}, COUNT(*)::int
                      FROM public.operation_drafts
                     WHERE {' AND '.join(filters)}
                       AND COALESCE(payload->>'type', payload->>'op_type', %s)=%s
                       AND {category_key}=ANY(%s)
                     GROUP BY {category_key}
                    """,
                    tuple(params + [op_type, op_type, keys]),
                )
                _merge_count_rows(values, "drafts", cur.fetchall())

            limit_columns = _table_columns(cur, "category_limits")
            if {"category", "user_id"} <= limit_columns:
                scope = _category_limit_scope(limit_columns, user_id, workspace_id, op_type)
                if scope:
                    filters, params = scope
                    category_key = category_key_sql("category")
                    filters.append(f"{category_key}=ANY(%s)")
                    params.append(keys)
                    cur.execute(
                        f"""
                        SELECT {category_key}, COUNT(*)::int
                          FROM public.category_limits
                         WHERE {' AND '.join(filters)}
                         GROUP BY {category_key}
                        """,
                        tuple(params),
                    )
                    _merge_count_rows(values, "category_limits", cur.fetchall())

            if _is_expense_category_type(op_type):
                for table, field in (
                    ("category_limit_state", "category_limit_states"),
                    ("subscription_patterns", "subscription_patterns"),
                    ("recurring_spend_patterns", "recurring_spend_patterns"),
                ):
                    columns = _table_columns(cur, table)
                    if not {"category", "user_id"} <= columns:
                        continue
                    filters, params = _owned_reference_scope(
                        columns,
                        user_id,
                        workspace_id,
                        owner_column="user_id",
                    )
                    category_key = category_key_sql("category")
                    filters.append(f"{category_key}=ANY(%s)")
                    params.append(keys)
                    cur.execute(
                        f"""
                        SELECT {category_key}, COUNT(*)::int
                          FROM public.{table}
                         WHERE {' AND '.join(filters)}
                         GROUP BY {category_key}
                        """,
                        tuple(params),
                    )
                    _merge_count_rows(values, field, cur.fetchall())

            cbg_columns = _table_columns(cur, "category_budget_group_members")
            if cbg_columns and _is_expense_category_type(op_type):
                group_columns = _table_columns(cur, "category_budget_groups")
                group_filters, group_params = _owned_reference_scope(
                    group_columns,
                    user_id,
                    workspace_id,
                    owner_column="owner_user_id",
                    alias="g",
                )
                category_key = category_key_sql("m.category_name")
                cur.execute(
                    f"""
                    SELECT {category_key}, COUNT(*)::int
                      FROM public.category_budget_group_members m
                      JOIN public.category_budget_groups g ON g.id=m.group_id
                     WHERE {' AND '.join(group_filters)}
                       AND {category_key}=ANY(%s)
                     GROUP BY {category_key}
                    """,
                    (*group_params, keys),
                )
                _merge_count_rows(values, "category_budget_groups", cur.fetchall())

            reminder_columns = _table_columns(cur, "user_reminders")
            if {"category", "user_id"} <= reminder_columns:
                filters, params = _reminder_scope(reminder_columns, user_id, workspace_id, op_type)
                category_key = category_key_sql("category")
                filters.append(f"{category_key}=ANY(%s)")
                params.append(keys)
                cur.execute(
                    f"""
                    SELECT {category_key}, COUNT(*)::int
                      FROM public.user_reminders
                     WHERE {' AND '.join(filters)}
                     GROUP BY {category_key}
                    """,
                    tuple(params),
                )
                _merge_count_rows(values, "reminders", cur.fetchall())

            alias_columns = _table_columns(cur, "user_aliases")
            if {"category", "user_id"} <= alias_columns:
                scope = _personalization_scope(alias_columns, user_id, workspace_id)
                if scope:
                    filters, params = scope
                    type_sql, type_params = _type_filter(alias_columns, op_type, ("type", "op_type"))
                    if type_sql:
                        filters.append(type_sql.removeprefix("AND "))
                        params.extend(type_params)
                    category_key = category_key_sql("category")
                    cur.execute(
                        f"""
                        SELECT {category_key}, COUNT(*)::int
                          FROM public.user_aliases
                         WHERE {' AND '.join(filters)}
                           AND {category_key}=ANY(%s)
                         GROUP BY {category_key}
                        """,
                        (*params, keys),
                    )
                    _merge_count_rows(values, "aliases", cur.fetchall())

            ml_columns = _table_columns(cur, "ml_observations")
            if {"chosen_category", "user_id"} <= ml_columns:
                scope = _personalization_scope(ml_columns, user_id, workspace_id)
                if scope:
                    filters, params = scope
                    type_sql, type_params = _type_filter(ml_columns, op_type, ("chosen_type", "detected_type", "op_type"))
                    if type_sql:
                        filters.append(type_sql.removeprefix("AND "))
                        params.extend(type_params)
                    category_key = category_key_sql("chosen_category")
                    cur.execute(
                        f"""
                        SELECT {category_key}, COUNT(*)::int
                          FROM public.ml_observations
                         WHERE {' AND '.join(filters)}
                           AND {category_key}=ANY(%s)
                         GROUP BY {category_key}
                        """,
                        (*params, keys),
                    )
                    _merge_count_rows(values, "ml_observations", cur.fetchall())
        conn.rollback()
    finally:
        conn.close()
    return {key: CategoryReferenceCounts(**values.get(key, {})) for key in keys}


def _category_reference_counts_cur(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    category_key: str,
    shared_workspace: bool | None = None,
) -> CategoryReferenceCounts:
    values: dict[str, int] = {}
    op_columns = _table_columns(cur, "operations")
    if {"category", "type"} <= op_columns:
        scope, params = _legacy_personal_clause(user_id, workspace_id, shared_workspace=shared_workspace)
        values["operations"] = _count_rows(
            cur,
            "operations",
            f"{scope} AND type=%s AND {category_key_sql('category')}=%s",
            (*params, op_type, category_key),
        )
    draft_columns = _table_columns(cur, "operation_drafts")
    if {"payload", "actor_user_id"} <= draft_columns:
        filters, params = _owned_reference_scope(
            draft_columns,
            user_id,
            workspace_id,
            owner_column="actor_user_id",
            shared_workspace=shared_workspace,
        )
        draft_category_key = category_key_sql("COALESCE(payload->>'category', payload->>'merchant', '')")
        values["drafts"] = _count_rows(
            cur,
            "operation_drafts",
            f"{' AND '.join(filters)} AND COALESCE(payload->>'type', payload->>'op_type', %s)=%s AND {draft_category_key}=%s",
            tuple(params + [op_type, op_type, category_key]),
        )
    limit_columns = _table_columns(cur, "category_limits")
    if {"category", "user_id"} <= limit_columns:
        scope = _category_limit_scope(
            limit_columns,
            user_id,
            workspace_id,
            op_type,
            shared_workspace=shared_workspace,
        )
        if scope:
            filters, params = scope
            filters.append(f"{category_key_sql('category')}=%s")
            params.append(category_key)
            values["category_limits"] = _count_rows(cur, "category_limits", " AND ".join(filters), tuple(params))
    if _is_expense_category_type(op_type):
        for table, field in (
            ("category_limit_state", "category_limit_states"),
            ("subscription_patterns", "subscription_patterns"),
            ("recurring_spend_patterns", "recurring_spend_patterns"),
        ):
            columns = _table_columns(cur, table)
            if not {"category", "user_id"} <= columns:
                continue
            filters, params = _owned_reference_scope(
                columns,
                user_id,
                workspace_id,
                owner_column="user_id",
                shared_workspace=shared_workspace,
            )
            filters.append(f"{category_key_sql('category')}=%s")
            params.append(category_key)
            values[field] = _count_rows(cur, table, " AND ".join(filters), tuple(params))
    cbg_columns = _table_columns(cur, "category_budget_group_members")
    if cbg_columns and _is_expense_category_type(op_type):
        group_columns = _table_columns(cur, "category_budget_groups")
        group_filters, group_params = _owned_reference_scope(
            group_columns,
            user_id,
            workspace_id,
            owner_column="owner_user_id",
            alias="g",
            shared_workspace=shared_workspace,
        )
        values["category_budget_groups"] = _count_rows(
            cur,
            "category_budget_group_members m JOIN public.category_budget_groups g ON g.id=m.group_id",
            f"{' AND '.join(group_filters)} AND {category_key_sql('m.category_name')}=%s",
            (*group_params, category_key),
        )
    reminder_columns = _table_columns(cur, "user_reminders")
    if {"category", "user_id"} <= reminder_columns:
        filters, params = _reminder_scope(
            reminder_columns,
            user_id,
            workspace_id,
            op_type,
            shared_workspace=shared_workspace,
        )
        filters.append(f"{category_key_sql('category')}=%s")
        params.append(category_key)
        values["reminders"] = _count_rows(cur, "user_reminders", " AND ".join(filters), tuple(params))
    alias_columns = _table_columns(cur, "user_aliases")
    if {"category", "user_id"} <= alias_columns:
        scope = _personalization_scope(
            alias_columns,
            user_id,
            workspace_id,
            shared_workspace=shared_workspace,
        )
        if scope:
            filters, params = scope
            type_sql, type_params = _type_filter(alias_columns, op_type, ("type", "op_type"))
            if type_sql:
                filters.append(type_sql.removeprefix("AND "))
                params.extend(type_params)
            filters.append(f"{category_key_sql('category')}=%s")
            params.append(category_key)
            values["aliases"] = _count_rows(cur, "user_aliases", " AND ".join(filters), tuple(params))
    ml_columns = _table_columns(cur, "ml_observations")
    if {"chosen_category", "user_id"} <= ml_columns:
        scope = _personalization_scope(
            ml_columns,
            user_id,
            workspace_id,
            shared_workspace=shared_workspace,
        )
        if scope:
            filters, params = scope
            type_sql, type_params = _type_filter(ml_columns, op_type, ("chosen_type", "detected_type", "op_type"))
            if type_sql:
                filters.append(type_sql.removeprefix("AND "))
                params.extend(type_params)
            filters.append(f"{category_key_sql('chosen_category')}=%s")
            params.append(category_key)
            values["ml_observations"] = _count_rows(cur, "ml_observations", " AND ".join(filters), tuple(params))
    return CategoryReferenceCounts(**values)


def _update_rows(cur, table: str, set_sql: str, where: str, params: tuple) -> int:
    cur.execute(f"UPDATE public.{table} SET {set_sql} WHERE {where}", params)
    return int(cur.rowcount or 0)


def _update_derived_expense_categories(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    source_key: str,
    destination_name: str,
    shared_workspace: bool | None,
) -> dict[str, int]:
    changed: dict[str, int] = {}
    for table, field in (
        ("category_limit_state", "category_limit_states"),
        ("subscription_patterns", "subscription_patterns"),
        ("recurring_spend_patterns", "recurring_spend_patterns"),
    ):
        columns = _table_columns(cur, table)
        if not {"category", "user_id"} <= columns:
            continue
        filters, params = _owned_reference_scope(
            columns,
            user_id,
            workspace_id,
            owner_column="user_id",
            shared_workspace=shared_workspace,
        )
        filters.append(f"{category_key_sql('category')}=%s")
        params.append(source_key)
        set_sql = "category=%s"
        if "updated_at" in columns:
            set_sql += ", updated_at=now()"
        changed[field] = _update_rows(
            cur,
            table,
            set_sql,
            " AND ".join(filters),
            (destination_name, *params),
        )
    return changed


def _migrate_user_category_preferences(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    source_key: str,
    destination_key: str | None,
    shared_workspace: bool | None,
) -> int:
    if not _table_columns(cur, "user_category_preferences"):
        return 0
    from services.category_preferences import migrate_category_preferences_cur

    return migrate_category_preferences_cur(
        cur,
        user_id=user_id,
        workspace_id=workspace_id,
        operation_type=op_type,
        source_key=source_key,
        destination_key=destination_key,
        shared_workspace=_is_shared_scope(workspace_id, shared_workspace),
    )


def transfer_category(
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    source: str,
    destination: str,
    archive_source: bool = False,
    budget_resolution: str = "delete_source",
    shared_workspace: bool | None = None,
) -> CategoryTransferResult:
    source_name = normalize_category_name(source)
    destination_name = normalize_category_name(destination)
    source_key = normalized_category_key(source_name)
    destination_key = normalized_category_key(destination_name)
    if source_key == destination_key:
        raise ValueError("same_category")
    if is_protected_category(source_name):
        raise ValueError("protected_category")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_category_exists(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                name=source_name,
                shared_workspace=shared_workspace,
            )
            if not _category_exists(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                name=destination_name,
                shared_workspace=shared_workspace,
            ):
                raise ValueError("destination_not_found")

            before = _category_reference_counts_cur(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                category_key=source_key,
                shared_workspace=shared_workspace,
            )
            changed: dict[str, int] = {}

            op_columns = _table_columns(cur, "operations")
            if {"category", "type"} <= op_columns:
                scope, params = _legacy_personal_clause(user_id, workspace_id, shared_workspace=shared_workspace)
                changed["operations"] = _update_rows(
                    cur,
                    "operations",
                    "category=%s, updated_at=COALESCE(updated_at, now())",
                    f"{scope} AND type=%s AND {category_key_sql('category')}=%s",
                    (destination_name, *params, op_type, source_key),
                )

            limit_columns = _table_columns(cur, "category_limits")
            source_budget_count = 0
            transferred_budget_count = 0
            skipped_destination_budget_count = 0
            if {"category", "user_id"} <= limit_columns:
                scope = _category_limit_scope(
                    limit_columns,
                    user_id,
                    workspace_id,
                    op_type,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    limit_filters, limit_params = scope
                    cur.execute(
                        f"SELECT id, user_id, period, amount, currency FROM public.category_limits WHERE {' AND '.join(limit_filters)} AND {category_key_sql('category')}=%s",
                        (*limit_params, source_key),
                    )
                    source_limits = cur.fetchall()
                    source_budget_count = len(source_limits)
                    for limit_id, owner_user_id, period, _amount, _currency in source_limits:
                        destination_filters = list(limit_filters)
                        destination_params = list(limit_params)
                        if workspace_id is not None and "workspace_id" in limit_columns:
                            destination_filters.append("user_id=%s")
                            destination_params.append(owner_user_id)
                        destination_filters.extend(["period=%s", f"{category_key_sql('category')}=%s"])
                        destination_params.extend([period, destination_key])
                        cur.execute(
                            f"SELECT 1 FROM public.category_limits WHERE {' AND '.join(destination_filters)} LIMIT 1",
                            tuple(destination_params),
                        )
                        destination_has_budget = cur.fetchone() is not None
                        if budget_resolution == "transfer_source" and not destination_has_budget:
                            cur.execute(
                                """
                                UPDATE public.category_limits
                                   SET category=%s, updated_at=now()
                                 WHERE id=%s
                                """,
                                (destination_name, limit_id),
                            )
                            transferred_budget_count += int(cur.rowcount or 0)
                        else:
                            if destination_has_budget:
                                skipped_destination_budget_count += 1
                            cur.execute(
                                "DELETE FROM public.category_limits WHERE id=%s",
                                (limit_id,),
                            )
                    changed["category_limits"] = source_budget_count

            if _is_expense_category_type(op_type):
                changed.update(_update_derived_expense_categories(
                    cur,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_key=source_key,
                    destination_name=destination_name,
                    shared_workspace=shared_workspace,
                ))

            cbg_columns = _table_columns(cur, "category_budget_group_members")
            if cbg_columns and _is_expense_category_type(op_type):
                group_columns = _table_columns(cur, "category_budget_groups")
                group_filters, group_params = _owned_reference_scope(
                    group_columns,
                    user_id,
                    workspace_id,
                    owner_column="owner_user_id",
                    alias="g",
                    shared_workspace=shared_workspace,
                )
                member_key = category_key_sql("m.category_name")
                cur.execute(
                    f"""
                    SELECT m.group_id
                      FROM public.category_budget_group_members m
                      JOIN public.category_budget_groups g ON g.id=m.group_id
                     WHERE {' AND '.join(group_filters)}
                       AND {member_key}=%s
                    """,
                    (*group_params, source_key),
                )
                group_ids = [int(r[0]) for r in cur.fetchall()]
                updated = 0
                for group_id in group_ids:
                    cur.execute(
                        f"SELECT 1 FROM public.category_budget_group_members WHERE group_id=%s AND {category_key_sql('category_name')}=%s LIMIT 1",
                        (group_id, destination_key),
                    )
                    if cur.fetchone():
                        cur.execute(
                            f"DELETE FROM public.category_budget_group_members WHERE group_id=%s AND {category_key_sql('category_name')}=%s",
                            (group_id, source_key),
                        )
                    else:
                        cur.execute(
                            f"""
                            UPDATE public.category_budget_group_members
                               SET category_name=%s, normalized_category_name=%s
                             WHERE group_id=%s AND {category_key_sql('category_name')}=%s
                            """,
                            (destination_name, destination_key, group_id, source_key),
                        )
                    updated += 1
                changed["category_budget_groups"] = updated

            reminder_columns = _table_columns(cur, "user_reminders")
            if {"category", "user_id"} <= reminder_columns:
                reminder_filters, reminder_params = _reminder_scope(
                    reminder_columns,
                    user_id,
                    workspace_id,
                    op_type,
                    shared_workspace=shared_workspace,
                )
                reminder_filters.append(f"{category_key_sql('category')}=%s")
                reminder_params.append(source_key)
                changed["reminders"] = _update_rows(
                    cur,
                    "user_reminders",
                    "category=%s, updated_at=now()",
                    " AND ".join(reminder_filters),
                    (destination_name, *reminder_params),
                )

            alias_columns = _table_columns(cur, "user_aliases")
            if {"category", "user_id"} <= alias_columns:
                scope = _personalization_scope(
                    alias_columns,
                    user_id,
                    workspace_id,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    alias_filters, alias_params = scope
                    type_sql, type_params = _type_filter(alias_columns, op_type, ("type", "op_type"))
                    if type_sql:
                        alias_filters.append(type_sql.removeprefix("AND "))
                        alias_params.extend(type_params)
                    alias_filters.append(f"{category_key_sql('category')}=%s")
                    alias_params.append(source_key)
                    changed["aliases"] = _update_rows(
                        cur,
                        "user_aliases",
                        "category=%s, updated_at=now()",
                        " AND ".join(alias_filters),
                        (destination_name, *alias_params),
                    )

            ml_columns = _table_columns(cur, "ml_observations")
            if {"chosen_category", "user_id"} <= ml_columns:
                scope = _personalization_scope(
                    ml_columns,
                    user_id,
                    workspace_id,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    ml_filters, ml_params = scope
                    type_sql, type_params = _type_filter(ml_columns, op_type, ("chosen_type", "detected_type", "op_type"))
                    if type_sql:
                        ml_filters.append(type_sql.removeprefix("AND "))
                        ml_params.extend(type_params)
                    ml_filters.append(f"{category_key_sql('chosen_category')}=%s")
                    ml_params.append(source_key)
                    changed["ml_observations"] = _update_rows(
                        cur,
                        "ml_observations",
                        "chosen_category=%s",
                        " AND ".join(ml_filters),
                        (destination_name, *ml_params),
                    )

            changed["drafts"] = _update_draft_category(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=source_key,
                destination_name=destination_name,
                shared_workspace=shared_workspace,
            )
            _migrate_user_category_preferences(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=source_key,
                destination_key=destination_key,
                shared_workspace=shared_workspace,
            )

            archived_category_id = None
            custom_columns = _table_columns(cur, "custom_categories")
            if archive_source and custom_columns:
                scope, params = _scope_clause(workspace_id)
                cur.execute(
                    f"""
                    UPDATE public.custom_categories
                       SET archived_at=COALESCE(archived_at, now()),
                           updated_at=now()
                     WHERE {scope}
                       AND type=%s
                       AND normalized_name=%s
                       AND archived_at IS NULL
                       AND (user_id=%s OR %s::boolean)
                     RETURNING id
                    """,
                    (*params, op_type, source_key, user_id, _is_shared_scope(workspace_id, shared_workspace)),
                )
                row = cur.fetchone()
                archived_category_id = int(row[0]) if row else None

            counts = CategoryReferenceCounts(**{**before.as_dict(), **changed})
        conn.commit()
        return CategoryTransferResult(
            source=source_name,
            destination=destination_name,
            op_type=op_type,
            counts=counts,
            archived_category_id=archived_category_id,
            deleted_budget_count=source_budget_count - transferred_budget_count,
            transferred_budget_count=transferred_budget_count,
            skipped_destination_budget_count=skipped_destination_budget_count,
            changed=bool(counts.total or archived_category_id),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rename_category(
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    source: str,
    destination: str,
    shared_workspace: bool | None = None,
) -> CategoryRenameResult:
    source_name = normalize_category_name(source)
    destination_name = normalize_category_name(destination)
    source_key = normalized_category_key(source_name)
    destination_key = normalized_category_key(destination_name)
    if is_protected_category(source_name):
        raise ValueError("protected_category")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_category_exists(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                name=source_name,
                shared_workspace=shared_workspace,
            )
            if source_key != destination_key and _category_exists(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                name=destination_name,
                shared_workspace=shared_workspace,
            ):
                raise ValueError("duplicate_category")

            before = _category_reference_counts_cur(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                category_key=source_key,
                shared_workspace=shared_workspace,
            )
            changed: dict[str, int] = {}

            op_columns = _table_columns(cur, "operations")
            if {"category", "type"} <= op_columns:
                scope, params = _legacy_personal_clause(user_id, workspace_id, shared_workspace=shared_workspace)
                changed["operations"] = _update_rows(
                    cur,
                    "operations",
                    "category=%s, updated_at=COALESCE(updated_at, now())",
                    f"{scope} AND type=%s AND {category_key_sql('category')}=%s",
                    (destination_name, *params, op_type, source_key),
                )

            limit_columns = _table_columns(cur, "category_limits")
            if {"category", "user_id"} <= limit_columns:
                scope = _category_limit_scope(
                    limit_columns,
                    user_id,
                    workspace_id,
                    op_type,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    limit_filters, limit_params = scope
                    limit_filters.append(f"{category_key_sql('category')}=%s")
                    limit_params.append(source_key)
                    changed["category_limits"] = _update_rows(
                        cur,
                        "category_limits",
                        "category=%s, updated_at=now()",
                        " AND ".join(limit_filters),
                        (destination_name, *limit_params),
                    )

            if _is_expense_category_type(op_type):
                changed.update(_update_derived_expense_categories(
                    cur,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_key=source_key,
                    destination_name=destination_name,
                    shared_workspace=shared_workspace,
                ))

            cbg_columns = _table_columns(cur, "category_budget_group_members")
            if cbg_columns and _is_expense_category_type(op_type):
                group_columns = _table_columns(cur, "category_budget_groups")
                group_filters, group_params = _owned_reference_scope(
                    group_columns,
                    user_id,
                    workspace_id,
                    owner_column="owner_user_id",
                    shared_workspace=shared_workspace,
                )
                changed["category_budget_groups"] = _update_rows(
                    cur,
                    "category_budget_group_members",
                    "category_name=%s, normalized_category_name=%s",
                    f"{category_key_sql('category_name')}=%s AND group_id IN (SELECT id FROM public.category_budget_groups WHERE {' AND '.join(group_filters)})",
                    (destination_name, destination_key, source_key, *group_params),
                )

            reminder_columns = _table_columns(cur, "user_reminders")
            if {"category", "user_id"} <= reminder_columns:
                reminder_filters, reminder_params = _reminder_scope(
                    reminder_columns,
                    user_id,
                    workspace_id,
                    op_type,
                    shared_workspace=shared_workspace,
                )
                reminder_filters.append(f"{category_key_sql('category')}=%s")
                reminder_params.append(source_key)
                changed["reminders"] = _update_rows(
                    cur,
                    "user_reminders",
                    "category=%s, updated_at=now()",
                    " AND ".join(reminder_filters),
                    (destination_name, *reminder_params),
                )

            alias_columns = _table_columns(cur, "user_aliases")
            if {"category", "user_id"} <= alias_columns:
                scope = _personalization_scope(
                    alias_columns,
                    user_id,
                    workspace_id,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    alias_filters, alias_params = scope
                    type_sql, type_params = _type_filter(alias_columns, op_type, ("type", "op_type"))
                    if type_sql:
                        alias_filters.append(type_sql.removeprefix("AND "))
                        alias_params.extend(type_params)
                    alias_filters.append(f"{category_key_sql('category')}=%s")
                    alias_params.append(source_key)
                    changed["aliases"] = _update_rows(
                        cur,
                        "user_aliases",
                        "category=%s, updated_at=now()",
                        " AND ".join(alias_filters),
                        (destination_name, *alias_params),
                    )

            ml_columns = _table_columns(cur, "ml_observations")
            if {"chosen_category", "user_id"} <= ml_columns:
                scope = _personalization_scope(
                    ml_columns,
                    user_id,
                    workspace_id,
                    shared_workspace=shared_workspace,
                )
                if scope:
                    ml_filters, ml_params = scope
                    type_sql, type_params = _type_filter(ml_columns, op_type, ("chosen_type", "detected_type", "op_type"))
                    if type_sql:
                        ml_filters.append(type_sql.removeprefix("AND "))
                        ml_params.extend(type_params)
                    ml_filters.append(f"{category_key_sql('chosen_category')}=%s")
                    ml_params.append(source_key)
                    changed["ml_observations"] = _update_rows(
                        cur,
                        "ml_observations",
                        "chosen_category=%s",
                        " AND ".join(ml_filters),
                        (destination_name, *ml_params),
                    )

            changed["drafts"] = _update_draft_category(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=source_key,
                destination_name=destination_name,
                shared_workspace=shared_workspace,
            )
            _migrate_user_category_preferences(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=source_key,
                destination_key=destination_key,
                shared_workspace=shared_workspace,
            )

            category_id = None
            custom_columns = _table_columns(cur, "custom_categories")
            if custom_columns:
                scope, params = _scope_clause(workspace_id)
                cur.execute(
                    f"""
                    UPDATE public.custom_categories
                       SET name=%s,
                           normalized_name=%s,
                           updated_at=now()
                     WHERE {scope}
                       AND type=%s
                       AND normalized_name=%s
                       AND archived_at IS NULL
                       AND (user_id=%s OR %s::boolean)
                     RETURNING id
                    """,
                    (
                        destination_name,
                        destination_key,
                        *params,
                        op_type,
                        source_key,
                        user_id,
                        _is_shared_scope(workspace_id, shared_workspace),
                    ),
                )
                row = cur.fetchone()
                category_id = int(row[0]) if row else None

            counts = CategoryReferenceCounts(**{**before.as_dict(), **changed})
        conn.commit()
        track_product_event(ProductEvent(
            event_name="category_renamed",
            user_id=user_id,
            workspace_id=workspace_id,
            status="success",
            entity_type="category",
            entity_id=category_id,
            properties={"op_type": op_type},
        ))
        return CategoryRenameResult(
            source=source_name,
            destination=destination_name,
            op_type=op_type,
            counts=counts,
            category_id=category_id,
            changed=source_name != destination_name or bool(counts.total or category_id),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hard_delete_category_with_operations(
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    category: str,
) -> CategoryDeleteResult:
    name = normalize_category_name(category)
    if is_protected_category(name):
        raise ValueError("protected_category")
    key = normalized_category_key(name)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_category_exists(cur, user_id=user_id, workspace_id=workspace_id, op_type=op_type, name=name)
            before = _category_reference_counts_cur(cur, user_id=user_id, workspace_id=workspace_id, op_type=op_type, category_key=key)

            operation_ids: list[int] = []
            op_columns = _table_columns(cur, "operations")
            if {"id", "category", "type"} <= op_columns:
                scope, params = _legacy_personal_clause(user_id, workspace_id)
                cur.execute(
                    f"""
                    SELECT id
                     FROM public.operations
                     WHERE {scope}
                       AND type=%s
                       AND {category_key_sql('category')}=%s
                    """,
                    (*params, op_type, key),
                )
                operation_ids = [int(r[0]) for r in cur.fetchall()]

            if operation_ids:
                for table, column in (
                    ("financial_activity_events", "operation_id"),
                    ("notification_events", "operation_id"),
                    ("ml_observations", "operation_id"),
                    ("operation_versions", "operation_id"),
                    ("operations_history", "operation_id"),
                ):
                    if column in _table_columns(cur, table):
                        cur.execute(f"DELETE FROM public.{table} WHERE {column}=ANY(%s)", (operation_ids,))
                cur.execute("DELETE FROM public.operations WHERE id=ANY(%s)", (operation_ids,))

            limit_columns = _table_columns(cur, "category_limits")
            if limit_columns:
                scope = _category_limit_scope(limit_columns, user_id, workspace_id, op_type)
                if scope:
                    limit_filters, limit_params = scope
                    limit_filters.append(f"{category_key_sql('category')}=%s")
                    limit_params.append(key)
                    cur.execute(f"DELETE FROM public.category_limits WHERE {' AND '.join(limit_filters)}", tuple(limit_params))
            if _table_columns(cur, "category_budget_group_members") and _is_expense_category_type(op_type):
                group_columns = _table_columns(cur, "category_budget_groups")
                group_filters, group_params = _owned_reference_scope(
                    group_columns,
                    user_id,
                    workspace_id,
                    owner_column="owner_user_id",
                    alias="g",
                )
                cur.execute(
                    f"""
                    DELETE FROM public.category_budget_group_members m
                     USING public.category_budget_groups g
                     WHERE g.id=m.group_id
                       AND {' AND '.join(group_filters)}
                       AND {category_key_sql('m.category_name')}=%s
                    """,
                    (*group_params, key),
                )
            reminder_columns = _table_columns(cur, "user_reminders")
            if reminder_columns:
                reminder_filters, reminder_params = _reminder_scope(reminder_columns, user_id, workspace_id, op_type)
                reminder_filters.append(f"{category_key_sql('category')}=%s")
                reminder_params.append(key)
                cur.execute(f"DELETE FROM public.user_reminders WHERE {' AND '.join(reminder_filters)}", tuple(reminder_params))
            alias_columns = _table_columns(cur, "user_aliases")
            if alias_columns:
                scope = _personalization_scope(alias_columns, user_id, workspace_id)
                if scope:
                    alias_filters, alias_params = scope
                    type_sql, type_params = _type_filter(alias_columns, op_type, ("type", "op_type"))
                    if type_sql:
                        alias_filters.append(type_sql.removeprefix("AND "))
                        alias_params.extend(type_params)
                    alias_filters.append(f"{category_key_sql('category')}=%s")
                    alias_params.append(key)
                    cur.execute(f"DELETE FROM public.user_aliases WHERE {' AND '.join(alias_filters)}", tuple(alias_params))
            ml_columns = _table_columns(cur, "ml_observations")
            if ml_columns:
                scope = _personalization_scope(ml_columns, user_id, workspace_id)
                if scope:
                    ml_filters, ml_params = scope
                    type_sql, type_params = _type_filter(ml_columns, op_type, ("chosen_type", "detected_type", "op_type"))
                    if type_sql:
                        ml_filters.append(type_sql.removeprefix("AND "))
                        ml_params.extend(type_params)
                    ml_filters.append(f"{category_key_sql('chosen_category')}=%s")
                    ml_params.append(key)
                    cur.execute(f"UPDATE public.ml_observations SET chosen_category=NULL WHERE {' AND '.join(ml_filters)}", tuple(ml_params))

            _update_draft_category(cur, user_id=user_id, workspace_id=workspace_id, op_type=op_type, source_key=key, destination_name=None)
            _migrate_user_category_preferences(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=key,
                destination_key=None,
                shared_workspace=False,
            )

            archived_category_id = None
            if _table_columns(cur, "custom_categories"):
                scope, params = _scope_clause(workspace_id)
                cur.execute(
                    f"""
                    UPDATE public.custom_categories
                       SET archived_at=COALESCE(archived_at, now()),
                           updated_at=now()
                     WHERE {scope}
                       AND type=%s
                       AND normalized_name=%s
                       AND archived_at IS NULL
                       AND (user_id=%s OR %s::bigint IS NOT NULL)
                     RETURNING id
                    """,
                    (*params, op_type, key, user_id, workspace_id),
                )
                row = cur.fetchone()
                archived_category_id = int(row[0]) if row else None
        conn.commit()
        return CategoryDeleteResult(
            source=name,
            op_type=op_type,
            counts=before,
            archived_category_id=archived_category_id,
            deleted_operation_count=len(operation_ids),
            changed=bool(operation_ids or before.total or archived_category_id),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def archive_empty_category(
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    category: str,
    shared_workspace: bool | None = None,
) -> CategoryTransferResult:
    name = normalize_category_name(category)
    if is_protected_category(name):
        raise ValueError("protected_category")
    key = normalized_category_key(name)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_category_exists(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                name=name,
                shared_workspace=shared_workspace,
            )
            counts = _category_reference_counts_cur(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                category_key=key,
                shared_workspace=shared_workspace,
            )
            if counts.total:
                raise ValueError("category_has_references")
            _migrate_user_category_preferences(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                op_type=op_type,
                source_key=key,
                destination_key=None,
                shared_workspace=shared_workspace,
            )
            archived_category_id = None
            if _table_columns(cur, "custom_categories"):
                scope, params = _scope_clause(workspace_id)
                cur.execute(
                    f"""
                    UPDATE public.custom_categories
                       SET archived_at=COALESCE(archived_at, now()),
                           updated_at=now()
                     WHERE {scope}
                       AND type=%s
                       AND normalized_name=%s
                       AND archived_at IS NULL
                       AND (user_id=%s OR %s::boolean)
                     RETURNING id
                    """,
                    (*params, op_type, key, user_id, _is_shared_scope(workspace_id, shared_workspace)),
                )
                row = cur.fetchone()
                archived_category_id = int(row[0]) if row else None
        conn.commit()
        return CategoryTransferResult(
            source=name,
            destination="",
            op_type=op_type,
            counts=counts,
            archived_category_id=archived_category_id,
            changed=bool(archived_category_id),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_category_without_operations(
    *,
    user_id: int,
    workspace_id: int | None,
    op_type: str,
    category: str,
    shared_workspace: bool | None = None,
) -> CategoryTransferResult:
    return archive_empty_category(
        user_id=user_id,
        workspace_id=workspace_id,
        op_type=op_type,
        category=category,
        shared_workspace=shared_workspace,
    )


def get_or_create_custom_category(
    *,
    workspace_id: int | None,
    user_id: int,
    op_type: str,
    name: str,
) -> CategoryResult:
    display_name = normalize_category_name(name)
    key = normalized_category_key(display_name)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name
                  FROM public.custom_categories
                 WHERE workspace_id IS NOT DISTINCT FROM %s
                   AND (user_id=%s OR %s::bigint IS NOT NULL)
                   AND type=%s
                   AND normalized_name=%s
                   AND archived_at IS NULL
                 LIMIT 1
                """,
                (workspace_id, user_id, workspace_id, op_type, key),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return CategoryResult(name=row[1], normalized_name=key, created=False, category_id=int(row[0]))
            cur.execute(
                """
                INSERT INTO public.custom_categories (workspace_id, user_id, type, name, normalized_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (workspace_id, user_id, op_type, display_name, key),
            )
            category_id = int(cur.fetchone()[0])
        conn.commit()
        track_product_event(ProductEvent(
            event_name="category_created",
            user_id=user_id,
            workspace_id=workspace_id,
            status="success",
            entity_type="category",
            entity_id=category_id,
            properties={"op_type": op_type},
        ))
        return CategoryResult(name=display_name, normalized_name=key, created=True, category_id=category_id)
    except errors.UndefinedTable:
        conn.rollback()
        return CategoryResult(name=display_name, normalized_name=key, created=False, category_id=None)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_custom_categories(workspace_id: int | None, op_type: str) -> list[dict]:
    try:
        rows = pg_fetchall(
            """
            SELECT id, name, icon, color
              FROM public.custom_categories
             WHERE workspace_id IS NOT DISTINCT FROM %s
               AND type=%s
               AND archived_at IS NULL
             ORDER BY name
            """,
            (workspace_id, op_type),
        )
    except errors.UndefinedTable:
        return []
    return [{"id": int(r[0]), "name": r[1], "icon": r[2], "color": r[3]} for r in rows]
