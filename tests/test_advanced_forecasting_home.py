from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI
from services.experiments import assign_variant, exposure_properties
from services.forecast_models import (
    BacktestMetrics,
    BacktestResult,
    CalibrationResult,
    ForecastObservation,
    PooledQuantileGBDTModel,
    QuantilePrediction,
    RobustRemainderModel,
    apply_calibration,
    bootstrap_scenarios,
    calibrate_quantiles,
    load_model_artifact,
    load_trusted_model_artifact,
    metrics_json,
    money,
    rolling_origin_backtest,
    save_model_artifact,
    select_champion,
)
from services.forecast_training import (
    backtest_from_snapshots,
    backtest_candidates,
    ensure_safe_training_dsn,
    extract_training_observations,
    finalize_forecast_outcomes,
    register_model,
    synthetic_observations,
    train_from_snapshots,
)
from services.forecasting import (
    ForecastInputs,
    ForecastPeriod,
    ForecastRepository,
    GoalContribution,
    HistoricalRemainder,
    KnownCommitment,
    RegisteredChampion,
    calculate_spendable,
    can_spend,
    comparable_periods,
    deduplicate_commitments,
    deterministic_currency_compatible,
    evaluate_target_validity,
    explain_forecast_change,
    record_forecast_feedback,
    forecast_source_fingerprint,
    operation_is_deterministic,
    operation_matches_snapshot_currency,
    safe_identity_hash,
)
from services.insights import (
    CategoryAggregate,
    InsightSnapshot,
    LimitAggregate,
    PeriodRef,
    detect_candidates,
    detect_forecast_candidates,
    detect_limit_pace,
    detect_projection_risks,
    rank_candidates,
)


TODAY = date(2026, 8, 13)
PERIOD = ForecastPeriod("current_month", date(2026, 8, 1), date(2026, 8, 31), TODAY)


def history(value: str, index: int = 0, *, coverage: str = "0.80", count: int = 8) -> HistoricalRemainder:
    start = date(2026, 1, 1) + (date(2026, 2, 1) - date(2026, 1, 1)) * 0
    start = start.replace(month=1 + index)
    end = start.replace(day=28)
    return HistoricalRemainder(
        start, end, start.replace(day=13), Decimal(value), count, 10,
        Decimal(coverage), 10, Decimal("0.67"), Decimal("100"), True,
        "valid_observed_activity",
    )


def inputs(**overrides) -> ForecastInputs:
    values = dict(
        user_id=42,
        workspace_id=10,
        workspace_kind="group",
        currency="RUB",
        period=PERIOD,
        realized_income=Decimal("1000.00"),
        realized_expense=Decimal("200.00"),
        current_operation_count=10,
        tracked_days=10,
    )
    values.update(overrides)
    return ForecastInputs(**values)


def metric(*, pinball: str, mae: str = "10", breach: str = "0.10") -> BacktestMetrics:
    return BacktestMetrics(
        folds=5,
        mae=Decimal(mae),
        mase=Decimal("1"),
        pinball_q50=Decimal(pinball),
        pinball_q80=Decimal(pinball),
        pinball_q90=Decimal(pinball),
        coverage_q80=Decimal("0.80"),
        coverage_q90=Decimal("0.90"),
        interval_width=Decimal("20"),
        breach_rate=Decimal(breach),
    )


def result(family: str, *, pinball: str, breach: str) -> BacktestResult:
    prediction = QuantilePrediction(Decimal("10"), Decimal("20"), Decimal("30"), family, "v1")
    return BacktestResult(family, "v1", metric(pinball=pinball, breach=breach), (prediction,), (Decimal("15"),))


def insight_snapshot(forecast: dict) -> InsightSnapshot:
    return InsightSnapshot(
        user_id=42,
        workspace_id=10,
        workspace_kind="group",
        currency="RUB",
        period=PeriodRef("current_month", date(2026, 8, 1), date(2026, 8, 31)),
        comparison_period=PeriodRef("previous_month", date(2026, 7, 1), date(2026, 7, 31)),
        current_total=Decimal("5000"),
        previous_total=Decimal("4500"),
        current_count=10,
        previous_count=10,
        categories=(CategoryAggregate("food", "Food", Decimal("3000"), Decimal("2500"), 6, 5),),
        merchants=(),
        forecast=forecast,
    )


def test_workspace_all_returns_explicit_unavailable_state():
    tx = type("Tx", (), {"all_scope": True})()
    state = MiniAppAPI()._overview_spendable(MiniAppAPI().request(42), {}, tx)
    assert state == {
        "available": False,
        "code": "workspace_all",
        "title": "Выберите пространство",
        "description": "Выберите пространство для прогноза.",
    }


def test_repository_scope_isolates_legacy_user_and_concrete_workspace():
    repository = ForecastRepository()
    assert repository._scope(42, None) == ("o.workspace_id IS NULL AND o.user_id=%s", (42,))
    assert repository._scope(42, 10) == ("o.workspace_id=%s", (10,))


def test_realized_income_and_expense_form_resource():
    forecast = calculate_spendable(inputs(realized_income=Decimal("900"), realized_expense=Decimal("250")))
    assert forecast.current_result == Decimal("650.00")
    assert forecast.amount == Decimal("650.00")


def test_expected_future_income_is_excluded_from_primary_spendable():
    forecast = calculate_spendable(inputs(expected_income=Decimal("5000")))
    assert forecast.expected_income == Decimal("5000.00")
    assert forecast.amount == Decimal("800.00")


def test_negative_realized_result_has_zero_floor():
    forecast = calculate_spendable(inputs(realized_income=Decimal("100"), realized_expense=Decimal("300")))
    assert forecast.current_result == Decimal("-200.00")
    assert forecast.amount == Decimal("0.00")


def test_known_commitments_and_goal_reserve_apply_once():
    commitment = KnownCommitment("reminder", "7", date(2026, 8, 20), Decimal("100"), "RUB", "future", "Платёж")
    goal = GoalContribution(3, date(2026, 8, 25), Decimal("50"))
    forecast = calculate_spendable(inputs(commitments=(commitment,), goal_contributions=(goal,)))
    assert forecast.amount == Decimal("650.00")
    assert forecast.expected_end_result == Decimal("650.00")


def test_general_budget_caps_spendable_without_subtracting_it():
    forecast = calculate_spendable(inputs(general_budget_amount=Decimal("500"), general_budget_spent=Decimal("450")))
    assert forecast.general_budget_remaining == Decimal("50.00")
    assert forecast.amount == Decimal("50.00")


