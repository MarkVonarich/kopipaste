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
    ForecastObservation,
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
from services.forecast_training import extract_training_observations, finalize_forecast_outcomes, synthetic_observations
from services.forecasting import (
    ForecastInputs,
    ForecastPeriod,
    ForecastRepository,
    GoalContribution,
    HistoricalRemainder,
    KnownCommitment,
    calculate_spendable,
    can_spend,
    comparable_periods,
    deduplicate_commitments,
    explain_forecast_change,
    record_forecast_feedback,
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
    return HistoricalRemainder(start, end, start.replace(day=13), Decimal(value), count, 10, Decimal(coverage))


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


def test_commitment_dedup_keeps_two_explicit_payments_but_suppresses_detected_duplicate():
    due = date(2026, 8, 20)
    values = (
        KnownCommitment("reminder", "1", due, Decimal("100"), "RUB", "future", "Платёж"),
        KnownCommitment("reminder", "2", due, Decimal("100"), "RUB", "future", "Платёж"),
        KnownCommitment("subscription", "s1", due, Decimal("100"), "RUB", "future", "Платёж", True),
        KnownCommitment("recurring", "r1", due, Decimal("100"), "RUB", "future", "Платёж", True),
    )
    selected = deduplicate_commitments(values)
    assert [item.source_key for item in selected] == ["1", "2"]
    assert [item.baseline_overlap for item in selected] == [True, False]


def test_recurring_commitment_is_not_counted_again_inside_historical_variable_reserve():
    recurring = KnownCommitment(
        "subscription", "s1", date(2026, 8, 20), Decimal("100"), "RUB", "recurring", "Платёж", True,
    )
    model_inputs = inputs(
        commitments=(recurring,),
        historical=(history("100", 0), history("100", 1), history("100", 2)),
    )
    forecast = calculate_spendable(model_inputs)
    assert forecast.known_commitments == Decimal("100.00")
    assert forecast.variable_reserve == Decimal("0.00")
    assert forecast.amount == Decimal("700.00")


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


def test_rolling_origin_rejects_equal_or_future_origin_leakage():
    rows = synthetic_observations(8)
    rows[6] = replace(rows[6], as_of_ordinal=rows[5].as_of_ordinal)
    with pytest.raises(ValueError, match="forecast_backtest_leakage"):
        rolling_origin_backtest(RobustRemainderModel, rows, minimum_train=6)


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


def test_training_extraction_uses_only_bounded_aggregate_features(monkeypatch):
    monkeypatch.setattr("db.database.pg_fetchall", lambda *_args, **_kwargs: [(
        "a" * 64, TODAY, 18,
        {"realized_expense": "100.00", "tracked_days": 5, "elapsed_days": 10, "operation_count": 7, "cycle_day": 13},
        Decimal("300.00"),
    )])
    row = extract_training_observations()[0]
    assert row.target_remainder == Decimal("300.00")
    assert len(row.features()) == 8
    assert "user" not in row.snapshot_key


def test_outcome_finalization_is_bounded_and_skip_locked(monkeypatch):
    captured = {}

    class Cursor:
        rowcount = 3
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params): captured.update(sql=" ".join(sql.split()), params=params)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("db.database.get_conn", lambda: Connection())
    assert finalize_forecast_outcomes(9999) == {"finalized": 3}
    assert "FOR UPDATE SKIP LOCKED" in captured["sql"]
    assert captured["params"] == (1000,)


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
    assert "q50 <= q80 AND q80 <= q90" in source
    assert "raw_text" not in source and "comment" not in source and "initData" not in source
    assert "COMMIT;" in source and "Rollback" in source
