from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from services.forecast_models import (
    ForecastObservation,
    QuantilePrediction,
    RobustRemainderModel,
    SeasonalRemainderModel,
    apply_calibration,
    bootstrap_scenarios,
    calibrate_quantiles,
    money,
    quantile,
    rolling_origin_backtest,
    select_champion,
)
from services.goal_planning import ScheduleConfig, occurrences_between


log = logging.getLogger(__name__)
FEATURE_SCHEMA_VERSION = "forecast-features-v1"
RISK_POLICY_VERSION = "downside-q80-v1"
DEFAULT_MODEL_VERSION = "personal-ensemble-v1"
QUALITY_TIERS = ("known_only", "limited", "personal", "strong", "calibrated")


@dataclass(frozen=True)
class ForecastPeriod:
    key: str
    start: date
    end: date
    as_of: date

    @property
    def horizon_days(self) -> int:
        return max(0, (self.end - self.as_of).days)


@dataclass(frozen=True)
class KnownCommitment:
    source: str
    source_key: str
    due_date: date
    amount: Decimal
    currency: str
    reason_code: str
    public_label: str
    baseline_overlap: bool = False


@dataclass(frozen=True)
class GoalContribution:
    goal_id: int
    due_date: date
    amount: Decimal


@dataclass(frozen=True)
class HistoricalRemainder:
    start: date
    end: date
    as_of: date
    remainder: Decimal
    operation_count: int
    tracked_days: int
    coverage_ratio: Decimal


@dataclass(frozen=True)
class ForecastInputs:
    user_id: int
    workspace_id: int | None
    workspace_kind: str
    currency: str
    period: ForecastPeriod
    realized_income: Decimal
    realized_expense: Decimal
    commitments: tuple[KnownCommitment, ...] = ()
    expected_income: Decimal = Decimal("0.00")
    goal_contributions: tuple[GoalContribution, ...] = ()
    historical: tuple[HistoricalRemainder, ...] = ()
    general_budget_amount: Decimal | None = None
    general_budget_spent: Decimal = Decimal("0.00")
    category_limits: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    grouped_budgets: tuple[tuple[str, tuple[str, ...], Decimal, Decimal], ...] = ()
    current_operation_count: int = 0
    tracked_days: int = 0


@dataclass(frozen=True)
class ForecastUnavailable:
    available: bool
    code: str
    title: str
    description: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpendableForecast:
    available: bool
    amount: Decimal
    currency: str
    approximate: bool
    period_label: str
    current_result: Decimal
    known_commitments: Decimal
    known_commitment_count: int
    expected_income: Decimal
    goal_reserve: Decimal
    variable_q50: Decimal
    variable_q80: Decimal
    variable_q90: Decimal
    variable_reserve: Decimal
    general_budget_remaining: Decimal | None
    expected_end_result: Decimal
    lower_spendable: Decimal
    upper_spendable: Decimal
    quality_tier: str
    quality_label: str
    risk_state: str
    model_family: str
    model_version: str
    risk_policy_version: str
    calibration_state: str
    history_periods: int
    reasons: tuple[dict[str, Any], ...]
    trajectory: tuple[dict[str, Any], ...]
    fingerprint: str
    feedback: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def unavailable(code: str) -> ForecastUnavailable:
    states = {
        "workspace_all": ("Выберите пространство", "Выберите пространство для прогноза."),
        "currency_required": ("Выберите валюту", "Прогноз доступен для одной валюты."),
        "period_completed": ("Период уже завершён", "Выберите текущий период с будущими днями."),
        "period_future": ("Период ещё не начался", "Ожидаемый доход не считается доступными деньгами."),
    }
    title, description = states[code]
    return ForecastUnavailable(False, code, title, description)


def comparable_periods(period: ForecastPeriod, count: int = 18) -> tuple[tuple[date, date, date], ...]:
    out: list[tuple[date, date, date]] = []
    elapsed = max(0, (period.as_of - period.start).days)
    if period.key == "current_month":
        cursor = period.start
        for _ in range(count):
            prior_end = cursor - timedelta(days=1)
            prior_start = prior_end.replace(day=1)
            prior_as_of = min(prior_start + timedelta(days=elapsed), prior_end)
            out.append((prior_start, prior_end, prior_as_of))
            cursor = prior_start
    elif period.key == "current_week":
        for offset in range(1, count + 1):
            start = period.start - timedelta(days=7 * offset)
            out.append((start, start + timedelta(days=6), min(start + timedelta(days=elapsed), start + timedelta(days=6))))
    else:
        length = (period.end - period.start).days + 1
        for offset in range(1, count + 1):
            end = period.start - timedelta(days=1 + length * (offset - 1))
            start = end - timedelta(days=length - 1)
            out.append((start, end, min(start + timedelta(days=elapsed), end)))
    return tuple(reversed(out))