def test_projected_general_budget_reserves_commitments_and_variable_q80():
    commitment = KnownCommitment(
        "reminder", "rent", date(2026, 8, 20), Decimal("20000"), "RUB", "future", "Rent",
    )
    model_inputs = inputs(
        realized_income=Decimal("100000"), realized_expense=Decimal("10000"),
        commitments=(commitment,),
        historical=(history("20000", 0), history("20000", 1), history("20000", 2)),
        general_budget_amount=Decimal("50000"), general_budget_spent=Decimal("10000"),
    )
    forecast = calculate_spendable(model_inputs)
    assert forecast.general_budget_current_remaining == Decimal("40000.00")
    assert forecast.general_budget_projected_remaining == Decimal("0.00")
    assert forecast.general_budget_remaining == Decimal("0.00")
    assert forecast.amount == Decimal("0.00")
    assert can_spend(model_inputs, forecast, Decimal("1"))["verdict"] == "does_not_fit"


def test_projected_general_budget_reserves_variable_forecast_without_double_subtracting_resource():
    forecast = calculate_spendable(inputs(
        realized_income=Decimal("100000"), realized_expense=Decimal("10000"),
        historical=(history("20000", 0), history("20000", 1), history("20000", 2)),
        general_budget_amount=Decimal("50000"), general_budget_spent=Decimal("10000"),
    ))
    assert forecast.general_budget_current_remaining == Decimal("40000.00")
    assert forecast.general_budget_projected_remaining == Decimal("20000.00")
    assert forecast.amount == Decimal("20000.00")


def test_planned_goal_reserve_is_not_invented_as_general_budget_expense():
    forecast = calculate_spendable(inputs(
        realized_income=Decimal("100000"), realized_expense=Decimal("10000"),
        goal_contributions=(GoalContribution(7, date(2026, 8, 20), Decimal("30000")),),
        general_budget_amount=Decimal("50000"), general_budget_spent=Decimal("10000"),
    ))
    assert forecast.general_budget_projected_remaining == Decimal("40000.00")
    assert forecast.amount == Decimal("40000.00")


def test_category_limit_does_not_reserve_global_spendable():
    forecast = calculate_spendable(inputs(category_limits={"food": (Decimal("100"), Decimal("90"))}))
    assert forecast.amount == Decimal("800.00")


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [("10", "fits"), ("85", "borderline"), ("101", "does_not_fit")],
)
def test_can_spend_verdicts_with_personal_history(purchase: str, expected: str):
    model_inputs = inputs(
        realized_income=Decimal("100"),
        realized_expense=Decimal("0"),
        historical=(history("0", 0), history("0", 1), history("0", 2)),
    )
    forecast = calculate_spendable(model_inputs)
    assert can_spend(model_inputs, forecast, Decimal(purchase))["verdict"] == expected


def test_can_spend_is_honest_when_history_is_insufficient():
    model_inputs = inputs(realized_income=Decimal("100"), realized_expense=Decimal("0"))
    assert can_spend(model_inputs, calculate_spendable(model_inputs), Decimal("10"))["verdict"] == "insufficient_data"


