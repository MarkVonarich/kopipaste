from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from miniapp.api import CHART_TOP_N, READ_PAGE_LIMIT, MiniAppAPI, MiniAppError, TransactionFilters
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
                "enabled": True,
                "alerts_enabled": bool(alerts_enabled),
            }
            self._next = (limit_id, name, amount, currency, period, workspace_id, True, alerts_enabled)
            return
        if compact.startswith("UPDATE public.general_spending_limits") and "amount=%s" in compact:
            name, amount, currency, period, alerts_enabled, _thresholds, limit_id, user_id, workspace_id = params
            row = self.conn.general_limits.get(int(limit_id))
            if row and row["owner_user_id"] == int(user_id) and row["workspace_id"] == workspace_id:
                row.update({
                    "name": name,
                    "amount": amount,
                    "currency": currency or row["currency"],
                    "period": period,
                    "alerts_enabled": bool(alerts_enabled),
                })
                self._next = (int(limit_id), name, amount, row["currency"], period, workspace_id, row.get("enabled", True), alerts_enabled)
            return
        if compact.startswith("UPDATE public.general_spending_limits") and "SET enabled=%s" in compact:
            enabled, limit_id, user_id, workspace_id = params
            row = self.conn.general_limits.get(int(limit_id))
            if row and row["owner_user_id"] == int(user_id) and row["workspace_id"] == workspace_id:
                row["enabled"] = bool(enabled)
                self._next = (int(limit_id), row["name"], row["amount"], row["currency"], row["period"], workspace_id, row["enabled"], row["alerts_enabled"])
            return
        if compact.startswith("SELECT currency FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            row = self.conn.category_limits.get((int(user_id), workspace_id, period, category))
            if row:
                self._next = (row["currency"],)
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
    assert data["category_structure"]["items"][-1]["category"] == "Остальные"
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


def test_category_structure_keeps_real_other_separate_from_synthetic_remainder(monkeypatch):
    api = _api(monkeypatch)

    def _fetch(sql, params=()):
        assert "GROUP BY category, COALESCE(currency" in sql
        assert params[-2] == "Расходы"
        return [
            ("Прочее", "RUB", Decimal("1000.00"), 1),
            ("прочее ", "RUB", Decimal("500.00"), 1),
            ("Категория A", "RUB", Decimal("900.00"), 1),
            ("Категория B", "RUB", Decimal("800.00"), 1),
            ("Категория C", "RUB", Decimal("700.00"), 1),
            ("Категория D", "RUB", Decimal("600.00"), 1),
            ("Категория E", "RUB", Decimal("500.00"), 1),
            ("Категория F", "RUB", Decimal("400.00"), 1),
            ("Категория G", "RUB", Decimal("300.00"), 1),
            ("Прочее", "EUR", Decimal("999.00"), 1),
        ]

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 7), "current_week", "all", None, "workspace_id=ANY(%s)", ([10],))

    structure = api._category_structure(api.request(42), tx, "Расходы", currencies=["RUB"])

    rub_items = structure["currency_groups"]["RUB"]["items"]
    assert [item["category"] for item in rub_items].count("Прочее") == 1
    real_other = next(item for item in rub_items if item["category"] == "Прочее")
    synthetic_other = next(item for item in rub_items if item["category"] == "Остальные")
    assert real_other["total"] == Decimal("1500.00")
    assert real_other["count"] == 2
    assert real_other["share"] == 26
    assert synthetic_other["total"] == Decimal("1200.00")
    assert synthetic_other["count"] == 3
    assert synthetic_other["share"] == 21
    assert "EUR" not in structure["currency_groups"]


def test_dimension_category_structure_exposes_comparison_and_synthetic_identity(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 7), "current_week", "all", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 25), date(2026, 7, 31), "previous_equal_period", "all", None, "workspace_id=ANY(%s)", ([10],))

    def _rows(_req, current_tx, _op_type, *, dimension, currencies=None):
        assert dimension == "category"
        if current_tx.start == date(2026, 8, 1):
            return [
                ("Остальные", "RUB", Decimal("1000.00"), 1),
                ("Food", "RUB", Decimal("900.00"), 3),
                ("Taxi", "RUB", Decimal("800.00"), 2),
                ("Cafe", "RUB", Decimal("700.00"), 1),
                ("Books", "RUB", Decimal("600.00"), 1),
                ("Health", "RUB", Decimal("500.00"), 1),
                ("Pets", "RUB", Decimal("400.00"), 1),
            ]
        return [
            ("Food", "RUB", Decimal("500.00"), 2),
            ("Health", "RUB", Decimal("700.00"), 3),
            ("Pets", "RUB", Decimal("100.00"), 1),
        ]

    monkeypatch.setattr(api, "_dimension_rows", _rows)

    structure = api._dimension_structure(api.request(42), tx, prev_tx, "Расходы", dimension="category", currencies=["RUB"])

    items = structure["currency_groups"]["RUB"]["items"]
    real_other = next(item for item in items if item["category"] == "Остальные" and item["key"] != "__synthetic_other_category__")
    synthetic_other = next(item for item in items if item["key"] == "__synthetic_other_category__")
    food = next(item for item in items if item["category"] == "Food")
    assert food["previous_total"] == Decimal("500.00")
    assert food["delta"] == Decimal("400.00")
    assert food["previous_count"] == 2
    assert real_other["synthetic"] is False
    assert real_other["drillable"] is True
    assert synthetic_other["category"] == "Остальные"
    assert synthetic_other["synthetic"] is True
    assert synthetic_other["drillable"] is False
    assert synthetic_other["total"] == Decimal("900.00")
    assert synthetic_other["delta"] == Decimal("100.00")


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

    stale = api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month", "currency": "USD"})["data"]
    assert stale["selected_currency"] is None
    assert stale["available_currencies"] == []

    with pytest.raises(MiniAppError) as exc:
        api.analytics(api.request(42), {"workspace_id": 10, "period": "current_month", "currency": "JPY"})
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


