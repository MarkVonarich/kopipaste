from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from services.planning import (
    PlanningControl,
    PlanningRequest,
    analyze_conflicts,
    arithmetic_mean,
    calculate_planning_estimate,
    complete_periods,
    goal_required_pace,
    history_confidence,
    other_goal_commitments,
)
from services.workspaces import WorkspaceContext
from services.announcements import ANNOUNCEMENTS


def _request(kind="category_limit", **overrides):
    values = {
        "user_id": 42,
        "workspace_id": 10,
        "kind": kind,
        "currency": "RUB",
        "default_currency": "RUB",
        "period": "month",
        "categories": ("Заведения",),
    }
    values.update(overrides)
    return PlanningRequest(**values)


def _history_row(
    month: int,
    *,
    selected: Decimal | int | str = 0,
    expense: Decimal | int | str = 0,
    income: Decimal | int | str = 0,
    expense_count: int = 1,
    income_count: int = 1,
):
    return (
        date(2026, month, 1),
        expense_count + income_count,
        expense_count,
        income_count,
        Decimal(str(selected)),
        Decimal(str(expense)),
        Decimal(str(income)),
    )


def test_exact_four_month_average_is_16750():
    assert arithmetic_mean(map(Decimal, ("15400", "17900", "12300", "21400"))) == Decimal("16750.00")
    assert history_confidence(4) == "good"


def test_monthly_history_excludes_current_incomplete_month():
    periods = complete_periods("month", date(2026, 9, 12))

    assert [(item.start, item.end) for item in periods] == [
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]
    assert [item.label for item in periods] == ["Май 2026", "Июнь 2026", "Июль 2026", "Август 2026"]


def test_weekly_history_uses_previous_complete_monday_weeks():
    periods = complete_periods("week", date(2026, 8, 12))

    assert periods[-1].start == date(2026, 8, 3)
    assert periods[-1].end == date(2026, 8, 9)
    assert all(item.end < date(2026, 8, 10) for item in periods)


