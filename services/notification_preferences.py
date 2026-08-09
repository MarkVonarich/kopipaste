from __future__ import annotations

from datetime import time

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from services.user_time import DEFAULT_TIMEZONE, is_valid_timezone_name

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
    "goals": "goal_notifications_enabled",
}

GROUPED_NOTIFICATION_FIELDS = {
    "daily": ("morning_enabled", "evening_enabled"),
    "plans": (
        "limit_alerts_enabled",
        "budget_alerts_enabled",
        "goal_notifications_enabled",
        "subscription_alerts_enabled",
        "recurring_spend_alerts_enabled",
    ),
    "reports": ("weekly_reports_enabled", "monthly_reports_enabled"),
}


def _preferences_rows(user_id: int):
    try:
        return pg_fetchall(
            """
            SELECT COALESCE(morning_enabled, true), COALESCE(evening_enabled, true),
                   COALESCE(limit_alerts_enabled, true), COALESCE(budget_alerts_enabled, true),
                   COALESCE(subscription_alerts_enabled, true), COALESCE(recurring_spend_alerts_enabled, true),
                   COALESCE(weekly_reports_enabled, true), COALESCE(monthly_reports_enabled, true),
                   COALESCE(challenge_notifications_enabled, false),
                   COALESCE(goal_notifications_enabled, false),
                   COALESCE(to_char(morning_time, 'HH24:MI'), '08:30'),
                   COALESCE(to_char(evening_time, 'HH24:MI'), '20:30'),
                   COALESCE(quiet_hours_enabled, false),
                   to_char(quiet_hours_start, 'HH24:MI'),
                   to_char(quiet_hours_end, 'HH24:MI'),
                   COALESCE(NULLIF(timezone, ''), %s)
              FROM public.notification_preferences
             WHERE user_id=%s
             LIMIT 1
            """,
            (DEFAULT_TIMEZONE, user_id),
        )
    except errors.UndefinedColumn:
        return pg_fetchall(
            """
            SELECT COALESCE(morning_enabled, true), COALESCE(evening_enabled, true),
                   COALESCE(limit_alerts_enabled, true), COALESCE(budget_alerts_enabled, true),
                   COALESCE(subscription_alerts_enabled, true), COALESCE(recurring_spend_alerts_enabled, true),
                   COALESCE(weekly_reports_enabled, true), COALESCE(monthly_reports_enabled, true),
                   COALESCE(challenge_notifications_enabled, false),
                   COALESCE(goal_notifications_enabled, false),
                   COALESCE(to_char(morning_time, 'HH24:MI'), '08:30'),
                   COALESCE(to_char(evening_time, 'HH24:MI'), '20:30'),
                   quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL,
                   to_char(quiet_hours_start, 'HH24:MI'),
                   to_char(quiet_hours_end, 'HH24:MI'),
                   COALESCE(NULLIF(timezone, ''), %s)
              FROM public.notification_preferences
             WHERE user_id=%s
             LIMIT 1
            """,
            (DEFAULT_TIMEZONE, user_id),
        )


def get_notification_preferences(user_id: int) -> dict:
    try:
        rows = _preferences_rows(user_id)
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
            "goal_notifications_enabled": False,
            "morning_time": "08:30",
            "evening_time": "20:30",
            "quiet_hours_enabled": False,
            "quiet_hours_start": None,
            "quiet_hours_end": None,
            "timezone": DEFAULT_TIMEZONE,
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
        "goal_notifications_enabled": bool(r[9]),
        "morning_time": r[10],
        "evening_time": r[11],
        "quiet_hours_enabled": bool(r[12]),
        "quiet_hours_start": r[13],
        "quiet_hours_end": r[14],
        "timezone": r[15] or DEFAULT_TIMEZONE,
    }


def grouped_notification_preferences(user_id: int) -> dict:
    prefs = get_notification_preferences(user_id)
    daily_enabled = bool(prefs.get("morning_enabled", True) or prefs.get("evening_enabled", True))
    plans_enabled = bool(
        prefs.get("limit_alerts_enabled", True)
        or prefs.get("budget_alerts_enabled", True)
        or prefs.get("goal_notifications_enabled", False)
        or prefs.get("subscription_alerts_enabled", True)
        or prefs.get("recurring_spend_alerts_enabled", True)
    )
    reports_enabled = bool(prefs.get("weekly_reports_enabled", True) or prefs.get("monthly_reports_enabled", True))
    return {
        **prefs,
        "daily_notifications": {
            "enabled": daily_enabled,
            "morning_time": prefs.get("morning_time") or "08:30",
            "evening_time": prefs.get("evening_time") or "20:30",
        },
        "plans_control": {"enabled": plans_enabled},
        "reports": {"enabled": reports_enabled},
        "quiet_hours": {
            "enabled": bool(prefs.get("quiet_hours_enabled")),
            "start": prefs.get("quiet_hours_start") or "22:30",
            "end": prefs.get("quiet_hours_end") or "08:00",
        },
        "timezone": prefs.get("timezone") or DEFAULT_TIMEZONE,
    }