def test_analytics_comparable_period_contracts(monkeypatch):
    api = _api(monkeypatch)

    assert api._previous_period(date(2026, 8, 1), date(2026, 8, 5), "current_month") == (
        date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date"
    )
    assert api._previous_period(date(2026, 8, 1), date(2026, 8, 31), "current_month") == (
        date(2026, 7, 1), date(2026, 7, 31), "previous_month"
    )
    assert api._previous_period(date(2026, 8, 10), date(2026, 8, 19), "custom") == (
        date(2026, 7, 31), date(2026, 8, 9), "previous_equal_period"
    )


def test_overview_metrics_financial_result_and_zero_baseline(monkeypatch):
    api = _api(monkeypatch)

    metrics = api._overview_metrics(
        {"RUB": {"income": Decimal("1000.00"), "expense": Decimal("650.00"), "count": 3}},
        {"RUB": {"income": Decimal("0.00"), "expense": Decimal("500.00"), "count": 2}},
    )

    assert metrics["RUB"]["income"]["state"] == "zero_baseline"
    assert metrics["RUB"]["expense"]["delta"] == Decimal("150.00")
    assert metrics["RUB"]["result"]["current"] == Decimal("350.00")
    assert metrics["RUB"]["result"]["previous"] == Decimal("-500.00")
    assert metrics["RUB"]["result"]["delta"] == Decimal("850.00")


def test_metric_comparison_signed_pct_negative_baseline_and_sign_crossing(monkeypatch):
    api = _api(monkeypatch)

    assert api._metric_comparison(Decimal("650.00"), Decimal("500.00"))["pct"] == Decimal("30.00")
    assert api._metric_comparison(Decimal("400.00"), Decimal("500.00"))["pct"] == Decimal("-20.00")
    improved = api._metric_comparison(Decimal("-500.00"), Decimal("-1000.00"), sign_change_on_cross_zero=True)
    crossed = api._metric_comparison(Decimal("350.00"), Decimal("-500.00"), sign_change_on_cross_zero=True)
    empty = api._metric_comparison(Decimal("0.00"), Decimal("0.00"), sign_change_on_cross_zero=True)

    assert improved["delta"] == Decimal("500.00")
    assert improved["pct"] == Decimal("50.00")
    assert improved["state"] == "ok"
    assert crossed["delta"] == Decimal("850.00")
    assert crossed["pct"] is None
    assert crossed["state"] == "sign_change"
    assert empty["state"] == "empty_previous"


def test_contribution_delta_reconciles_by_currency(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "all", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "all", None, "workspace_id=ANY(%s)", ([10],))

    def _rows(_req, current_tx, _op_type, *, dimension, currencies=None):
        assert dimension == "category"
        if current_tx.start == date(2026, 8, 1):
            return [("Food", "RUB", Decimal("300.00"), 3), ("Taxi", "RUB", Decimal("100.00"), 1)]
        return [("food ", "RUB", Decimal("200.00"), 2), ("Taxi", "RUB", Decimal("150.00"), 1), ("Fun", "RUB", Decimal("50.00"), 1)]

    monkeypatch.setattr(api, "_dimension_rows", _rows)

    contribution = api._change_contribution(api.request(42), tx, prev_tx, "Расходы", currencies=["RUB"])

    group = contribution["currency_groups"]["RUB"]
    assert group["current_total"] == Decimal("400.00")
    assert group["previous_total"] == Decimal("400.00")
    assert group["total_delta"] == Decimal("0.00")
    assert group["reconciles"] is True
    deltas = {item["category"]: item["delta"] for item in group["items"]}
    assert deltas["Food"] == Decimal("100.00")
    assert deltas["Fun"] == Decimal("-50.00")


def test_contribution_reconciles_more_than_read_page_limit_categories(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "all", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "all", None, "workspace_id=ANY(%s)", ([10],))
    categories = [(f"Category {idx:03d}", "RUB", Decimal("1.00"), 1) for idx in range(READ_PAGE_LIMIT + 1)]

    def _rows(_req, current_tx, _op_type, *, dimension, currencies=None):
        assert dimension == "category"
        return categories if current_tx.start == date(2026, 8, 1) else []

    monkeypatch.setattr(api, "_dimension_rows", _rows)

    contribution = api._change_contribution(api.request(42), tx, prev_tx, "Расходы", currencies=["RUB"])

    group = contribution["currency_groups"]["RUB"]
    assert group["current_total"] == Decimal("101.00")
    assert group["previous_total"] == Decimal("0.00")
    assert group["total_delta"] == Decimal("101.00")
    assert group["reconciles"] is True
    assert len(group["items"]) == CHART_TOP_N + 1
    assert group["items"][-1]["category"] == "Остальные"
    assert group["items"][-1]["delta"] == Decimal("96.00")


