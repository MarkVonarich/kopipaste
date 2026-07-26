from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any


PRIORITY = {
    "subscription_upcoming": 10,
    "limit_exceeded": 20,
    "budget_exceeded": 21,
    "limit_near": 30,
    "budget_near": 31,
    "pace_overspend": 40,
    "recurring_spend_detected": 50,
    "period_comparison": 60,
    "inactivity": 70,
    "fallback": 99,
}


@dataclass(frozen=True)
class NotificationFact:
    notification_type: str
    dedupe_key: str
    text: str
    priority: int
    workspace_id: int | None = None
    related_entity_type: str | None = None
    related_entity_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    buttons: tuple[tuple[tuple[str, str], ...], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NotificationPreferences:
    morning_enabled: bool = True
    evening_enabled: bool = True
    limit_alerts_enabled: bool = True
    budget_alerts_enabled: bool = True
    subscription_alerts_enabled: bool = True
    recurring_spend_alerts_enabled: bool = True
    weekly_reports_enabled: bool = True
    monthly_reports_enabled: bool = True
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None


def _parse_time(value: str | time | None) -> time | None:
    if value is None or isinstance(value, time):
        return value
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def preferences_from_dict(values: dict) -> NotificationPreferences:
    return NotificationPreferences(
        morning_enabled=bool(values.get("morning_enabled", True)),
        evening_enabled=bool(values.get("evening_enabled", True)),
        limit_alerts_enabled=bool(values.get("limit_alerts_enabled", True)),
        budget_alerts_enabled=bool(values.get("budget_alerts_enabled", True)),
        subscription_alerts_enabled=bool(values.get("subscription_alerts_enabled", True)),
        recurring_spend_alerts_enabled=bool(values.get("recurring_spend_alerts_enabled", True)),
        weekly_reports_enabled=bool(values.get("weekly_reports_enabled", True)),
        monthly_reports_enabled=bool(values.get("monthly_reports_enabled", True)),
        quiet_hours_start=_parse_time(values.get("quiet_hours_start")),
        quiet_hours_end=_parse_time(values.get("quiet_hours_end")),
    )


def is_quiet_time(local_dt: datetime, prefs: NotificationPreferences) -> bool:
    start = prefs.quiet_hours_start
    end = prefs.quiet_hours_end
    if not start or not end:
        return False
    current = local_dt.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def quiet_hours_end_datetime(local_dt: datetime, prefs: NotificationPreferences) -> datetime | None:
    if not is_quiet_time(local_dt, prefs) or not prefs.quiet_hours_end:
        return None
    end = prefs.quiet_hours_end
    candidate = local_dt.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= local_dt:
        candidate += timedelta(days=1)
    return candidate


def should_send_now(local_dt: datetime, prefs: NotificationPreferences, *, manual_preview: bool = False) -> bool:
    if manual_preview:
        return True
    return not is_quiet_time(local_dt, prefs)


def preference_allows(fact: NotificationFact, prefs: NotificationPreferences) -> bool:
    t = fact.notification_type
    if t.startswith("limit_"):
        return prefs.limit_alerts_enabled
    if t.startswith("budget_"):
        return prefs.budget_alerts_enabled
    if t == "subscription_upcoming":
        return prefs.subscription_alerts_enabled
    if t == "recurring_spend_detected":
        return prefs.recurring_spend_alerts_enabled
    if t == "weekly_report":
        return prefs.weekly_reports_enabled
    if t == "monthly_report":
        return prefs.monthly_reports_enabled
    return True


def choose_best_fact(facts: list[NotificationFact], prefs: NotificationPreferences, local_dt: datetime) -> NotificationFact | None:
    allowed = [f for f in facts if preference_allows(f, prefs)]
    if not allowed or is_quiet_time(local_dt, prefs):
        return None
    return sorted(allowed, key=lambda f: (f.priority, f.notification_type, f.dedupe_key))[0]


def fact_from_limit_alert(*, text: str, dedupe_key: str, percentage: int, workspace_id: int | None = None, entity_type: str = "category_limit", entity_id: int | None = None) -> NotificationFact:
    exceeded = percentage >= 100
    return NotificationFact(
        notification_type="limit_exceeded" if exceeded else "limit_near",
        dedupe_key=dedupe_key,
        text=text,
        priority=PRIORITY["limit_exceeded" if exceeded else "limit_near"],
        workspace_id=workspace_id,
        related_entity_type=entity_type,
        related_entity_id=entity_id,
        payload={"percentage": percentage},
    )


def fallback_fact(user_id: int, local_date: str, locale: str = "ru") -> NotificationFact:
    text = "No meaningful finance events right now." if locale == "en" else "Пока нет важных финансовых событий."
    return NotificationFact("fallback", f"fallback:{user_id}:{local_date}", text, PRIORITY["fallback"])
