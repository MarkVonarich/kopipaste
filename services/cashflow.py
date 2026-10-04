"""Cash scenarios from a user-confirmed balance, independent of budget headroom."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from db.database import pg_fetchall
from services.forecasting import ForecastPeriod, ForecastRepository
from services.reminders import _advance_date
from utils.money import to_decimal_money

ZERO = Decimal("0.00")
MAX_HORIZON_DAYS = 366


def cash_amount(value) -> Decimal:
    amount = to_decimal_money(value)
    if not amount.is_finite() or abs(amount) > Decimal("1000000000000"):
        raise ValueError("invalid_amount")
    return amount


@dataclass(frozen=True)
class CashEvent:
    key: str
    due: date
    amount: Decimal
    kind: str
    label: str
    overdue: bool = False


def reminder_occurrences(reminders: list[dict], *, today: date, end: date) -> tuple[CashEvent, ...]:
    events: dict[str, CashEvent] = {}
    for reminder in reminders:
        if not reminder.get("is_active", True):
            continue
        due = reminder["event_date"]
        recorded = set(reminder.get("recorded_dates") or ())
        kind = {"Доходы": "income", "Расходы": "expense"}.get(reminder["rem_type"])
        if kind is None:
            continue
        for _ in range(10000):
            if due > end:
                break
            if due not in recorded:
                key = f"reminder:{reminder['id']}:{due.isoformat()}"
                events[key] = CashEvent(key, due, to_decimal_money(reminder["amount"], positive=True), kind, reminder["title"], due < today)
            following = _advance_date(due, reminder.get("repeat_rule", "none"), reminder.get("repeat_interval_days"))
            if following is None:
                break
            if following <= due:
                raise ValueError("invalid_repeat")
            due = following
        else:
            raise ValueError("too_many_occurrences")
    return tuple(sorted(events.values(), key=lambda event: (event.due, event.key)))


def forecast_end(today: date, horizon: str, end_date: str | None, events: tuple[CashEvent, ...]) -> date | None:
    if horizon == "next_income":
        return min((event.due for event in events if event.kind == "income" and event.due >= today), default=None)
    if horizon == "month_end":
        return today.replace(day=monthrange(today.year, today.month)[1])
    if horizon != "date":
        raise ValueError("invalid_horizon")
    try:
        end = date.fromisoformat(str(end_date or ""))
    except ValueError as exc:
        raise ValueError("invalid_horizon_date") from exc
    if not today <= end <= today + timedelta(days=MAX_HORIZON_DAYS):
        raise ValueError("invalid_horizon_date")
    return end


def calculate_cashflow(*, today: date, end: date | None, balance: Decimal | None,
                       reserve: Decimal, events: tuple[CashEvent, ...],
                       daily_pace: Decimal | None, currency: str) -> dict:
    balance = cash_amount(balance) if balance is not None else None
    reserve = cash_amount(reserve)
    daily_pace = cash_amount(daily_pace) if daily_pace is not None else None
    if reserve < 0 or (daily_pace is not None and daily_pace < 0):
        raise ValueError("invalid_amount")
    if end is not None and not today <= end <= today + timedelta(days=MAX_HORIZON_DAYS):
        raise ValueError("invalid_horizon_date")
    missing = []
    if balance is None:
        missing.append("balance")
    if end is None:
        missing.append("next_income_date")
    if daily_pace is None:
        missing.append("spending_history")
    # Only identical source occurrences are duplicates; equal amounts are not evidence.
    unique = {event.key: event for event in events}
    selected = [event for event in unique.values() if end is not None and event.due <= end
                and event.kind in {"income", "expense", "goal"}
                and not (event.kind == "income" and event.due < today)]
    expenses = sum((event.amount for event in selected if event.kind == "expense"), ZERO)
    income = sum((event.amount for event in selected if event.kind == "income"), ZERO)
    goals = sum((event.amount for event in selected if event.kind == "goal"), ZERO)
    days = (end - today).days + 1 if end else 0
    ordinary = (daily_pace * days).quantize(Decimal("0.01")) if daily_pace is not None else None
    available = None
    daily_allowance = None
    if not missing:
        available = max(ZERO, balance - reserve - goals - expenses - ordinary)
        daily_allowance = (max(ZERO, balance - reserve - goals - expenses) / days).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    trajectory = []
    exhaustion = None
    if balance is not None and end is not None:
        without_income = balance
        expected = balance
        daily_events: dict[date, list[CashEvent]] = {}
        for event in selected:
            daily_events.setdefault(max(event.due, today), []).append(event)
        for offset in range(days):
            day = today + timedelta(days=offset)
            due = daily_events.get(day, [])
            expense_today = sum((event.amount for event in due if event.kind == "expense"), ZERO)
            income_today = sum((event.amount for event in due if event.kind == "income"), ZERO)
            cost = expense_today + (daily_pace or ZERO)
            without_income -= cost
            expected += income_today - cost
            trajectory.append({"date": day, "without_income": without_income, "with_expected_income": expected})
            if daily_pace is not None and exhaustion is None and without_income <= 0:
                exhaustion = day
    return {
        "currency": currency, "as_of": today, "end_date": end, "approximate": True,
        "current_balance": balance, "protected_reserve": reserve, "goal_reserve": goals,
        "planned_expense": expenses, "expected_income": income, "ordinary_expense": ordinary,
        "can_spend": available, "daily_allowance": daily_allowance, "daily_pace": daily_pace,
        "exhaustion_without_income": exhaustion, "missing": missing,
        "trajectory": trajectory, "events": [
            {"date": event.due, "amount": event.amount, "kind": event.kind, "label": event.label, "overdue": event.overdue}
            for event in sorted(selected, key=lambda item: (item.due, item.key))
        ],
        "overdue_income_count": sum(event.kind == "income" and event.due < today for event in unique.values()),
    }


class CashflowRepository:
    def reminders(self, user_id: int, workspace_id: int | None, currency: str, personal: bool) -> list[dict]:
        rows = pg_fetchall(
            """SELECT r.id, r.title, r.rem_type, r.event_date, r.amount,
                      r.repeat_rule, r.repeat_interval_days,
                      ARRAY(SELECT e.event_date FROM public.user_reminder_events e
                            WHERE e.reminder_id=r.id AND e.user_id=r.user_id AND e.event_type='recorded')
                 FROM public.user_reminders r
                WHERE r.user_id=%s AND r.is_active=TRUE AND r.currency=%s
                  AND (r.workspace_id=%s OR (r.workspace_id IS NULL AND %s))
                ORDER BY r.id LIMIT 201""",
            (user_id, currency, workspace_id, personal),
        )
        if len(rows) > 200:
            raise ValueError("too_many_reminders")
        return [dict(zip(("id", "title", "rem_type", "event_date", "amount", "repeat_rule", "repeat_interval_days", "recorded_dates"), row)) for row in rows]

    def daily_pace(self, user_id: int, workspace_id: int | None, currency: str, default_currency: str, today: date) -> Decimal | None:
        scope, params = ForecastRepository()._scope(user_id, workspace_id)
        rows = pg_fetchall(
            f"""SELECT o.op_date,
                COALESCE(SUM(o.amount) FILTER (
                    WHERE o.type='Расходы' AND COALESCE(o.source,'')<>'reminder'
                      AND NOT EXISTS (SELECT 1 FROM public.goal_movements gm WHERE gm.linked_operation_id=o.id)
                ),0)
                FROM public.operations o
                WHERE {scope} AND COALESCE(o.currency,%s)=%s
                  AND o.op_date >= %s AND o.op_date < %s
                  AND ((o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций')
                       OR (o.type='noop' AND o.category='Без операций'))
                GROUP BY o.op_date ORDER BY o.op_date""",
            (*params, default_currency, currency, today - timedelta(days=60), today),
        )
        if len(rows) < 14 or Decimal(len(rows)) / Decimal((today - rows[0][0]).days) < Decimal("0.35"):
            return None
        return (sum((to_decimal_money(row[1]) for row in rows), ZERO) / len(rows)).quantize(Decimal("0.01"))

    def preview(self, *, user_id: int, workspace_id: int | None, currency: str, default_currency: str,
                today: date, horizon: str, end_date: str | None, balance: Decimal | None, reserve: Decimal,
                workspace_kind: str) -> dict:
        if workspace_kind not in {"personal", "legacy_personal"}:
            raise ValueError("personal_workspace_required")
        scan_end = today + timedelta(days=MAX_HORIZON_DAYS)
        if horizon != "next_income":
            scan_end = forecast_end(today, horizon, end_date, ())
        reminders = self.reminders(user_id, workspace_id, currency, True)
        events = reminder_occurrences(reminders, today=today, end=scan_end)
        end = forecast_end(today, horizon, end_date, events)
        if end is not None:
            # Use the existing goal schedule calculator, including remaining-target caps.
            goals = ForecastRepository()._goals(user_id, workspace_id, currency, ForecastPeriod("custom", today, end, today))
            events += tuple(CashEvent(f"goal:{goal.goal_id}:{goal.due_date}", goal.due_date, goal.amount, "goal", "Взнос в цель") for goal in goals)
        return calculate_cashflow(today=today, end=end, balance=balance, reserve=reserve, events=events,
                                  daily_pace=self.daily_pace(user_id, workspace_id, currency, default_currency, today), currency=currency)
