from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError, TransactionFilters
from services.announcements import (
    announcement_candidate,
    report_ready_announcements,
    resolve_announcement_candidates,
)
from services.reports import (
    ReportBuildRequest,
    build_report,
    comparable_period,
    completed_report_period,
    report_ready_kinds,
)


def _analytics(*, period_key: str = "current_month", start: date = date(2026, 8, 1), end: date = date(2026, 8, 12)) -> dict:
    rub_category = {
        "key": "food",
        "category": "Продукты",
        "currency": "RUB",
        "total": Decimal("300.00"),
        "previous_total": Decimal("100.00"),
        "delta": Decimal("200.00"),
        "count": 2,
        "previous_count": 1,
        "share": 75,
        "drillable": True,
        "synthetic": False,
        "fallback": False,
    }
    other_category = {
        "key": "__synthetic_other_category__",
        "category": "Остальные",
        "currency": "RUB",
        "total": Decimal("100.00"),
        "previous_total": Decimal("50.00"),
        "delta": Decimal("50.00"),
        "count": 1,
        "previous_count": 1,
        "share": 25,
        "drillable": False,
        "synthetic": True,
        "fallback": False,
    }
    merchant = {
        "key": "яндекс лавка",
        "merchant": "Яндекс Лавка",
        "currency": "RUB",
        "total": Decimal("240.00"),
        "previous_total": Decimal("120.00"),
        "delta": Decimal("120.00"),
        "count": 2,
        "previous_count": 1,
        "share": 60,
        "drillable": True,
        "synthetic": False,
        "fallback": False,
    }
    fallback_merchant = {
        "key": "__empty_merchant__",
        "merchant": "Без описания",
        "currency": "RUB",
        "total": Decimal("160.00"),
        "previous_total": Decimal("30.00"),
        "delta": Decimal("130.00"),
        "count": 1,
        "previous_count": 1,
        "share": 40,
        "drillable": False,
        "synthetic": False,
        "fallback": True,
    }
    return {
        "period": {"key": period_key, "start_date": start, "end_date": end},
        "previous_period": {"key": "previous_month_to_date", "start_date": date(2026, 7, 1), "end_date": date(2026, 7, 12)},
        "filters": {"operation_type": "all", "category": "Продукты"},
        "available_currencies": ["EUR", "RUB"],
        "summary": {
            "currency_groups": {
                "RUB": {"income": Decimal("1000.00"), "expense": Decimal("400.00"), "result": Decimal("600.00"), "count": 4},
                "EUR": {"income": Decimal("80.00"), "expense": Decimal("20.00"), "result": Decimal("60.00"), "count": 2},
            }
        },
        "overview_metrics": {
            "RUB": {
                "income": {"current": Decimal("1000.00"), "previous": Decimal("0.00"), "delta": Decimal("1000.00"), "pct": None, "state": "zero_baseline"},
                "expense": {"current": Decimal("400.00"), "previous": Decimal("150.00"), "delta": Decimal("250.00"), "pct": Decimal("166.67"), "state": "ok"},
                "result": {"current": Decimal("600.00"), "previous": Decimal("-150.00"), "delta": Decimal("750.00"), "pct": None, "state": "sign_change"},
                "count": 4,
                "previous_count": 2,
            },
            "EUR": {
                "income": {"current": Decimal("80.00"), "previous": Decimal("0.00"), "delta": Decimal("80.00"), "pct": None, "state": "zero_baseline"},
                "expense": {"current": Decimal("20.00"), "previous": Decimal("0.00"), "delta": Decimal("20.00"), "pct": None, "state": "zero_baseline"},
                "result": {"current": Decimal("60.00"), "previous": Decimal("0.00"), "delta": Decimal("60.00"), "pct": None, "state": "zero_baseline"},
                "count": 2,
                "previous_count": 0,
            },
        },
        "category_structure": {
            "type": "expense",
            "currency_groups": {
                "RUB": {"currency": "RUB", "total": Decimal("400.00"), "items": [rub_category, other_category]},
                "EUR": {"currency": "EUR", "total": Decimal("20.00"), "items": [{**rub_category, "currency": "EUR", "total": Decimal("20.00"), "count": 1, "share": 100}]},
            },
        },
        "merchant_structure": {
            "currency_groups": {
                "RUB": {"currency": "RUB", "total": Decimal("400.00"), "items": [merchant, fallback_merchant]},
                "EUR": {"currency": "EUR", "total": Decimal("20.00"), "items": []},
            }
        },
        "change_contribution": {
            "currency_groups": {
                "RUB": {"items": [rub_category, other_category]},
                "EUR": {"items": []},
            }
        },
    }