def _quality(inputs: ForecastInputs, calibration_state: str = "insufficient") -> tuple[str, str]:
    valid = [row for row in inputs.historical if row.coverage_ratio >= Decimal("0.35") and row.operation_count > 0]
    if calibration_state == "calibrated" and len(valid) >= 8:
        return "calibrated", "Калиброванный прогноз"
    if len(valid) >= 6 and inputs.current_operation_count >= 8:
        return "strong", "Высокая опора на историю"
    if len(valid) >= 3:
        return "personal", "По вашей истории"
    if valid:
        return "limited", "Истории пока мало"
    return "known_only", "По известным платежам"


def _forecast_observations(inputs: ForecastInputs) -> list[ForecastObservation]:
    rows = [row for row in inputs.historical if row.coverage_ratio >= Decimal("0.35") and row.operation_count > 0]
    return [
        ForecastObservation(
            snapshot_key=f"{inputs.workspace_id}:{inputs.currency}:{row.start.isoformat()}:{row.as_of.isoformat()}",
            as_of_ordinal=row.as_of.toordinal(),
            horizon_days=max(0, (row.end - row.as_of).days),
            elapsed_ratio=Decimal((row.as_of - row.start).days + 1) / Decimal(max(1, (row.end - row.start).days + 1)),
            realized_expense=Decimal("0.00"),
            recent_daily_pace=Decimal("0.00"),
            weekday=row.as_of.weekday(),
            cycle_day=(row.as_of - row.start).days + 1,
            operation_count=row.operation_count,
            coverage_ratio=row.coverage_ratio,
            target_remainder=money(row.remainder),
        )
        for row in rows
    ]


def _prediction(inputs: ForecastInputs) -> tuple[QuantilePrediction, str]:
    observations = _forecast_observations(inputs)
    values = [row.target_remainder for row in observations]
    if not values:
        return QuantilePrediction(Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), "known_only", "known-v1"), "insufficient"
    target = ForecastObservation(
        snapshot_key="current",
        as_of_ordinal=inputs.period.as_of.toordinal(),
        horizon_days=inputs.period.horizon_days,
        elapsed_ratio=Decimal((inputs.period.as_of - inputs.period.start).days + 1) / Decimal(max(1, (inputs.period.end - inputs.period.start).days + 1)),
        realized_expense=money(inputs.realized_expense),
        recent_daily_pace=money(inputs.realized_expense / Decimal(max(1, inputs.tracked_days))),
        weekday=inputs.period.as_of.weekday(),
        cycle_day=(inputs.period.as_of - inputs.period.start).days + 1,
        operation_count=inputs.current_operation_count,
        coverage_ratio=Decimal(inputs.tracked_days) / Decimal(max(1, (inputs.period.as_of - inputs.period.start).days + 1)),
        target_remainder=Decimal("0.00"),
    )
    champion_model = RobustRemainderModel().fit(observations)
    backtest = None
    if len(observations) >= 7:
        results = [
            rolling_origin_backtest(factory, observations)
            for factory in (RobustRemainderModel, SeasonalRemainderModel)
        ]
        backtest = select_champion(results)
        champion_model = (
            SeasonalRemainderModel() if backtest.family == SeasonalRemainderModel.family else RobustRemainderModel()
        ).fit(observations)
    selected = champion_model.predict(target)
    scenarios = bootstrap_scenarios(
        values,
        fingerprint=f"{inputs.user_id}:{inputs.workspace_id}:{inputs.currency}:{inputs.period.as_of.isoformat()}",
    )
    bootstrap = QuantilePrediction(
        quantile(scenarios, Decimal("0.50")),
        quantile(scenarios, Decimal("0.80")),
        quantile(scenarios, Decimal("0.90")),
        "bootstrap_scenarios",
        "bootstrap-v1",
    )
    blended = QuantilePrediction(
        money((selected.q50 + bootstrap.q50) / 2),
        max(selected.q80, bootstrap.q80),
        max(selected.q90, bootstrap.q90),
        "personal_ensemble",
        DEFAULT_MODEL_VERSION,
    )
    calibration_state = "insufficient"
    if backtest is not None:
        calibration = calibrate_quantiles(list(backtest.predictions), list(backtest.actuals), minimum_samples=8)
        blended = apply_calibration(blended, calibration)
        calibration_state = calibration.state
    return blended, calibration_state


