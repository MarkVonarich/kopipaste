from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from services.forecast_models import (
    CalibrationResult,
    ForecastObservation,
    QuantilePrediction,
    RobustRemainderModel,
    SeasonalRemainderModel,
    apply_calibration,
    bootstrap_scenarios,
    calibrate_quantiles,
    money,
    monotonic_prediction,
    quantile,
    rolling_origin_backtest,
    select_champion,
    load_trusted_model_artifact,
)
from services.goal_planning import ScheduleConfig, occurrences_between
from services.merchant_intelligence import merchant_key_sql, normalize_merchant_key


log = logging.getLogger(__name__)
FEATURE_SCHEMA_VERSION = "forecast-features-v1"
RISK_POLICY_VERSION = "downside-q80-v1"
DEFAULT_MODEL_VERSION = "personal-ensemble-v1"
QUALITY_TIERS = ("known_only", "limited", "personal", "strong", "calibrated")
MIN_INPUT_COVERAGE_RATIO = Decimal("0.35")
MIN_TARGET_COVERAGE_RATIO = Decimal("0.35")
TARGET_VALIDITY_POLICY_VERSION = "target-coverage-v1"
SUBSCRIPTION_MIN_CONFIDENCE = Decimal("0.60")
RECURRING_MIN_CONFIDENCE = Decimal("0.70")
SUBSCRIPTION_ELIGIBLE_STATUSES = ("detected", "confirmed")
RECURRING_ELIGIBLE_STATUS = "detected"


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
    identity_kind: str | None = None
    identity_hash: str | None = None


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
    input_operation_count: int
    input_tracked_days: int
    input_coverage_ratio: Decimal
    target_tracked_days: int = 1
    target_coverage_ratio: Decimal = Decimal("0.01")
    input_variable_expense: Decimal = Decimal("0.00")
    target_valid: bool = False
    target_validity_reason: str = "legacy_unassessed"


@dataclass(frozen=True)
class RegisteredChampion:
    model: Any
    family: str
    version: str
    currency: str
    calibration: CalibrationResult


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
    current_expense_count: int = 0
    current_expense_tracked_days: int = 0
    current_no_operation_marker_days: int = 0
    tracked_days: int = 0
    realized_variable_expense: Decimal = Decimal("0.00")
    default_currency: str = "RUB"
    timezone_name: str = "UTC"
    registered_champion: RegisteredChampion | None = None


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
    general_budget_current_remaining: Decimal | None
    general_budget_projected_remaining: Decimal | None
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


def safe_identity_hash(kind: str, value: str | None) -> str | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    return hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()


def commitment_facts(items: Iterable[KnownCommitment]) -> list[dict[str, Any]]:
    return [
        {
            "source": item.source,
            "source_id": item.source_key[:180],
            "due_date": item.due_date.isoformat(),
            "amount": str(money(item.amount)),
            "currency": item.currency,
            "identity_kind": item.identity_kind,
            "identity_hash": item.identity_hash,
        }
        for item in items
    ]


def goal_reserve_facts(items: Iterable[GoalContribution]) -> list[dict[str, Any]]:
    return [
        {
            "goal_id": item.goal_id,
            "due_date": item.due_date.isoformat(),
            "amount": str(money(item.amount)),
        }
        for item in items
    ]


def operation_is_deterministic(
    operation: dict[str, Any],
    known_facts: Iterable[dict[str, Any]],
    *,
    allow_source_markers: bool = True,
) -> bool:
    if bool(operation.get("goal_linked")):
        return True
    if allow_source_markers and (str(operation.get("source") or "") == "reminder" or bool(operation.get("pattern_linked"))):
        return True
    op_date = operation.get("op_date")
    op_currency = str(operation.get("currency") or "").upper()
    op_amount = money(operation.get("amount") or 0)
    merchant_hash = safe_identity_hash("merchant", normalize_merchant_key(str(operation.get("comment") or "")))
    category_hash = safe_identity_hash("category", str(operation.get("category") or ""))
    for fact in known_facts:
        try:
            due_date = date.fromisoformat(str(fact.get("due_date")))
        except (TypeError, ValueError):
            continue
        if due_date != op_date or str(fact.get("currency") or "").upper() != op_currency or money(fact.get("amount") or 0) != op_amount:
            continue
        identity_hash = str(fact.get("identity_hash") or "")
        identity_kind = str(fact.get("identity_kind") or "")
        if identity_hash and (
            (identity_kind == "merchant" and identity_hash == merchant_hash)
            or (identity_kind == "category" and identity_hash == category_hash)
        ):
            return True
    return False