def _request(**overrides) -> ReportBuildRequest:
    values = {
        "report_kind": "selected",
        "workspace_scope": 10,
        "workspace_name": "Семья",
        "workspace_type": "group",
        "read_only": False,
        "selected_currency": "RUB",
        "fallback_currency": "RUB",
    }
    values.update(overrides)
    return ReportBuildRequest(**values)


@pytest.mark.parametrize(
    ("kind", "today", "expected"),
    [
        ("completed_week", date(2026, 8, 12), (date(2026, 8, 3), date(2026, 8, 9), "completed_week")),
        ("completed_week", date(2026, 8, 10), (date(2026, 8, 3), date(2026, 8, 9), "completed_week")),
        ("completed_month", date(2026, 8, 12), (date(2026, 7, 1), date(2026, 7, 31), "completed_month")),
        ("completed_month", date(2026, 3, 1), (date(2026, 2, 1), date(2026, 2, 28), "completed_month")),
    ],
)
def test_completed_report_period_uses_only_finished_periods(kind, today, expected):
    assert completed_report_period(kind, today) == expected


def test_comparable_period_matches_analytics_calendar_and_equal_period_semantics():
    assert comparable_period(date(2026, 7, 1), date(2026, 7, 31), "completed_month") == (
        date(2026, 6, 1), date(2026, 6, 30), "month_before_report"
    )
    assert comparable_period(date(2026, 8, 3), date(2026, 8, 9), "custom") == (
        date(2026, 7, 27), date(2026, 8, 2), "previous_equal_period"
    )


def test_report_never_sums_mixed_currencies_and_preserves_selected_currency():
    rub = build_report(_analytics(), _request(selected_currency="RUB"))
    eur = build_report(_analytics(), _request(selected_currency="EUR"))
    assert rub["summary"] == {"currency": "RUB", "income": Decimal("1000.00"), "expense": Decimal("400.00"), "result": Decimal("600.00"), "operation_count": 4}
    assert eur["summary"] == {"currency": "EUR", "income": Decimal("80.00"), "expense": Decimal("20.00"), "result": Decimal("60.00"), "operation_count": 2}
    assert rub["summary"]["income"] != Decimal("1080.00")


def test_legacy_default_currency_group_is_consumed_without_leaking_into_other_currency():
    analytics = _analytics()
    analytics["summary"]["currency_groups"]["RUB"]["income"] = Decimal("1125.00")
    rub = build_report(analytics, _request(selected_currency="RUB"))
    eur = build_report(analytics, _request(selected_currency="EUR"))
    assert rub["summary"]["income"] == Decimal("1125.00")
    assert eur["summary"]["income"] == Decimal("80.00")


def test_absent_requested_currency_falls_back_to_an_available_exact_currency():
    report = build_report(_analytics(), _request(selected_currency="USD"))
    assert report["selected_currency"] == "EUR"
    assert report["summary"]["currency"] == "EUR"