def test_can_spend_api_rejects_removed_purchase_date(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    with pytest.raises(Exception) as raised:
        api.forecast_can_spend(api.request(42), {"purchase_date": "2026-08-20"})
    assert getattr(raised.value, "code", None) == "unsupported_forecast_purchase_date"


def test_category_limit_constrains_only_category_can_spend():
    model_inputs = inputs(
        realized_income=Decimal("100"), realized_expense=Decimal("0"),
        historical=(history("0", 0), history("0", 1), history("0", 2)),
        category_limits={"food": (Decimal("50"), Decimal("45"))},
    )
    outcome = can_spend(model_inputs, calculate_spendable(model_inputs), Decimal("10"), "Food")
    assert outcome["verdict"] == "does_not_fit"
    assert outcome["category_limit_remaining"] == Decimal("5.00")


def test_grouped_budget_constrains_matching_category():
    model_inputs = inputs(
        realized_income=Decimal("100"), realized_expense=Decimal("0"),
        historical=(history("0", 0), history("0", 1), history("0", 2)),
        grouped_budgets=(("Food", ("Food", "Cafe"), Decimal("60"), Decimal("55")),),
    )
    outcome = can_spend(model_inputs, calculate_spendable(model_inputs), Decimal("10"), "Cafe")
    assert outcome["verdict"] == "does_not_fit"
    assert outcome["grouped_budget_remaining"] == Decimal("5.00")


def test_commitment_dedup_keeps_unrelated_equal_payments_and_suppresses_strong_identity_duplicate():
    due = date(2026, 8, 20)
    values = (
        KnownCommitment("reminder", "1", due, Decimal("100"), "RUB", "future", "Платёж"),
        KnownCommitment("reminder", "2", due, Decimal("100"), "RUB", "future", "Платёж"),
        KnownCommitment("subscription", "s1", due, Decimal("100"), "RUB", "future", "Платёж", "merchant", "a" * 64),
        KnownCommitment("recurring", "r1", due, Decimal("100"), "RUB", "future", "Платёж", "merchant", "a" * 64),
    )
    selected = deduplicate_commitments(values)
    assert [item.source_key for item in selected] == ["1", "2", "s1"]


def test_commitment_dedup_never_collapses_coincidental_internet_and_gym_payments():
    due = date(2026, 8, 20)
    values = (
        KnownCommitment(
            "reminder", "internet", due, Decimal("1000"), "RUB", "future", "Internet",
            "category", safe_identity_hash("category", "Internet"),
        ),
        KnownCommitment(
            "subscription", "gym", due, Decimal("1000"), "RUB", "future", "Gym",
            "merchant", safe_identity_hash("merchant", "Gym"),
        ),
    )
    assert len(deduplicate_commitments(values)) == 2


def test_commitment_dedup_requires_compatible_strong_identity():
    due = date(2026, 8, 20)
    identity = safe_identity_hash("merchant", "Provider")
    values = (
        KnownCommitment("subscription", "s1", due, Decimal("1000"), "RUB", "future", "A", "merchant", identity),
        KnownCommitment("recurring", "r1", due, Decimal("1000"), "RUB", "future", "A", "merchant", identity),
    )
    assert [item.source_key for item in deduplicate_commitments(values)] == ["s1"]


def test_reminder_and_recurring_with_authoritative_same_identity_deduplicate():
    due = date(2026, 8, 20)
    identity = safe_identity_hash("merchant", "Provider")
    values = (
        KnownCommitment("reminder", "reminder-7", due, Decimal("1000"), "RUB", "future", "A", "merchant", identity),
        KnownCommitment("recurring", "pattern-9", due, Decimal("1000"), "RUB", "future", "A", "merchant", identity),
    )
    assert [item.source_key for item in deduplicate_commitments(values)] == ["reminder-7"]


def test_commitment_dedup_keeps_explicit_reminders_and_different_currencies():
    due = date(2026, 8, 20)
    identity = safe_identity_hash("category", "Internet")
    values = (
        KnownCommitment("reminder", "r1", due, Decimal("1000"), "RUB", "future", "A", "category", identity),
        KnownCommitment("reminder", "r2", due, Decimal("1000"), "RUB", "future", "A", "category", identity),
        KnownCommitment("subscription", "usd", due, Decimal("1000"), "USD", "future", "A", "category", identity),
    )
    assert len(deduplicate_commitments(values)) == 3


def test_variable_baseline_is_already_decomposed_and_commitment_is_reserved_once():
    recurring = KnownCommitment(
        "subscription", "s1", date(2026, 8, 20), Decimal("100"), "RUB", "recurring", "Платёж", "merchant", "a" * 64,
    )
    model_inputs = inputs(
        commitments=(recurring,),
        historical=(history("0", 0), history("0", 1), history("0", 2)),
    )
    forecast = calculate_spendable(model_inputs)
    assert forecast.known_commitments == Decimal("100.00")
    assert forecast.variable_reserve == Decimal("0.00")
    assert forecast.amount == Decimal("700.00")


def test_historical_recurring_rent_and_current_reminder_are_reserved_exactly_once():
    rent = KnownCommitment(
        "reminder", "rent", date(2026, 8, 20), Decimal("30000"), "RUB",
        "future_expense_reminder", "Будущий платёж", "category", safe_identity_hash("category", "Жильё"),
    )
    model_inputs = inputs(
        realized_income=Decimal("100000"),
        realized_expense=Decimal("0"),
        commitments=(rent,),
        historical=(history("5000", 0), history("5000", 1), history("5000", 2)),
    )
    forecast = calculate_spendable(model_inputs)
    assert forecast.known_commitments == Decimal("30000.00")
    assert forecast.variable_reserve == Decimal("5000.00")
    assert forecast.amount == Decimal("65000.00")


def test_snapshot_commitment_fact_excludes_only_exact_deterministic_payment():
    fact = {
        "source": "reminder",
        "source_id": "7",
        "due_date": "2026-08-20",
        "amount": "30000.00",
        "currency": "RUB",
        "identity_kind": "category",
        "identity_hash": safe_identity_hash("category", "Жильё"),
    }
    operation = {
        "op_date": date(2026, 8, 20), "amount": "30000", "currency": "RUB",
        "category": "Жильё", "comment": "", "source": "text", "goal_linked": False,
    }
    assert operation_is_deterministic(operation, [fact]) is True
    assert operation_is_deterministic({**operation, "amount": "29999"}, [fact]) is False
    assert operation_is_deterministic({**operation, "source": "reminder"}, [], allow_source_markers=False) is False


def test_goal_linked_expense_is_not_a_variable_training_target():
    operation = {
        "op_date": date(2026, 8, 20), "amount": "1000", "currency": "RUB",
        "category": "Накопления", "comment": "", "source": "text", "goal_linked": True,
    }
    assert operation_is_deterministic(operation, []) is True


@pytest.mark.parametrize(
    ("operation_currency", "snapshot_currency", "default_currency", "included"),
    [
        (None, "RUB", "RUB", True),
        (None, "USD", "RUB", False),
        ("USD", "USD", "RUB", True),
        ("USD", "RUB", "RUB", False),
        ("RUB", "RUB", "RUB", True),
        ("RUB", "USD", "RUB", False),
    ],
)
def test_outcome_currency_uses_canonical_legacy_default_only(operation_currency, snapshot_currency, default_currency, included):
    assert operation_matches_snapshot_currency(operation_currency, snapshot_currency, default_currency) is included


@pytest.mark.parametrize(
    ("operation_currency", "default_currency", "pattern_currency", "matches"),
    [
        (None, "RUB", "RUB", True),
        (None, "RUB", "USD", False),
        ("RUB", "USD", "RUB", True),
        ("RUB", "USD", "USD", False),
        ("USD", "RUB", "USD", True),
        ("USD", "RUB", "RUB", False),
    ],
)
def test_deterministic_currency_uses_explicit_or_validated_default_only(
    operation_currency, default_currency, pattern_currency, matches,
):
    assert deterministic_currency_compatible(operation_currency, pattern_currency, default_currency) is matches


def test_historical_features_stop_at_as_of_while_target_uses_future_days(monkeypatch):
    rows = [
        (date(2026, 7, 5), Decimal("100"), 2, 2, 2),
        (date(2026, 7, 20), Decimal("900"), 20, 20, 20),
        (date(2026, 7, 21), Decimal("0"), 0, 0, 1),
    ]
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: rows)
    period = ForecastPeriod("current_month", date(2026, 8, 1), date(2026, 8, 31), date(2026, 8, 13))
    july = ForecastRepository()._history(42, 10, "RUB", "RUB", period)[-1]
    assert july.as_of == date(2026, 7, 13)
    assert july.input_operation_count == 2
    assert july.input_tracked_days == 1
    assert july.input_variable_expense == Decimal("100.00")
    assert july.remainder == Decimal("900.00")
    assert july.target_tracked_days == 2
    assert july.target_valid is False


def test_historical_remainder_sql_excludes_authoritative_deterministic_sources(monkeypatch):
    captured = {}

    def fake(sql, params=()):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return []

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    ForecastRepository()._history(42, 10, "RUB", "RUB", PERIOD)
    assert "COALESCE(o.source,'')='reminder'" in captured["sql"]
    assert "public.goal_movements" in captured["sql"]
    assert "public.subscription_patterns" in captured["sql"]
    assert "public.recurring_spend_patterns" in captured["sql"]
    assert "sp.currency=COALESCE(o.currency,forecast_ctx.default_currency)" in captured["sql"]
    assert "rp.currency=COALESCE(o.currency,forecast_ctx.default_currency)" in captured["sql"]
    assert captured["params"][0] == "RUB"
    assert "AND NOT" in captured["sql"]