def operation_matches_snapshot_currency(operation_currency: str | None, snapshot_currency: str, default_currency: str) -> bool:
    if operation_currency is None:
        return str(default_currency).upper() == str(snapshot_currency).upper()
    return str(operation_currency).upper() == str(snapshot_currency).upper()


def deterministic_currency_compatible(
    operation_currency: str | None,
    pattern_currency: str,
    default_currency: str,
) -> bool:
    resolved = operation_currency if operation_currency is not None else default_currency
    return str(resolved).upper() == str(pattern_currency).upper()


def evaluate_target_validity(
    *,
    horizon_days: int,
    expense_count: int,
    expense_tracked_days: int,
    no_operation_marker_days: int,
    variable_expense: Decimal,
) -> tuple[bool, str, Decimal]:
    horizon = max(0, int(horizon_days))
    expense_days = max(0, int(expense_tracked_days))
    marker_days = max(0, int(no_operation_marker_days))
    tracked = min(horizon, expense_days + marker_days)
    expenses = max(0, int(expense_count))
    if horizon <= 0:
        return False, "no_target_horizon", Decimal("0")
    coverage = min(Decimal("1"), Decimal(tracked) / Decimal(horizon))
    if tracked == 0:
        return False, "missing_future_tracking", coverage
    if coverage < MIN_TARGET_COVERAGE_RATIO:
        return False, "insufficient_future_coverage", coverage
    if money(variable_expense) > 0 and expenses == 0:
        return False, "inconsistent_future_evidence", coverage
    if expenses == 0:
        return True, "valid_tracked_zero", coverage
    return True, "valid_observed_activity", coverage


def subscription_eligibility_sql(alias: str = "sp") -> str:
    statuses = ",".join(f"'{value}'" for value in SUBSCRIPTION_ELIGIBLE_STATUSES)
    return (
        f"{alias}.status IN ({statuses}) "
        f"AND {alias}.confidence >= {SUBSCRIPTION_MIN_CONFIDENCE}"
    )


def recurring_eligibility_sql(alias: str = "rp") -> str:
    return f"{alias}.status='{RECURRING_ELIGIBLE_STATUS}' AND {alias}.confidence >= {RECURRING_MIN_CONFIDENCE}"


def subscription_pattern_eligible(status: str, confidence: Decimal) -> bool:
    return status in SUBSCRIPTION_ELIGIBLE_STATUSES and Decimal(str(confidence)) >= SUBSCRIPTION_MIN_CONFIDENCE


def recurring_pattern_eligible(status: str, confidence: Decimal) -> bool:
    return status == RECURRING_ELIGIBLE_STATUS and Decimal(str(confidence)) >= RECURRING_MIN_CONFIDENCE


def variable_daily_pace(inputs: ForecastInputs) -> Decimal:
    return money(inputs.realized_variable_expense / Decimal(max(1, inputs.tracked_days)))


def _valid_history_rows(inputs: ForecastInputs) -> list[HistoricalRemainder]:
    return [
        row for row in inputs.historical
        if row.input_coverage_ratio >= MIN_INPUT_COVERAGE_RATIO
        and row.input_operation_count > 0
        and row.target_valid
    ]


