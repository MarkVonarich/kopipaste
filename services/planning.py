from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from psycopg2 import errors

from db.database import pg_fetchall
from services.categories import category_key_sql, normalized_category_key
from services.goal_planning import (
    FREQUENCY_MONTHLY,
    FREQUENCY_NONE,
    FREQUENCY_SALARY_MONTHLY,
    FREQUENCY_SALARY_TWICE_MONTHLY,
    FREQUENCY_TWICE_MONTHLY,
    FREQUENCY_WEEKLY,
    ScheduleConfig,
    calculate_contribution_first,
    calculate_deadline_first,
)


PLANNING_KINDS = {"category_limit", "general_limit", "category_budget", "goal"}
PLANNING_PERIODS = {"week", "month"}
EXPENSE_TYPE = "Расходы"
INCOME_TYPE = "Доходы"
MONEY_STEP = Decimal("0.01")
MONTH_LABELS = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


class PlanningError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PlanningPeriod:
    start: date
    end: date
    label: str


@dataclass(frozen=True)
class PlanningHistoryPeriod:
    start_date: date
    end_date: date
    label: str
    amount: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    operation_count: int


@dataclass(frozen=True)
class PlanningConflict:
    kind: str
    severity: str
    title: str
    description: str
    entity_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True)
class PlanningRequest:
    user_id: int
    workspace_id: int | None
    kind: str
    currency: str
    period: str = "month"
    categories: tuple[str, ...] = ()
    editing_entity_id: str | None = None
    target_amount: Decimal | None = None
    current_amount: Decimal = Decimal("0.00")
    deadline: date | None = None
    frequency: str = FREQUENCY_NONE
    schedule_config: dict[str, Any] | None = None
    editing_goal_id: int | None = None


@dataclass(frozen=True)
class PlanningControl:
    kind: str
    entity_id: str
    title: str
    amount: Decimal
    currency: str
    period: str
    categories: tuple[str, ...] = ()
    workspace_id: int | None = None
    enabled: bool = True


def _money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _previous_month_start(value: date) -> date:
    return (value.replace(day=1) - timedelta(days=1)).replace(day=1)


def complete_periods(period: str, today: date, count: int = 4) -> tuple[PlanningPeriod, ...]:
    if period not in PLANNING_PERIODS:
        raise PlanningError("bad_planning_period")
    if count <= 0 or count > 12:
        raise PlanningError("bad_period_count")
    periods: list[PlanningPeriod] = []
    if period == "month":
        cursor = today.replace(day=1)
        for _ in range(count):
            cursor = _previous_month_start(cursor)
            end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            periods.append(PlanningPeriod(cursor, end, MONTH_LABELS[cursor.month]))
    else:
        cursor = today - timedelta(days=today.weekday())
        for _ in range(count):
            cursor -= timedelta(days=7)
            end = cursor + timedelta(days=6)
            periods.append(PlanningPeriod(cursor, end, f"{cursor.strftime('%d.%m')}–{end.strftime('%d.%m')}"))
    return tuple(reversed(periods))


def history_confidence(valid_period_count: int) -> str:
    if valid_period_count >= 4:
        return "good"
    if valid_period_count >= 2:
        return "limited"
    return "insufficient"


def arithmetic_mean(values: Iterable[Decimal]) -> Decimal | None:
    items = [_money(value) for value in values]
    if not items:
        return None
    return (sum(items, Decimal("0.00")) / Decimal(len(items))).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _workspace_scope(workspace_id: int | None, *, workspace_column: str, user_column: str) -> tuple[str, tuple[Any, ...]]:
    if workspace_id is None:
        return f"{workspace_column} IS NULL AND {user_column}=%s", ()
    return f"{workspace_column}=%s", (int(workspace_id),)