@pytest.mark.parametrize(
    ("income", "expense", "count", "state"),
    [
        ("0.00", "0.00", 0, "no_data"),
        ("100.00", "0.00", 1, "income_only"),
        ("0.00", "100.00", 1, "expense_only"),
        ("100.00", "40.00", 2, "complete"),
    ],
)
def test_report_data_states_are_explicit(income, expense, count, state):
    analytics = _analytics()
    analytics["available_currencies"] = ["RUB"]
    analytics["summary"]["currency_groups"]["RUB"].update(income=Decimal(income), expense=Decimal(expense), count=count)
    assert build_report(analytics, _request())["data_state"] == state


def test_report_preserves_zero_baseline_and_result_sign_change():
    report = build_report(_analytics(), _request())
    assert report["comparison"]["income"]["state"] == "zero_baseline"
    assert report["comparison"]["result"]["state"] == "sign_change"
    assert report["observations"][0]["kind"] == "result_sign_change"


def test_canonical_category_and_merchant_keys_drive_exact_operation_scopes():
    report = build_report(_analytics(), _request())
    category_scope = report["categories"][0]["operation_scope"]
    merchant_scope = report["merchants"][0]["operation_scope"]
    assert category_scope == {
        "workspace_id": 10,
        "period": "current_month",
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 12),
        "operation_type": "expense",
        "currency": "RUB",
        "category": "all",
        "category_key": "food",
        "merchant_key": None,
        "scope_category": "Продукты",
    }
    assert merchant_scope["merchant_key"] == "яндекс лавка"
    assert merchant_scope["category"] == "Продукты"
    assert merchant_scope["workspace_id"] == 10


def test_synthetic_remainder_and_empty_merchant_fallback_stay_non_drillable():
    report = build_report(_analytics(), _request())
    assert report["categories"][1]["category"] == "Остальные"
    assert report["categories"][1]["operation_scope"] is None
    assert report["merchants"][1]["merchant"] == "Без описания"
    assert report["merchants"][1]["operation_scope"] is None
    assert report["merchants"][0]["average_check"] == Decimal("120.00")


def test_report_export_is_exact_only_for_selected_single_currency_scope():
    analytics = _analytics()
    assert build_report(analytics, _request())["export_available"] is False
    analytics["available_currencies"] = ["RUB"]
    assert build_report(analytics, _request())["export_available"] is True
    assert build_report(analytics, _request(report_kind="completed_week"))["export_available"] is False


def test_report_ready_eligibility_is_one_bounded_query(monkeypatch):
    calls = []

    def _fetch(sql, params):
        calls.append((sql, params))
        return [(3, 0)]

    monkeypatch.setattr("services.reports.pg_fetchall", _fetch)
    result = report_ready_kinds("workspace_id = ANY(%s)", ([10, 11],), today=date(2026, 8, 12))
    assert result == {"completed_week"}
    assert len(calls) == 1
    assert "COUNT(*) FILTER" in calls[0][0]
    assert calls[0][1] == (date(2026, 8, 3), date(2026, 8, 9), date(2026, 7, 1), date(2026, 7, 31), [10, 11])


def test_report_ready_eligibility_uses_current_type_and_category_scope(monkeypatch):
    calls = []

    def _fetch(sql, params):
        calls.append((sql, params))
        category = params[-1] if "category=%s" in sql else None
        return [(1, 0)] if category in {None, "Продукты"} else [(0, 0)]

    monkeypatch.setattr("services.reports.pg_fetchall", _fetch)
    workspace_where = "(workspace_id = ANY(%s) OR (workspace_id IS NULL AND user_id=%s))"
    workspace_params = ([10], 42)

    assert report_ready_kinds(workspace_where, workspace_params, today=date(2026, 8, 12), operation_type="expense", category="Такси") == set()
    assert report_ready_kinds(workspace_where, workspace_params, today=date(2026, 8, 12), operation_type="expense", category="Продукты") == {"completed_week"}
    assert report_ready_kinds(workspace_where, workspace_params, today=date(2026, 8, 12), operation_type="expense", category=None) == {"completed_week"}
    assert len(calls) == 3
    assert all("AND type=%s" in sql for sql, _params in calls)
    assert "AND category=%s" in calls[0][0]
    assert calls[0][1][-2:] == ("Расходы", "Такси")
    assert calls[2][1][-1] == "Расходы"


