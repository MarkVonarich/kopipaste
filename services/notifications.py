from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from services.activity import has_financial_activity_today

NOTIFICATION_TYPES = {
    "inactivity",
    "upcoming_commitment",
    "budget_progress",
    "category_limit_progress",
    "overspending",
    "unusual_spending",
    "positive_feedback",
    "summary",
    "reminder_followup",
}


@dataclass(frozen=True)
class NotificationCandidate:
    user_id: int
    notification_type: str
    dedupe_key: str
    reason: str
    workspace_id: int | None = None
    payload: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def build_inactivity_candidate(user_id: int, local_date: date, timezone_name: str = "Europe/Moscow") -> NotificationCandidate | None:
    if has_financial_activity_today(user_id, timezone_name):
        return None
    return NotificationCandidate(
        user_id=user_id,
        notification_type="inactivity",
        dedupe_key=f"inactivity:{local_date.isoformat()}",
        reason="No successful financial activity recorded today in the user's local timezone.",
        payload={"local_date": local_date.isoformat(), "timezone": timezone_name},
    )


def enqueue_notification(candidate: NotificationCandidate) -> bool:
    if candidate.notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"unsupported notification type: {candidate.notification_type}")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.notification_events
                    (user_id, workspace_id, notification_type, dedupe_key, reason, payload, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (user_id, notification_type, dedupe_key) DO NOTHING
                """,
                (
                    candidate.user_id,
                    candidate.workspace_id,
                    candidate.notification_type,
                    candidate.dedupe_key,
                    candidate.reason,
                    Json(candidate.payload or {}),
                ),
            )
            inserted = cur.rowcount == 1
        conn.commit()
        return inserted
    except errors.UndefinedTable:
        conn.rollback()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def should_skip_for_daily_limits(user_id: int, local_date: date) -> bool:
    try:
        rows = pg_fetchall(
            """
            WITH prefs AS (
                SELECT COALESCE(max_per_day, 2) AS max_per_day
                  FROM public.notification_preferences
                 WHERE user_id=%s
            )
            SELECT COUNT(*) >= COALESCE((SELECT max_per_day FROM prefs), 2)
              FROM public.notification_events
             WHERE user_id=%s
               AND created_at::date=%s
               AND status IN ('pending', 'sent')
            """,
            (user_id, user_id, local_date),
        )
        return bool(rows and rows[0][0])
    except errors.UndefinedTable:
        return False