def test_commitment_query_excludes_paid_expired_wrong_scope_and_wrong_currency(monkeypatch):
    seen = []

    def fake(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return []

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    ForecastRepository()._commitments(42, 10, "group", "RUB", PERIOD)
    reminder_sql, reminder_params = seen[0]
    assert "NOT EXISTS" in reminder_sql and "event_type='recorded'" in reminder_sql
    assert "r.event_date > %s AND r.event_date <= %s" in reminder_sql
    assert "r.currency=%s" in reminder_sql
    assert reminder_params[1:3] == (10, False)


def test_future_income_reminder_is_separate_from_expense_commitments(monkeypatch):
    def fake(sql, _params=()):
        if "FROM public.user_reminders" in sql:
            return [(1, "Доходы", date(2026, 8, 20), Decimal("500"), "RUB", "Salary")]
        return []

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    commitments, expected_income = ForecastRepository()._commitments(42, 10, "group", "RUB", PERIOD)
    assert commitments == ()
    assert expected_income == Decimal("500.00")


def test_goal_query_uses_only_active_matching_scope_and_currency(monkeypatch):
    captured = {}

    def fake(sql, params=()):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return [(7, Decimal("1000"), Decimal("200"), "deadline", "monthly", None, Decimal("100"), {"day": 20})]

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    goals = ForecastRepository()._goals(42, 10, "RUB", PERIOD)
    assert "status='active'" in captured["sql"]
    assert captured["params"] == (42, 10, "RUB")
    assert goals == (GoalContribution(7, date(2026, 8, 20), Decimal("100.00")),)


def test_comparable_month_uses_same_cycle_position_and_excludes_current_period():
    periods = comparable_periods(PERIOD, count=3)
    assert [row[2].day for row in periods] == [13, 13, 13]
    assert all(row[1] < PERIOD.start for row in periods)


def test_invalid_coverage_and_missing_periods_are_not_zero_evidence():
    forecast = calculate_spendable(inputs(historical=(history("900", coverage="0.20"),)))
    assert forecast.variable_reserve == Decimal("0.00")
    assert forecast.quality_tier == "known_only"


def test_genuine_zero_with_tracking_evidence_remains_valid():
    forecast = calculate_spendable(inputs(historical=(history("0"),)))
    assert forecast.variable_reserve == Decimal("0.00")
    assert forecast.history_periods == 1
    assert forecast.quality_tier == "limited"


def test_target_validity_rejects_one_isolated_record_in_long_horizon():
    valid, reason, coverage = evaluate_target_validity(
        horizon_days=18, operation_count=1, tracked_days=1, variable_expense=Decimal("100"),
    )
    assert valid is False
    assert reason == "insufficient_future_coverage"
    assert coverage == Decimal(1) / Decimal(18)


def test_target_validity_accepts_strong_tracking_and_genuine_tracked_zero():
    observed = evaluate_target_validity(
        horizon_days=18, operation_count=7, tracked_days=7, variable_expense=Decimal("1000"),
    )
    tracked_zero = evaluate_target_validity(
        horizon_days=10, operation_count=0, tracked_days=4, variable_expense=Decimal("0"),
    )
    assert observed[:2] == (True, "valid_observed_activity")
    assert tracked_zero[:2] == (True, "valid_tracked_zero")


def test_target_validity_rejects_missing_future_history():
    assert evaluate_target_validity(
        horizon_days=18, operation_count=0, tracked_days=0, variable_expense=Decimal("0"),
    )[:2] == (False, "missing_future_tracking")


def test_invalid_target_is_excluded_without_entering_input_features():
    invalid = replace(history("999"), target_valid=False, target_validity_reason="insufficient_future_coverage")
    valid = history("100")
    forecast = calculate_spendable(inputs(historical=(invalid, valid)))
    assert forecast.variable_reserve == Decimal("100.00")
    assert forecast.history_periods == 1
    observation = ForecastObservation(
        "safe", TODAY.toordinal(), 18, Decimal("0.4"), Decimal("100"), Decimal("10"),
        3, 13, 4, Decimal("0.4"), Decimal("200"),
    )
    assert len(observation.features()) == 8


def test_quantiles_reject_crossing_and_serialize_decimal_safely():
    with pytest.raises(ValueError, match="non_monotonic"):
        QuantilePrediction(Decimal("20"), Decimal("10"), Decimal("30"), "x", "v1")
    assert QuantilePrediction(Decimal("1.235"), Decimal("2"), Decimal("3"), "x", "v1").as_dict()["q50"] == "1.24"


def test_rolling_origin_has_no_future_training_rows():
    fit_sizes = []

    class TrackingModel(RobustRemainderModel):
        def fit(self, observations):
            fit_sizes.append(len(observations))
            return super().fit(observations)

    rows = synthetic_observations(10)
    backtest = rolling_origin_backtest(TrackingModel, rows, minimum_train=6)
    assert fit_sizes == [6, 7, 8, 9]
    assert backtest.metrics.folds == 4


def test_rolling_origin_groups_same_date_without_training_peers():
    fit_keys = []

    class TrackingModel(RobustRemainderModel):
        def fit(self, observations):
            fit_keys.append(tuple(item.snapshot_key for item in observations))
            return super().fit(observations)

    rows = [
        replace(synthetic_observations(1)[0], snapshot_key=key, as_of_ordinal=origin, target_remainder=Decimal(target))
        for key, origin, target in (
            ("a", 1, "10"), ("b", 1, "20"), ("c", 2, "30"),
            ("d", 2, "40"), ("e", 3, "50"),
        )
    ]
    backtest = rolling_origin_backtest(TrackingModel, rows, minimum_train=2)
    assert fit_keys == [("a", "b"), ("a", "b", "c", "d")]
    assert backtest.metrics.folds == 3
    assert backtest.actuals == (Decimal("30.00"), Decimal("40.00"), Decimal("50.00"))
    assert rolling_origin_backtest(TrackingModel, list(reversed(rows)), minimum_train=2).predictions == backtest.predictions


def test_grouped_origin_calibration_uses_only_oos_predictions():
    rows = []
    for origin in range(1, 9):
        for suffix in ("a", "b"):
            rows.append(replace(
                synthetic_observations(1)[0],
                snapshot_key=f"{origin}-{suffix}",
                as_of_ordinal=origin,
                target_remainder=Decimal(origin * 10),
            ))
    result = rolling_origin_backtest(RobustRemainderModel, rows, minimum_train=2)
    calibration = calibrate_quantiles(list(result.predictions), list(result.actuals), minimum_samples=12)
    assert result.metrics.folds == 14
    assert calibration.sample_count == 14
    assert calibration.state == "calibrated"


def test_backtest_metrics_are_complete_and_deterministic():
    first = rolling_origin_backtest(RobustRemainderModel, synthetic_observations(12))
    second = rolling_origin_backtest(RobustRemainderModel, synthetic_observations(12))
    assert first.metrics == second.metrics
    assert set(__import__("json").loads(metrics_json(first))) == {
        "breach_rate", "coverage_q80", "coverage_q90", "folds", "interval_width", "mae", "mase",
        "pinball_q50", "pinball_q80", "pinball_q90",
    }


def test_champion_selection_enforces_downside_breach_guardrail():
    robust = result("robust_remainder", pinball="8", breach="0.10")
    unsafe = result("pooled_quantile_gbdt", pinball="1", breach="0.20")
    assert select_champion([robust, unsafe]).family == "robust_remainder"


def test_champion_can_select_better_safe_challenger():
    robust = result("robust_remainder", pinball="8", breach="0.10")
    safe = result("seasonal_temporal", pinball="4", breach="0.11")
    assert select_champion([robust, safe]).family == "seasonal_temporal"


class FixedRegisteredModel:
    def predict(self, _observation):
        return QuantilePrediction(Decimal("11"), Decimal("22"), Decimal("33"), "artifact", "artifact-v1")


def test_registered_valid_gbdt_champion_is_used_and_reported_exactly():
    champion = RegisteredChampion(
        FixedRegisteredModel(), "pooled_quantile_gbdt", "gbdt-production-v7", "RUB",
        CalibrationResult("insufficient", (Decimal("0"),) * 3, 3, None, None),
    )
    forecast = calculate_spendable(inputs(registered_champion=champion))
    assert (forecast.variable_q50, forecast.variable_q80, forecast.variable_q90) == (
        Decimal("11.00"), Decimal("22.00"), Decimal("33.00"),
    )
    assert forecast.model_family == "pooled_quantile_gbdt"
    assert forecast.model_version == "gbdt-production-v7"
    assert forecast.calibration_state == "insufficient"


def test_missing_registered_champion_uses_truthful_personal_fallback():
    forecast = calculate_spendable(inputs(historical=(history("10", 0), history("20", 1), history("30", 2))))
    assert forecast.model_family == "personal_ensemble"
    assert forecast.model_version == "personal-ensemble-v1"


def test_registry_rejects_bad_checksum_schema_and_risk_policy(monkeypatch, tmp_path):
    monkeypatch.setattr("settings.FORECAST_MODEL_DIR", str(tmp_path))
    path = tmp_path / "champion.joblib"
    metadata = {
        "feature_schema": "forecast-features-v1",
        "risk_policy": "downside-q80-v1",
        "model_family": "pooled_quantile_gbdt",
        "model_version": "v7",
        "currency": "RUB",
    }
    checksum = save_model_artifact(FixedRegisteredModel(), path, metadata)
    base = (
        "pooled_quantile_gbdt", "v7", str(path), checksum, "forecast-features-v1",
        "downside-q80-v1", TODAY, {"guardrail_eligible": True, "folds": 12},
        {"state": "insufficient", "sample_count": 3},
    )
    repository = ForecastRepository()
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: [{"unused": True}] and [base])
    assert repository._registered_champion("RUB", TODAY) is not None
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: [[*base[:3], "0" * 64, *base[4:]]])
    assert repository._registered_champion("RUB", TODAY) is None
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: [[*base[:4], "wrong-schema", *base[5:]]])
    assert repository._registered_champion("RUB", TODAY) is None
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: [[*base[:5], "wrong-policy", *base[6:]]])
    assert repository._registered_champion("RUB", TODAY) is None