def test_report_ready_candidates_reuse_period_ids_dismissal_ttl_and_family():
    candidates = report_ready_announcements({"completed_week", "completed_month"}, today=date(2026, 8, 12))
    ids = {item.id for item in candidates}
    assert ids == {"report-ready-weekly-2026-08-09", "report-ready-monthly-2026-07-31"}
    visible = resolve_announcement_candidates(candidates, {"report-ready-weekly-2026-08-09"}, today=date(2026, 8, 12))
    assert [item["id"] for item in visible] == ["report-ready-monthly-2026-07-31"]
    assert resolve_announcement_candidates(
        report_ready_announcements({"completed_month"}, today=date(2026, 8, 21)),
        set(),
        today=date(2026, 8, 21),
    ) == []
    assert announcement_candidate("report-ready-weekly-2026-08-09", today=date(2026, 8, 12)).family == "report-ready-weekly"
    assert announcement_candidate("report-ready-weekly-2026-08-08", today=date(2026, 8, 12)) is None
    assert announcement_candidate("report-ready-weekly-not-a-date", today=date(2026, 8, 12)) is None


def test_dynamic_report_dismissal_uses_the_same_user_local_ttl_date(monkeypatch):
    api = MiniAppAPI()
    candidate_id = "report-ready-weekly-2026-08-09"
    local_today = date(2026, 8, 29)
    captured = []
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _scope: ([10], False))
    monkeypatch.setattr("miniapp.api.user_local_date", lambda _user_id, workspace_id: captured.append(("local", workspace_id)) or local_today)
    monkeypatch.setattr("miniapp.api.dismiss_announcement", lambda user_id, item_id, today: captured.append(("dismiss", user_id, item_id, today)) or True)
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)

    assert announcement_candidate(candidate_id, local_today) is not None
    assert announcement_candidate(candidate_id, date(2026, 8, 30)) is None
    api.dismiss_announcement(api.request(42), candidate_id, {"workspace_id": 10})

    assert captured == [
        ("local", 10),
        ("dismiss", 42, candidate_id, local_today),
    ]


def _tx(workspace_ids, *, all_scope=False, period_key="current_month", start=date(2026, 8, 1), end=date(2026, 8, 12)):
    return TransactionFilters(
        workspace_ids=workspace_ids,
        all_scope=all_scope,
        start=start,
        end=end,
        period_key=period_key,
        operation_type="all",
        category="Продукты",
        where_sql="TRUE",
        params=(),
    )


def test_report_api_preserves_selected_analytics_period_scope_and_safe_event(monkeypatch):
    api = MiniAppAPI()
    seen = {}
    events = []
    def _analytics_call(_req, params):
        seen["analytics_params"] = dict(params)
        return {"data": _analytics()}
    monkeypatch.setattr(api, "analytics", _analytics_call)
    monkeypatch.setattr(api, "_transaction_filters", lambda _req, _params: _tx([10]))
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [{"workspace_id": 10, "name": "Семья", "kind": "group", "read_only": True}])
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(api, "_track", lambda _req, name, **kwargs: events.append((name, kwargs)))

    result = api.report(api.request(42), {"workspace_id": 10, "period": "current_month", "operation_type": "all", "category": "Продукты", "currency": "RUB"})["data"]["report"]

    assert seen["analytics_params"]["period"] == "current_month"
    assert result["workspace"] == {"scope": 10, "name": "Семья", "type": "group", "read_only": True}
    assert events[0][0] == "report_opened"
    assert set(events[0][1]["properties"]) == {"report_kind", "period_kind", "workspace_type", "operation_type", "result", "source", "currency"}
    assert not ({"amount", "category", "merchant", "description"} & set(events[0][1]["properties"]))


