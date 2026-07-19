from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

MAX_EXPORT_DAYS = 366 * 5


@dataclass(frozen=True)
class ExportPeriod:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def parse_export_date(text: str, today: date | None = None) -> date | None:
    today = today or date.today()
    value = (text or "").strip().lower()
    if value in {"сегодня", "today"}:
        return today
    if value in {"вчера", "yesterday"}:
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    try:
        if len(value.split(".")) == 2:
            return datetime.strptime(f"{value}.{today.year}", "%d.%m.%Y").date()
    except ValueError:
        return None
    return None


def validate_export_period(start: date, end: date, max_days: int = MAX_EXPORT_DAYS) -> tuple[bool, str | None]:
    if end < start:
        return False, "end_before_start"
    if (end - start).days + 1 > max_days:
        return False, "range_too_large"
    return True, None


def preset_period(key: str, today: date | None = None) -> ExportPeriod:
    today = today or date.today()
    if key == "today":
        return ExportPeriod(today, today)
    if key == "7":
        return ExportPeriod(today - timedelta(days=6), today)
    if key == "14":
        return ExportPeriod(today - timedelta(days=13), today)
    if key == "month":
        return ExportPeriod(today.replace(day=1), today)
    if key == "previous_month":
        first_this_month = today.replace(day=1)
        prev_end = first_this_month - timedelta(days=1)
        return ExportPeriod(prev_end.replace(day=1), prev_end)
    if key == "year":
        return ExportPeriod(today.replace(month=1, day=1), today)
    if key == "previous_year":
        prev_year = today.year - 1
        return ExportPeriod(date(prev_year, 1, 1), date(prev_year, 12, 31))
    raise ValueError(f"unknown preset: {key}")