def _calibration_from_metadata(raw: Any) -> CalibrationResult:
    values = raw if isinstance(raw, dict) else {}
    offsets = values.get("offsets")
    sample_count = int(values.get("sample_count") or 0)
    if (
        values.get("state") != "calibrated"
        or sample_count < 12
        or not isinstance(offsets, (list, tuple))
        or len(offsets) != 3
        or values.get("empirical_coverage_80") is None
        or values.get("empirical_coverage_90") is None
    ):
        return CalibrationResult("insufficient", (Decimal("0.00"),) * 3, sample_count, None, None)
    return CalibrationResult(
        "calibrated",
        tuple(money(value) for value in offsets),
        sample_count,
        Decimal(str(values["empirical_coverage_80"])) if values.get("empirical_coverage_80") is not None else None,
        Decimal(str(values["empirical_coverage_90"])) if values.get("empirical_coverage_90") is not None else None,
    )


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
    valid = _valid_history_rows(inputs)
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
    rows = _valid_history_rows(inputs)
    return [
        ForecastObservation(
            snapshot_key=f"{inputs.workspace_id}:{inputs.currency}:{row.start.isoformat()}:{row.as_of.isoformat()}",
            as_of_ordinal=row.as_of.toordinal(),
            horizon_days=max(0, (row.end - row.as_of).days),
            elapsed_ratio=Decimal((row.as_of - row.start).days + 1) / Decimal(max(1, (row.end - row.start).days + 1)),
            realized_expense=money(row.input_variable_expense),
            recent_daily_pace=money(row.input_variable_expense / Decimal(max(1, row.input_tracked_days))),
            weekday=row.as_of.weekday(),
            cycle_day=(row.as_of - row.start).days + 1,
            operation_count=row.input_operation_count,
            coverage_ratio=row.input_coverage_ratio,
            target_remainder=money(row.remainder),
        )
        for row in rows
    ]


def _prediction(inputs: ForecastInputs) -> tuple[QuantilePrediction, str]:
    observations = _forecast_observations(inputs)
    values = [row.target_remainder for row in observations]
    target = ForecastObservation(
        snapshot_key="current",
        as_of_ordinal=inputs.period.as_of.toordinal(),
        horizon_days=inputs.period.horizon_days,
        elapsed_ratio=Decimal((inputs.period.as_of - inputs.period.start).days + 1) / Decimal(max(1, (inputs.period.end - inputs.period.start).days + 1)),
        realized_expense=money(inputs.realized_variable_expense),
        recent_daily_pace=variable_daily_pace(inputs),
        weekday=inputs.period.as_of.weekday(),
        cycle_day=(inputs.period.as_of - inputs.period.start).days + 1,
        operation_count=inputs.current_operation_count,
        coverage_ratio=Decimal(inputs.tracked_days) / Decimal(max(1, (inputs.period.as_of - inputs.period.start).days + 1)),
        target_remainder=Decimal("0.00"),
    )
    if not values:
        if inputs.registered_champion is not None and inputs.registered_champion.currency.upper() == inputs.currency.upper():
            champion = inputs.registered_champion
            try:
                registered = champion.model.predict(target)
                registered = QuantilePrediction(registered.q50, registered.q80, registered.q90, champion.family, champion.version)
                return apply_calibration(registered, champion.calibration), champion.calibration.state
            except Exception as exc:
                log.info("forecast_registered_model_fallback reason=%s", type(exc).__name__)
        return QuantilePrediction(Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), "known_only", "known-v1"), "insufficient"
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
    if inputs.registered_champion is not None and inputs.registered_champion.currency.upper() == inputs.currency.upper():
        champion = inputs.registered_champion
        try:
            registered = champion.model.predict(target)
            registered = QuantilePrediction(registered.q50, registered.q80, registered.q90, champion.family, champion.version)
            registered = apply_calibration(registered, champion.calibration)
            return registered, champion.calibration.state
        except Exception as exc:
            log.info("forecast_registered_model_fallback reason=%s", type(exc).__name__)
    return blended, calibration_state