def _set_fields(user_id: int, fields: tuple[str, ...], enabled: bool) -> dict:
    assignments = ", ".join(f"{field}=%s" for field in fields)
    values = [bool(enabled)] * len(fields)
    insert_columns = ", ".join(fields)
    insert_placeholders = ", ".join(["%s"] * len(fields))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.notification_preferences (user_id, {insert_columns})
                VALUES (%s, {insert_placeholders})
                ON CONFLICT (user_id) DO UPDATE
                   SET {assignments}, updated_at=now()
                """,
                (user_id, *values, *values),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if not enabled:
        try:
            from services.automatic_notifications import suppress_pending_preference_notifications

            for key in ("morning", "evening"):
                if key in {"morning", "evening"} and f"{key}_enabled" in fields:
                    suppress_pending_preference_notifications(user_id, key)
        except Exception:
            pass
    return grouped_notification_preferences(user_id)


def set_daily_notifications_enabled(user_id: int, enabled: bool) -> dict:
    return _set_fields(user_id, GROUPED_NOTIFICATION_FIELDS["daily"], enabled)


def set_plans_notifications_enabled(user_id: int, enabled: bool) -> dict:
    return _set_fields(user_id, GROUPED_NOTIFICATION_FIELDS["plans"], enabled)


def set_reports_notifications_enabled(user_id: int, enabled: bool) -> dict:
    return _set_fields(user_id, GROUPED_NOTIFICATION_FIELDS["reports"], enabled)


def set_grouped_notification_preference(user_id: int, group: str, enabled: bool) -> dict:
    if group == "daily":
        return set_daily_notifications_enabled(user_id, enabled)
    if group == "plans":
        return set_plans_notifications_enabled(user_id, enabled)
    if group == "reports":
        return set_reports_notifications_enabled(user_id, enabled)
    raise KeyError(group)


def toggle_notification_preference(user_id: int, key: str) -> bool:
    field = TOGGLE_FIELDS[key]
    default_enabled = False if key in {"challenges", "goals"} else True
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
        if key in {"morning", "evening"} and value is False:
            try:
                from services.automatic_notifications import suppress_pending_preference_notifications

                suppress_pending_preference_notifications(user_id, key)
            except Exception:
                pass
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
            try:
                cur.execute(
                    """
                    INSERT INTO public.notification_preferences (user_id, quiet_hours_enabled, quiet_hours_start, quiet_hours_end)
                    VALUES (%s, true, '22:30'::time, '08:00'::time)
                    ON CONFLICT (user_id) DO UPDATE
                       SET quiet_hours_enabled = NOT COALESCE(
                               public.notification_preferences.quiet_hours_enabled,
                               public.notification_preferences.quiet_hours_start IS NOT NULL
                                 AND public.notification_preferences.quiet_hours_end IS NOT NULL,
                               false
                           ),
                           quiet_hours_start = COALESCE(public.notification_preferences.quiet_hours_start, '22:30'::time),
                           quiet_hours_end = COALESCE(public.notification_preferences.quiet_hours_end, '08:00'::time),
                           updated_at=now()
                    RETURNING quiet_hours_enabled
                    """,
                    (user_id,),
                )
            except errors.UndefinedColumn:
                conn.rollback()
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
            try:
                cur.execute(
                    f"""
                    INSERT INTO public.notification_preferences (user_id, quiet_hours_enabled, {column}, {other})
                    VALUES (%s, true, %s, %s::time)
                    ON CONFLICT (user_id) DO UPDATE
                       SET quiet_hours_enabled=true,
                           {column}=EXCLUDED.{column},
                           {other}=COALESCE(public.notification_preferences.{other}, EXCLUDED.{other}),
                           updated_at=now()
                    """,
                    (user_id, parsed, default_other),
                )
            except errors.UndefinedColumn:
                conn.rollback()
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


def set_daily_notification_time(user_id: int, field: str, value: str) -> dict:
    if field not in {"morning", "evening"}:
        raise ValueError("invalid_field")
    parsed = parse_hhmm(value)
    column = "morning_time" if field == "morning" else "evening_time"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.notification_preferences (user_id, {column})
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET {column}=EXCLUDED.{column},
                       updated_at=now()
                """,
                (user_id, parsed),
            )
        conn.commit()
        return get_notification_preferences(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_quiet_hours(user_id: int, *, enabled: bool, start: str | None = None, end: str | None = None) -> dict:
    start_time = parse_hhmm(start) if start is not None else None
    end_time = parse_hhmm(end) if end is not None else None
    insert_start = start_time if start_time is not None else (time(22, 30) if enabled else None)
    insert_end = end_time if end_time is not None else (time(8, 0) if enabled else None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.notification_preferences (user_id, quiet_hours_enabled, quiet_hours_start, quiet_hours_end)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET quiet_hours_enabled=EXCLUDED.quiet_hours_enabled,
                       quiet_hours_start=CASE
                           WHEN %s THEN EXCLUDED.quiet_hours_start
                           WHEN EXCLUDED.quiet_hours_enabled THEN COALESCE(public.notification_preferences.quiet_hours_start, '22:30'::time)
                           ELSE public.notification_preferences.quiet_hours_start
                       END,
                       quiet_hours_end=CASE
                           WHEN %s THEN EXCLUDED.quiet_hours_end
                           WHEN EXCLUDED.quiet_hours_enabled THEN COALESCE(public.notification_preferences.quiet_hours_end, '08:00'::time)
                           ELSE public.notification_preferences.quiet_hours_end
                       END,
                       updated_at=now()
                """,
                (user_id, bool(enabled), insert_start, insert_end, start is not None, end is not None),
            )
        conn.commit()
        return get_notification_preferences(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_notification_timezone(user_id: int, timezone_name: str) -> dict:
    name = str(timezone_name or "").strip()
    if not is_valid_timezone_name(name):
        raise ValueError("invalid_timezone")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.notification_preferences (user_id, timezone)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET timezone=EXCLUDED.timezone,
                       updated_at=now()
                """,
                (user_id, name),
            )
        conn.commit()
        return get_notification_preferences(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