def aggregate_history(request: PlanningRequest, *, today: date) -> list[PlanningHistoryPeriod]:
    period_kind = "month" if request.kind == "goal" else request.period
    periods = complete_periods(period_kind, today)
    scope_sql, workspace_params = _workspace_scope(
        request.workspace_id,
        workspace_column="o.workspace_id",
        user_column="o.user_id",
    )
    scope_params: tuple[Any, ...] = workspace_params
    if request.workspace_id is None:
        scope_params = (int(request.user_id),)
    category_keys = tuple(dict.fromkeys(normalized_category_key(value) for value in request.categories if str(value).strip()))
    category_filter = ""
    category_params: tuple[Any, ...] = ()
    if request.kind in {"category_limit", "category_budget"}:
        if not category_keys:
            raise PlanningError("categories_required")
        category_filter = f"AND {category_key_sql('o.category')}=ANY(%s)"
        category_params = (list(category_keys),)
    grouping = "month" if period_kind == "month" else "week"
    rows = pg_fetchall(
        f"""
        SELECT date_trunc(%s, o.op_date)::date AS period_start,
               COUNT(*)::int AS operation_count,
               COALESCE(SUM(o.amount) FILTER (
                   WHERE o.type=%s {category_filter}
               ), 0) AS selected_expense,
               COALESCE(SUM(o.amount) FILTER (WHERE o.type=%s), 0) AS total_expense,
               COALESCE(SUM(o.amount) FILTER (WHERE o.type=%s), 0) AS total_income
          FROM public.operations o
         WHERE {scope_sql}
           AND o.op_date BETWEEN %s AND %s
           AND o.currency=%s
           AND COALESCE(o.category, '')<>'Без операций'
         GROUP BY date_trunc(%s, o.op_date)::date
         ORDER BY period_start
        """,
        (
            grouping,
            EXPENSE_TYPE,
            *category_params,
            EXPENSE_TYPE,
            INCOME_TYPE,
            *scope_params,
            periods[0].start,
            periods[-1].end,
            request.currency,
            grouping,
        ),
    )
    by_start = {row[0]: row for row in rows}
    history: list[PlanningHistoryPeriod] = []
    for period in periods:
        row = by_start.get(period.start)
        if not row or int(row[1] or 0) <= 0:
            continue
        selected_expense = _money(row[2] or 0)
        total_expense = _money(row[3] or 0)
        total_income = _money(row[4] or 0)
        amount = total_income - total_expense if request.kind == "goal" else selected_expense
        history.append(
            PlanningHistoryPeriod(
                start_date=period.start,
                end_date=period.end,
                label=period.label,
                amount=_money(amount),
                income=total_income,
                expense=total_expense,
                net=_money(total_income - total_expense),
                operation_count=int(row[1]),
            )
        )
    return history