@pytest.mark.parametrize(
    ("kind", "expected_period", "expected_start", "expected_end"),
    [
        ("completed_week", "custom", "2026-08-03", "2026-08-09"),
        ("completed_month", "previous_month", None, None),
    ],
)
def test_report_api_completed_periods_use_user_local_finished_boundaries(monkeypatch, kind, expected_period, expected_start, expected_end):
    api = MiniAppAPI()
    seen = {}

    def _analytics_call(_req, params):
        seen.update(params)
        period_key = "previous_month" if params["period"] == "previous_month" else "custom"
        return {"data": _analytics(period_key=period_key, start=date(2026, 7, 1) if kind == "completed_month" else date(2026, 8, 3), end=date(2026, 7, 31) if kind == "completed_month" else date(2026, 8, 9))}

    monkeypatch.setattr(api, "analytics", _analytics_call)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _scope: ([10], False))
    monkeypatch.setattr(api, "_transaction_filters", lambda _req, _params: _tx([10], period_key=expected_period))
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [{"workspace_id": 10, "name": "Семья", "kind": "group", "read_only": False}])
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 12))
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)

    api.report(api.request(42), {"workspace_id": 10, "report_kind": kind, "currency": "RUB"})
    assert seen["period"] == expected_period
    assert seen.get("start_date") == expected_start
    assert seen.get("end_date") == expected_end


@pytest.mark.parametrize(
    ("tx", "scope", "name", "kind"),
    [
        (_tx([None]), None, "Личное", "legacy_personal"),
        (_tx([10]), 10, "Семья", "group"),
        (_tx([None, 10], all_scope=True), "all", "Все пространства", "all"),
    ],
)
def test_report_api_concrete_personal_and_all_workspace_scope(monkeypatch, tx, scope, name, kind):
    api = MiniAppAPI()
    monkeypatch.setattr(api, "analytics", lambda _req, _params: {"data": _analytics()})
    monkeypatch.setattr(api, "_transaction_filters", lambda _req, _params: tx)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [{"workspace_id": 10, "name": "Семья", "kind": "group", "read_only": False}])
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)
    result = api.report(api.request(42), {"workspace_id": scope, "currency": "RUB"})["data"]["report"]
    assert (result["workspace"]["scope"], result["workspace"]["name"], result["workspace"]["type"]) == (scope, name, kind)


def test_report_api_propagates_existing_workspace_authorization_failure(monkeypatch):
    api = MiniAppAPI()

    def _denied(_req, _params):
        raise MiniAppError(403, "workspace_access_denied", "Denied")

    monkeypatch.setattr(api, "analytics", _denied)
    with pytest.raises(MiniAppError) as exc:
        api.report(api.request(42), {"workspace_id": 999, "report_kind": "selected"})
    assert exc.value.code == "workspace_access_denied"


def test_report_ui_events_accept_only_bounded_privacy_safe_values(monkeypatch):
    api = MiniAppAPI()
    events = []
    monkeypatch.setattr(api, "_track", lambda _req, name, **kwargs: events.append((name, kwargs["properties"])))

    api.track_ui_event(api.request(42), {
        "event": "report_drilldown_opened",
        "properties": {
            "report_kind": "completed_week",
            "currency": "RUB",
            "kind": "category",
            "source": "forged",
            "result": "raw financial description",
            "merchant": "must not pass",
        },
    })
    api.track_ui_event(api.request(42), {
        "event": "report_export_requested",
        "properties": {"report_kind": "forged", "currency": "private text", "kind": "merchant"},
    })

    assert events == [
        ("report_drilldown_opened", {"source": "mini_app", "report_kind": "completed_week", "currency": "RUB", "kind": "category"}),
        ("report_export_requested", {"source": "mini_app"}),
    ]
