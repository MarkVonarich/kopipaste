from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from services.goals import Goal, GoalMovement
from services.workspaces import WorkspaceContext


def _api(monkeypatch) -> MiniAppAPI:
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [
        {"workspace_id": 10, "name": "Family", "kind": "group", "role": "member", "active": True, "read_only": False}
    ])
    monkeypatch.setattr(api, "_workspace_detail", lambda _req, _workspace_id: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args, **_kwargs: date(2026, 8, 5))
    return api


def _goal(**overrides) -> Goal:
    values = dict(
        id=7,
        owner_user_id=42,
        workspace_id=10,
        display_name="Trip",
        normalized_name="trip",
        currency="RUB",
        target_amount=Decimal("1000.00"),
        current_balance=Decimal("250.00"),
        deadline=date(2026, 12, 31),
        strategy="deadline",
        frequency="monthly",
        comfortable_amount=None,
        planned_contribution_amount=Decimal("150.00"),
        schedule_config={"day": 5},
        status="active",
        reminders_enabled=False,
        salary_categories=[],
        projected_completion_date=date(2026, 12, 5),
        next_contribution_date=date(2026, 9, 5),
        movement_count=1,
    )
    values.update(overrides)
    return Goal(**values)


def test_analytics_returns_summary_category_other_dynamics_and_radar(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "overview", lambda _req, _params: {"data": {
        "aggregation_available": True,
        "totals_by_currency": {"RUB": {"income": Decimal("1000.00"), "expense": Decimal("650.00"), "count": 8}},
        "recent_operations": [],
    }})

    def _fetch(sql, params=()):
        if "GROUP BY category, COALESCE(currency" in sql:
            return [
                ("Food", "RUB", Decimal("300.00"), 2),
                ("Taxi", "RUB", Decimal("200.00"), 1),
                ("Cafe", "RUB", Decimal("100.00"), 1),
                ("Books", "RUB", Decimal("50.00"), 1),
                ("Health", "RUB", Decimal("40.00"), 1),
                ("Other", "RUB", Decimal("10.00"), 1),
            ]
        if "GROUP BY op_date, type" in sql:
            return [
                (date(2026, 8, 1), "Расходы", "RUB", Decimal("100.00"), 1),
                (date(2026, 8, 2), "Доходы", "RUB", Decimal("1000.00"), 1),
            ]
        if "GROUP BY bucket, category" in sql:
            return [
                ("current", "Food", Decimal("300.00")),
                ("current", "Taxi", Decimal("200.00")),
                ("previous", "Food", Decimal("100.00")),
                ("previous", "Taxi", Decimal("300.00")),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    data = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month"})["data"]

    assert data["summary"]["result_by_currency"]["RUB"] == "350.00"
    assert data["category_structure"]["items"][-1]["category"] == "Прочее"
    assert data["time_dynamics"]["grouping"] == "day"
    assert data["radar"]["insufficient_data"] is False
    assert data["radar"]["metric"] == "normalized_category_share_percent"


def test_radar_insufficient_data_and_foreign_workspace_denied(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [
        {"workspace_id": 10, "name": "Family", "kind": "group", "role": "member", "active": True, "read_only": False}
    ])
    monkeypatch.setattr(api, "overview", lambda _req, _params: {"data": {"aggregation_available": True, "totals_by_currency": {}, "recent_operations": []}})
    monkeypatch.setattr("miniapp.api.pg_fetchall", lambda *_args, **_kwargs: [])

    data = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month"})["data"]
    assert data["radar"]["insufficient_data"] is True

    with pytest.raises(MiniAppError) as exc:
        api.analytics(api.request(42), {"workspace_id": 99, "period": "current_month"})
    assert exc.value.code == "workspace_access_denied"


def test_goal_create_preview_contribution_and_idempotent_replay(monkeypatch):
    api = _api(monkeypatch)
    events = []
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append((event_name, kwargs)))
    monkeypatch.setattr("miniapp.api.create_financial_goal", lambda **_kwargs: _goal())
    monkeypatch.setattr("miniapp.api.update_goal_plan", lambda **_kwargs: _goal(reminders_enabled=True))

    data = api.create_goal(api.request(42), {
        "workspace_id": 10,
        "title": "Trip",
        "target_amount": "1000.00",
        "current_amount": "250.00",
        "deadline": "2026-12-31",
        "strategy": "deadline",
        "frequency": "monthly",
        "reminders_enabled": True,
    })["data"]

    assert data["goal"]["target"] == "1000.00"
    assert data["plan_preview"]["recommended_amount"] is not None
    assert events[-1][0] == "mini_app_goal_created"

    created = []

    def _movement(**kwargs):
        is_new = not created
        created.append(kwargs["idempotency_key"])
        movement = GoalMovement(1, 7, "contribution", Decimal("100.00"), Decimal("350.00"), datetime(2026, 8, 5), "miniapp")
        return _goal(current_balance=Decimal("350.00")), movement if is_new else None, is_new

    monkeypatch.setattr("miniapp.api.add_goal_movement", _movement)
    first = api.goal_contribution(api.request(42), 7, {"workspace_id": 10, "amount": "100.00", "idempotency_key": "idem"})["data"]
    second = api.goal_contribution(api.request(42), 7, {"workspace_id": 10, "amount": "100.00", "idempotency_key": "idem"})["data"]

    assert first["created"] is True
    assert second["created"] is False
    assert [event for event, _props in events].count("mini_app_goal_contribution_added") == 1


def test_limits_list_uses_existing_threshold_policy_and_workspace_scope(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.list_goals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("miniapp.api.list_general_limits", lambda *_args, **_kwargs: [])

    def _fetch(sql, params=()):
        if "FROM public.category_limits" in sql:
            assert "workspace_id=%s" in sql
            return [("month", "Food", Decimal("1000.00"), "RUB")]
        if "FROM public.operations" in sql:
            return [(Decimal("900.00"),)]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)
    data = api.limits(api.request(42), {"workspace_id": 10})["data"]

    assert data["items"][0]["status"] == "approaching"
    assert data["items"][0]["percent"] == 90


def test_profile_notification_toggle_and_sanitized_product_event(monkeypatch):
    api = _api(monkeypatch)
    events = []
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append((event_name, kwargs)))
    monkeypatch.setattr("miniapp.api.toggle_notification_preference", lambda _user_id, key: key == "goals")
    monkeypatch.setattr("miniapp.api.get_notification_preferences", lambda _user_id: {"goal_notifications_enabled": True, "limit_alerts_enabled": True})

    data = api.update_notification_preferences(api.request(42), {"action": "toggle", "key": "goals"})["data"]
    assert data["goal_notifications_enabled"] is True
    assert events[-1][0] == "mini_app_notification_setting_changed"
    assert "amount" not in events[-1][1]["properties"]

    tracked = api.track_ui_event(api.request(42), {
        "event": "mini_app_analytics_chart_filter_changed",
        "properties": {"chart_type": "radar", "filter_kind": "expense", "amount": "999", "category": "Food"},
    })["data"]
    assert tracked["tracked"] is True
