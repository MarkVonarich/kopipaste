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
        self.general_limits = {}
        self.category_limits = {}
        self.next_limit_id = 1
        self.fail_complete = False


class _IdemConn:
    def __init__(self, db: _IdemDB) -> None:
        self.db = db
        self.rows = {key: value.copy() for key, value in db.rows.items()}
        self.general_limits = {key: value.copy() for key, value in db.general_limits.items()}
        self.category_limits = {key: value.copy() for key, value in db.category_limits.items()}
        self.next_limit_id = db.next_limit_id

    def cursor(self):
        return _IdemCursor(self)

    def commit(self):
        self.db.rows = {key: value.copy() for key, value in self.rows.items()}
        self.db.general_limits = {key: value.copy() for key, value in self.general_limits.items()}
        self.db.category_limits = {key: value.copy() for key, value in self.category_limits.items()}
        self.db.next_limit_id = self.next_limit_id

    def rollback(self):
        pass

    def close(self):
        pass


class _IdemCursor:
    def __init__(self, conn: _IdemConn) -> None:
        self.conn = conn
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
            if idem_key not in self.conn.rows:
                self.conn.rows[idem_key] = {"request_hash": request_hash, "status": "pending", "response_json": None, "operation_id": None}
                self._next = ("pending",)
            return
        if compact.startswith("SELECT request_hash, status, response_json"):
            user_id, key = params
            row = self.conn.rows.get((int(user_id), str(key)))
            if row:
                self._next = (row["request_hash"], row["status"], row["response_json"], row["operation_id"], None, True)
            return
        if compact.startswith("UPDATE public.miniapp_idempotency_keys") and "SET operation_id=%s" in compact:
            if self.conn.db.fail_complete:
                raise RuntimeError("complete failed")
            operation_id, response, user_id, key, request_hash = params
            row = self.conn.rows[(int(user_id), str(key))]
            if row["request_hash"] == request_hash and row["status"] == "pending":
                row["operation_id"] = operation_id
                row["status"] = "completed"
                row["response_json"] = getattr(response, "adapted", response)
                self.rowcount = 1
            return
        if compact.startswith("INSERT INTO public.general_spending_limits"):
            workspace_id, user_id, name, amount, currency, period, alerts_enabled, _thresholds = params
            limit_id = self.conn.next_limit_id
            self.conn.next_limit_id += 1
            self.conn.general_limits[limit_id] = {
                "workspace_id": workspace_id,
                "owner_user_id": int(user_id),
                "name": name,
                "amount": amount,
                "currency": currency,
                "period": period,
                "alerts_enabled": bool(alerts_enabled),
            }
            self._next = (limit_id, name, amount, currency, period, workspace_id, alerts_enabled)
            return
        if compact.startswith("DELETE FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            self.conn.category_limits.pop((int(user_id), workspace_id, period, category), None)
            self.rowcount = 1
            return
        if compact.startswith("INSERT INTO public.category_limits"):
            user_id, workspace_id, period, category, amount, currency = params
            self.conn.category_limits[(int(user_id), workspace_id, period, category)] = {"amount": amount, "currency": currency}
            self._next = (period, category, amount, currency, workspace_id)
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
        if "SELECT DISTINCT COALESCE(currency" in sql:
            return [("RUB",)]
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
    assert data["radar"]["metric"] == "absolute_amount"
    axes = {axis["category"]: axis for axis in data["radar"]["axes"]}
    assert axes["Food"]["current_amount"] == "300.00"


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
    monkeypatch.setattr(api, "_run_idempotent_create", lambda req, action, idem, body, creator: ({"goal": api._goal_dict(_goal(reminders_enabled=True)), "plan_preview": api._plan_preview(req, body, _goal(reminders_enabled=True))}, True))

    body = {
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
    }
    req = api.request(42)
    body["preview_payload_hash"] = api.goal_plan_preview(req, body)["data"]["plan_preview"]["preview_payload_hash"]
    data = api.create_goal(req, body)["data"]

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
        if "SELECT DISTINCT COALESCE(currency" in sql:
            return [("EUR",), ("RUB",)]
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


def test_radar_currency_discovery_uses_previous_period_when_current_empty(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "overview", lambda _req, _params: {"data": {
        "aggregation_available": True,
        "totals_by_currency": {},
        "recent_operations": [],
    }})

    def _fetch(sql, params=()):
        if "SELECT DISTINCT COALESCE(currency" in sql:
            assert "workspace_id = ANY" in sql
            return [("EUR",), ("RUB",)]
        if "GROUP BY category, COALESCE(currency" in sql or "GROUP BY op_date, type" in sql:
            return []
        if "GROUP BY bucket, category" in sql:
            assert "COALESCE(currency" in sql
            assert "RUB" in params
            return [
                ("previous", "Food", Decimal("100.00")),
                ("previous", "Taxi", Decimal("50.00")),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    mixed = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month"})["data"]
    assert mixed["available_currencies"] == []
    assert mixed["radar_available_currencies"] == ["EUR", "RUB"]
    assert mixed["radar"]["reason"] == "mixed_currencies"
    assert mixed["radar"]["axes"] == []

    filtered = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month", "currency": "RUB"})["data"]
    assert filtered["radar"]["currency"] == "RUB"
    assert filtered["radar"]["reason"] is None
    assert filtered["radar"]["axes"][0]["previous_amount"] == "100.00"

    with pytest.raises(MiniAppError) as exc:
        api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month", "currency": "USD"})
    assert exc.value.code == "bad_currency"


def test_radar_single_previous_currency_auto_filters_when_current_empty(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "overview", lambda _req, _params: {"data": {
        "aggregation_available": True,
        "totals_by_currency": {},
        "recent_operations": [],
    }})

    def _fetch(sql, params=()):
        if "SELECT DISTINCT COALESCE(currency" in sql:
            return [("RUB",)]
        if "GROUP BY category, COALESCE(currency" in sql or "GROUP BY op_date, type" in sql:
            return []
        if "GROUP BY bucket, category" in sql:
            assert "RUB" in params
            return [
                ("previous", "Food", Decimal("100.00")),
                ("previous", "Taxi", Decimal("50.00")),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)
    data = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month"})["data"]
    assert data["radar_available_currencies"] == ["RUB"]
    assert data["radar"]["currency"] == "RUB"


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


def test_goal_create_requires_exact_preview_hash(monkeypatch):
    api = _api(monkeypatch)
    saved = []
    monkeypatch.setattr(api, "_run_idempotent_create", lambda req, action, idem, body, creator: saved.append(body) or ({"goal": {"id": 7}}, True))
    body = {
        "workspace_id": 10,
        "idempotency_key": "goal-k",
        "title": "Trip",
        "target_amount": "1000.00",
        "current_amount": "100.00",
        "deadline": "2026-12-31",
        "strategy": "deadline",
        "frequency": "monthly",
        "day": 5,
        "reminders_enabled": True,
    }
    req = api.request(42)
    preview_hash = api.goal_plan_preview(req, body)["data"]["plan_preview"]["preview_payload_hash"]
    api.create_goal(req, {**body, "preview_payload_hash": preview_hash})
    assert saved

    with pytest.raises(MiniAppError) as exc:
        api.create_goal(req, {**body, "target_amount": "1200.00", "preview_payload_hash": preview_hash})
    assert exc.value.status == 409
    assert exc.value.code == "goal_preview_stale"


def test_goal_edit_requires_exact_preview_hash(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: _goal())
    monkeypatch.setattr("miniapp.api.update_goal_details", lambda **_kwargs: _goal(target_amount=Decimal("1200.00")))
    monkeypatch.setattr("miniapp.api.update_goal_plan", lambda **_kwargs: _goal(target_amount=Decimal("1200.00")))
    body = {
        "workspace_id": 10,
        "title": "Trip",
        "target_amount": "1200.00",
        "current_amount": "250.00",
        "deadline": "2026-12-31",
        "strategy": "deadline",
        "frequency": "monthly",
        "day": 5,
        "reminders_enabled": False,
    }
    req = api.request(42)
    preview_hash = api.goal_plan_preview(req, body, goal_id=7)["data"]["plan_preview"]["preview_payload_hash"]
    updated = api.update_goal(req, 7, {**body, "preview_payload_hash": preview_hash})["data"]
    assert updated["goal"]["target"] == "1200.00"

    with pytest.raises(MiniAppError) as exc:
        api.update_goal(req, 7, {**body, "day": 6, "preview_payload_hash": preview_hash})
    assert exc.value.code == "goal_preview_stale"


def test_idempotent_goal_create_replays_and_conflicts(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    events = []
    created = []
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append(event_name))
    def _creator(_cur):
        created.append(1)
        return {"goal": {"id": 7}}

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

    first, first_created = api._run_idempotent_create(api.request(42), "goal:create", "retry-key", body, _creator)
    second, second_created = api._run_idempotent_create(api.request(42), "goal:create", "retry-key", body, _creator)

    assert first == second
    assert first_created is True
    assert second_created is False
    assert len(created) == 1
    assert (42, "goal:create:retry-key") in db.rows

    with pytest.raises(MiniAppError) as exc:
        api._run_idempotent_create(api.request(42), "goal:create", "retry-key", {**body, "title": "Different"}, _creator)
    assert exc.value.status == 409
    assert exc.value.code == "idempotency_conflict"


def test_idempotent_general_limit_create_replays_and_conflicts(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    created = []
    events = []
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append(event_name))
    monkeypatch.setattr(api, "_limit_spent", lambda *_args, **_kwargs: Decimal("0.00"))

    body = {"workspace_id": 10, "idempotency_key": "limit-key", "scope": "all_expenses", "amount": "1000.00", "period": "month"}
    first = api.create_limit(api.request(42), body)["data"]
    second = api.create_limit(api.request(42), body)["data"]

    assert first == second
    assert len(db.general_limits) == 1
    assert events.count("mini_app_budget_limit_created") == 1
    assert (42, "limit:create:limit-key") in db.rows

    with pytest.raises(MiniAppError) as exc:
        api.create_limit(api.request(42), {**body, "amount": "1200.00"})
    assert exc.value.code == "idempotency_conflict"


def test_atomic_idempotent_create_rolls_back_entity_when_creator_fails(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))

    def _creator(cur):
        cur.conn.general_limits[1] = {"amount": Decimal("1000.00")}
        raise RuntimeError("entity insert failed")

    with pytest.raises(RuntimeError):
        api._run_idempotent_create(api.request(42), "general_limit:create", "boom", {"amount": "1000.00"}, _creator)

    assert db.general_limits == {}
    assert db.rows == {}


def test_atomic_idempotent_create_rolls_back_when_completion_fails_then_retry_succeeds(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))

    def _creator(cur):
        cur.conn.general_limits[1] = {"amount": Decimal("1000.00")}
        return {"limit": {"id": "general:1"}}

    db.fail_complete = True
    with pytest.raises(RuntimeError):
        api._run_idempotent_create(api.request(42), "general_limit:create", "retry", {"amount": "1000.00"}, _creator)
    assert db.general_limits == {}
    assert db.rows == {}

    db.fail_complete = False
    first, created = api._run_idempotent_create(api.request(42), "general_limit:create", "retry", {"amount": "1000.00"}, _creator)
    second, replay_created = api._run_idempotent_create(api.request(42), "general_limit:create", "retry", {"amount": "1000.00"}, _creator)
    assert created is True
    assert replay_created is False
    assert first == second
    assert len(db.general_limits) == 1


def test_idempotent_category_limit_create_replays(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    events = []
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append(event_name))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)
    monkeypatch.setattr(api, "_limit_spent", lambda *_args, **_kwargs: Decimal("0.00"))

    body = {"workspace_id": 10, "idempotency_key": "category-limit-key", "scope": "category", "category": "Food", "amount": "1000.00", "period": "month"}
    first = api.create_limit(api.request(42), body)["data"]
    second = api.create_limit(api.request(42), body)["data"]

    assert first == second
    assert len(db.category_limits) == 1
    assert events.count("mini_app_budget_limit_created") == 1


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


def test_frontend_goal_preview_invalidation_is_wired():
    source = open("/root/bot_finuchet/frontend/src/main.ts", encoding="utf-8").read()
    assert "goalPreviewPayloadHash = undefined" in source
    assert "invalidateGoalPreview" in source
    assert "button[data-submit-mode=\"confirm\"]" in source
    assert "preview_payload_hash: state.goalPreviewPayloadHash" in source