def _load_controls(request: PlanningRequest) -> list[PlanningControl]:
    if request.workspace_id is None:
        category_scope = "workspace_id IS NULL AND user_id=%s"
        owner_scope = "workspace_id IS NULL AND owner_user_id=%s"
        params: tuple[Any, ...] = (request.user_id,)
    else:
        category_scope = "workspace_id=%s AND user_id=%s"
        owner_scope = "workspace_id=%s AND owner_user_id=%s"
        params = (request.workspace_id, request.user_id)
    controls: list[PlanningControl] = []
    try:
        category_rows = pg_fetchall(
            f"SELECT period, category, amount, currency FROM public.category_limits WHERE {category_scope} AND period=%s AND currency=%s",
            (*params, request.period, request.currency),
        )
        general_rows = pg_fetchall(
            f"SELECT id, name, amount, currency, period_type, enabled FROM public.general_spending_limits WHERE {owner_scope} AND period_type=%s AND currency=%s AND enabled=true",
            (*params, request.period, request.currency),
        )
        budget_rows = pg_fetchall(
            f"""
            SELECT g.id, g.name, g.amount, g.currency, g.period_type, g.enabled,
                   COALESCE(array_agg(m.category_name) FILTER (WHERE m.category_name IS NOT NULL), '{{}}')
              FROM public.category_budget_groups g
              LEFT JOIN public.category_budget_group_members m ON m.group_id=g.id
             WHERE {owner_scope.replace('workspace_id', 'g.workspace_id').replace('owner_user_id', 'g.owner_user_id')}
               AND g.period_type=%s AND g.currency=%s AND g.enabled=true
             GROUP BY g.id
            """,
            (*params, request.period, request.currency),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return []
    for period, category, amount, currency in category_rows:
        controls.append(
            PlanningControl(
                "category_limit",
                f"category:{period}:{category}",
                str(category),
                _money(amount),
                str(currency),
                str(period),
                (str(category),),
                request.workspace_id,
            )
        )
    for control_id, title, amount, currency, period, enabled in general_rows:
        controls.append(
            PlanningControl(
                "general_limit",
                f"general:{int(control_id)}",
                str(title),
                _money(amount),
                str(currency),
                str(period),
                workspace_id=request.workspace_id,
                enabled=bool(enabled),
            )
        )
    for control_id, title, amount, currency, period, enabled, categories in budget_rows:
        controls.append(
            PlanningControl(
                "category_budget",
                f"budget:{int(control_id)}",
                str(title),
                _money(amount),
                str(currency),
                str(period),
                tuple(str(value) for value in categories or ()),
                request.workspace_id,
                bool(enabled),
            )
        )
    return controls


def analyze_conflicts(
    request: PlanningRequest,
    controls: Iterable[PlanningControl],
    recommendation: Decimal | None,
) -> list[PlanningConflict]:
    selected_keys = {normalized_category_key(value) for value in request.categories}
    conflicts: list[PlanningConflict] = []
    for control in controls:
        if not control.enabled or control.workspace_id != request.workspace_id:
            continue
        if control.currency != request.currency or control.period != request.period:
            continue
        if request.editing_entity_id and control.entity_id == request.editing_entity_id:
            continue
        control_keys = {normalized_category_key(value) for value in control.categories}
        if control.kind == "general_limit" and recommendation is not None and recommendation > control.amount:
            conflicts.append(
                PlanningConflict(
                    "above_general_limit",
                    "warning",
                    "Выше общего лимита",
                    "Предлагаемая сумма выше действующего общего лимита.",
                    control.entity_id,
                    control.amount,
                    control.currency,
                )
            )
        if control.kind == "category_limit" and selected_keys & control_keys:
            conflicts.append(
                PlanningConflict(
                    "existing_category_limit",
                    "info",
                    "Уже есть лимит категории",
                    f"Для категории «{control.title}» уже действует отдельный лимит.",
                    control.entity_id,
                    control.amount,
                    control.currency,
                )
            )
        if control.kind == "category_budget" and selected_keys & control_keys:
            overlaps = sorted(selected_keys & control_keys)
            conflicts.append(
                PlanningConflict(
                    "grouped_budget_overlap",
                    "info",
                    "Категории пересекаются",
                    f"Выбрано категорий, которые уже входят в бюджет «{control.title}»: {len(overlaps)}.",
                    control.entity_id,
                )
            )
    return conflicts


def _schedule(frequency: str, config: dict[str, Any] | None) -> ScheduleConfig:
    config = config or {}
    return ScheduleConfig(
        frequency=frequency,
        day=int(config["day"]) if config.get("day") is not None else None,
        days=tuple(int(value) for value in config.get("days") or ()),
        weekday=int(config["weekday"]) if config.get("weekday") is not None else None,
        salary_payments_per_month=int(config["salary_payments_per_month"]) if config.get("salary_payments_per_month") else None,
    )


def monthly_frequency_factor(frequency: str) -> Decimal | None:
    if frequency in {FREQUENCY_MONTHLY, FREQUENCY_SALARY_MONTHLY}:
        return Decimal("1")
    if frequency in {FREQUENCY_TWICE_MONTHLY, FREQUENCY_SALARY_TWICE_MONTHLY}:
        return Decimal("2")
    if frequency == FREQUENCY_WEEKLY:
        return Decimal("52") / Decimal("12")
    return None


def goal_required_pace(request: PlanningRequest, *, today: date) -> dict[str, Any]:
    if request.target_amount is None or request.deadline is None:
        return {"amount": None, "monthly_amount": None, "reason": "deadline_required"}
    factor = monthly_frequency_factor(request.frequency)
    if factor is None:
        return {"amount": None, "monthly_amount": None, "reason": "schedule_required"}
    plan = calculate_deadline_first(
        target_amount=request.target_amount,
        current_balance=request.current_amount,
        deadline=request.deadline,
        schedule=_schedule(request.frequency, request.schedule_config),
        today=today,
    )
    if not plan.feasible or plan.recommended_amount is None:
        return {"amount": None, "monthly_amount": None, "reason": plan.reason or "pace_unavailable"}
    return {
        "amount": _money(plan.recommended_amount),
        "monthly_amount": _money(plan.recommended_amount * factor),
        "occurrence_count": plan.occurrence_count,
        "next_occurrence": plan.next_occurrence,
        "reason": None,
    }


def _monthly_goal_commitment(row: tuple[Any, ...], *, today: date) -> Decimal | None:
    _, target, current, deadline, strategy, frequency, comfortable, planned, config = row
    factor = monthly_frequency_factor(str(frequency))
    if factor is None:
        return None
    amount = planned if planned is not None else comfortable
    if amount is None and str(strategy) == "deadline" and deadline is not None:
        plan = calculate_deadline_first(
            target_amount=_money(target),
            current_balance=_money(current),
            deadline=deadline,
            schedule=_schedule(str(frequency), config or {}),
            today=today,
        )
        amount = plan.recommended_amount if plan.feasible else None
    if amount is None or _money(amount) <= 0:
        return None
    return _money(_money(amount) * factor)


def other_goal_commitments(request: PlanningRequest, *, today: date) -> tuple[Decimal, int]:
    if request.workspace_id is None:
        scope = "workspace_id IS NULL AND owner_user_id=%s"
        params: tuple[Any, ...] = (request.user_id, request.currency)
    else:
        scope = "workspace_id=%s AND owner_user_id=%s"
        params = (request.workspace_id, request.user_id, request.currency)
    rows = pg_fetchall(
        f"""
        SELECT id, target_amount, current_balance, deadline, strategy, frequency,
               comfortable_amount, planned_contribution_amount, schedule_config
          FROM public.financial_goals
         WHERE {scope}
           AND currency=%s
           AND status='active'
           AND (%s::bigint IS NULL OR id<>%s)
         ORDER BY id
         LIMIT 100
        """,
        (*params, request.editing_goal_id, request.editing_goal_id),
    )
    commitments = [value for row in rows if (value := _monthly_goal_commitment(row, today=today)) is not None]
    return _money(sum(commitments, Decimal("0.00"))), len(commitments)


def _comfortable_completion(request: PlanningRequest, monthly_available: Decimal, *, today: date) -> date | None:
    factor = monthly_frequency_factor(request.frequency)
    if factor is None or monthly_available <= 0 or request.target_amount is None:
        return None
    contribution = _money(monthly_available / factor)
    if contribution <= 0:
        return None
    plan = calculate_contribution_first(
        target_amount=request.target_amount,
        current_balance=request.current_amount,
        comfortable_amount=contribution,
        schedule=_schedule(request.frequency, request.schedule_config),
        today=today,
    )
    return plan.projected_completion_date if plan.feasible else None


def _goal_result(request: PlanningRequest, history: list[PlanningHistoryPeriod], *, today: date) -> dict[str, Any]:
    confidence = history_confidence(len(history))
    average_net = arithmetic_mean(item.net for item in history)
    required = goal_required_pace(request, today=today)
    commitment_total, commitment_count = other_goal_commitments(request, today=today)
    comfortable_monthly = None
    comfortable_contribution = None
    if confidence != "insufficient" and average_net is not None:
        comfortable_monthly = _money(max(Decimal("0.00"), average_net - commitment_total))
        factor = monthly_frequency_factor(request.frequency)
        if factor is not None:
            comfortable_contribution = _money(comfortable_monthly / factor)
    required_monthly = required.get("monthly_amount")
    if comfortable_monthly is None:
        feasibility = "insufficient_history"
        gap = None
    elif required_monthly is None:
        feasibility = "required_pace_unavailable"
        gap = None
    elif required_monthly <= comfortable_monthly:
        feasibility = "compatible"
        gap = Decimal("0.00")
    else:
        feasibility = "stretched"
        gap = _money(required_monthly - comfortable_monthly)
    return {
        "history_confidence": confidence,
        "baseline_average": average_net,
        "recommendation": comfortable_contribution if comfortable_contribution is not None and comfortable_contribution > 0 else None,
        "required_pace": required,
        "comfortable_pace": {
            "amount": comfortable_contribution,
            "monthly_amount": comfortable_monthly,
            "average_monthly_net": average_net,
            "other_goal_commitments": commitment_total,
            "commitment_count": commitment_count,
        },
        "feasibility": feasibility,
        "gap": gap,
        "comfortable_completion_date": _comfortable_completion(request, comfortable_monthly, today=today) if comfortable_monthly is not None else None,
        "conflicts": [],
    }


def calculate_planning_estimate(request: PlanningRequest, *, today: date) -> dict[str, Any]:
    if request.kind not in PLANNING_KINDS:
        raise PlanningError("bad_planning_kind")
    if request.kind != "goal" and request.period not in PLANNING_PERIODS:
        raise PlanningError("bad_planning_period")
    history = aggregate_history(request, today=today)
    if request.kind == "goal":
        result = _goal_result(request, history, today=today)
    else:
        confidence = history_confidence(len(history))
        baseline = arithmetic_mean(item.amount for item in history)
        recommendation = baseline if confidence != "insufficient" and baseline is not None and baseline > 0 else None
        controls = _load_controls(request)
        result = {
            "history_confidence": confidence,
            "baseline_average": baseline,
            "recommendation": recommendation,
            "required_pace": None,
            "comfortable_pace": None,
            "feasibility": None,
            "gap": None,
            "comfortable_completion_date": None,
            "conflicts": [asdict(item) for item in analyze_conflicts(request, controls, recommendation)],
        }
    return {
        "kind": request.kind,
        "scope": {
            "workspace_id": request.workspace_id,
            "currency": request.currency,
            "period": "month" if request.kind == "goal" else request.period,
            "categories": list(dict.fromkeys(normalized_category_key(value) for value in request.categories)),
        },
        "history": [asdict(item) for item in history],
        "periods_requested": 4,
        "valid_periods": len(history),
        **result,
    }
