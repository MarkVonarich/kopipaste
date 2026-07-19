from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from db.queries import ensure_user

WorkspaceRole = Literal["owner", "admin", "member", "viewer"]

WRITE_ROLES = {"owner", "admin", "member"}
ADMIN_ROLES = {"owner", "admin"}


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: int | None
    chat_id: int
    actor_user_id: int
    kind: str
    role: str
    name: str
    is_configured: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _personal_workspace_name(user_id: int) -> str:
    return f"Personal {user_id}"


def ensure_personal_workspace(user_id: int) -> int | None:
    ensure_user(user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.workspaces (name, kind, owner_user_id)
                VALUES (%s, 'personal', %s)
                ON CONFLICT DO NOTHING
                """,
                (_personal_workspace_name(user_id), user_id),
            )
            cur.execute(
                """
                SELECT id
                  FROM public.workspaces
                 WHERE kind='personal' AND owner_user_id=%s
                 LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None
            workspace_id = int(row[0])
            cur.execute(
                """
                INSERT INTO public.workspace_members (workspace_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active')
                ON CONFLICT (workspace_id, user_id) DO UPDATE
                   SET role='owner', status='active', updated_at=now()
                """,
                (workspace_id, user_id),
            )
            cur.execute(
                """
                INSERT INTO public.user_workspace_settings (user_id, active_workspace_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET active_workspace_id=COALESCE(public.user_workspace_settings.active_workspace_id, EXCLUDED.active_workspace_id),
                       updated_at=now()
                """,
                (user_id, workspace_id),
            )
        conn.commit()
        return workspace_id
    except (errors.UndefinedTable, errors.UndefinedColumn):
        conn.rollback()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_active_private_workspace(user_id: int) -> int | None:
    try:
        rows = pg_fetchall(
            """
            SELECT active_workspace_id
              FROM public.user_workspace_settings
             WHERE user_id=%s
             LIMIT 1
            """,
            (user_id,),
        )
    except errors.UndefinedTable:
        return None
    if rows and rows[0][0]:
        return int(rows[0][0])
    return ensure_personal_workspace(user_id)


def resolve_workspace(chat_id: int, actor_user_id: int, chat_type: str = "private") -> WorkspaceContext:
    if chat_type in {"group", "supergroup"}:
        try:
            rows = pg_fetchall(
                """
                SELECT w.id, w.name, COALESCE(m.role, 'viewer')
                  FROM public.workspaces w
                  LEFT JOIN public.workspace_members m
                    ON m.workspace_id=w.id AND m.user_id=%s AND m.status='active'
                 WHERE w.telegram_chat_id=%s AND w.archived_at IS NULL
                 LIMIT 1
                """,
                (actor_user_id, chat_id),
            )
        except errors.UndefinedTable:
            return WorkspaceContext(None, chat_id, actor_user_id, "legacy_group", "member", "Legacy group", True)
        if not rows:
            return WorkspaceContext(None, chat_id, actor_user_id, "group", "viewer", "Unconfigured group", False)
        workspace_id, name, role = rows[0]
        return WorkspaceContext(int(workspace_id), chat_id, actor_user_id, "group", role, name, True)

    workspace_id = get_active_private_workspace(actor_user_id)
    if workspace_id is None:
        return WorkspaceContext(None, chat_id, actor_user_id, "legacy_personal", "owner", _personal_workspace_name(actor_user_id), True)
    try:
        rows = pg_fetchall(
            """
            SELECT w.name, w.kind, COALESCE(m.role, 'owner')
              FROM public.workspaces w
              LEFT JOIN public.workspace_members m
                ON m.workspace_id=w.id AND m.user_id=%s AND m.status='active'
             WHERE w.id=%s AND w.archived_at IS NULL
             LIMIT 1
            """,
            (actor_user_id, workspace_id),
        )
    except errors.UndefinedTable:
        return WorkspaceContext(None, chat_id, actor_user_id, "legacy_personal", "owner", _personal_workspace_name(actor_user_id), True)
    if not rows:
        workspace_id = ensure_personal_workspace(actor_user_id)
        return WorkspaceContext(workspace_id, chat_id, actor_user_id, "personal", "owner", _personal_workspace_name(actor_user_id), True)
    name, kind, role = rows[0]
    return WorkspaceContext(int(workspace_id), chat_id, actor_user_id, kind, role, name, True)


def can_add_operation(ctx: WorkspaceContext) -> bool:
    return ctx.is_configured and ctx.role in WRITE_ROLES


def can_manage_workspace(ctx: WorkspaceContext) -> bool:
    return ctx.is_configured and ctx.role in ADMIN_ROLES


def can_edit_operation(ctx: WorkspaceContext, operation_actor_user_id: int | None) -> bool:
    if ctx.role in ADMIN_ROLES:
        return True
    return ctx.role == "member" and operation_actor_user_id == ctx.actor_user_id


def list_accessible_workspaces(user_id: int) -> list[dict]:
    ensure_personal_workspace(user_id)
    try:
        rows = pg_fetchall(
            """
            SELECT w.id, w.name, w.kind, m.role, COALESCE(s.active_workspace_id = w.id, false) AS active
              FROM public.workspace_members m
              JOIN public.workspaces w ON w.id=m.workspace_id
              LEFT JOIN public.user_workspace_settings s ON s.user_id=m.user_id
             WHERE m.user_id=%s AND m.status='active' AND w.archived_at IS NULL
             ORDER BY active DESC, w.kind='personal' DESC, w.name ASC
            """,
            (user_id,),
        )
    except errors.UndefinedTable:
        return [{"workspace_id": None, "name": "Personal", "kind": "legacy_personal", "role": "owner", "active": True}]
    return [
        {"workspace_id": int(r[0]), "name": r[1], "kind": r[2], "role": r[3], "active": bool(r[4])}
        for r in rows
    ]


def set_active_workspace(user_id: int, workspace_id: int) -> bool:
    try:
        rows = pg_fetchall(
            """
            SELECT 1
              FROM public.workspace_members
             WHERE user_id=%s AND workspace_id=%s AND status='active'
             LIMIT 1
            """,
            (user_id, workspace_id),
        )
    except errors.UndefinedTable:
        return workspace_id is None
    if not rows:
        return False
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_workspace_settings (user_id, active_workspace_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET active_workspace_id=EXCLUDED.active_workspace_id, updated_at=now()
                """,
                (user_id, workspace_id),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