def _fingerprint(inputs: ForecastInputs, prediction: QuantilePrediction, amount: Decimal) -> str:
    payload = {
        "user": inputs.user_id,
        "workspace": inputs.workspace_id,
        "currency": inputs.currency,
        "period": [inputs.period.start.isoformat(), inputs.period.end.isoformat(), inputs.period.as_of.isoformat()],
        "realized": [str(money(inputs.realized_income)), str(money(inputs.realized_expense))],
        "commitments": commitment_facts(inputs.commitments),
        "goals": [(item.goal_id, item.due_date.isoformat(), str(money(item.amount))) for item in inputs.goal_contributions],
        "history": [(
            item.start.isoformat(), str(money(item.remainder)), item.input_operation_count,
            item.input_tracked_days, str(item.input_coverage_ratio), item.target_tracked_days,
            str(item.target_coverage_ratio), str(money(item.input_variable_expense)),
            item.target_valid, item.target_validity_reason,
        ) for item in inputs.historical],
        "counts": [
            inputs.current_operation_count, inputs.current_expense_count,
            inputs.current_expense_tracked_days, inputs.current_no_operation_marker_days,
            inputs.tracked_days,
        ],
        "variable_realized": str(money(inputs.realized_variable_expense)),
        "general_budget": [str(money(inputs.general_budget_amount)) if inputs.general_budget_amount is not None else None, str(money(inputs.general_budget_spent))],
        "category_limits": sorted((key, str(money(value[0])), str(money(value[1]))) for key, value in inputs.category_limits.items()),
        "grouped_budgets": sorted((name, sorted(categories), str(money(total)), str(money(spent))) for name, categories, total, spent in inputs.grouped_budgets),
        "model": [prediction.family, prediction.version],
        "amount": str(money(amount)),
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "risk": RISK_POLICY_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def forecast_source_fingerprint(inputs: ForecastInputs) -> str:
    payload = {
        "scope": [inputs.user_id, inputs.workspace_id, inputs.currency, inputs.default_currency, inputs.timezone_name],
        "period": [inputs.period.key, inputs.period.start.isoformat(), inputs.period.end.isoformat(), inputs.period.as_of.isoformat()],
        "realized": [str(money(inputs.realized_income)), str(money(inputs.realized_expense)), str(money(inputs.realized_variable_expense))],
        "coverage": [
            inputs.current_operation_count, inputs.current_expense_count,
            inputs.current_expense_tracked_days, inputs.current_no_operation_marker_days,
            inputs.tracked_days,
        ],
        "commitments": commitment_facts(inputs.commitments),
        "goals": goal_reserve_facts(inputs.goal_contributions),
        "history": [(
            item.start.isoformat(), item.end.isoformat(), item.as_of.isoformat(),
            str(money(item.remainder)), item.input_operation_count, item.input_tracked_days,
            str(item.input_coverage_ratio), item.target_tracked_days, str(item.target_coverage_ratio),
            str(money(item.input_variable_expense)), item.target_valid, item.target_validity_reason,
        ) for item in inputs.historical],
        "budget": [str(money(inputs.general_budget_amount)) if inputs.general_budget_amount is not None else None, str(money(inputs.general_budget_spent))],
        "category_limits": sorted((key, str(money(total)), str(money(spent))) for key, (total, spent) in inputs.category_limits.items()),
        "grouped_budgets": sorted((name, sorted(categories), str(money(total)), str(money(spent))) for name, categories, total, spent in inputs.grouped_budgets),
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "risk_policy": RISK_POLICY_VERSION,
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
    prediction, measured_calibration_state = _prediction(inputs)
    prediction = monotonic_prediction(
        (max(Decimal("0.00"), prediction.q50), max(Decimal("0.00"), prediction.q80), max(Decimal("0.00"), prediction.q90)),
        prediction.family,
        prediction.version,
    )
    calibration_state = calibration_state or measured_calibration_state
    current_result = money(inputs.realized_income - inputs.realized_expense)
    commitments = money(sum((item.amount for item in inputs.commitments), Decimal("0.00")))
    goals = money(sum((item.amount for item in inputs.goal_contributions), Decimal("0.00")))
    variable_reserve = money(max(Decimal("0.00"), prediction.q80))
    raw_spendable = money(current_result - commitments - goals - variable_reserve)
    budget_current_remaining = None
    budget_projected_remaining = None
    if inputs.general_budget_amount is not None:
        budget_current_remaining = money(max(Decimal("0.00"), inputs.general_budget_amount - inputs.general_budget_spent))
        budget_projected_remaining = money(max(
            Decimal("0.00"),
            budget_current_remaining - commitments - variable_reserve,
        ))
        raw_spendable = min(raw_spendable, budget_projected_remaining)
    amount = money(max(Decimal("0.00"), raw_spendable))
    lower = money(max(Decimal("0.00"), current_result - commitments - goals - max(Decimal("0.00"), prediction.q90)))
    upper = money(max(Decimal("0.00"), current_result - commitments - goals - max(Decimal("0.00"), prediction.q50)))
    if budget_current_remaining is not None:
        lower_budget = money(max(Decimal("0.00"), budget_current_remaining - commitments - max(Decimal("0.00"), prediction.q90)))
        upper_budget = money(max(Decimal("0.00"), budget_current_remaining - commitments - max(Decimal("0.00"), prediction.q50)))
        lower, upper = min(lower, lower_budget), min(upper, upper_budget)
    quality, quality_label = _quality(inputs, calibration_state)
    reasons: list[dict[str, Any]] = []
    if commitments:
        reasons.append({"code": "known_commitments", "label": "Будущие обязательные платежи", "amount": commitments, "count": len(inputs.commitments)})
    if goals:
        reasons.append({"code": "goal_reserve", "label": "Защищено на цели", "amount": goals, "count": len(inputs.goal_contributions)})
    if variable_reserve:
        reasons.append({"code": "variable_spend", "label": "Прогноз обычных расходов", "amount": variable_reserve})
    if budget_projected_remaining is not None and amount == budget_projected_remaining:
        reasons.append({"code": "general_budget_binding", "label": "Ограничено общим бюджетом", "amount": budget_projected_remaining})
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
        general_budget_remaining=budget_projected_remaining,
        general_budget_current_remaining=budget_current_remaining,
        general_budget_projected_remaining=budget_projected_remaining,
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
        history_periods=len(_valid_history_rows(inputs)),
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
        "general_budget_current_remaining": forecast.general_budget_current_remaining,
        "general_budget_projected_remaining": forecast.general_budget_projected_remaining,
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
        proven_duplicate = any(
            existing.source != item.source
            and bool(existing.identity_kind)
            and existing.identity_kind == item.identity_kind
            and bool(existing.identity_hash)
            and existing.identity_hash == item.identity_hash
            and existing.due_date == item.due_date
            and existing.currency == item.currency
            and money(existing.amount) == money(item.amount)
            and priority.get(existing.source, 9) <= priority.get(item.source, 9)
            for existing in selected
        )
        if not proven_duplicate:
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.due_date, item.source, item.source_key)))