def test_registry_rejects_artifact_for_another_currency(monkeypatch, tmp_path):
    monkeypatch.setattr("settings.FORECAST_MODEL_DIR", str(tmp_path))
    path = tmp_path / "usd.joblib"
    checksum = save_model_artifact(FixedRegisteredModel(), path, {
        "feature_schema": "forecast-features-v1",
        "risk_policy": "downside-q80-v1",
        "model_family": "pooled_quantile_gbdt",
        "model_version": "v7",
        "currency": "USD",
    })
    row = (
        "pooled_quantile_gbdt", "v7", str(path), checksum, "forecast-features-v1",
        "downside-q80-v1", TODAY, {"guardrail_eligible": True, "folds": 12},
        {"state": "insufficient", "sample_count": 3},
    )
    monkeypatch.setattr("services.forecasting.pg_fetchall", lambda *_args, **_kwargs: [row])
    assert ForecastRepository()._registered_champion("RUB", TODAY) is None


def test_wrong_currency_registered_champion_is_never_used():
    usd = RegisteredChampion(
        FixedRegisteredModel(), "pooled_quantile_gbdt", "usd-v1", "USD",
        CalibrationResult("insufficient", (Decimal("0"),) * 3, 3, None, None),
    )
    forecast = calculate_spendable(inputs(currency="RUB", registered_champion=usd))
    assert forecast.model_family == "known_only"


def test_registered_champion_lookup_is_exact_currency(monkeypatch):
    monkeypatch.setattr("settings.FORECAST_MODEL_DIR", "/tmp/models")
    captured = {}

    def fake(sql, params=()):
        captured.update(sql=" ".join(sql.split()), params=params)
        return []

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    assert ForecastRepository()._registered_champion("USD", TODAY) is None
    assert "WHERE currency=%s AND status='champion'" in captured["sql"]
    assert captured["params"] == ("USD",)


def test_real_backtest_candidates_are_rolling_origin_and_include_gbdt():
    evaluated = backtest_candidates(synthetic_observations(24))
    families = {evaluated["champion"].family, *(item.family for item in evaluated["challengers"])}
    assert families == {"robust_remainder", "seasonal_temporal", "pooled_quantile_gbdt"}
    assert all(item.metrics.folds == 18 for item in (evaluated["champion"], *evaluated["challengers"]))