def _fingerprint(inputs: ForecastInputs, prediction: QuantilePrediction, amount: Decimal) -> str:
    payload = {
        "user": inputs.user_id,
        "workspace": inputs.workspace_id,
        "currency": inputs.currency,
        "period": [inputs.period.start.isoformat(), inputs.period.end.isoformat(), inputs.period.as_of.isoformat()],
        "realized": [str(money(inputs.realized_income)), str(money(inputs.realized_expense))],
        "commitments": [(item.source, item.source_key, item.due_date.isoformat(), str(money(item.amount)), item.baseline_overlap) for item in inputs.commitments],
        "goals": [(item.goal_id, item.due_date.isoformat(), str(money(item.amount))) for item in inputs.goal_contributions],
        "history": [(item.start.isoformat(), str(money(item.remainder)), item.operation_count) for item in inputs.historical],
        "model": [prediction.family, prediction.version],
        "amount": str(money(amount)),
        "risk": RISK_POLICY_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _trajectory(inputs: ForecastInputs, prediction: QuantilePrediction) -> tuple[dict[str, Any], ...]:
    horizon = inputs.period.horizon_days
    if horizon <= 0:
        return ()
    points = min(8, horizon)
    result = []
    for index in range(points + 1):
        ratio = Decimal(index) / Decimal(points)
        day = inputs.period.as_of + timedelta(days=round(horizon * float(ratio)))
        result.append({
            "date": day,
            "expected_expense": money(inputs.realized_expense + prediction.q50 * ratio),
            "upper_expense": money(inputs.realized_expense + prediction.q90 * ratio),
        })
    return tuple(result)


def calculate_spendable(inputs: ForecastInputs, *, calibration_state: str | None = None) -> SpendableForecast:
    gross_prediction, measured_calibration_state = _prediction(inputs)
    calibration_state = calibration_state or measured_calibration_state
    current_result = money(inputs.realized_income - inputs.realized_expense)
    commitments = money(sum((item.amount for item in inputs.commitments), Decimal("0.00")))
    baseline_overlap = money(sum((item.amount for item in inputs.commitments if item.baseline_overlap), Decimal("0.00")))
    prediction = QuantilePrediction(
        money(max(Decimal("0.00"), gross_prediction.q50 - baseline_overlap)),
        money(max(Decimal("0.00"), gross_prediction.q80 - baseline_overlap)),
        money(max(Decimal("0.00"), gross_prediction.q90 - baseline_overlap)),
        gross_prediction.family,
        gross_prediction.version,
    )
    goals = money(sum((item.amount for item in inputs.goal_contributions), Decimal("0.00")))
    variable_reserve = money(max(Decimal("0.00"), prediction.q80))
    raw_spendable = money(current_result - commitments - goals - variable_reserve)
    budget_remaining = None
    if inputs.general_budget_amount is not None:
        budget_remaining = money(max(Decimal("0.00"), inputs.general_budget_amount - inputs.general_budget_spent))
        raw_spendable = min(raw_spendable, budget_remaining)
    amount = money(max(Decimal("0.00"), raw_spendable))
    lower = money(max(Decimal("0.00"), current_result - commitments - goals - max(Decimal("0.00"), prediction.q90)))
    upper = money(max(Decimal("0.00"), current_result - commitments - goals - max(Decimal("0.00"), prediction.q50)))
    if budget_remaining is not None:
        lower, upper = min(lower, budget_remaining), min(upper, budget_remaining)
    quality, quality_label = _quality(inputs, calibration_state)
    reasons: list[dict[str, Any]] = []
    if commitments:
        reasons.append({"code": "known_commitments", "label": "Будущие обязательные платежи", "amount": commitments, "count": len(inputs.commitments)})
    if goals:
        reasons.append({"code": "goal_reserve", "label": "Защищено на цели", "amount": goals, "count": len(inputs.goal_contributions)})
    if variable_reserve:
        reasons.append({"code": "variable_spend", "label": "Прогноз обычных расходов", "amount": variable_reserve})
    if budget_remaining is not None and amount == budget_remaining:
        reasons.append({"code": "general_budget_binding", "label": "Ограничено общим бюджетом", "amount": budget_remaining})
    if quality in {"known_only", "limited"}:
        reasons.append({"code": "limited_history", "label": "Истории пока мало — прогноз больше опирается на известные платежи."})
    risk_state = "attention" if amount <= 0 or lower <= 0 else "watch" if variable_reserve > max(current_result, Decimal("0.00")) * Decimal("0.60") else "normal"
    fingerprint = _fingerprint(inputs, prediction, amount)
    return SpendableForecast(
        available=True,
        amount=amount,
        currency=inputs.currency,
        approximate=True,
        period_label="до конца периода",
        current_result=current_result,
        known_commitments=commitments,
        known_commitment_count=len(inputs.commitments),
        expected_income=money(inputs.expected_income),
        goal_reserve=goals,
        variable_q50=prediction.q50,
        variable_q80=prediction.q80,
        variable_q90=prediction.q90,
        variable_reserve=variable_reserve,
        general_budget_remaining=budget_remaining,
        expected_end_result=money(current_result - commitments - goals - prediction.q50),
        lower_spendable=lower,
        upper_spendable=upper,
        quality_tier=quality,
        quality_label=quality_label,
        risk_state=risk_state,
        model_family=prediction.family,
        model_version=prediction.version,
        risk_policy_version=RISK_POLICY_VERSION,
        calibration_state=calibration_state,
        history_periods=len([item for item in inputs.historical if item.operation_count > 0]),
        reasons=tuple(reasons[:4]),
        trajectory=_trajectory(inputs, prediction),
        fingerprint=fingerprint,
    )


def can_spend(inputs: ForecastInputs, forecast: SpendableForecast, amount: Decimal, category: str | None = None) -> dict[str, Any]:
    purchase = money(amount)
    if purchase <= 0:
        raise ValueError("amount_must_be_positive")
    after = money(max(Decimal("0.00"), forecast.amount - purchase))
    constraints: list[dict[str, Any]] = []
    category_remaining = None
    grouped_remaining = None
    key = (category or "").strip().casefold()
    if key and key in inputs.category_limits:
        limit_amount, spent = inputs.category_limits[key]
        category_remaining = money(max(Decimal("0.00"), limit_amount - spent))
        constraints.append({"kind": "category_limit", "remaining": category_remaining})
    if key:
        matching = [money(max(Decimal("0.00"), total - spent)) for _name, categories, total, spent in inputs.grouped_budgets if key in {value.casefold() for value in categories}]
        if matching:
            grouped_remaining = min(matching)
            constraints.append({"kind": "grouped_budget", "remaining": grouped_remaining})
    hard_remaining = min([forecast.amount, *[item["remaining"] for item in constraints]])
    if forecast.quality_tier in {"known_only", "limited"} and purchase <= hard_remaining:
        verdict = "insufficient_data"
    elif purchase > hard_remaining:
        verdict = "does_not_fit"
    elif purchase >= hard_remaining * Decimal("0.80") or after <= forecast.variable_q90 - forecast.variable_q80:
        verdict = "borderline"
    else:
        verdict = "fits"
    return {
        "verdict": verdict,
        "amount_before": forecast.amount,
        "projected_spendable_after": after,
        "general_budget_remaining": forecast.general_budget_remaining,
        "category_limit_remaining": category_remaining,
        "grouped_budget_remaining": grouped_remaining,
        "goal_reserve": forecast.goal_reserve,
        "risk_state_before": forecast.risk_state,
        "risk_state_after": "attention" if after <= 0 else "watch" if after < forecast.lower_spendable else forecast.risk_state,
        "reasons": list(forecast.reasons[:3]),
    }


def explain_forecast_change(forecast: SpendableForecast, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if not previous:
        return None
    previous_amount = money(previous.get("amount") or 0)
    delta = money(forecast.amount - previous_amount)
    reason_codes: list[str] = []
    previous_commitments = money(previous.get("known_commitments") or 0)
    previous_goals = money(previous.get("goal_reserve") or 0)
    previous_variable = money(previous.get("variable_reserve") or 0)
    if forecast.known_commitments > previous_commitments:
        reason_codes.append("new_commitment")
    elif forecast.known_commitments < previous_commitments:
        reason_codes.append("commitment_paid")
    if forecast.goal_reserve != previous_goals:
        reason_codes.append("goal_reserve_changed")
    if forecast.variable_reserve > previous_variable:
        reason_codes.append("variable_spend_above_forecast")
    elif forecast.variable_reserve < previous_variable:
        reason_codes.append("variable_spend_below_forecast")
    if forecast.model_version != previous.get("model_version") and not reason_codes:
        reason_codes.append("model_update")
    return {
        "previous_amount": previous_amount,
        "delta": delta,
        "reason_codes": reason_codes[:3],
    }


def deduplicate_commitments(items: Iterable[KnownCommitment]) -> tuple[KnownCommitment, ...]:
    priority = {"reminder": 0, "subscription": 1, "recurring": 2}
    selected: list[KnownCommitment] = []
    for item in sorted(items, key=lambda value: (value.due_date, priority.get(value.source, 9), value.source_key)):
        matching_higher_priority = [
            index
            for index, existing in enumerate(selected)
            if existing.source != item.source
            and existing.due_date == item.due_date
            and existing.currency == item.currency
            and money(existing.amount) == money(item.amount)
            and priority.get(existing.source, 9) < priority.get(item.source, 9)
        ]
        if matching_higher_priority:
            if item.baseline_overlap:
                index = matching_higher_priority[0]
                selected[index] = replace(selected[index], baseline_overlap=True)
        else:
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.due_date, item.source, item.source_key)))