class ForecastRepository:
    def _deterministic_expense_sql(self, alias: str = "o", default_currency_ref: str = "forecast_ctx.default_currency") -> str:
        merchant = merchant_key_sql(f"{alias}.comment")
        resolved_currency = f"COALESCE({alias}.currency,{default_currency_ref})"
        subscription_eligible = subscription_eligibility_sql("sp")
        recurring_eligible = recurring_eligibility_sql("rp")
        return f"""(
            COALESCE({alias}.source,'')='reminder'
            OR EXISTS (
                SELECT 1 FROM public.goal_movements gm
                 WHERE gm.linked_operation_id={alias}.id
                   AND gm.movement_type IN ('initial','contribution')
            )
            OR EXISTS (
                SELECT 1 FROM public.subscription_patterns sp
                 WHERE sp.user_id={alias}.user_id
                   AND sp.workspace_id IS NOT DISTINCT FROM {alias}.workspace_id
                   AND sp.currency={resolved_currency}
                   AND {subscription_eligible}
                   AND (sp.last_operation_id={alias}.id OR (
                       sp.normalized_merchant={merchant}
                       AND sp.amount IS NOT NULL AND sp.amount={alias}.amount
                   ))
            )
            OR EXISTS (
                SELECT 1 FROM public.recurring_spend_patterns rp
                 WHERE rp.user_id={alias}.user_id
                   AND rp.workspace_id IS NOT DISTINCT FROM {alias}.workspace_id
                   AND rp.currency={resolved_currency}
                   AND {recurring_eligible}
                   AND rp.normalized_merchant={merchant}
                   AND lower(rp.category)=lower(COALESCE({alias}.category,''))
                   AND rp.average_amount={alias}.amount
            )
        )"""

    def _registered_champion(self, currency: str, as_of: date) -> RegisteredChampion | None:
        from settings import FORECAST_MODEL_DIR

        if not FORECAST_MODEL_DIR:
            return None
        exact_currency = str(currency).upper()
        try:
            rows = pg_fetchall(
                """
                SELECT model_family, model_version, artifact_path, artifact_sha256,
                       feature_schema_version, risk_policy_version, training_cutoff,
                       metrics, calibration
                 FROM public.forecast_model_registry
                 WHERE currency=%s AND status='champion'
                 ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (exact_currency,),
            )
            if not rows:
                return None
            family, version, artifact_path, checksum, feature_schema, risk_policy, cutoff, metrics, calibration = rows[0]
            metric_values = metrics if isinstance(metrics, dict) else {}
            if (
                feature_schema != FEATURE_SCHEMA_VERSION
                or risk_policy != RISK_POLICY_VERSION
                or cutoff is None
                or cutoff > as_of
                or not metric_values.get("guardrail_eligible")
                or int(metric_values.get("folds") or 0) < 1
            ):
                log.info("forecast_registered_model_fallback reason=registry_incompatible")
                return None
            model, metadata = load_trusted_model_artifact(
                FORECAST_MODEL_DIR,
                artifact_path,
                str(checksum),
                expected_feature_schema=FEATURE_SCHEMA_VERSION,
            )
            if (
                metadata.get("risk_policy") != RISK_POLICY_VERSION
                or metadata.get("model_family") != family
                or metadata.get("model_version") != version
                or str(metadata.get("currency") or "").upper() != exact_currency
            ):
                raise ValueError("model_artifact_metadata_mismatch")
            return RegisteredChampion(model, str(family), str(version), exact_currency, _calibration_from_metadata(calibration))
        except errors.UndefinedTable:
            return None
        except Exception as exc:
            log.info("forecast_registered_model_fallback reason=%s", type(exc).__name__)
            return None

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
        timezone_name: str = "UTC",
    ) -> ForecastInputs:
        scope, scope_params = self._scope(user_id, workspace_id)
        deterministic = self._deterministic_expense_sql("o")
        current = pg_fetchall(
            f"""
            WITH forecast_ctx AS (SELECT %s::text AS default_currency)
            SELECT COALESCE(SUM(o.amount) FILTER (WHERE o.type='Доходы' AND COALESCE(o.category,'')<>'Без операций'),0),
                   COALESCE(SUM(o.amount) FILTER (WHERE o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций'),0),
                   COUNT(*) FILTER (WHERE o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций' AND NOT {deterministic}),
                   COUNT(*) FILTER (WHERE o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций'),
                   COUNT(DISTINCT o.op_date) FILTER (
                       WHERE o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций'
                   ),
                   COUNT(DISTINCT o.op_date) FILTER (
                       WHERE o.type='noop' AND o.category='Без операций'
                   ),
                   COUNT(DISTINCT o.op_date) FILTER (
                       WHERE (o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций')
                          OR (o.type='noop' AND o.category='Без операций')
                   ),
                   COALESCE(SUM(o.amount) FILTER (
                       WHERE o.type='Расходы' AND COALESCE(o.category,'')<>'Без операций' AND NOT {deterministic}
                   ),0)
              FROM public.operations o
              CROSS JOIN forecast_ctx
             WHERE {scope}
               AND o.op_date BETWEEN %s AND %s
               AND COALESCE(o.currency,forecast_ctx.default_currency)=%s
            """,
            (default_currency, *scope_params, period.start, period.as_of, currency),
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
            current_expense_count=int(current[3] or 0),
            current_expense_tracked_days=int(current[4] or 0),
            current_no_operation_marker_days=int(current[5] or 0),
            tracked_days=int(current[6] or 0),
            realized_variable_expense=money(current[7]),
            default_currency=default_currency,
            timezone_name=timezone_name,
            registered_champion=self._registered_champion(currency, period.as_of),
        )

    def _history(self, user_id: int, workspace_id: int | None, currency: str, default_currency: str, period: ForecastPeriod) -> tuple[HistoricalRemainder, ...]:
        periods = comparable_periods(period)
        scope, params = self._scope(user_id, workspace_id)
        deterministic = self._deterministic_expense_sql("o")
        rows = pg_fetchall(
            f"""
            WITH forecast_ctx AS (SELECT %s::text AS default_currency)
            SELECT o.op_date,
                   COALESCE(SUM(o.amount) FILTER (
                       WHERE o.type='Расходы'
                         AND COALESCE(o.category,'')<>'Без операций'
                         AND NOT {deterministic}
                   ),0),
                   COUNT(*) FILTER (
                       WHERE o.type='Расходы'
                         AND COALESCE(o.category,'')<>'Без операций'
                         AND NOT {deterministic}
                   ),
                   COUNT(*) FILTER (
                       WHERE o.type='Расходы'
                         AND COALESCE(o.category,'')<>'Без операций'
                   ),
                   COUNT(*) FILTER (
                       WHERE o.type='noop' AND o.category='Без операций'
                   )
              FROM public.operations o
              CROSS JOIN forecast_ctx
             WHERE {scope}
               AND o.op_date BETWEEN %s AND %s
               AND COALESCE(o.currency,forecast_ctx.default_currency)=%s
             GROUP BY o.op_date
             ORDER BY o.op_date
            """,
            (default_currency, *params, periods[0][0], periods[-1][1], currency),
        )
        daily = {
            row[0]: (money(row[1]), int(row[2] or 0), int(row[3] or 0), int(row[4] or 0))
            for row in rows
        }
        values = []
        for start, end, as_of in periods:
            input_days = [day for day in daily if start <= day <= as_of]
            future_days = [day for day in daily if as_of < day <= end]
            remainder = sum((daily[day][0] for day in future_days), Decimal("0.00"))
            input_variable = sum((daily[day][0] for day in input_days), Decimal("0.00"))
            input_count = sum(daily[day][1] for day in input_days)
            input_tracked = len([day for day in input_days if daily[day][2] > 0 or daily[day][3] > 0])
            target_expense_days = len([day for day in future_days if daily[day][2] > 0])
            target_marker_days = len([day for day in future_days if daily[day][2] == 0 and daily[day][3] > 0])
            target_tracked = target_expense_days + target_marker_days
            elapsed_days = max(1, (as_of - start).days + 1)
            horizon_days = max(1, (end - as_of).days)
            input_coverage = min(Decimal("1"), Decimal(input_tracked) / Decimal(elapsed_days))
            target_coverage = min(Decimal("1"), Decimal(target_tracked) / Decimal(horizon_days))
            target_valid, target_reason, target_coverage = evaluate_target_validity(
                horizon_days=horizon_days,
                expense_count=sum(daily[day][2] for day in future_days),
                expense_tracked_days=target_expense_days,
                no_operation_marker_days=target_marker_days,
                variable_expense=money(remainder),
            )
            values.append(HistoricalRemainder(
                start,
                end,
                as_of,
                money(remainder),
                input_count,
                input_tracked,
                input_coverage,
                target_tracked,
                target_coverage,
                money(input_variable),
                target_valid,
                target_reason,
            ))
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
            items.append(KnownCommitment(
                "reminder", str(rid), due, money(amount), row_currency,
                "future_expense_reminder", "Будущий платёж",
                "category", safe_identity_hash("category", str(category or "")),
            ))
        subscription_eligible = subscription_eligibility_sql("sp")
        recurring_eligible = recurring_eligibility_sql("rp")
        pattern_rows = pg_fetchall(
            f"""
            SELECT 'subscription', id, next_expected_on, amount, currency, normalized_merchant
              FROM public.subscription_patterns sp
             WHERE sp.user_id=%s AND sp.workspace_id IS NOT DISTINCT FROM %s
               AND {subscription_eligible}
               AND sp.next_expected_on > %s AND sp.next_expected_on <= %s AND sp.currency=%s
            UNION ALL
            SELECT 'recurring', id, (metadata->>'next_expected_on')::date,
                   average_amount, currency, normalized_merchant
              FROM public.recurring_spend_patterns rp
             WHERE rp.user_id=%s AND rp.workspace_id IS NOT DISTINCT FROM %s
               AND {recurring_eligible}
               AND rp.metadata->>'next_expected_on' ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
               AND (rp.metadata->>'next_expected_on')::date > %s
               AND (rp.metadata->>'next_expected_on')::date <= %s AND rp.currency=%s
             ORDER BY 3, 1, 2 LIMIT 100
            """,
            (int(user_id), workspace_id, period.as_of, period.end, currency, int(user_id), workspace_id, period.as_of, period.end, currency),
        )
        for source, pid, due, amount, row_currency, merchant_key in pattern_rows:
            if amount is None or due is None:
                continue
            items.append(KnownCommitment(
                source, f"{source}:{pid}", due, money(amount), row_currency,
                "recurring_commitment", "Регулярный платёж",
                "merchant", safe_identity_hash("merchant", str(merchant_key or "")),
            ))
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
            "realized_variable_expense": str(inputs.realized_variable_expense),
            "operation_count": inputs.current_operation_count,
            "expense_count": inputs.current_expense_count,
            "expense_tracked_days": inputs.current_expense_tracked_days,
            "no_operation_marker_days": inputs.current_no_operation_marker_days,
            "tracked_days": inputs.tracked_days,
            "elapsed_days": max(1, (inputs.period.as_of - inputs.period.start).days + 1),
            "elapsed_ratio": str(Decimal((inputs.period.as_of - inputs.period.start).days + 1) / Decimal(max(1, (inputs.period.end - inputs.period.start).days + 1))),
            "cycle_day": (inputs.period.as_of - inputs.period.start).days + 1,
            "recent_daily_pace": str(variable_daily_pace(inputs)),
            "history_periods": forecast.history_periods,
            "commitment_count": forecast.known_commitment_count,
            "horizon_days": inputs.period.horizon_days,
        }
        source_fingerprint = forecast_source_fingerprint(inputs)
        known_facts = commitment_facts(inputs.commitments)
        goal_facts = goal_reserve_facts(inputs.goal_contributions)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.forecast_snapshots
                      (user_id, workspace_id, currency, period_key, period_start, period_end,
                       as_of_date, horizon_days, legacy_default_currency, timezone_name,
                       feature_schema_version, features, known_commitment_facts,
                       goal_reserve_facts, source_fingerprint)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, workspace_scope_key, currency, period_start, period_end, as_of_date, source_fingerprint)
                    DO UPDATE SET updated_at=now()
                    RETURNING id
                    """,
                    (
                        inputs.user_id, inputs.workspace_id, inputs.currency, inputs.period.key,
                        inputs.period.start, inputs.period.end, inputs.period.as_of,
                        inputs.period.horizon_days, inputs.default_currency, inputs.timezone_name,
                        FEATURE_SCHEMA_VERSION, Json(features), Json(known_facts), Json(goal_facts),
                        source_fingerprint,
                    ),
                )
                snapshot_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO public.forecast_predictions
                      (snapshot_id, model_family, model_version, risk_policy_version,
                       q50, q80, q90, calibration_state, known_commitments, goal_reserve,
                       general_budget_remaining, general_budget_current_remaining,
                       general_budget_projected_remaining, spendable_amount, expected_end_result,
                       risk_state, quality_tier, reasons, prediction_fingerprint)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (snapshot_id, prediction_fingerprint) DO NOTHING
                    """,
                    (
                        snapshot_id, forecast.model_family, forecast.model_version,
                        forecast.risk_policy_version, forecast.variable_q50, forecast.variable_q80,
                        forecast.variable_q90, forecast.calibration_state,
                        forecast.known_commitments, forecast.goal_reserve,
                        forecast.general_budget_remaining, forecast.general_budget_current_remaining,
                        forecast.general_budget_projected_remaining, forecast.amount,
                        forecast.expected_end_result, forecast.risk_state, forecast.quality_tier,
                        Json(list(forecast.reasons)), forecast.fingerprint,
                    ),
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
