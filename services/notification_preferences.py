from __future__ import annotations

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
}


def get_notification_preferences(user_id: int) -> dict:
    try:
        rows = pg_fetchall(
            """
            SELECT COALESCE(morning_enabled, true), COALESCE(evening_enabled, true),
                   COALESCE(limit_alerts_enabled, true), COALESCE(budget_alerts_enabled, true),
                   COALESCE(subscription_alerts_enabled, true), COALESCE(recurring_spend_alerts_enabled, true),
                   COALESCE(weekly_reports_enabled, true), COALESCE(monthly_reports_enabled, true),
                   COALESCE(to_char(morning_time, 'HH24:MI'), '08:30'),
                   COALESCE(to_char(evening_time, 'HH24:MI'), '20:30')
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
            "morning_time": "08:30",
            "evening_time": "20:30",
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
        "morning_time": r[8],
        "evening_time": r[9],
    }


def toggle_notification_preference(user_id: int, key: str) -> bool:
    field = TOGGLE_FIELDS[key]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.notification_preferences (user_id, {field})
                VALUES (%s, false)
                ON CONFLICT (user_id) DO UPDATE
                   SET {field}=NOT COALESCE(public.notification_preferences.{field}, true)
                RETURNING {field}
                """,
                (user_id,),
            )
            value = bool(cur.fetchone()[0])
        conn.commit()
        return value
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