def test_history_query_is_bounded_scoped_legacy_currency_compatible_and_canonical(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        calls.append((" ".join(sql.split()), params))
        if "FROM public.operations" in sql:
            return [
                _history_row(5, selected=15400, expense=16000, income=30000),
                _history_row(6, selected=17900, expense=18000, income=30000),
                _history_row(7, selected=12300, expense=13000, income=30000),
                _history_row(8, selected=21400, expense=22000, income=30000),
            ]
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    result = calculate_planning_estimate(
        _request(categories=("Заведения", " ЗАВЕДЕНИЯ ", "заведения")),
        today=date(2026, 9, 12),
    )

    assert result["recommendation"] == Decimal("16750.00")
    assert result["scope"]["categories"] == ["заведения"]
    operations_sql, params = calls[0]
    assert "o.workspace_id=%s" in operations_sql
    assert "COALESCE(o.currency, %s)=%s" in operations_sql
    assert "AS expense_count" in operations_sql
    assert "AS income_count" in operations_sql
    assert "=ANY(%s)" in operations_sql
    assert params[-3:-1] == ("RUB", "RUB")
    assert date(2026, 9, 1) not in params


def test_legacy_currency_fallback_matches_analytics_for_requested_currency(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        if "FROM public.operations" not in sql:
            return []
        normalized_sql = " ".join(sql.split())
        calls.append((normalized_sql, params))
        default_currency, requested_currency = params[-3:-1]
        assert default_currency == "RUB"
        totals = {
            "RUB": Decimal("1500"),  # NULL 1000 + explicit RUB 500
            "USD": Decimal("10"),
            "EUR": Decimal("25"),
        }
        amount = totals[requested_currency]
        return [_history_row(month, selected=amount, expense=amount, income=0, expense_count=3, income_count=0) for month in (5, 6, 7, 8)]

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)

    rub = calculate_planning_estimate(
        _request(categories=("Продукты", " продукты ", "ПРОДУКТЫ")),
        today=date(2026, 9, 12),
    )
    usd = calculate_planning_estimate(
        _request(currency="USD", categories=("Продукты",)),
        today=date(2026, 9, 12),
    )
    eur = calculate_planning_estimate(
        _request(currency="EUR", categories=("Продукты",)),
        today=date(2026, 9, 12),
    )

    assert rub["recommendation"] == Decimal("1500.00")
    assert rub["scope"]["categories"] == ["продукты"]
    assert usd["recommendation"] == Decimal("10.00")
    assert eur["recommendation"] == Decimal("25.00")
    assert all("COALESCE(o.currency, %s)=%s" in sql for sql, _params in calls)


def test_goal_cash_flow_uses_same_legacy_currency_fallback(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        if "FROM public.operations" in sql:
            calls.append((" ".join(sql.split()), params))
            default_currency, requested_currency = params[-3:-1]
            assert (default_currency, requested_currency) == ("RUB", "RUB")
            return [
                _history_row(month, expense=500, income=2000)
                for month in (5, 6, 7, 8)
            ]
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    result = calculate_planning_estimate(
        _request(
            "goal",
            categories=(),
            target_amount=Decimal("120000"),
            deadline=date(2027, 2, 28),
            frequency="monthly",
            schedule_config={"day": 5},
        ),
        today=date(2026, 9, 1),
    )

    assert result["comfortable_pace"]["average_monthly_net"] == Decimal("1500.00")
    assert "COALESCE(o.currency, %s)=%s" in calls[0][0]


def test_missing_periods_are_not_fabricated_as_zero(monkeypatch):
    monkeypatch.setattr(
        "services.planning.pg_fetchall",
        lambda sql, _params: [_history_row(8, selected=100, expense=100, income_count=0)]
        if "FROM public.operations" in sql
        else [],
    )

    result = calculate_planning_estimate(_request(), today=date(2026, 9, 12))

    assert result["valid_periods"] == 1
    assert result["history_confidence"] == "insufficient"
    assert result["recommendation"] is None


def test_covered_period_can_prove_genuine_zero_category_spend(monkeypatch):
    monkeypatch.setattr(
        "services.planning.pg_fetchall",
        lambda sql, _params: [_history_row(month, selected=0, expense=100, income_count=0) for month in (5, 6, 7, 8)]
        if "FROM public.operations" in sql
        else [],
    )

    result = calculate_planning_estimate(_request(), today=date(2026, 9, 12))

    assert result["history_confidence"] == "good"
    assert result["baseline_average"] == Decimal("0.00")
    assert result["recommendation"] is None
    assert all(item["expense_count"] == 1 and item["income_count"] == 0 for item in result["history"])


@pytest.mark.parametrize("kind", ("category_limit", "general_limit"))
def test_income_only_periods_do_not_establish_spending_history(monkeypatch, kind):
    monkeypatch.setattr(
        "services.planning.pg_fetchall",
        lambda sql, _params: [
            _history_row(month, income=100000, expense_count=0, income_count=1)
            for month in (5, 6, 7, 8)
        ]
        if "FROM public.operations" in sql
        else [],
    )

    result = calculate_planning_estimate(_request(kind), today=date(2026, 9, 12))

    assert result["valid_periods"] == 0
    assert result["history"] == []
    assert result["history_confidence"] == "insufficient"
    assert result["baseline_average"] is None
    assert result["recommendation"] is None


def test_spending_confidence_counts_only_expense_covered_periods(monkeypatch):
    rows = [
        _history_row(5, selected=100, expense=100, income_count=0),
        _history_row(6, income=1000, expense_count=0, income_count=1),
        _history_row(7, selected=300, expense=300, income_count=0),
        _history_row(8, income=1000, expense_count=0, income_count=1),
    ]
    monkeypatch.setattr("services.planning.pg_fetchall", lambda sql, _params: rows if "FROM public.operations" in sql else [])

    result = calculate_planning_estimate(_request(), today=date(2026, 9, 12))

    assert result["valid_periods"] == 2
    assert result["history_confidence"] == "limited"
    assert result["baseline_average"] == Decimal("200.00")


def test_grouped_budget_sums_canonical_categories_in_one_query(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        calls.append((sql, params))
        if "FROM public.operations" in sql:
            return [_history_row(month, selected=value, expense=value, expense_count=4, income_count=0) for month, value in ((5, 31200), (6, 35100), (7, 28900), (8, 34800))]
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    result = calculate_planning_estimate(
        _request("category_budget", categories=("Продукты", " продукты ", "Такси")),
        today=date(2026, 9, 12),
    )

    assert result["recommendation"] == Decimal("32500.00")
    assert result["scope"]["categories"] == ["продукты", "такси"]
    assert len([sql for sql, _ in calls if "FROM public.operations" in sql]) == 1


def test_conflicts_do_not_mutate_recommendation_and_ignore_other_scopes():
    request = _request()
    controls = [
        PlanningControl("general_limit", "general:1", "Все расходы", Decimal("15000"), "RUB", "month", workspace_id=10),
        PlanningControl("category_limit", "category:month:Заведения", "Заведения", Decimal("18000"), "RUB", "month", ("Заведения",), 10),
        PlanningControl("category_budget", "budget:2", "Повседневные", Decimal("30000"), "RUB", "month", ("Заведения", "Такси"), 10),
        PlanningControl("general_limit", "general:3", "USD", Decimal("1"), "USD", "month", workspace_id=10),
        PlanningControl("general_limit", "general:4", "Week", Decimal("1"), "RUB", "week", workspace_id=10),
        PlanningControl("general_limit", "general:5", "Other workspace", Decimal("1"), "RUB", "month", workspace_id=11),
    ]

    conflicts = analyze_conflicts(request, controls, Decimal("16750"))

    assert {item.kind for item in conflicts} == {"above_general_limit", "existing_category_limit", "grouped_budget_overlap"}
    assert Decimal("16750") == Decimal("16750")


def test_editing_existing_category_limit_is_not_reported_as_duplicate():
    request = _request(editing_entity_id="category:month:Заведения")
    controls = [PlanningControl("category_limit", "category:month:Заведения", "Заведения", Decimal("18000"), "RUB", "month", ("Заведения",), 10)]

    assert analyze_conflicts(request, controls, Decimal("16750")) == []


def test_goal_required_pace_uses_existing_occurrence_math():
    request = _request(
        "goal",
        categories=(),
        target_amount=Decimal("120000"),
        current_amount=Decimal("0"),
        deadline=date(2027, 2, 28),
        frequency="monthly",
        schedule_config={"day": 5},
    )

    required = goal_required_pace(request, today=date(2026, 9, 1))

    assert required["occurrence_count"] == 6
    assert required["amount"] == Decimal("20000.00")
    assert required["monthly_amount"] == Decimal("20000.00")


def test_goal_comfortable_pace_subtracts_only_other_active_commitments(monkeypatch):
    def fake_fetch(sql, _params):
        if "FROM public.operations" in sql:
            return [
                _history_row(month, expense=70000, income=100000, expense_count=2, income_count=2)
                for month in (5, 6, 7, 8)
            ]
        if "FROM public.financial_goals" in sql:
            return [(9, Decimal("50000"), Decimal("0"), None, "contribution", "monthly", Decimal("10000"), None, {})]
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    request = _request(
        "goal",
        categories=(),
        target_amount=Decimal("120000"),
        deadline=date(2027, 2, 28),
        frequency="monthly",
        schedule_config={"day": 5},
        editing_goal_id=3,
    )

    result = calculate_planning_estimate(request, today=date(2026, 9, 1))

    assert result["valid_periods"] == 4
    assert result["history_confidence"] == "good"
    assert result["comfortable_pace"]["average_monthly_net"] == Decimal("30000.00")
    assert result["comfortable_pace"]["other_goal_commitments"] == Decimal("10000.00")
    assert result["comfortable_pace"]["monthly_amount"] == Decimal("20000.00")
    assert result["feasibility"] == "compatible"


@pytest.mark.parametrize(
    ("expense_count", "income_count", "expense", "income"),
    (
        (0, 1, 0, 100000),
        (1, 0, 70000, 0),
    ),
)
def test_goal_single_sided_history_keeps_required_pace_but_disables_comfort(monkeypatch, expense_count, income_count, expense, income):
    monkeypatch.setattr(
        "services.planning.pg_fetchall",
        lambda sql, _params: [
            _history_row(
                month,
                expense=expense,
                income=income,
                expense_count=expense_count,
                income_count=income_count,
            )
            for month in (5, 6, 7, 8)
        ]
        if "FROM public.operations" in sql
        else [],
    )
    request = _request(
        "goal",
        categories=(),
        target_amount=Decimal("120000"),
        deadline=date(2027, 2, 28),
        frequency="monthly",
        schedule_config={"day": 5},
    )

    result = calculate_planning_estimate(request, today=date(2026, 9, 1))

    assert result["valid_periods"] == 0
    assert result["history_confidence"] == "insufficient"
    assert result["required_pace"]["monthly_amount"] == Decimal("20000.00")
    assert result["comfortable_pace"]["average_monthly_net"] is None
    assert result["comfortable_pace"]["monthly_amount"] is None
    assert result["recommendation"] is None
    assert result["feasibility"] == "insufficient_history"


def test_goal_stretched_and_insufficient_history_states(monkeypatch):
    def four_periods(sql, _params):
        if "FROM public.operations" in sql:
            return [_history_row(month, expense=1000, income=19000) for month in (5, 6, 7, 8)]
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", four_periods)
    request = _request(
        "goal",
        categories=(),
        target_amount=Decimal("150000"),
        deadline=date(2027, 2, 28),
        frequency="monthly",
        schedule_config={"day": 5},
    )
    stretched = calculate_planning_estimate(request, today=date(2026, 9, 1))
    assert stretched["required_pace"]["monthly_amount"] == Decimal("25000.00")
    assert stretched["comfortable_pace"]["monthly_amount"] == Decimal("18000.00")
    assert stretched["gap"] == Decimal("7000.00")
    assert stretched["feasibility"] == "stretched"

    monkeypatch.setattr(
        "services.planning.pg_fetchall",
        lambda sql, _params: [_history_row(8, expense=1000, income=19000)] if "FROM public.operations" in sql else [],
    )
    insufficient = calculate_planning_estimate(request, today=date(2026, 9, 1))
    assert insufficient["required_pace"]["monthly_amount"] == Decimal("25000.00")
    assert insufficient["comfortable_pace"]["monthly_amount"] is None
    assert insufficient["feasibility"] == "insufficient_history"


def test_other_goal_query_enforces_workspace_currency_active_and_current_goal_exclusion(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        calls.append((" ".join(sql.split()), params))
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    request = _request("goal", categories=(), editing_goal_id=77)

    assert other_goal_commitments(request, today=date(2026, 9, 1)) == (Decimal("0.00"), 0)
    sql, params = calls[0]
    assert "workspace_id=%s AND owner_user_id=%s" in sql
    assert "currency=%s" in sql
    assert "status='active'" in sql
    assert "id<>%s" in sql
    assert params == (10, 42, "RUB", 77, 77)


def test_legacy_personal_history_is_user_scoped(monkeypatch):
    calls = []

    def fake_fetch(sql, params):
        calls.append((" ".join(sql.split()), params))
        return []

    monkeypatch.setattr("services.planning.pg_fetchall", fake_fetch)
    calculate_planning_estimate(_request(workspace_id=None), today=date(2026, 9, 12))

    operations_sql, params = calls[0]
    assert "o.workspace_id IS NULL AND o.user_id=%s" in operations_sql
    assert 42 in params


def test_planning_api_rejects_all_scope_with_russian_message(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_read_scope", lambda *_args: ([10, 11], True))

    with pytest.raises(MiniAppError) as exc:
        api.planning_estimate(api.request(42), {"workspace_id": "all", "kind": "general_limit"})

    assert exc.value.code == "concrete_workspace_required"
    assert exc.value.message == "Выберите пространство для расчёта."


def test_planning_api_marks_viewer_result_read_only(monkeypatch):
    api = MiniAppAPI()
    events = []
    monkeypatch.setattr(api, "_read_scope", lambda *_args: ([10], False))
    monkeypatch.setattr(api, "_workspace_detail", lambda *_args: WorkspaceContext(10, -1, 42, "group", "viewer", "Family", True))
    monkeypatch.setattr(api, "_track", lambda _req, name, **kwargs: events.append((name, kwargs)))
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 9, 12))
    captured = []
    monkeypatch.setattr(
        "miniapp.api.calculate_planning_estimate",
        lambda request, **_kwargs: captured.append(request) or {"recommendation": Decimal("100"), "history_confidence": "good"},
    )

    data = api.planning_estimate(api.request(42), {"workspace_id": 10, "kind": "general_limit", "currency": "USD", "period": "month"})["data"]["estimate"]

    assert data["read_only"] is True
    assert data["can_apply"] is False
    assert captured[0].default_currency == "RUB"
    assert captured[0].currency == "USD"
    assert events[0][0] == "smart_planning_calculated"
    assert set(events[0][1]["properties"]) == {"planning_kind", "period_kind", "history_confidence", "source", "workspace_type"}


def test_whats_new_contains_unreleased_smart_planning_candidate():
    candidate = next(item for item in ANNOUNCEMENTS if item.family == "smart-planning")

    assert candidate.kind == "feature"
    assert candidate.title == "Планируйте суммы по своим данным"
    assert candidate.description == "КопиPaste анализирует прошлые расходы и помогает подобрать лимит, общий бюджет или темп для цели."
    assert candidate.action_type == "OPEN_PLANS"