class ForecastRepository:
    def _scope(self, user_id: int, workspace_id: int | None) -> tuple[str, tuple[Any, ...]]:
        if workspace_id is None:
            return "o.workspace_id IS NULL AND o.user_id=%s", (int(user_id),)
        return "o.workspace_id=%s", (int(workspace_id),)

    def load_inputs(
        self,
        *,
        user_id: int,
        workspace_id: int | None,
        workspace_kind: str,
        currency: str,
        period: ForecastPeriod,
        default_currency: str,
    ) -> ForecastInputs:
        scope, scope_params = self._scope(user_id, workspace_id)
        current = pg_fetchall(
            f"""
            SELECT COALESCE(SUM(o.amount) FILTER (WHERE o.type='Доходы'),0),
                   COALESCE(SUM(o.amount) FILTER (WHERE o.type='Расходы'),0),
                   COUNT(*) FILTER (WHERE o.type IN ('Доходы','Расходы')),
                   COUNT(DISTINCT o.op_date)
              FROM public.operations o
             WHERE {scope}
               AND o.op_date BETWEEN %s AND %s
               AND COALESCE(o.currency,%s)=%s
               AND COALESCE(o.category,'')<>'Без операций'
            """,
            (*scope_params, period.start, period.as_of, default_currency, currency),
        )[0]
        history = self._history(user_id, workspace_id, currency, default_currency, period)
        commitments, expected_income = self._commitments(user_id, workspace_id, workspace_kind, currency, period)
        goals = self._goals(user_id, workspace_id, currency, period)
        general_amount, general_spent = self._general_budget(user_id, workspace_id, currency, period, money(current[1]))
        category_limits, grouped = self._category_controls(user_id, workspace_id, currency, period, scope, scope_params, default_currency)
        return ForecastInputs(
            user_id=int(user_id),
            workspace_id=workspace_id,
            workspace_kind=workspace_kind,
            currency=currency,
            period=period,
            realized_income=money(current[0]),
            realized_expense=money(current[1]),
            commitments=commitments,
            expected_income=expected_income,
            goal_contributions=goals,
            historical=history,
            general_budget_amount=general_amount,
            general_budget_spent=general_spent,
            category_limits=category_limits,
            grouped_budgets=grouped,
            current_operation_count=int(current[2] or 0),
            tracked_days=int(current[3] or 0),
        )

    def _history(self, user_id: int, workspace_id: int | None, currency: str, default_currency: str, period: ForecastPeriod) -> tuple[HistoricalRemainder, ...]:
        periods = comparable_periods(period)
        scope, params = self._scope(user_id, workspace_id)
        rows = pg_fetchall(
            f"""
            SELECT o.op_date,
                   COALESCE(SUM(o.amount) FILTER (WHERE o.type='Расходы'),0),
                   COUNT(*) FILTER (WHERE o.type='Расходы')
              FROM public.operations o
             WHERE {scope}
               AND o.op_date BETWEEN %s AND %s
               AND COALESCE(o.currency,%s)=%s
               AND COALESCE(o.category,'')<>'Без операций'
             GROUP BY o.op_date
             ORDER BY o.op_date
            """,
            (*params, periods[0][0], periods[-1][1], default_currency, currency),
        )
        daily = {row[0]: (money(row[1]), int(row[2] or 0)) for row in rows}
        values = []
        for start, end, as_of in periods:
            future_days = [day for day in daily if as_of < day <= end]
            all_days = [day for day in daily if start <= day <= end]
            remainder = sum((daily[day][0] for day in future_days), Decimal("0.00"))
            count = sum(daily[day][1] for day in all_days)
            tracked = len(all_days)
            elapsed_days = max(1, (as_of - start).days + 1)
            coverage = min(Decimal("1"), Decimal(tracked) / Decimal(elapsed_days))
            values.append(HistoricalRemainder(start, end, as_of, money(remainder), count, tracked, coverage))
        return tuple(values)

    def _commitments(self, user_id: int, workspace_id: int | None, workspace_kind: str, currency: str, period: ForecastPeriod) -> tuple[tuple[KnownCommitment, ...], Decimal]:
        include_legacy = workspace_id is None or workspace_kind in {"personal", "legacy_personal"}
        reminder_rows = pg_fetchall(
            """
            SELECT r.id, r.rem_type, r.event_date, r.amount, r.currency, r.category
              FROM public.user_reminders r
             WHERE r.user_id=%s AND r.is_active=TRUE
               AND (r.workspace_id=%s OR (r.workspace_id IS NULL AND %s))
               AND r.event_date > %s AND r.event_date <= %s
               AND r.currency=%s
               AND NOT EXISTS (
                   SELECT 1 FROM public.user_reminder_events e
                    WHERE e.reminder_id=r.id AND e.user_id=r.user_id
                      AND e.event_date=r.event_date AND e.event_type='recorded'
               )
             ORDER BY r.event_date, r.id LIMIT 100
            """,
            (int(user_id), workspace_id, include_legacy, period.as_of, period.end, currency),
        )
        items: list[KnownCommitment] = []
        expected_income = Decimal("0.00")
        for rid, rem_type, due, amount, row_currency, category in reminder_rows:
            if rem_type == "Доходы":
                expected_income += money(amount)
                continue
            if rem_type != "Расходы":
                continue
            items.append(KnownCommitment("reminder", str(rid), due, money(amount), row_currency, "future_expense_reminder", "Будущий платёж"))
        pattern_rows = pg_fetchall(
            """
            SELECT 'subscription', id, next_expected_on, amount, currency, normalized_merchant
              FROM public.subscription_patterns
             WHERE user_id=%s AND workspace_id IS NOT DISTINCT FROM %s
               AND status IN ('detected','confirmed') AND confidence >= 0.60
               AND next_expected_on > %s AND next_expected_on <= %s AND currency=%s
            UNION ALL
            SELECT 'recurring', id, (metadata->>'next_expected_on')::date,
                   average_amount, currency, normalized_merchant
              FROM public.recurring_spend_patterns
             WHERE user_id=%s AND workspace_id IS NOT DISTINCT FROM %s
               AND status='detected' AND confidence >= 0.70
               AND metadata->>'next_expected_on' ~ '^\\d{4}-\\d{2}-\\d{2}$'
               AND (metadata->>'next_expected_on')::date > %s
               AND (metadata->>'next_expected_on')::date <= %s AND currency=%s
             ORDER BY 3, 1, 2 LIMIT 100
            """,
            (int(user_id), workspace_id, period.as_of, period.end, currency, int(user_id), workspace_id, period.as_of, period.end, currency),
        )
        for source, pid, due, amount, row_currency, merchant_key in pattern_rows:
            if amount is None or due is None:
                continue
            items.append(KnownCommitment(source, f"{source}:{pid}:{merchant_key}", due, money(amount), row_currency, "recurring_commitment", "Регулярный платёж", True))
        return deduplicate_commitments(items), money(expected_income)

    def _goals(self, user_id: int, workspace_id: int | None, currency: str, period: ForecastPeriod) -> tuple[GoalContribution, ...]:
        rows = pg_fetchall(
            """
            SELECT id, target_amount, current_balance, strategy, frequency,
                   comfortable_amount, planned_contribution_amount, schedule_config
              FROM public.financial_goals
             WHERE owner_user_id=%s AND workspace_id IS NOT DISTINCT FROM %s
               AND currency=%s AND status='active'
             ORDER BY id LIMIT 100
            """,
            (int(user_id), workspace_id, currency),
        )
        values: list[GoalContribution] = []
        for goal_id, target, current, _strategy, frequency, comfortable, planned, config in rows:
            remaining = max(Decimal("0.00"), money(target) - money(current))
            amount = money(planned if planned is not None else comfortable or 0)
            if remaining <= 0 or amount <= 0:
                continue
            config = config or {}
            schedule = ScheduleConfig(
                frequency=str(frequency or "none"),
                day=int(config.get("day") or 1) if config.get("day") is not None else None,
                days=tuple(int(value) for value in config.get("days") or ()),
                weekday=int(config["weekday"]) if config.get("weekday") is not None else None,
                salary_payments_per_month=config.get("salary_payments_per_month"),
            )
            for due in occurrences_between(period.as_of + timedelta(days=1), period.end, schedule):
                reserved = min(amount, remaining)
                if reserved <= 0:
                    break
                values.append(GoalContribution(int(goal_id), due, reserved))
                remaining -= reserved
        return tuple(values)

    def _general_budget(self, user_id: int, workspace_id: int | None, currency: str, period: ForecastPeriod, current_expense: Decimal) -> tuple[Decimal | None, Decimal]:
        period_type = "week" if period.key == "current_week" else "month" if period.key == "current_month" else None
        if period_type is None:
            return None, current_expense
        rows = pg_fetchall(
            """
            SELECT amount FROM public.general_spending_limits
             WHERE owner_user_id=%s AND workspace_id IS NOT DISTINCT FROM %s
               AND currency=%s AND enabled=TRUE AND period_type=%s
             ORDER BY updated_at DESC, id DESC LIMIT 1
            """,
            (int(user_id), workspace_id, currency, period_type),
        )
        return (money(rows[0][0]) if rows else None), current_expense

    def _category_controls(self, user_id: int, workspace_id: int | None, currency: str, period: ForecastPeriod, scope: str, scope_params: tuple[Any, ...], default_currency: str) -> tuple[dict[str, tuple[Decimal, Decimal]], tuple[tuple[str, tuple[str, ...], Decimal, Decimal], ...]]:
        period_type = "week" if period.key == "current_week" else "month"
        limit_rows = pg_fetchall(
            f"""
            SELECT c.category, c.amount,
                   COALESCE(SUM(o.amount) FILTER (WHERE o.type='Расходы'),0)
              FROM public.category_limits c
              LEFT JOIN public.operations o ON {scope}
                   AND lower(o.category)=lower(c.category)
                   AND o.op_date BETWEEN %s AND %s
                   AND COALESCE(o.currency,%s)=c.currency
             WHERE c.user_id=%s AND c.workspace_id IS NOT DISTINCT FROM %s
               AND c.currency=%s AND c.period=%s
             GROUP BY c.category, c.amount
            """,
            (*scope_params, period.start, period.as_of, default_currency, int(user_id), workspace_id, currency, period_type),
        )
        limits = {str(category).casefold(): (money(amount), money(spent)) for category, amount, spent in limit_rows}
        group_rows = pg_fetchall(
            f"""
            SELECT g.name,
                   g.amount,
                   array_agg(m.category_name ORDER BY m.category_name),
                   COALESCE((
                       SELECT SUM(o.amount)
                         FROM public.operations o
                         JOIN public.category_budget_group_members spent_member
                           ON spent_member.group_id=g.id
                          AND lower(o.category)=lower(spent_member.category_name)
                        WHERE {scope}
                          AND o.type='Расходы'
                          AND o.op_date BETWEEN %s AND %s
                          AND COALESCE(o.currency,%s)=%s
                   ),0)
              FROM public.category_budget_groups g
              JOIN public.category_budget_group_members m ON m.group_id=g.id
             WHERE g.owner_user_id=%s AND g.workspace_id IS NOT DISTINCT FROM %s
               AND g.currency=%s AND g.enabled=TRUE AND g.period_type=%s
             GROUP BY g.id, g.name, g.amount ORDER BY g.id LIMIT 100
            """,
            (*scope_params, period.start, period.as_of, default_currency, currency, int(user_id), workspace_id, currency, period_type),
        )
        grouped = [
            (str(name), tuple(str(value) for value in categories or ()), money(amount), money(spent))
            for name, amount, categories, spent in group_rows
        ]
        return limits, tuple(grouped)

    def persist_prediction(self, inputs: ForecastInputs, forecast: SpendableForecast) -> None:
        features = {
            "realized_income": str(inputs.realized_income),
            "realized_expense": str(inputs.realized_expense),
            "operation_count": inputs.current_operation_count,
            "tracked_days": inputs.tracked_days,
            "elapsed_days": max(1, (inputs.period.as_of - inputs.period.start).days + 1),
            "elapsed_ratio": str(Decimal((inputs.period.as_of - inputs.period.start).days + 1) / Decimal(max(1, (inputs.period.end - inputs.period.start).days + 1))),
            "cycle_day": (inputs.period.as_of - inputs.period.start).days + 1,
            "recent_daily_pace": str(money(inputs.realized_expense / Decimal(max(1, inputs.tracked_days)))),
            "history_periods": forecast.history_periods,
            "commitment_count": forecast.known_commitment_count,
            "horizon_days": inputs.period.horizon_days,
        }
        source_fingerprint = hashlib.sha256(json.dumps(features, sort_keys=True).encode("utf-8")).hexdigest()
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.forecast_snapshots
                      (user_id, workspace_id, currency, period_key, period_start, period_end,
                       as_of_date, horizon_days, feature_schema_version, features, source_fingerprint)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, workspace_scope_key, currency, period_start, period_end, as_of_date, source_fingerprint)
                    DO UPDATE SET updated_at=now()
                    RETURNING id
                    """,
                    (inputs.user_id, inputs.workspace_id, inputs.currency, inputs.period.key, inputs.period.start, inputs.period.end, inputs.period.as_of, inputs.period.horizon_days, FEATURE_SCHEMA_VERSION, Json(features), source_fingerprint),
                )
                snapshot_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO public.forecast_predictions
                      (snapshot_id, model_family, model_version, risk_policy_version,
                       q50, q80, q90, calibration_state, known_commitments, goal_reserve,
                       general_budget_remaining, spendable_amount, expected_end_result,
                       risk_state, quality_tier, reasons, prediction_fingerprint)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (snapshot_id, prediction_fingerprint) DO NOTHING
                    """,
                    (snapshot_id, forecast.model_family, forecast.model_version, forecast.risk_policy_version, forecast.variable_q50, forecast.variable_q80, forecast.variable_q90, forecast.calibration_state, forecast.known_commitments, forecast.goal_reserve, forecast.general_budget_remaining, forecast.amount, forecast.expected_end_result, forecast.risk_state, forecast.quality_tier, Json(list(forecast.reasons)), forecast.fingerprint),
                )
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
        except Exception as exc:
            conn.rollback()
            log.info("forecast_snapshot_write_failed reason=%s", type(exc).__name__)
        finally:
            conn.close()

    def previous_prediction(self, inputs: ForecastInputs, forecast: SpendableForecast) -> dict[str, Any] | None:
        try:
            rows = pg_fetchall(
                """
                SELECT p.spendable_amount,
                       p.known_commitments,
                       p.goal_reserve,
                       p.q80,
                       p.model_version
                  FROM public.forecast_predictions p
                  JOIN public.forecast_snapshots s ON s.id=p.snapshot_id
                 WHERE s.user_id=%s
                   AND s.workspace_scope_key=COALESCE(%s::bigint,0)
                   AND s.currency=%s
                   AND s.period_start=%s
                   AND s.period_end=%s
                   AND s.invalidated_at IS NULL
                   AND p.prediction_fingerprint<>%s
                 ORDER BY s.as_of_date DESC, p.created_at DESC, p.id DESC
                 LIMIT 1
                """,
                (inputs.user_id, inputs.workspace_id, inputs.currency, inputs.period.start, inputs.period.end, forecast.fingerprint),
            )
        except errors.UndefinedTable:
            return None
        except Exception as exc:
            log.info("forecast_previous_read_failed reason=%s", type(exc).__name__)
            return None
        if not rows:
            return None
        row = rows[0]
        return {
            "amount": money(row[0]),
            "known_commitments": money(row[1]),
            "goal_reserve": money(row[2]),
            "variable_reserve": money(row[3]),
            "model_version": str(row[4]),
        }


def record_forecast_feedback(user_id: int, workspace_id: int | None, fingerprint: str, feedback_type: str) -> bool:
    fingerprint = str(fingerprint or "").strip().lower()
    if (
        feedback_type not in {"useful", "not_useful"}
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError("invalid_forecast_feedback")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.forecast_feedback
                  (user_id, workspace_id, forecast_fingerprint, feedback_type)
                SELECT %s,%s,%s,%s
                  FROM public.forecast_predictions p
                  JOIN public.forecast_snapshots s ON s.id=p.snapshot_id
                 WHERE s.user_id=%s
                   AND s.workspace_scope_key=COALESCE(%s::bigint,0)
                   AND p.prediction_fingerprint=%s
                   AND s.invalidated_at IS NULL
                 LIMIT 1
                ON CONFLICT (user_id, workspace_scope_key, forecast_fingerprint)
                DO NOTHING
                """,
                (int(user_id), workspace_id, fingerprint, feedback_type, int(user_id), workspace_id, fingerprint),
            )
            created = cur.rowcount == 1
        conn.commit()
        return created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