def test_drilldown_summary_uses_full_rows_preview_stays_limited(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "expense", None, "workspace_id=ANY(%s)", ([10],))
    preview = [
        {"id": idx, "op_date": date(2026, 8, 5), "type": "Расходы", "category": "Food", "amount": Decimal("500.00"), "currency": "RUB", "description": "Lavka", "workspace_id": 10}
        for idx in range(8)
    ]
    monkeypatch.setattr(api, "_detail_operation_rows", lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(api, "_category_merchant_breakdown", lambda *_args, **_kwargs: {"currency": "RUB", "total": Decimal("12500.00"), "count": 25, "items": []})

    def _summary(_req, current_tx, _op_type, _currency, **_kwargs):
        if current_tx.start == date(2026, 8, 1):
            return {"total": Decimal("12500.00"), "operation_count": 25, "average_check": Decimal("500.00")}
        return {"total": Decimal("10000.00"), "operation_count": 20, "average_check": Decimal("500.00")}

    monkeypatch.setattr(api, "_detail_summary", _summary)
    monkeypatch.setattr(api, "_merchant_context", lambda *_args, **_kwargs: {"scope_total": Decimal("12500.00"), "primary_category": None, "categories": []})
    monkeypatch.setattr(api, "_merchant_identity_snapshot", lambda *_args, **_kwargs: {"display_name": "Lavka", "raw_aliases": ["Lavka"]})
    monkeypatch.setattr(api, "_merchant_baseline", lambda *_args, **_kwargs: {"method": "trailing_median", "periods_used": 0, "amount": Decimal("0.00"), "count": 0, "average_check": Decimal("0.00"), "sufficient_data": False})

    merchant = api._analytics_detail(api.request(42), tx, prev_tx, {"detail_kind": "merchant", "detail_value": "Lavka", "detail_currency": "RUB"}, "Расходы")
    category = api._analytics_detail(api.request(42), tx, prev_tx, {"detail_kind": "category", "detail_value": "Food", "detail_currency": "RUB"}, "Расходы")

    assert merchant["total"] == Decimal("12500.00")
    assert merchant["operation_count"] == 25
    assert merchant["average_check"] == Decimal("500.00")
    assert merchant["delta"] == Decimal("2500.00")
    assert len(merchant["operations"]) == 8
    assert category["visible_total"] == Decimal("12500.00")
    assert category["operation_count"] == 25


def test_category_merchant_breakdown_full_total_and_other_share(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _fetch(sql, params=()):
        assert "LIMIT" not in sql
        assert "REPLACE(LOWER" in sql
        assert params[-1] == "vse dlya doma"
        return [(None, Decimal("200.00"), 1)] + [
            (f"Merchant {idx}", Decimal("100.00"), 1)
            for idx in range(6)
        ]

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    breakdown = api._category_merchant_breakdown(api.request(42), tx, "Расходы", "RUB", "vse dlya doma")

    assert breakdown["total"] == Decimal("800.00")
    assert breakdown["count"] == 7
    assert len(breakdown["items"]) == CHART_TOP_N + 1
    assert breakdown["items"][-1]["merchant"] == "Остальные"
    assert breakdown["items"][-1]["total"] == Decimal("200.00")
    assert breakdown["items"][0]["share"] == 25
    assert breakdown["items"][0]["merchant"] == "Без описания"
    assert breakdown["items"][0]["key"] == "__empty_merchant__"
    assert breakdown["items"][0]["drillable"] is False
    assert breakdown["items"][-1]["synthetic"] is True
    assert breakdown["items"][-1]["drillable"] is False


def test_merchant_structure_folds_safe_aliases_with_stable_key(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _rows(_req, current_tx, _op_type, *, dimension, currencies=None):
        assert dimension == "merchant"
        if current_tx.start == date(2026, 8, 1):
            return [
                ("Яндекс Лавка", "RUB", Decimal("300.00"), 1),
                ("ЯНДЕКС*ЛАВКА", "RUB", Decimal("500.00"), 2),
                ("Яндекс-Лавка", "EUR", Decimal("40.00"), 1),
                ("Coffee Point", "RUB", Decimal("100.00"), 1),
            ]
        return [(" яндекс  лавка ", "RUB", Decimal("200.00"), 1)]

    monkeypatch.setattr(api, "_dimension_rows", _rows)

    structure = api._dimension_structure(api.request(42), tx, prev_tx, "Расходы", dimension="merchant", currencies=None)

    rub_items = {item["key"]: item for item in structure["currency_groups"]["RUB"]["items"]}
    assert rub_items["яндекс лавка"]["merchant"] == "Яндекс Лавка"
    assert rub_items["яндекс лавка"]["total"] == Decimal("800.00")
    assert rub_items["яндекс лавка"]["previous_total"] == Decimal("200.00")
    assert rub_items["яндекс лавка"]["count"] == 3
    assert set(rub_items["яндекс лавка"]["raw_aliases"]) == {"Яндекс Лавка", "ЯНДЕКС*ЛАВКА"}
    assert "coffee point" in rub_items
    assert structure["currency_groups"]["EUR"]["items"][0]["key"] == "яндекс лавка"


def test_merchant_detail_uses_key_preserves_raw_comments_and_features(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "expense", None, "workspace_id=ANY(%s)", ([10],))
    operations_seen = {}

    def _operation_rows(_req, _tx, _op_type, _currency, **kwargs):
        operations_seen.update(kwargs)
        return [
            {"id": 1, "description": "Яндекс Лавка", "amount": Decimal("300.00"), "currency": "RUB"},
            {"id": 2, "description": "ЯНДЕКС*ЛАВКА", "amount": Decimal("500.00"), "currency": "RUB"},
        ]

    def _summary(_req, current_tx, _op_type, _currency, **kwargs):
        assert kwargs["merchant_key"] == "яндекс лавка"
        if current_tx.start == date(2026, 8, 1):
            return {"total": Decimal("800.00"), "operation_count": 2, "average_check": Decimal("400.00")}
        return {"total": Decimal("300.00"), "operation_count": 1, "average_check": Decimal("300.00")}

    monkeypatch.setattr(api, "_detail_operation_rows", _operation_rows)
    monkeypatch.setattr(api, "_detail_summary", _summary)
    monkeypatch.setattr(api, "_merchant_context", lambda *_args, **_kwargs: {"scope_total": Decimal("1000.00"), "primary_category": {"category_key": "produkty", "category": "Продукты", "category_total": Decimal("1000.00"), "merchant_total": Decimal("800.00"), "merchant_count": 2, "merchant_share_of_category": Decimal("80.00")}, "categories": []})
    monkeypatch.setattr(api, "_merchant_identity_snapshot", lambda *_args, **_kwargs: {"display_name": "Яндекс Лавка", "raw_aliases": ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА"]})
    monkeypatch.setattr(api, "_merchant_baseline", lambda *_args, **_kwargs: {"method": "trailing_median", "periods_used": 3, "amount": Decimal("700.00"), "count": Decimal("2.00"), "average_check": Decimal("350.00"), "sufficient_data": True})

    detail = api._analytics_detail(api.request(42), tx, prev_tx, {"detail_kind": "merchant", "detail_value": "яндекс лавка", "detail_currency": "RUB"}, "Расходы")

    assert operations_seen["merchant_key"] == "яндекс лавка"
    assert [row["description"] for row in detail["operations"]] == ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА"]
    assert detail["merchant_key"] == "яндекс лавка"
    assert detail["title"] == "Яндекс Лавка"
    assert detail["raw_aliases"] == ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА"]
    assert detail["total"] == Decimal("800.00")
    assert detail["frequency_delta"] == 1
    assert detail["average_check_delta"] == Decimal("100.00")
    assert detail["merchant_share_of_category"] == Decimal("80.00")
    assert detail["merchant_share_of_total"] == Decimal("80.00")
    assert detail["operation_scope"]["merchant_key"] == "яндекс лавка"
    assert detail["baseline"]["sufficient_data"] is True


def test_merchant_detail_preserves_canonical_category_key_across_drilldown(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "expense", None, "workspace_id=ANY(%s)", ([10],))
    seen = {"operations": [], "summaries": [], "context": [], "identity": [], "baseline": []}

    monkeypatch.setattr(api, "_detail_operation_rows", lambda *_args, **kwargs: seen["operations"].append(kwargs) or [])

    def _summary(_req, _tx, _op_type, _currency, **kwargs):
        seen["summaries"].append(kwargs)
        return {"total": Decimal("6000.00"), "operation_count": 6, "average_check": Decimal("1000.00")}

    monkeypatch.setattr(api, "_detail_summary", _summary)
    monkeypatch.setattr(api, "_merchant_context", lambda *_args, **kwargs: seen["context"].append(kwargs) or {"scope_total": Decimal("6000.00"), "primary_category": None, "categories": []})
    monkeypatch.setattr(api, "_merchant_identity_snapshot", lambda *_args, **kwargs: seen["identity"].append(kwargs) or {"display_name": "Shop", "raw_aliases": ["Shop"]})
    monkeypatch.setattr(api, "_merchant_baseline", lambda *_args, **kwargs: seen["baseline"].append(kwargs) or {"method": "trailing_median", "periods_used": 0, "amount": Decimal("0.00"), "count": 0, "average_check": Decimal("0.00"), "sufficient_data": False})

    detail = api._analytics_detail(
        api.request(42),
        tx,
        prev_tx,
        {
            "detail_kind": "merchant",
            "detail_value": "Shop",
            "detail_currency": "RUB",
            "detail_category_key": " ПРОЧЕЕ ",
        },
        "Расходы",
    )

    assert detail["category_key"] == "прочее"
    assert detail["operation_scope"]["category_key"] == "прочее"
    assert all(item["category_key"] == "прочее" for values in seen.values() for item in values)


def test_category_merchant_breakdown_folds_alias_share_against_full_denominator(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _fetch(_sql, _params=()):
        return [
            ("Яндекс Лавка", Decimal("5000.00"), 5),
            ("ЯНДЕКС*ЛАВКА", Decimal("3000.00"), 4),
            ("Другой магазин", Decimal("2000.00"), 2),
        ]

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    breakdown = api._category_merchant_breakdown(api.request(42), tx, "Расходы", "RUB", "produkty")

    assert breakdown["total"] == Decimal("10000.00")
    assert breakdown["items"][0]["key"] == "яндекс лавка"
    assert breakdown["items"][0]["total"] == Decimal("8000.00")
    assert breakdown["items"][0]["share"] == 80
    assert breakdown["items"][0]["drillable"] is True


def test_analytics_search_returns_canonical_merchant_result_and_key(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _fetch(sql, params=()):
        if "AS merchant_key" in sql and "COALESCE(currency" in sql:
            assert "%лавка%" in params
            assert "LIMIT 40" not in sql
            return [
                ("яндекс лавка", "Яндекс Лавка", "RUB", Decimal("300.00"), 1),
                ("яндекс лавка", "ЯНДЕКС*ЛАВКА", "RUB", Decimal("500.00"), 1),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    search = api._analytics_search(api.request(42), tx, {"analytics_search": "лавка", "currency": "RUB"}, "Расходы", currencies=["RUB"])

    assert len(search["items"]) == 1
    item = search["items"][0]
    assert item["kind"] == "merchant"
    assert item["amount"] == Decimal("800.00")
    assert item["subtitle"] == "2 операций"
    assert item["params"]["detail_value"] == "яндекс лавка"


def test_analytics_search_does_not_undercount_more_than_raw_prefold_limit(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _fetch(sql, params=()):
        if "AS merchant_key" in sql and "COALESCE(currency" in sql:
            assert "LIMIT 40" not in sql
            return [
                ("яндекс лавка", f"Яндекс{'*' * idx}Лавка", "RUB", Decimal("10.00"), 1)
                for idx in range(1, 46)
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    search = api._analytics_search(api.request(42), tx, {"analytics_search": "лавка", "currency": "RUB"}, "Расходы", currencies=["RUB"])

    merchant = search["items"][0]
    assert merchant["kind"] == "merchant"
    assert merchant["params"]["detail_value"] == "яндекс лавка"
    assert merchant["amount"] == Decimal("450.00")
    assert merchant["subtitle"] == "45 операций"


def test_merchant_reconciliation_structure_search_detail_and_open_operations(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))
    prev_tx = TransactionFilters([10], False, date(2026, 7, 1), date(2026, 7, 5), "previous_month_to_date", "expense", None, "workspace_id=ANY(%s)", ([10],))
    source_rows = [
        ("Яндекс Лавка", Decimal("300.00"), 1),
        ("ЯНДЕКС*ЛАВКА", Decimal("500.00"), 1),
        ("Яндекс-Лавка", Decimal("200.00"), 1),
    ]

    monkeypatch.setattr(api, "_dimension_rows", lambda _req, current_tx, _op_type, *, dimension, currencies=None: [(raw, "RUB", amount, count) for raw, amount, count in source_rows] if dimension == "merchant" and current_tx.start == date(2026, 8, 1) else [])
    monkeypatch.setattr(api, "_detail_operation_rows", lambda *_args, **kwargs: [
        {"id": 1, "description": "Яндекс Лавка"},
        {"id": 2, "description": "ЯНДЕКС*ЛАВКА"},
        {"id": 3, "description": "Яндекс-Лавка"},
    ] if kwargs.get("merchant_key") == "яндекс лавка" else [])
    monkeypatch.setattr(api, "_detail_summary", lambda _req, current_tx, _op_type, _currency, **kwargs: {"total": Decimal("1000.00"), "operation_count": 3, "average_check": Decimal("333.33")} if kwargs.get("merchant_key") == "яндекс лавка" and current_tx.start == date(2026, 8, 1) else {"total": Decimal("0.00"), "operation_count": 0, "average_check": Decimal("0.00")})
    monkeypatch.setattr(api, "_merchant_context", lambda *_args, **_kwargs: {"scope_total": Decimal("1000.00"), "primary_category": {"category_key": "produkty", "category": "Продукты", "category_total": Decimal("1000.00"), "merchant_total": Decimal("1000.00"), "merchant_count": 3}, "categories": []})
    monkeypatch.setattr(api, "_merchant_identity_snapshot", lambda *_args, **_kwargs: {"display_name": "Яндекс Лавка", "raw_aliases": ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА", "Яндекс-Лавка"]})
    monkeypatch.setattr(api, "_merchant_baseline", lambda *_args, **_kwargs: {"method": "trailing_median", "periods_used": 0, "amount": Decimal("0.00"), "count": 0, "average_check": Decimal("0.00"), "sufficient_data": False})

    def _fetch(sql, params=()):
        if "AS merchant_key" in sql and "COALESCE(currency" in sql:
            return [("яндекс лавка", raw, "RUB", amount, count) for raw, amount, count in source_rows]
        if "SELECT o.id, o.op_date" in sql:
            assert "яндекс лавка" in params
            return [
                (1, date(2026, 8, 1), "Расходы", "Продукты", Decimal("300.00"), "RUB", "Яндекс Лавка", 10, 42, datetime(2026, 8, 1, 12), "Family"),
                (2, date(2026, 8, 2), "Расходы", "Продукты", Decimal("500.00"), "RUB", "ЯНДЕКС*ЛАВКА", 10, 42, datetime(2026, 8, 2, 12), "Family"),
                (3, date(2026, 8, 3), "Расходы", "Продукты", Decimal("200.00"), "RUB", "Яндекс-Лавка", 10, 42, datetime(2026, 8, 3, 12), "Family"),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    structure = api._dimension_structure(api.request(42), tx, prev_tx, "Расходы", dimension="merchant", currencies=["RUB"])
    search = api._analytics_search(api.request(42), tx, {"analytics_search": "лавка", "currency": "RUB"}, "Расходы", currencies=["RUB"])
    detail = api._analytics_detail(api.request(42), tx, prev_tx, {"detail_kind": "merchant", "detail_value": "яндекс лавка", "detail_currency": "RUB"}, "Расходы")
    operations = api.operations(api.request(42), {"workspace_id": 10, "period": "custom", "start_date": "2026-08-01", "end_date": "2026-08-05", "operation_type": "expense", "currency": "RUB", "merchant_key": "яндекс лавка"})["data"]["items"]

    merchant_row = structure["currency_groups"]["RUB"]["items"][0]
    assert merchant_row["key"] == "яндекс лавка"
    assert merchant_row["total"] == Decimal("1000.00")
    assert merchant_row["count"] == 3
    assert search["items"][0]["amount"] == Decimal("1000.00")
    assert search["items"][0]["subtitle"] == "3 операций"
    assert detail["total"] == Decimal("1000.00")
    assert detail["operation_count"] == 3
    assert [item["description"] for item in operations] == ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА", "Яндекс-Лавка"]


def test_operations_merchant_key_filter_keeps_workspace_authorization(monkeypatch):
    api = _api(monkeypatch)
    seen = {}

    def _fetch(sql, params=()):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return [
            (1, date(2026, 8, 1), "Расходы", "Продукты", Decimal("300.00"), "RUB", "Яндекс Лавка", 10, 42, datetime(2026, 8, 1, 12), "Family"),
            (2, date(2026, 8, 2), "Расходы", "Продукты", Decimal("500.00"), "RUB", "ЯНДЕКС*ЛАВКА", 10, 42, datetime(2026, 8, 2, 12), "Family"),
        ]

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    data = api.operations(api.request(42), {
        "workspace_id": 10,
        "period": "current_month",
        "operation_type": "expense",
        "merchant_key": "яндекс лавка",
        "currency": "RUB",
    })["data"]

    assert [item["description"] for item in data["items"]] == ["Яндекс Лавка", "ЯНДЕКС*ЛАВКА"]
    assert "workspace_id = ANY" in seen["sql"]
    assert "REGEXP_REPLACE" in seen["sql"]
    assert "яндекс лавка" in seen["params"]


def test_analytics_search_and_detail_scope_use_current_filters(monkeypatch):
    api = _api(monkeypatch)
    seen_sql = []

    def _fetch(sql, params=()):
        seen_sql.append(" ".join(sql.split()))
        if "GROUP BY REPLACE(LOWER" in sql:
            assert "workspace_id=ANY" in sql
            assert "type=%s" in sql
            return []
        if "AS merchant_key" in sql and "COALESCE(currency" in sql:
            return [("lavka", "Lavka", "RUB", Decimal("500.00"), 2)]
        if "GROUP BY NULLIF(TRIM" in sql:
            return [("Lavka", Decimal("500.00"), 2)]
        if "SELECT id, op_date, category" in sql:
            return [(9, date(2026, 8, 2), "Food", Decimal("300.00"), "RUB", "Lavka")]
        if "SELECT o.id, o.op_date" in sql:
            assert "COALESCE(o.currency" in sql
            assert "REGEXP_REPLACE" in sql
            return [(9, date(2026, 8, 2), "Расходы", "Food", Decimal("500.00"), "RUB", "Lavka", 10, 42, datetime(2026, 8, 2, 12), "Family")]
        if "SELECT COALESCE(SUM(amount),0), COUNT(*)" in sql:
            return [(Decimal("500.00"), 2)]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s) AND op_date BETWEEN %s AND %s", ([10], date(2026, 8, 1), date(2026, 8, 5)))

    search = api._analytics_search(api.request(42), tx, {"analytics_search": "Lav"}, "Расходы", currencies=["RUB"])
    detail = api._analytics_detail(api.request(42), tx, tx, {"detail_kind": "category", "detail_value": " food ", "detail_currency": "RUB"}, "Расходы")

    assert search["items"][0]["kind"] == "merchant"
    assert detail["operation_scope"]["category_key"] == "food"
    assert detail["operations"][0]["id"] == 9
    assert any("workspace_id=ANY" in sql for sql in seen_sql)


def test_analytics_search_aggregates_categories_and_returns_real_operations(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "expense", None, "workspace_id=ANY(%s)", ([10],))

    def _fetch(sql, params=()):
        if "GROUP BY REPLACE(LOWER" in sql:
            assert "%food%" in params
            return [("food", " Food ", "RUB", Decimal("800.00"), 2)]
        if "AS merchant_key" in sql and "COALESCE(currency" in sql:
            return [("lavka", "Lavka", "RUB", Decimal("800.00"), 2)]
        if "SELECT id, op_date, category" in sql:
            return [
                (12, date(2026, 8, 3), "Food", Decimal("500.00"), "RUB", "Lavka"),
                (11, date(2026, 8, 2), " food ", Decimal("300.00"), "RUB", "Lavka"),
            ]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    search = api._analytics_search(api.request(42), tx, {"analytics_search": "Food", "currency": "RUB"}, "Расходы", currencies=["RUB"])

    category = next(item for item in search["items"] if item["kind"] == "category")
    operation = next(item for item in search["items"] if item["kind"] == "operation" and item["operation_id"] == 12)
    assert category["amount"] == Decimal("800.00")
    assert category["subtitle"] == "2 операций"
    assert operation["amount"] == Decimal("500.00")


def test_operations_category_key_uses_canonical_scope_without_exact_category(monkeypatch):
    api = _api(monkeypatch)
    seen = {}

    def _fetch(sql, params=()):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return [
            (1, date(2026, 8, 1), "Расходы", "Всё для дома", Decimal("100.00"), "RUB", "A", 10, 42, datetime(2026, 8, 1, 12), "Family"),
            (2, date(2026, 8, 2), "Расходы", " все   для дома ", Decimal("200.00"), "RUB", "B", 10, 42, datetime(2026, 8, 2, 12), "Family"),
            (3, date(2026, 8, 3), "Расходы", "ВСЁ ДЛЯ ДОМА", Decimal("300.00"), "RUB", "C", 10, 42, datetime(2026, 8, 3, 12), "Family"),
        ]

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    data = api.operations(api.request(42), {
        "workspace_id": 10,
        "period": "current_month",
        "operation_type": "expense",
        "category": "ВСЁ ДЛЯ ДОМА",
        "category_key": "Всё   для дома",
    })["data"]

    assert [item["id"] for item in data["items"]] == [1, 2, 3]
    assert "o.category=%s" not in seen["sql"]
    assert "REPLACE(LOWER" in seen["sql"]
    assert "все для дома" in seen["params"]


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


def test_goal_edit_preview_uses_persisted_balance_and_explicit_empty_deadline(monkeypatch):
    api = _api(monkeypatch)
    goal = _goal(current_balance=Decimal("250.00"), deadline=date(2026, 12, 31))
    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: goal)
    body = {
        "workspace_id": 10,
        "title": "Updated trip",
        "target_amount": "1500.00",
        "current_amount": "999999.00",
        "deadline": "",
        "strategy": "contribution",
        "frequency": "monthly",
        "comfortable_amount": "200.00",
        "day": 10,
        "reminders_enabled": False,
    }

    first = api.goal_plan_preview(api.request(42), body, goal_id=7)["data"]["plan_preview"]
    second = api.goal_plan_preview(api.request(42), {**body, "title": "Another title"}, goal_id=7)["data"]["plan_preview"]

    assert first["remaining_amount"] == "1250.00"
    assert first["preview_payload_hash"] == second["preview_payload_hash"]
    assert first["projected_completion_date"] is not None


def test_goal_edit_clears_comfortable_amount_and_stale_schedule(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_safe_goal_event", lambda *_args, **_kwargs: None)
    goal = _goal(strategy="contribution", comfortable_amount=Decimal("200.00"), schedule_config={"day": 5})
    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: goal)
    captured = []
    monkeypatch.setattr(
        "miniapp.api.update_goal_plan",
        lambda **kwargs: captured.append(kwargs) or _goal(strategy="none", frequency="none", comfortable_amount=None, schedule_config={}),
    )
    body = {
        "workspace_id": 10,
        "strategy": "none",
        "frequency": "none",
        "comfortable_amount": "",
        "reminders_enabled": False,
    }
    req = api.request(42)
    body["preview_payload_hash"] = api.goal_plan_preview(req, body, goal_id=7)["data"]["plan_preview"]["preview_payload_hash"]

    updated = api.update_goal(req, 7, body)["data"]["goal"]

    assert captured[0]["comfortable_amount"] is None
    assert captured[0]["schedule_config"] == {}
    assert updated["strategy"] == "none"


def test_goal_archive_restore_and_permanent_delete_are_workspace_scoped(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_safe_goal_event", lambda *_args, **_kwargs: None)
    archived = _goal(status="archived")
    monkeypatch.setattr("miniapp.api.set_goal_status", lambda goal_id, owner_user_id, workspace_id, status: _goal(id=goal_id, owner_user_id=owner_user_id, workspace_id=workspace_id, status=status))

    restored = api.goal_status(api.request(42), 7, {"workspace_id": 10, "status": "active"})["data"]["goal"]
    assert restored["status"] == "active"

    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: archived)
    deleted = []
    monkeypatch.setattr("miniapp.api.delete_goal_permanently", lambda goal_id, owner_user_id, workspace_id: deleted.append((goal_id, owner_user_id, workspace_id)) or 3)
    response = api.delete_goal(api.request(42), 7, {"workspace_id": 10})["data"]
    assert response == {"deleted": True, "goal_id": 7, "deleted_movement_count": 3}
    assert deleted == [(7, 42, 10)]

    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: _goal(status="active"))
    with pytest.raises(MiniAppError) as active:
        api.delete_goal(api.request(42), 7, {"workspace_id": 10})
    assert active.value.code == "goal_not_archived"


def test_goal_mutations_reject_read_only_workspace_and_foreign_goal(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_detail", lambda _req, _workspace_id: WorkspaceContext(10, -100, 42, "group", "viewer", "Family", True))
    called = []
    monkeypatch.setattr("miniapp.api.set_goal_status", lambda *_args, **_kwargs: called.append(True))

    with pytest.raises(MiniAppError) as read_only:
        api.goal_status(api.request(42), 7, {"workspace_id": 10, "status": "archived"})
    assert read_only.value.code == "workspace_read_only"
    assert called == []

    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.get_goal", lambda *_args, **_kwargs: None)
    with pytest.raises(MiniAppError) as foreign:
        api.delete_goal(api.request(42), 999, {"workspace_id": 10})
    assert foreign.value.code == "goal_not_found"


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


def test_update_general_limit_without_currency_preserves_existing_currency(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    db.general_limits[1] = {
        "workspace_id": 10,
        "owner_user_id": 42,
        "name": "EUR cap",
        "amount": Decimal("1000.00"),
        "currency": "EUR",
        "period": "month",
        "enabled": False,
        "alerts_enabled": True,
    }
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_limit_spent", lambda *_args, **_kwargs: Decimal("0.00"))

    data = api.update_limit(api.request(42), "general:1", {
        "workspace_id": 10,
        "scope": "all_expenses",
        "title": "Updated",
        "amount": "900.00",
        "period": "month",
    })["data"]

    assert data["limit"]["currency"] == "EUR"
    assert data["limit"]["enabled"] is False
    assert db.general_limits[1]["currency"] == "EUR"
    assert db.general_limits[1]["enabled"] is False


def test_toggle_general_limit_changes_enabled_without_alerts(monkeypatch):
    api = _api(monkeypatch)
    db = _IdemDB()
    db.general_limits[1] = {
        "workspace_id": 10,
        "owner_user_id": 42,
        "name": "General",
        "amount": Decimal("1000.00"),
        "currency": "RUB",
        "period": "month",
        "enabled": False,
        "alerts_enabled": False,
    }
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _IdemConn(db))
    monkeypatch.setattr(api, "_limit_spent", lambda *_args, **_kwargs: Decimal("0.00"))

    enabled = api.update_limit(api.request(42), "general:1", {"workspace_id": 10, "toggle": True, "enabled": True})["data"]["limit"]
    disabled = api.update_limit(api.request(42), "general:1", {"workspace_id": 10, "toggle": True, "enabled": False})["data"]["limit"]

    assert enabled["enabled"] is True
    assert enabled["alerts_enabled"] is False
    assert disabled["enabled"] is False
    assert disabled["alerts_enabled"] is False


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
    notification_state = {
        "goal_notifications_enabled": False,
        "limit_alerts_enabled": False,
        "budget_alerts_enabled": False,
        "subscription_alerts_enabled": False,
        "recurring_spend_alerts_enabled": False,
    }

    def _toggle_notification_preference(_user_id, key):
        assert key == "goals"
        notification_state["goal_notifications_enabled"] = not notification_state["goal_notifications_enabled"]
        return notification_state["goal_notifications_enabled"]

    def _grouped_notification_preferences(_user_id):
        plans_enabled = bool(
            notification_state["limit_alerts_enabled"]
            or notification_state["budget_alerts_enabled"]
            or notification_state["goal_notifications_enabled"]
            or notification_state["subscription_alerts_enabled"]
            or notification_state["recurring_spend_alerts_enabled"]
        )
        return {
            **notification_state,
            "daily_notifications": {"enabled": False, "morning_time": "08:30", "evening_time": "20:30"},
            "plans_control": {"enabled": plans_enabled},
            "reports": {"enabled": False},
            "quiet_hours": {"enabled": False, "start": "22:30", "end": "08:00"},
            "timezone": "Europe/Moscow",
        }

    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append((event_name, kwargs)))
    monkeypatch.setattr("miniapp.api.toggle_notification_preference", _toggle_notification_preference)
    monkeypatch.setattr("miniapp.api.grouped_notification_preferences", _grouped_notification_preferences)
    monkeypatch.setattr("miniapp.api.get_notification_preferences", lambda _user_id: (_ for _ in ()).throw(AssertionError("unexpected real notification DB access")))

    data = api.update_notification_preferences(api.request(42), {"action": "toggle", "key": "goals"})["data"]
    assert data["goal_notifications_enabled"] is True
    assert data["plans_control"]["enabled"] is True
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
