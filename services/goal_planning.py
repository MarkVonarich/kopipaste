from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from math import ceil
from typing import Iterable


FREQUENCY_NONE = "none"
FREQUENCY_MONTHLY = "monthly"
FREQUENCY_TWICE_MONTHLY = "twice_monthly"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_SALARY_MONTHLY = "salary_monthly"
FREQUENCY_SALARY_TWICE_MONTHLY = "salary_twice_monthly"

STRATEGY_NONE = "none"
STRATEGY_DEADLINE = "deadline"
STRATEGY_CONTRIBUTION = "contribution"


@dataclass(frozen=True)
class ScheduleConfig:
    frequency: str = FREQUENCY_NONE
    day: int | None = None
    days: tuple[int, ...] = ()
    weekday: int | None = None
    salary_payments_per_month: int | None = None


@dataclass(frozen=True)
class PlanCalculation:
    strategy: str
    frequency: str
    remaining_amount: Decimal
    occurrence_count: int
    recommended_amount: Decimal | None = None
    comfortable_amount: Decimal | None = None
    next_occurrence: date | None = None
    projected_completion_date: date | None = None
    required_contributions: int | None = None
    feasible: bool = True
    reason: str | None = None


def normalize_decimal(value: Decimal | int | str) -> Decimal:
    amount = Decimal(str(value))
    return amount.quantize(Decimal("0.01"))


def ceil_money(amount: Decimal, minor_unit: Decimal = Decimal("1")) -> Decimal:
    if amount <= 0:
        return Decimal("0").quantize(minor_unit)
    return (amount / minor_unit).to_integral_value(rounding=ROUND_CEILING) * minor_unit


def remaining_amount(target_amount: Decimal, current_balance: Decimal) -> Decimal:
    return max(normalize_decimal(target_amount) - normalize_decimal(current_balance), Decimal("0.00"))


def _safe_month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _add_month(year: int, month: int) -> tuple[int, int]:
    month += 1
    if month > 12:
        return year + 1, 1
    return year, month


def monthly_occurrences(start: date, end: date, days: Iterable[int]) -> list[date]:
    clean_days = sorted({int(day) for day in days if 1 <= int(day) <= 28})
    if not clean_days or end < start:
        return []
    year, month = start.year, start.month
    out: list[date] = []
    while date(year, month, 1) <= end.replace(day=1):
        for day in clean_days:
            candidate = _safe_month_date(year, month, day)
            if start <= candidate <= end:
                out.append(candidate)
        year, month = _add_month(year, month)
    return sorted(out)


def weekly_occurrences(start: date, end: date, weekday: int) -> list[date]:
    if end < start or weekday < 0 or weekday > 6:
        return []
    delta = (weekday - start.weekday()) % 7
    current = start + timedelta(days=delta)
    out: list[date] = []
    while current <= end:
        out.append(current)
        current += timedelta(days=7)
    return out


def occurrences_between(start: date, end: date, schedule: ScheduleConfig) -> list[date]:
    frequency = schedule.frequency
    if frequency == FREQUENCY_MONTHLY:
        return monthly_occurrences(start, end, (schedule.day or 1,))
    if frequency == FREQUENCY_TWICE_MONTHLY:
        return monthly_occurrences(start, end, schedule.days or (5, 20))
    if frequency == FREQUENCY_WEEKLY:
        return weekly_occurrences(start, end, 0 if schedule.weekday is None else int(schedule.weekday))
    if frequency == FREQUENCY_SALARY_MONTHLY:
        return monthly_occurrences(start, end, (schedule.day or 1,))
    if frequency == FREQUENCY_SALARY_TWICE_MONTHLY:
        return monthly_occurrences(start, end, schedule.days or (5, 20))
    return []


def next_occurrence(today: date, schedule: ScheduleConfig, horizon_days: int = 370) -> date | None:
    items = occurrences_between(today, today + timedelta(days=horizon_days), schedule)
    return items[0] if items else None