def test_training_dsn_rejects_unsafe_database_by_default(monkeypatch):
    monkeypatch.delenv("FORECAST_PRODUCTION_TRAINING_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="unsafe_forecast_training_dsn"):
        ensure_safe_training_dsn("postgresql://db/finuchet")
    ensure_safe_training_dsn("postgresql://localhost/finuchet_test")


def test_real_training_and_backtest_pass_exact_currency_scope(monkeypatch, tmp_path):
    seen = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args, **_kwargs): pass
        def fetchone(self): return (True,)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    monkeypatch.setattr("services.forecast_training.acquire_training_lock", lambda _cur: True)
    monkeypatch.setattr(
        "services.forecast_training.extract_training_observations",
        lambda currency, *_args, **_kwargs: seen.append(currency) or [],
    )
    with pytest.raises(ValueError, match="insufficient_training_dataset"):
        train_from_snapshots(
            currency="rub", limit=24, model_directory=tmp_path,
            database_url="postgresql://localhost/finuchet_test",
        )
    with pytest.raises(ValueError, match="insufficient_training_dataset"):
        backtest_from_snapshots(
            currency="usd", limit=24, database_url="postgresql://localhost/finuchet_test",
        )
    assert seen == ["RUB", "USD"]


def test_failed_artifact_write_does_not_demote_working_champion(monkeypatch, tmp_path):
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=()): statements.append(" ".join(sql.split()))
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    monkeypatch.setattr("services.forecast_training.acquire_training_lock", lambda _cur: True)
    monkeypatch.setattr("services.forecast_training.extract_training_observations", lambda *_args, **_kwargs: synthetic_observations(24))
    monkeypatch.setattr("services.forecast_training.save_model_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        train_from_snapshots(
            currency="RUB",
            limit=24,
            model_directory=tmp_path,
            database_url="postgresql://localhost/finuchet_test",
        )
    assert not any("UPDATE public.forecast_model_registry" in sql for sql in statements)


def test_registering_challenger_never_demotes_champion(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=()): statements.append(" ".join(sql.split()))
        def fetchone(self): return (True,)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    assert register_model(
        currency="RUB",
        family="seasonal_temporal", version="candidate-v1", status="challenger",
        artifact_path="", artifact_sha256="", training_cutoff=TODAY,
        metrics={"guardrail_eligible": True}, calibration={"state": "insufficient"},
    ) is True
    assert not any("SET status='challenger'" in sql for sql in statements)


def test_registry_allows_one_independent_champion_per_currency(monkeypatch):
    writes = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=()): writes.append((" ".join(sql.split()), params))
        def fetchone(self): return (True,)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("settings.FORECAST_MODEL_DIR", "/tmp/models")
    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    for currency in ("RUB", "USD"):
        monkeypatch.setattr(
            "services.forecast_training.load_trusted_model_artifact",
            lambda *_args, currency=currency, **_kwargs: (object(), {
                "risk_policy": "downside-q80-v1",
                "model_family": "pooled_quantile_gbdt",
                "model_version": "v1",
                "currency": currency,
            }),
        )
        assert register_model(
            currency=currency, family="pooled_quantile_gbdt", version="v1", status="champion",
            artifact_path=f"{currency}.joblib", artifact_sha256="a" * 64,
            training_cutoff=TODAY, metrics={"guardrail_eligible": True},
            calibration={"state": "insufficient"},
        ) is True
    demotions = [params for sql, params in writes if "SET status='challenger'" in sql]
    inserts = [params for sql, params in writes if sql.startswith("INSERT INTO public.forecast_model_registry")]
    assert demotions == [("RUB",), ("USD",)]
    assert [params[0] for params in inserts] == ["RUB", "USD"]


def test_calibration_is_honest_when_insufficient():
    prediction = QuantilePrediction(Decimal("10"), Decimal("20"), Decimal("30"), "x", "v1")
    calibration = calibrate_quantiles([prediction], [Decimal("15")])
    assert calibration.state == "insufficient"
    assert apply_calibration(prediction, calibration) == prediction


def test_calibration_uses_separate_prediction_residuals_and_stays_monotonic():
    predictions = [QuantilePrediction(Decimal("10"), Decimal("15"), Decimal("20"), "x", "v1") for _ in range(12)]
    actuals = [Decimal(12 + index) for index in range(12)]
    calibration = calibrate_quantiles(predictions, actuals)
    adjusted = apply_calibration(predictions[0], calibration)
    assert calibration.state == "calibrated"
    assert calibration.sample_count == 12
    assert adjusted.q50 <= adjusted.q80 <= adjusted.q90


def test_bootstrap_is_seeded_stable_and_sorted():
    values = [Decimal("1"), Decimal("5"), Decimal("3")]
    first = bootstrap_scenarios(values, fingerprint="a" * 64, count=50)
    assert first == bootstrap_scenarios(values, fingerprint="a" * 64, count=50)
    assert first == sorted(first)


def test_forecast_change_prefers_financial_reason_over_model_reason():
    forecast = calculate_spendable(inputs(commitments=(KnownCommitment("reminder", "1", date(2026, 8, 20), Decimal("100"), "RUB", "future", "Платёж"),)))
    change = explain_forecast_change(forecast, {
        "amount": Decimal("800"), "known_commitments": Decimal("0"), "goal_reserve": Decimal("0"),
        "variable_reserve": Decimal("0"), "model_version": "old",
    })
    assert change["reason_codes"] == ["new_commitment"]


def test_grouped_budget_spend_is_loaded_in_one_bounded_query(monkeypatch):
    calls = []

    def fake(sql, _params=()):
        calls.append(" ".join(sql.split()))
        return []

    monkeypatch.setattr("services.forecasting.pg_fetchall", fake)
    ForecastRepository()._category_controls(42, 10, "RUB", PERIOD, "o.workspace_id=%s", (10,), "RUB")
    assert len(calls) == 2
    assert "LIMIT 100" in calls[1]
    assert "spent_member.group_id=g.id" in calls[1]


def test_training_extraction_uses_only_valid_exact_currency_bounded_features(monkeypatch):
    captured = {}

    def fake(sql, params=()):
        captured.update(sql=" ".join(sql.split()), params=params)
        return [(
        "a" * 64, TODAY, 18,
        {"realized_expense": "100.00", "tracked_days": 5, "elapsed_days": 10, "operation_count": 7, "cycle_day": 13},
        Decimal("300.00"),
        )]

    monkeypatch.setattr("db.database.pg_fetchall", fake)
    row = extract_training_observations("RUB")[0]
    assert row.target_remainder == Decimal("300.00")
    assert len(row.features()) == 8
    assert "user" not in row.snapshot_key
    assert "s.currency=%s" in captured["sql"]
    assert "s.target_valid=TRUE" in captured["sql"]
    assert captured["params"][1] == "RUB"


