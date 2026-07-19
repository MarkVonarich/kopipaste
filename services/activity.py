from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg2.extras import Json
from psycopg2 import errors

from db.database import get_conn, pg_fetchall


def _local_date(tz_name: str, at: datetime | None = None) -> date:
    base = at or datetime.now(timezone.utc)
    try:
        return base.astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        return base.date()


def record_financial_activity(
    user_id: int,
    source: str,
    workspace_id: int | None = None,
    operation_id: int | None = None,
    timezone_name: str = "Europe/Moscow",
    metadata: dict | None = None,
) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.financial_activity_events
                    (user_id, workspace_id, operation_id, source, activity_type, local_date, metadata)
                VALUES (%s, %s, %s, %s, 'operation_recorded', %s, %s::jsonb)
                """,
                (user_id, workspace_id, operation_id, source, _local_date(timezone_name), Json(metadata or {})),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_financial_activity_today(user_id: int, timezone_name: str = "Europe/Moscow") -> bool:
    today = _local_date(timezone_name)
    try:
        rows = pg_fetchall(
            """
            SELECT 1
              FROM public.financial_activity_events
             WHERE user_id=%s AND local_date=%s
             LIMIT 1
            """,
            (user_id, today),
        )
    except errors.UndefinedTable:
        rows = []
    if rows:
        return True

    try:
        tz = ZoneInfo(timezone_name)
        local_start = datetime.combine(today, datetime.min.time(), tzinfo=tz)
        local_end = local_start + timedelta(days=1)
        utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        utc_end = local_end.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        utc_start = datetime.combine(today, datetime.min.time())
        utc_end = utc_start + timedelta(days=1)

    rows = pg_fetchall(
        """
        SELECT 1
          FROM public.operations
         WHERE user_id=%s
           AND created_at >= %s
           AND created_at < %s
           AND COALESCE(type,'') <> 'noop'
           AND COALESCE(category,'') <> 'Без операций'
         LIMIT 1
        """,
        (user_id, utc_start, utc_end),
    )
    return bool(rows)
