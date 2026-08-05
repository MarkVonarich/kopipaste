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


class _IdemDB:
    def __init__(self) -> None:
        self.rows = {}


class _IdemConn:
    def __init__(self, db: _IdemDB) -> None:
        self.db = db

    def cursor(self):
        return _IdemCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _IdemCursor:
    def __init__(self, db: _IdemDB) -> None:
        self.db = db
        self._next = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchone(self):
        row = self._next
        self._next = None
        return row

    def execute(self, sql: str, params=()) -> None:
        compact = " ".join(sql.split())
        self._next = None
        self.rowcount = 0
        if compact.startswith("INSERT INTO public.miniapp_idempotency_keys"):
            user_id, key, request_hash, _lease = params
            idem_key = (int(user_id), str(key))
            if idem_key not in self.db.rows:
                self.db.rows[idem_key] = {"request_hash": request_hash, "status": "pending", "response_json": None, "operation_id": None}
                self._next = ("pending",)
            return
        if compact.startswith("SELECT request_hash, status, response_json"):
            user_id, key = params
            row = self.db.rows.get((int(user_id), str(key)))
            if row:
                self._next = (row["request_hash"], row["status"], row["response_json"], row["operation_id"], None, True)
            return
        if compact.startswith("UPDATE public.miniapp_idempotency_keys") and "SET operation_id=%s" in compact:
            operation_id, response, user_id, key, request_hash = params
            row = self.db.rows[(int(user_id), str(key))]
            if row["request_hash"] == request_hash and row["status"] == "pending":
                row["operation_id"] = operation_id
                row["status"] = "completed"
                row["response_json"] = getattr(response, "adapted", response)
                self.rowcount = 1
            return
        raise AssertionError(compact)


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
    monkeypatch.setattr(api, "_run_idempotent_create", lambda req, action, idem, body, creator: (creator(), True))

    data = api.create_goal(api.request(42), {
        "workspace_id": 10,
        "idempotency_key": "goal-k1",
        "title": "Trip",
        "target_amount": "1000.00",
        "current_amount": "250.00",
        "deadline": "2026-12-31",
        "strategy": "deadline",
        "frequency": "monthly",
        "day": 5,
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


def test_mixed_currency_analytics_groups_without_arithmetic(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "overview", lambda _req, _params: {"data": {
        "aggregation_available": False,
        "totals_by_currency": {
            "RUB": {"income": Decimal("1000.00"), "expense": Decimal("650.00"), "count": 8},
            "EUR": {"income": Decimal("10.00"), "expense": Decimal("2.00"), "count": 2},
        },
        "recent_operations": [],
    }})

    def _fetch(sql, params=()):
        if "GROUP BY category, COALESCE(currency" in sql:
            return [
                ("Food", "RUB", Decimal("300.00"), 2),
                ("Taxi", "RUB", Decimal("200.00"), 1),
                ("Cafe", "EUR", Decimal("2.00"), 1),
            ]
        if "GROUP BY op_date, type" in sql:
            return [
                (date(2026, 8, 1), "Расходы", "RUB", Decimal("100.00"), 1),
                (date(2026, 8, 1), "Расходы", "EUR", Decimal("2.00"), 1),
                (date(2026, 8, 2), "Доходы", "RUB", Decimal("1000.00"), 1),
            ]
        if "GROUP BY bucket, category" in sql:
            assert "COALESCE(currency" in sql
            assert "RUB" in params
            return [
                ("current", "Food", Decimal("300.00")),
                ("previous", "Food", Decimal("100.00")),
                ("current", "Taxi", Decimal("200.00")),
                ("previous", "Taxi", Decimal("100.00")),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    mixed = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month"})["data"]
    assert mixed["aggregation_available"] is False
    assert mixed["available_currencies"] == ["EUR", "RUB"]
    assert mixed["summary"]["result_by_currency"] == {"RUB": "350.00", "EUR": "8.00"}
    assert mixed["category_structure"]["currency_groups"]["RUB"]["items"][0]["total"] == "300.00"
    assert mixed["category_structure"]["currency_groups"]["EUR"]["items"][0]["share"] == 100
    assert mixed["time_dynamics"]["currency_groups"]["RUB"]["currency"] == "RUB"
    assert mixed["time_dynamics"]["currency_groups"]["EUR"]["currency"] == "EUR"
    assert mixed["radar"]["insufficient_data"] is True
    assert mixed["radar"]["reason"] == "mixed_currencies"

    filtered = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month", "currency": "RUB"})["data"]
    assert filtered["selected_currency"] == "RUB"
    assert filtered["radar"]["currency"] == "RUB"
    assert filtered["radar"]["insufficient_data"] is False


def test_goal_preview_requires_visible_schedule_and_does_not_default(monkeypatch):
    api = _api(monkeypatch)
    with pytest.raises(MiniAppError) as exc:
        api.goal_plan_preview(api.request(42), {
            "workspace_id": 10,
            "target_amount": "1000.00",
            "current_amount": "100.00",
            "deadline": "2026-12-31",
            "strategy": "deadline",
            "frequency": "monthly",
        })
    assert exc.value.code == "schedule_required"

    preview = api.goal_plan_preview(api.request(42), {
        "workspace_id": 10,
        "target_amount": "1000.00",
        "current_amount": "100.00",
        "deadline": "2026-12-31",
        "strategy": "deadline",
        "frequency": "twice_monthly",
        "days": [5, 20],
        "reminders_enabled": True,
    })["data"]["plan_preview"]
    assert preview["schedule_config"] == {"days": [5, 20]}
    assert preview["next_occurrence"] == "2026-08-05"


def test_idempotent_goal_create_replays_and_conflicts(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    events = []
    created = []
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append(event_name))
    monkeypatch.setattr("miniapp.api.create_financial_goal", lambda **_kwargs: created.append(1) or _goal())
    monkeypatch.setattr("miniapp.api.update_goal_plan", lambda **_kwargs: _goal())

    body = {
        "workspace_id": 10,
        "idempotency_key": "retry-key",
        "title": "Trip",
        "target_amount": "1000.00",
        "current_amount": "0.00",
        "strategy": "monthly",
        "frequency": "none",
    }
    body["strategy"] = "none"

    first = api.create_goal(api.request(42), body)["data"]
    second = api.create_goal(api.request(42), body)["data"]

    assert first == second
    assert len(created) == 1
    assert events.count("mini_app_goal_created") == 1
    assert (42, "goal:create:retry-key") in db.rows

    with pytest.raises(MiniAppError) as exc:
        api.create_goal(api.request(42), {**body, "title": "Different"})
    assert exc.value.status == 409
    assert exc.value.code == "idempotency_conflict"


def test_idempotent_general_limit_create_replays_and_conflicts(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    created = []
    events = []
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append(event_name))
    monkeypatch.setattr("miniapp.api.create_or_update_general_limit", lambda **kwargs: created.append(kwargs) or type("Stored", (), {
        "kind": "general",
        "identifier": "general:9",
        "title": "Все расходы",
        "category": None,
        "amount": Decimal("1000.00"),
        "currency": "RUB",
        "period": "month",
        "workspace_id": 10,
        "alerts_enabled": True,
    })())
    monkeypatch.setattr(api, "_limit_spent", lambda *_args, **_kwargs: Decimal("0.00"))

    body = {"workspace_id": 10, "idempotency_key": "limit-key", "scope": "all_expenses", "amount": "1000.00", "period": "month"}
    first = api.create_limit(api.request(42), body)["data"]
    second = api.create_limit(api.request(42), body)["data"]

    assert first == second
    assert len(created) == 1
    assert events.count("mini_app_budget_limit_created") == 1
    assert (42, "limit:create:limit-key") in db.rows

    with pytest.raises(MiniAppError) as exc:
        api.create_limit(api.request(42), {**body, "amount": "1200.00"})
    assert exc.value.code == "idempotency_conflict"


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


def test_frontend_chart_code_uses_visual_decimal_adapter():
    source = open("/root/bot_finuchet/frontend/src/main.ts", encoding="utf-8").read()
    assert "Number(item.expense)" not in source
    assert "Number(item.income)" not in source
    assert "parseFloat" not in source
    assert "decimalStringToVisualPoint" in source
