from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg2 import errors

from db.database import pg_fetchall

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Moscow"

TIMEZONE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Московское время", "Europe/Moscow"),
    ("Калининград", "Europe/Kaliningrad"),
    ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"),
    ("Красноярск", "Asia/Krasnoyarsk"),
    ("Иркутск", "Asia/Irkutsk"),
    ("Якутск", "Asia/Yakutsk"),
    ("Владивосток", "Asia/Vladivostok"),
)

LEGACY_OFFSET_TIMEZONES: dict[int, str] = {
    0: "UTC",
    60: "Europe/Berlin",
    120: "Europe/Kaliningrad",
    180: "Europe/Moscow",
    240: "Europe/Samara",
    300: "Asia/Yekaterinburg",
    360: "Asia/Omsk",
    420: "Asia/Krasnoyarsk",
    480: "Asia/Irkutsk",
    540: "Asia/Yakutsk",
    600: "Asia/Vladivostok",
}


@dataclass(frozen=True)
class ResolvedUserTimezone:
    timezone_name: str
    source: str
    fallback_reason: str | None = None


def is_valid_timezone_name(value: str | None) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(str(value).strip())
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _valid_or_none(value: str | None) -> str | None:
    name = str(value or "").strip()
    if is_valid_timezone_name(name):
        return name
    return None


def legacy_offset_to_timezone(offset_min: int | None) -> str:
    try:
        offset = int(offset_min if offset_min is not None else 180)
    except (TypeError, ValueError):
        offset = 180
    return LEGACY_OFFSET_TIMEZONES.get(offset, DEFAULT_TIMEZONE)


def resolve_user_timezone(user_id: int, workspace_id: int | None = None) -> ResolvedUserTimezone:
    try:
        rows = pg_fetchall(
            """
            SELECT np.timezone,
                   uws.timezone,
                   w.timezone,
                   u.tz_offset_min
              FROM public.users u
              LEFT JOIN public.notification_preferences np ON np.user_id=u.user_id
              LEFT JOIN public.user_workspace_settings uws ON uws.user_id=u.user_id
              LEFT JOIN public.workspaces w
                ON w.id = COALESCE(%s, uws.active_workspace_id)
             WHERE u.user_id=%s
             LIMIT 1
            """,
            (workspace_id, user_id),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        rows = []
    except Exception as exc:
        log.info("user_timezone_lookup_failed reason=%s", type(exc).__name__)
        rows = []

    if rows:
        pref_tz, user_workspace_tz, workspace_tz, legacy_offset = rows[0]
        for source, candidate in (
            ("notification_preferences.timezone", pref_tz),
            ("user_workspace_settings.timezone", user_workspace_tz),
            ("workspaces.timezone", workspace_tz),
        ):
            valid = _valid_or_none(candidate)
            if valid:
                return ResolvedUserTimezone(valid, source)
            if candidate:
                log.info("user_timezone_invalid source=%s", source)

        mapped = legacy_offset_to_timezone(legacy_offset)
        return ResolvedUserTimezone(mapped, "users.tz_offset_min" if legacy_offset is not None else "default", None if legacy_offset is not None else "missing_timezone")

    return ResolvedUserTimezone(DEFAULT_TIMEZONE, "default", "missing_timezone")


def user_timezone_name(user_id: int, workspace_id: int | None = None) -> tuple[str, str | None]:
    resolved = resolve_user_timezone(user_id, workspace_id)
    return resolved.timezone_name, resolved.fallback_reason


def _aware_utc(now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def user_local_now(user_id: int, workspace_id: int | None = None, now_utc: datetime | None = None) -> datetime:
    resolved = resolve_user_timezone(user_id, workspace_id)
    return _aware_utc(now_utc).astimezone(ZoneInfo(resolved.timezone_name))


def user_local_date(user_id: int, workspace_id: int | None = None, now_utc: datetime | None = None) -> date:
    return user_local_now(user_id, workspace_id, now_utc).date()


def local_datetime_to_utc(
    user_id: int,
    local_dt: datetime,
    workspace_id: int | None = None,
) -> datetime:
    resolved = resolve_user_timezone(user_id, workspace_id)
    tz = ZoneInfo(resolved.timezone_name)
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=tz)
    else:
        local_dt = local_dt.astimezone(tz)
    return local_dt.astimezone(timezone.utc)


def local_date_time_to_utc(
    user_id: int,
    local_day: date,
    local_time: time,
    workspace_id: int | None = None,
) -> datetime:
    return local_datetime_to_utc(user_id, datetime.combine(local_day, local_time), workspace_id)


def is_local_time_in_window(local_dt: datetime, start: time | None, end: time | None) -> bool:
    if not start or not end or start == end:
        return False
    current = local_dt.time().replace(second=0, microsecond=0)
    if start < end:
        return start <= current < end
    return current >= start or current < end