def calculate_deadline_first(
    *,
    target_amount: Decimal,
    current_balance: Decimal,
    deadline: date | None,
    schedule: ScheduleConfig,
    today: date,
    minor_unit: Decimal = Decimal("1"),
) -> PlanCalculation:
    remaining = remaining_amount(target_amount, current_balance)
    if remaining <= 0:
        return PlanCalculation(STRATEGY_DEADLINE, schedule.frequency, remaining, 0, recommended_amount=Decimal("0"), projected_completion_date=today)
    if deadline is None:
        return PlanCalculation(STRATEGY_DEADLINE, schedule.frequency, remaining, 0, feasible=False, reason="missing_deadline")
    occurrences = occurrences_between(today, deadline, schedule)
    if not occurrences:
        return PlanCalculation(STRATEGY_DEADLINE, schedule.frequency, remaining, 0, feasible=False, reason="no_occurrences")
    recommended = ceil_money(remaining / Decimal(len(occurrences)), minor_unit)
    return PlanCalculation(
        STRATEGY_DEADLINE,
        schedule.frequency,
        remaining,
        len(occurrences),
        recommended_amount=recommended,
        next_occurrence=occurrences[0],
        projected_completion_date=occurrences[-1],
    )


def calculate_contribution_first(
    *,
    target_amount: Decimal,
    current_balance: Decimal,
    comfortable_amount: Decimal,
    schedule: ScheduleConfig,
    today: date,
) -> PlanCalculation:
    remaining = remaining_amount(target_amount, current_balance)
    contribution = normalize_decimal(comfortable_amount)
    if remaining <= 0:
        return PlanCalculation(STRATEGY_CONTRIBUTION, schedule.frequency, remaining, 0, comfortable_amount=contribution, projected_completion_date=today, required_contributions=0)
    if contribution <= 0:
        return PlanCalculation(STRATEGY_CONTRIBUTION, schedule.frequency, remaining, 0, comfortable_amount=contribution, feasible=False, reason="invalid_contribution")
    required = int(ceil(remaining / contribution))
    if schedule.frequency == FREQUENCY_NONE:
        return PlanCalculation(
            STRATEGY_CONTRIBUTION,
            schedule.frequency,
            remaining,
            0,
            comfortable_amount=contribution,
            required_contributions=required,
            feasible=True,
            reason="no_schedule",
        )
    occurrences = occurrences_between(today, today + timedelta(days=3660), schedule)
    if len(occurrences) < required:
        return PlanCalculation(STRATEGY_CONTRIBUTION, schedule.frequency, remaining, len(occurrences), comfortable_amount=contribution, required_contributions=required, feasible=False, reason="horizon_exceeded")
    return PlanCalculation(
        STRATEGY_CONTRIBUTION,
        schedule.frequency,
        remaining,
        len(occurrences[:required]),
        comfortable_amount=contribution,
        next_occurrence=occurrences[0],
        projected_completion_date=occurrences[required - 1],
        required_contributions=required,
    )


def progress_percent(target_amount: Decimal, current_balance: Decimal) -> int:
    target = normalize_decimal(target_amount)
    if target <= 0:
        return 0
    return max(0, min(100, int((normalize_decimal(current_balance) * Decimal("100") / target).to_integral_value(rounding=ROUND_CEILING))))


def status_for_goal(
    *,
    status: str,
    target_amount: Decimal,
    current_balance: Decimal,
    deadline: date | None,
    plan: PlanCalculation | None,
    today: date,
) -> str:
    if status in {"archived", "paused", "deleted"}:
        return status
    if remaining_amount(target_amount, current_balance) <= 0:
        return "achieved"
    if deadline and today > deadline:
        return "overdue"
    if plan is None or plan.strategy == STRATEGY_NONE or plan.frequency == FREQUENCY_NONE:
        return "no_plan"
    if not plan.feasible:
        return "behind"
    if plan.strategy == STRATEGY_DEADLINE:
        return "on_track"
    if deadline and plan.projected_completion_date:
        if plan.projected_completion_date < deadline:
            return "ahead"
        if plan.projected_completion_date > deadline:
            return "behind"
    return "on_track"