def test_outcome_finalization_is_bounded_and_skip_locked(monkeypatch):
    captured = {}

    class Cursor:
        rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params):
            captured.update(sql=" ".join(sql.split()), params=params)
            self.rowcount = 0
        def fetchall(self): return []
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    assert finalize_forecast_outcomes(9999) == {"finalized": 0}
    assert "FOR UPDATE SKIP LOCKED" in captured["sql"]
    assert captured["params"] == (1000,)


def test_finalized_variable_target_excludes_forecast_commitment_and_goal(monkeypatch):
    updates = []
    fact = {
        "due_date": "2026-08-20", "amount": "30000", "currency": "RUB",
        "identity_kind": "category", "identity_hash": safe_identity_hash("category", "Жильё"),
    }
    candidate = (1, 42, 10, "RUB", "RUB", date(2026, 8, 1), date(2026, 8, 31), date(2026, 8, 13), [fact], [{"goal_id": 9}])
    operations = [
        (1, date(2026, 8, 20), "Расходы", Decimal("30000"), "RUB", "text", "", "Жильё", False),
        (2, date(2026, 8, 21), "Расходы", Decimal("1000"), "RUB", "text", "", "Накопления", True),
        (3, date(2026, 8, 22), "Расходы", Decimal("500"), "RUB", "text", "", "Еда", False),
    ]

    class Cursor:
        rowcount = 0
        result = []
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params):
            compact = " ".join(sql.split())
            if compact.startswith("SELECT id, user_id"):
                self.result = [candidate]
            elif compact.startswith("SELECT o.id"):
                self.result = operations
                assert "o.currency=%s OR (o.currency IS NULL AND %s=%s)" in compact
            elif compact.startswith("UPDATE public.forecast_snapshots"):
                updates.append(params)
                self.rowcount = 1
        def fetchall(self): return self.result
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    monkeypatch.setattr("services.user_time.user_local_date", lambda *_args, **_kwargs: date(2026, 9, 1))
    assert finalize_forecast_outcomes() == {"finalized": 1}
    assert updates[0][0] == Decimal("500.00")
    assert updates[0][5] is False
    assert updates[0][6] == "insufficient_future_coverage"
    assert updates[0][7] == "target-coverage-v1"


def test_outcome_finalization_waits_for_user_local_period_end(monkeypatch):
    candidate = (1, 42, 10, "RUB", "RUB", date(2026, 8, 1), TODAY, date(2026, 8, 5), [], [])
    calls = []

    class Cursor:
        rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params): calls.append(" ".join(sql.split()))
        def fetchall(self): return [candidate]
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    monkeypatch.setattr("services.user_time.user_local_date", lambda *_args, **_kwargs: TODAY)
    assert finalize_forecast_outcomes(now_utc=datetime(2026, 8, 13, 23, tzinfo=timezone.utc)) == {"finalized": 0}
    assert len(calls) == 1


def test_source_fingerprint_changes_for_financially_relevant_inputs():
    base = inputs(
        commitments=(KnownCommitment("reminder", "1", date(2026, 8, 20), Decimal("100"), "RUB", "future", "Платёж"),),
        goal_contributions=(GoalContribution(1, date(2026, 8, 25), Decimal("50")),),
        general_budget_amount=Decimal("1000"),
        category_limits={"food": (Decimal("500"), Decimal("100"))},
        grouped_budgets=(("daily", ("food",), Decimal("800"), Decimal("200")),),
    )
    original = forecast_source_fingerprint(base)
    variants = (
        replace(base, realized_income=Decimal("1001")),
        replace(base, realized_expense=Decimal("201")),
        replace(base, current_operation_count=11),
        replace(base, tracked_days=11),
        replace(base, commitments=()),
        replace(base, goal_contributions=()),
        replace(base, general_budget_amount=Decimal("999")),
        replace(base, category_limits={"food": (Decimal("501"), Decimal("100"))}),
        replace(base, grouped_budgets=()),
        replace(base, currency="USD"),
    )
    assert all(forecast_source_fingerprint(value) != original for value in variants)


def test_model_artifact_round_trip_and_checksum(tmp_path):
    path = tmp_path / "models" / "forecast.joblib"
    model = RobustRemainderModel().fit(synthetic_observations(8))
    checksum = save_model_artifact(model, path, {"feature_schema": "forecast-features-v1"})
    loaded, metadata = load_model_artifact(path, checksum)
    assert isinstance(loaded, RobustRemainderModel)
    assert metadata["feature_schema"] == "forecast-features-v1"
    with pytest.raises(ValueError, match="checksum"):
        load_model_artifact(path, "0" * 64)


def test_trusted_model_loader_rejects_paths_outside_configured_directory(tmp_path):
    model_dir = tmp_path / "trusted"
    model_dir.mkdir()
    outside = tmp_path / "outside.joblib"
    checksum = save_model_artifact(RobustRemainderModel(), outside, {"feature_schema": "forecast-features-v1"})
    with pytest.raises(ValueError, match="outside_trusted"):
        load_trusted_model_artifact(model_dir, outside, checksum, expected_feature_schema="forecast-features-v1")


def test_experiment_assignment_is_stable_and_can_vary():
    assert assign_variant("spendable-explanation-v1", 42) == assign_variant("spendable-explanation-v1", 42)
    assert len({assign_variant("spendable-explanation-v1", user_id) for user_id in range(1, 50)}) == 2


def test_experiment_payload_contains_no_financial_values_or_policy_variant():
    properties = exposure_properties("spendable-explanation-v1", 42, "home_spendable", "personal")
    assert set(properties) == {"experiment_key", "experiment_version", "variant", "surface", "quality_tier"}
    assert not any("amount" in key or "risk_policy" in key for key in properties)


def test_forecast_feedback_rejects_unissued_syntax_before_database_access():
    with pytest.raises(ValueError, match="invalid_forecast_feedback"):
        record_forecast_feedback(42, 10, "z" * 64, "useful")


def test_forecast_feedback_is_bound_to_issued_user_workspace_prediction(monkeypatch):
    captured = {}

    class Cursor:
        rowcount = 1
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params): captured.update(sql=" ".join(sql.split()), params=params)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("services.forecasting.get_conn", lambda: Connection())
    assert record_forecast_feedback(42, 10, "a" * 64, "useful") is True
    assert "JOIN public.forecast_snapshots" in captured["sql"]
    assert "s.user_id=%s" in captured["sql"]
    assert captured["params"][-3:] == (42, 10, "a" * 64)


