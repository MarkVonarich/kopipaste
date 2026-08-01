from __future__ import annotations

from datetime import time

from psycopg2 import errors

from db.database import get_conn, pg_fetchall

TOGGLE_FIELDS = {
    "morning": "morning_enabled",
    "evening": "evening_enabled",
    "limits": "limit_alerts_enabled",
    "budgets": "budget_alerts_enabled",
    "subscriptions": "subscription_alerts_enabled",
    "recurring": "recurring_spend_alerts_enabled",
    "weekly": "weekly_reports_enabled",
    "monthly": "monthly_reports_enabled",
    "challenges": "challenge_notifications_enabled",
}


def get_notification_preferences(user_id: int) -> dict:
    try:
        rows = pg_fetchall(
            """
            SELECT COALESCE(morning_enabled, true), COALESCE(evening_enabled, true),
                   COALESCE(limit_alerts_enabled, true), COALESCE(budget_alerts_enabled, true),
                   COALESCE(subscription_alerts_enabled, true), COALESCE(recurring_spend_alerts_enabled, true),
                   COALESCE(weekly_reports_enabled, true), COALESCE(monthly_reports_enabled, true),
                   COALESCE(challenge_notifications_enabled, false),
                   COALESCE(to_char(morning_time, 'HH24:MI'), '08:30'),
                   COALESCE(to_char(evening_time, 'HH24:MI'), '20:30'),
                   to_char(quiet_hours_start, 'HH24:MI'),
                   to_char(quiet_hours_end, 'HH24:MI')
              FROM public.notification_preferences
             WHERE user_id=%s
             LIMIT 1
            """,
            (user_id,),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        rows = []
    if not rows:
        return {
            "morning_enabled": True,
            "evening_enabled": True,
            "limit_alerts_enabled": True,
            "budget_alerts_enabled": True,
            "subscription_alerts_enabled": True,
            "recurring_spend_alerts_enabled": True,
            "weekly_reports_enabled": True,
            "monthly_reports_enabled": True,
            "challenge_notifications_enabled": False,
            "morning_time": "08:30",
            "evening_time": "20:30",
            "quiet_hours_enabled": False,
            "quiet_hours_start": None,
            "quiet_hours_end": None,
        }
    r = rows[0]
    return {
        "morning_enabled": bool(r[0]),
        "evening_enabled": bool(r[1]),
        "limit_alerts_enabled": bool(r[2]),
        "budget_alerts_enabled": bool(r[3]),
        "subscription_alerts_enabled": bool(r[4]),
        "recurring_spend_alerts_enabled": bool(r[5]),
        "weekly_reports_enabled": bool(r[6]),
        "monthly_reports_enabled": bool(r[7]),
        "challenge_notifications_enabled": bool(r[8]),
        "morning_time": r[9],
        "evening_time": r[10],
        "quiet_hours_enabled": bool(r[11] and r[12]),
        "quiet_hours_start": r[11],
        "quiet_hours_end": r[12],
    }


def toggle_notification_preference(user_id: int, key: str) -> bool:
    field = TOGGLE_FIELDS[key]
    default_enabled = False if key == "challenges" else True
    insert_value = not default_enabled
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.notification_preferences (user_id, {field})
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET {field}=NOT COALESCE(public.notification_preferences.{field}, %s)
                RETURNING {field}
                """,
                (user_id, insert_value, default_enabled),
            )
            value = bool(cur.fetchone()[0])
        conn.commit()
        return value
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_hhmm(value: str) -> time:
    parts = (value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError("invalid_time")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("invalid_time")
    return time(hour, minute)


def toggle_quiet_hours(user_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.notification_preferences (user_id, quiet_hours_start, quiet_hours_end)
                VALUES (%s, '22:30'::time, '08:00'::time)
                ON CONFLICT (user_id) DO UPDATE
                   SET quiet_hours_start = CASE
                           WHEN public.notification_preferences.quiet_hours_start IS NULL
                             OR public.notification_preferences.quiet_hours_end IS NULL
                           THEN COALESCE(public.notification_preferences.quiet_hours_start, '22:30'::time)
                           ELSE NULL
                       END,
                       quiet_hours_end = CASE
                           WHEN public.notification_preferences.quiet_hours_start IS NULL
                             OR public.notification_preferences.quiet_hours_end IS NULL
                           THEN COALESCE(public.notification_preferences.quiet_hours_end, '08:00'::time)
                           ELSE NULL
                       END
                RETURNING quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL
                """,
                (user_id,),
            )
            enabled = bool(cur.fetchone()[0])
        conn.commit()
        return enabled
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_quiet_hours_time(user_id: int, field: str, value: str) -> dict:
    if field not in {"start", "end"}:
        raise ValueError("invalid_field")
    parsed = parse_hhmm(value)
    column = "quiet_hours_start" if field == "start" else "quiet_hours_end"
    other = "quiet_hours_end" if field == "start" else "quiet_hours_start"
    default_other = "08:00" if field == "start" else "22:30"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.notification_preferences (user_id, {column}, {other})
                VALUES (%s, %s, %s::time)
                ON CONFLICT (user_id) DO UPDATE
                   SET {column}=EXCLUDED.{column},
                       {other}=COALESCE(public.notification_preferences.{other}, EXCLUDED.{other})
                """,
                (user_id, parsed, default_other),
            )
        conn.commit()
        return get_notification_preferences(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