def test_forecast_insights_cover_risk_commitments_goals_end_result_and_change():
    candidates = detect_forecast_candidates(insight_snapshot({
        "available": True,
        "amount": "100",
        "currency": "RUB",
        "risk_state": "attention",
        "quality_tier": "personal",
        "quality_label": "По вашей истории",
        "current_result": "1000",
        "known_commitments": "600",
        "known_commitment_count": 2,
        "goal_reserve": "600",
        "expected_end_result": "-200",
        "change": {"previous_amount": "1000", "delta": "-900", "reason_codes": ["new_commitment"]},
    }))
    assert {item.family for item in candidates} >= {
        "spendable_risk", "forecast_end_result", "upcoming_commitment_pressure", "goal_affordability", "spendable_change",
    }


def test_forecast_insights_cover_general_budget_and_recurring_pressure():
    candidates = detect_forecast_candidates(insight_snapshot({
        "available": True,
        "amount": "200",
        "risk_state": "watch",
        "quality_tier": "personal",
        "current_result": "3000",
        "known_commitments": "1000",
        "goal_reserve": "0",
        "expected_end_result": "500",
        "general_budget_remaining": "200",
        "reason_codes": ["general_budget_binding"],
        "recurring_commitment_count": 2,
        "recurring_commitments": "800",
    }))
    assert {item.family for item in candidates} >= {
        "general_budget_breach_risk", "recurring_pressure",
    }


def test_limit_pace_distinguishes_general_category_and_grouped_controls():
    base = insight_snapshot({"available": False})
    limits = tuple(
        LimitAggregate(str(index), title, category, Decimal("1000"), Decimal("900"), "RUB", "month", 90, True, kind)
        for index, (title, category, kind) in enumerate((
            ("Общий", None, "general_limit"),
            ("Еда", "Food", "category_limit"),
            ("Дом", "Food", "category_budget"),
        ), start=1)
    )
    families = {item.family for item in detect_limit_pace(replace(base, limits=limits), today=TODAY)}
    assert families == {
        "general_budget_breach_risk",
        "category_limit_breach_risk",
        "grouped_budget_breach_risk",
    }


def test_projection_detectors_emit_grounded_future_category_and_anomaly_families():
    snapshot = replace(
        insight_snapshot({"available": False}),
        current_total=Decimal("5000"),
        previous_total=Decimal("2500"),
        current_count=10,
        previous_count=10,
        categories=(CategoryAggregate("food", "Food", Decimal("4000"), Decimal("1500"), 6, 5),),
    )
    families = {item.family for item in detect_projection_risks(snapshot, today=TODAY)}
    assert families >= {
        "future_expense_acceleration", "category_projection", "unusual_spend_anomaly",
    }


def test_all_requested_insight_families_are_reachable_from_evidence_detectors():
    snapshot = replace(
        insight_snapshot({
            "available": True,
            "amount": "0",
            "risk_state": "attention",
            "quality_tier": "personal",
            "current_result": "3000",
            "known_commitments": "1200",
            "known_commitment_count": 2,
            "goal_reserve": "700",
            "expected_end_result": "-500",
            "general_budget_remaining": "0",
            "reason_codes": ["general_budget_binding"],
            "recurring_commitment_count": 2,
            "recurring_commitments": "800",
            "change": {"previous_amount": "1000", "delta": "-1000", "reason_codes": ["new_commitment"]},
        }),
        current_total=Decimal("5000"),
        previous_total=Decimal("2500"),
        current_count=10,
        previous_count=10,
        categories=(CategoryAggregate("food", "Food", Decimal("4000"), Decimal("1500"), 6, 5),),
        limits=(LimitAggregate("1", "Еда", "Food", Decimal("1000"), Decimal("900"), "RUB", "month", 90),),
    )
    families = {item.family for item in detect_candidates(snapshot, today=TODAY)}
    assert families >= {
        "forecast_end_result",
        "spendable_change",
        "spendable_risk",
        "future_expense_acceleration",
        "general_budget_breach_risk",
        "category_limit_breach_risk",
        "goal_affordability",
        "upcoming_commitment_pressure",
        "recurring_pressure",
        "category_projection",
        "category_mix_shift",
        "persistent_spending_trend",
        "unusual_spend_anomaly",
    }


def test_forecast_consequence_outranks_shallow_comparison():
    snapshot = insight_snapshot({
        "available": True, "amount": "0", "risk_state": "attention", "quality_tier": "personal",
        "current_result": "100", "known_commitments": "0", "goal_reserve": "0", "expected_end_result": "-5000",
    })
    candidates = detect_forecast_candidates(snapshot)
    for candidate in candidates:
        candidate.fingerprint = candidate.detector_type.rjust(64, "0")
    ranked = rank_candidates(candidates, now=datetime(2026, 8, 13, tzinfo=timezone.utc), limit=2)
    assert ranked[0].family in {"forecast_end_result", "spendable_risk"}


def test_insufficient_forecast_data_generates_no_candidate():
    assert detect_forecast_candidates(insight_snapshot({"available": False})) == []


def test_migration_is_additive_idempotent_and_privacy_safe():
    source = open("/root/bot_finuchet/migrations/20260813_024_advanced_forecasting_home.sql", encoding="utf-8").read()
    assert "ADD COLUMN IF NOT EXISTS display_name" in source
    assert "CREATE TABLE IF NOT EXISTS public.forecast_snapshots" in source
    assert "known_commitment_facts JSONB" in source
    assert "goal_reserve_facts JSONB" in source
    assert "legacy_default_currency TEXT" in source
    assert "timezone_name TEXT" in source
    assert "target_tracked_days INTEGER" in source
    assert "target_coverage_ratio NUMERIC" in source
    assert "target_valid BOOLEAN" in source
    assert "target_validity_reason TEXT" in source
    assert "target_validity_policy_version TEXT" in source
    assert "currency TEXT NOT NULL" in source
    assert "UNIQUE (currency, model_family, model_version)" in source
    assert "ON public.forecast_model_registry(currency)" in source
    assert "general_budget_current_remaining NUMERIC" in source
    assert "general_budget_projected_remaining NUMERIC" in source
    assert "q50 <= q80 AND q80 <= q90" in source
    assert "raw_text" not in source and "comment" not in source and "initData" not in source
    assert "COMMIT;" in source and "Rollback" in source
