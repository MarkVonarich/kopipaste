from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError, TransactionFilters
from miniapp.auth import MiniAppUser
from services.challenges import ChallengeCard, ChallengeDefinition
from services.operations import RecordedOperation
from services.workspaces import WorkspaceContext


def _api(monkeypatch) -> MiniAppAPI:
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr("miniapp.api.get_user_locale", lambda _user_id: "ru")
    monkeypatch.setattr("miniapp.api.get_user_preferred_name", lambda _user_id: None)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 4))
    return api


def test_bootstrap_starts_home_ready_workspace_context(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [
        {"workspace_id": 10, "name": "Family", "kind": "group", "role": "member", "active": True, "read_only": False}
    ])
    monkeypatch.setattr(api, "_profile_theme", lambda _user_id: "telegram")
    monkeypatch.setattr("miniapp.api.user_timezone_name", lambda _user_id: ("Europe/Moscow", "user"))

    data = api.bootstrap(api.request(42))["data"]

    assert data["user"]["id"] == "42"
    assert data["workspaces"][0]["workspace_id"] == "all"
    assert data["workspaces"][1]["workspace_id"] == 10
    assert data["theme"] == "telegram"


def test_bootstrap_exposes_current_week_not_last_30(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [])
    monkeypatch.setattr(api, "_profile_theme", lambda _user_id: "telegram")
    monkeypatch.setattr("miniapp.api.user_timezone_name", lambda _user_id: ("Europe/Moscow", "user"))

    periods = api.bootstrap(api.request(42))["data"]["periods"]

    assert "current_week" in periods
    assert "last_30" not in periods


def test_current_week_period_uses_local_monday(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 7))

    start, end, key = api._period(api.request(42), {"period": "current_week"}, 10)

    assert key == "current_week"
    assert start == date(2026, 8, 3)
    assert end == date(2026, 8, 7)


def test_legacy_last_30_period_is_mapped_to_current_week(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 7))

    start, _end, key = api._period(api.request(42), {"period": "last_30"}, 10)

    assert key == "current_week"
    assert start == date(2026, 8, 3)


def test_transaction_filter_sql_is_parameterized(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _workspace_id: ([10], False))
    monkeypatch.setattr(api, "_workspace_filter_sql", lambda _ids, _user_id, alias="": ("o.workspace_id = ANY(%s)", ([10],)))

    tx = api._transaction_filters(api.request(42), {"workspace_id": 10, "period": "current_month", "operation_type": "expense", "category": "Food'); DROP TABLE operations; --"}, alias="o")

    assert "DROP TABLE" not in tx.where_sql
    assert tx.category in tx.params
    assert "o.category=%s" in tx.where_sql


def test_radar_returns_absolute_amounts_and_scale(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters(
        workspace_ids=[10],
        all_scope=False,
        start=date(2026, 8, 1),
        end=date(2026, 8, 7),
        period_key="current_week",
        operation_type="expense",
        category=None,
        where_sql="workspace_id = ANY(%s) AND op_date BETWEEN %s AND %s AND COALESCE(type,'') <> 'noop' AND COALESCE(category,'') <> 'Без операций' AND type=%s",
        params=([10], date(2026, 8, 1), date(2026, 8, 7), "Расходы"),
    )
    monkeypatch.setattr(api, "_workspace_filter_sql", lambda _ids, _user_id: ("workspace_id = ANY(%s)", ([10],)))
    monkeypatch.setattr("miniapp.api.pg_fetchall", lambda _sql, _params: [
        ("current", "Продукты", Decimal("15000.00")),
        ("previous", "Продукты", Decimal("12000.00")),
        ("current", "Заведения", Decimal("7000.00")),
        ("previous", "Заведения", Decimal("10000.00")),
    ])

    radar = api._radar(api.request(42), tx, date(2026, 7, 25), date(2026, 7, 31), "previous_equal_period", "Расходы", currency="RUB", available_currencies=["RUB"])

    assert radar["metric"] == "absolute_amount"
    assert radar["axes"][0]["current_amount"] == Decimal("15000.00")
    assert "current" not in radar["axes"][0]
    assert Decimal(radar["scale"]["max"]) >= Decimal("15000.00")


def test_activity_calendar_counts_operations_not_amount(monkeypatch):
    api = _api(monkeypatch)
    tx = TransactionFilters(
        workspace_ids=[10],
        all_scope=False,
        start=date(2026, 8, 1),
        end=date(2026, 8, 3),
        period_key="custom",
        operation_type="all",
        category=None,
        where_sql="workspace_id = ANY(%s) AND op_date BETWEEN %s AND %s",
        params=([10], date(2026, 8, 1), date(2026, 8, 3)),
    )
    monkeypatch.setattr("miniapp.api.pg_fetchall", lambda _sql, _params: [(date(2026, 8, 1), 2), (date(2026, 8, 3), 1)])

    calendar = api._activity_calendar(api.request(42), tx)

    assert calendar["max_count"] == 2
    assert [day["count"] for day in calendar["days"]] == [2, 0, 1]


def test_home_reminder_completed_today_uses_existing_events(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_timezone_name", lambda _user_id: ("Europe/Moscow", None))

    def _fetch(sql, _params):
        if "event_type='recorded'" in sql:
            return [(5, "Интернет", "Подписки", Decimal("800.00"), "RUB", date(2026, 8, 7), date(2026, 9, 7), "monthly", True, None)]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    reminder = api._home_reminder(api.request(42))

    assert reminder["state"] == "completed_today"
    assert reminder["next_event_date"] == date(2026, 9, 7)


def test_create_operation_uses_authenticated_actor_and_decimal_amount(monkeypatch):
    api = _api(monkeypatch)
    ctx = WorkspaceContext(10, -100, 42, "group", "member", "Family", True)
    captured = {}
    post_commit = []

    monkeypatch.setattr(api, "_write_workspace", lambda _req, workspace_id: ctx if workspace_id == 10 else pytest.fail("bad workspace"))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)

    def _create(**kwargs):
        captured.update(kwargs)
        recorded = RecordedOperation(
            operation_id=777,
            workspace_id=10,
            actor_user_id=kwargs["req"].user_id,
            user_id=42,
            chat_id=-100,
            amount=kwargs["amount"],
            currency="RUB",
            type=kwargs["op_type"],
            category=kwargs["category"],
            operation_date=kwargs["op_date"],
            source="miniapp",
            comment=kwargs["description"],
        )
        return {"operation": {"id": 777}}, recorded, True

    monkeypatch.setattr(api, "_create_operation_atomically", _create)
    monkeypatch.setattr("miniapp.api.record_financial_operation_post_commit", lambda *args, **kwargs: post_commit.append((args, kwargs)))

    response = api.create_operation(api.request(42), {
        "user_id": 999,
        "workspace_id": 10,
        "idempotency_key": "k1",
        "type": "expense",
        "amount": "216.34",
        "category": "Food",
        "description": "Lunch",
        "op_date": "2026-08-04",
    })

    assert captured["req"].user_id == 42
    assert captured["amount"] == Decimal("216.34")
    assert response["data"]["operation"]["id"] == 777
    assert post_commit


def test_duplicate_create_returns_idempotent_response_without_insert(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_write_workspace", lambda *_args: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)
    monkeypatch.setattr(api, "_create_operation_atomically", lambda **_kwargs: ({"operation": {"id": 777}}, None, False))
    monkeypatch.setattr("miniapp.api.record_financial_operation_post_commit", lambda *_args, **_kwargs: pytest.fail("replay must not emit side effects"))

    response = api.create_operation(api.request(42), {
        "workspace_id": 10,
        "idempotency_key": "k1",
        "type": "expense",
        "amount": "216.34",
        "category": "Food",
        "description": "Lunch",
        "op_date": "2026-08-04",
    })

    assert response["data"]["operation"]["id"] == 777


def test_create_operation_post_commit_hook_failure_does_not_fail_saved_operation(monkeypatch):
    api = _api(monkeypatch)
    recorded = RecordedOperation(
        operation_id=777,
        workspace_id=10,
        actor_user_id=42,
        user_id=42,
        chat_id=-100,
        amount=Decimal("216.34"),
        currency="RUB",
        type="Расходы",
        category="Food",
        operation_date=date(2026, 8, 4),
        source="miniapp",
        comment="Lunch",
    )
    monkeypatch.setattr(api, "_write_workspace", lambda *_args: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)
    monkeypatch.setattr(api, "_create_operation_atomically", lambda **_kwargs: ({"operation": {"id": 777}}, recorded, True))
    monkeypatch.setattr(
        "miniapp.api.record_financial_operation_post_commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("activity unavailable")),
    )

    response = api.create_operation(api.request(42), {
        "workspace_id": 10,
        "idempotency_key": "k1",
        "type": "expense",
        "amount": "216.34",
        "category": "Food",
        "description": "Lunch",
        "op_date": "2026-08-04",
    })

    assert response["data"]["operation"]["id"] == 777


def test_all_workspaces_are_read_only_for_writes(monkeypatch):
    api = _api(monkeypatch)

    with pytest.raises(MiniAppError) as exc:
        api.create_operation(api.request(42), {"workspace_id": "all", "idempotency_key": "k1"})

    assert exc.value.code == "concrete_workspace_required"


def test_foreign_workspace_is_denied(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [
        {"workspace_id": 10, "name": "Family", "kind": "group", "role": "member", "active": True, "read_only": False}
    ])

    with pytest.raises(MiniAppError) as exc:
        api._read_scope(api.request(42), 99)

    assert exc.value.code == "workspace_access_denied"


def test_update_operation_uses_shared_service(monkeypatch):
    api = _api(monkeypatch)
    calls = []
    monkeypatch.setattr(api, "_operation_row", lambda _req, _operation_id: (
        7, date(2026, 8, 4), "Расходы", "Food", Decimal("100.00"), "RUB", "Old", 10, 42, None, "Family"
    ))
    monkeypatch.setattr(api, "_operation_write_context", lambda _req, _workspace_id: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))

    def _update(**kwargs):
        calls.append(kwargs)
        return {"id": kwargs["operation_id"], "amount": kwargs["amount"], "category": "Food"}

    monkeypatch.setattr("miniapp.api.update_financial_operation", _update)

    data = api.update_operation(api.request(42), 7, {"amount": "150.25"})["data"]

    assert data["operation"]["id"] == 7
    assert calls[0]["source"] == "miniapp"
    assert calls[0]["require_user_id"] is False


def test_delete_operation_uses_shared_service_and_requires_confirmation_endpoint_only(monkeypatch):
    api = _api(monkeypatch)
    calls = []
    monkeypatch.setattr(api, "_operation_row", lambda _req, _operation_id: (
        7, date(2026, 8, 4), "Расходы", "Food", Decimal("100.00"), "RUB", "Old", 10, 42, None, "Family"
    ))
    monkeypatch.setattr(api, "_operation_write_context", lambda _req, _workspace_id: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr("miniapp.api.delete_financial_operation", lambda **kwargs: calls.append(kwargs) or {"id": kwargs["operation_id"]})

    data = api.delete_operation(api.request(42), 7)["data"]

    assert data["deleted"] is True
    assert calls[0]["source"] == "miniapp"


def test_idempotency_conflict_returns_409(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_write_workspace", lambda *_args: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)

    def _create(**_kwargs):
        raise MiniAppError(409, "idempotency_conflict", "conflict")

    monkeypatch.setattr(api, "_create_operation_atomically", _create)

    with pytest.raises(MiniAppError) as exc:
        api.create_operation(api.request(42), {
            "workspace_id": 10,
            "idempotency_key": "k1",
            "type": "expense",
            "amount": "216.34",
            "category": "Food",
            "description": "Lunch",
            "op_date": "2026-08-04",
        })

    assert exc.value.status == 409
    assert exc.value.code == "idempotency_conflict"


def test_pending_idempotency_does_not_create_second_operation(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_write_workspace", lambda *_args: WorkspaceContext(10, -100, 42, "group", "member", "Family", True))
    monkeypatch.setattr(api, "_validate_category", lambda _req, _workspace_id, _op_type, category: category)

    def _create(**_kwargs):
        raise MiniAppError(409, "idempotency_pending", "pending")

    monkeypatch.setattr(api, "_create_operation_atomically", _create)

    with pytest.raises(MiniAppError) as exc:
        api.create_operation(api.request(42), {
            "workspace_id": 10,
            "idempotency_key": "k1",
            "type": "expense",
            "amount": "216.34",
            "category": "Food",
            "description": "Lunch",
            "op_date": "2026-08-04",
        })

    assert exc.value.code == "idempotency_pending"


def test_categories_endpoint_uses_managed_categories(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _workspace_id: ([10], False))
    monkeypatch.setattr("miniapp.api.list_managed_categories", lambda **_kwargs: [
        type("Cat", (), {"name": "Food", "normalized_name": "food", "op_type": "Расходы", "source": "custom", "operation_count": 3, "has_budget": True})()
    ])

    data = api.categories(api.request(42), {"workspace_id": 10, "type": "expense"})["data"]

    assert data["items"][0]["name"] == "Food"


def test_profile_links_are_configured_not_repo_paths(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [])
    monkeypatch.setattr(api, "_profile_theme", lambda _user_id: "telegram")
    monkeypatch.setattr("miniapp.api.user_timezone_name", lambda _user_id: ("Europe/Moscow", "user"))
    monkeypatch.delenv("MINIAPP_PRIVACY_URL", raising=False)
    monkeypatch.delenv("MINIAPP_TERMS_URL", raising=False)

    data = api.profile(api.request(42))["data"]

    assert data["links"]["privacy"] is None
    assert data["links"]["terms"] is None


def test_plans_limit_spent_is_workspace_scoped(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _workspace_id: ([10], False))
    monkeypatch.setattr("miniapp.api.list_goals", lambda *_args, **_kwargs: [])

    def _fetch(sql, _params=()):
        if "FROM public.category_limits" in sql:
            assert "workspace_id=%s" in sql
            assert _params == (42, 10)
            return [("month", "Food", Decimal("1000.00"), "RUB")]
        if "FROM public.operations" in sql:
            assert "workspace_id = ANY" in sql
            return [(Decimal("250.00"),)]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    data = api.plans(api.request(42), {"workspace_id": 10})["data"]

    assert data["limits"][0]["spent"] == "250.00"


def test_overview_keeps_mixed_currencies_unaggregated(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_read_scope", lambda _req, _workspace_id: ([10], False))
    monkeypatch.setattr(api, "_period", lambda *_args: (date(2026, 8, 1), date(2026, 8, 4), "current_month"))
    monkeypatch.setattr(api, "operations", lambda req, params: {"data": {"items": []}})

    def _fetch(sql, _params=()):
        if "GROUP BY COALESCE(currency" in sql:
            return [("RUB", "Расходы", Decimal("100.00"), 1), ("USD", "Доходы", Decimal("10.00"), 1)]
        return []

    monkeypatch.setattr("miniapp.api.pg_fetchall", _fetch)

    data = api.overview(api.request(42), {"workspace_id": 10})["data"]

    assert data["aggregation_available"] is False
    assert data["totals_by_currency"]["RUB"]["expense"] == "100.00"
    assert data["totals_by_currency"]["USD"]["income"] == "10.00"


def test_overview_recent_operations_are_limited_to_three(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [
        {"workspace_id": 10, "name": "Family", "kind": "group", "role": "member", "active": True, "read_only": False}
    ])
    monkeypatch.setattr("miniapp.api.pg_fetchall", lambda *_args, **_kwargs: [("RUB", "Расходы", Decimal("100.00"), 1)])
    monkeypatch.setattr(api, "operations", lambda _req, _params: {"data": {"items": [{"id": i} for i in range(5)]}})
    monkeypatch.setattr(api, "_home_challenge", lambda _req: None)
    monkeypatch.setattr(api, "_home_focus", lambda *_args: None)
    monkeypatch.setattr(api, "_home_insight", lambda *_args: {"kind": "fallback", "tone": "neutral", "title": "x", "text": "x"})
    monkeypatch.setattr(api, "_home_reminder", lambda _req: {"state": "empty"})

    data = api.overview(api.request(42), {"workspace_id": 10})["data"]

    assert [item["id"] for item in data["recent_operations"]] == [0, 1, 2]


def test_home_challenge_serializes_existing_daily_challenge(monkeypatch):
    api = _api(monkeypatch)
    definition = ChallengeDefinition("daily_test", "Две записи", "Запишите две операции.", "day", "operation_count", 2, "daily", "Добавить", "menu_examples", "Готово.")
    monkeypatch.setattr("miniapp.api.upsert_assignments", lambda _user_id, section: [ChallengeCard(definition, 1, 2, False, "2026-08-04", date(2026, 8, 4))])

    card = api._home_challenge(api.request(42))

    assert card["key"] == "daily_test"
    assert card["progress"] == 1
    assert card["completed"] is False


def test_home_focus_priority_is_stable(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": [
        {"id": 2, "title": "Urgent goal", "percent": 20, "deadline": "2026-08-10", "next_action": "Пополнить"},
    ]}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [
        {"id": "normal", "title": "Normal limit", "percent": 40},
        {"id": "warn", "title": "Warning limit", "percent": 90},
    ]}})

    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 4), "current_month", "all", None, "", ())
    item = api._home_focus(api.request(42), {"workspace_id": 10}, tx)

    assert item["kind"] == "limit"
    assert item["id"] == "warn"
    assert item["target_mode"] == "limits"


def _focus_tx(category=None):
    return TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 7), "current_week", "all", category, "", ())


def test_home_focus_limit_month_pace_projection_high_risk(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 7))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [{
        "id": "category:month:Food",
        "title": "Food",
        "category": "Food",
        "percent": 60,
        "spent": Decimal("18000.00"),
        "amount": Decimal("30000.00"),
        "period": "month",
    }]}})

    item = api._home_focus(api.request(42), {"workspace_id": 10, "period": "previous_month"}, _focus_tx())

    assert item["severity"] == "high"
    assert item["projected_percent"] > 100
    assert item["percent"] == 60


def test_home_focus_limit_week_pace_projection_risk(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 4))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [{
        "id": "category:week:Taxi",
        "title": "Taxi",
        "category": "Taxi",
        "percent": 60,
        "spent": "6000.00",
        "amount": "10000.00",
        "period": "week",
    }]}})

    item = api._home_focus(api.request(42), {"workspace_id": 10}, _focus_tx())

    assert item["severity"] == "high"
    assert item["projected_percent"] >= 115


def test_home_focus_limit_first_day_no_unstable_projection(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 1))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [{
        "id": "category:month:Food",
        "title": "Food",
        "category": "Food",
        "percent": 60,
        "spent": "18000.00",
        "amount": "30000.00",
        "period": "month",
    }]}})

    item = api._home_focus(api.request(42), {"workspace_id": 10}, _focus_tx())

    assert item["severity"] == "normal"
    assert item["projected_percent"] is None


def test_home_focus_limit_low_percent_near_period_end_stays_normal(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 30))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [{
        "id": "category:month:Food",
        "title": "Food",
        "category": "Food",
        "percent": 40,
        "spent": "12000.00",
        "amount": "30000.00",
        "period": "month",
    }]}})

    item = api._home_focus(api.request(42), {"workspace_id": 10}, _focus_tx())

    assert item["severity"] == "normal"
    assert item["projected_percent"] is None


def test_home_focus_limit_actual_thresholds_win(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 30))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda _req, _params: {"data": {"items": [
        {"id": "high", "title": "High", "percent": 95, "spent": "950.00", "amount": "1000.00", "period": "month"},
        {"id": "critical", "title": "Critical", "percent": 101, "spent": "1010.00", "amount": "1000.00", "period": "month"},
    ]}})

    item = api._home_focus(api.request(42), {"workspace_id": 10}, _focus_tx())

    assert item["severity"] == "critical"
    assert item["id"] == "critical"


def test_global_category_focus_relevance_does_not_recompute_limit_spent(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 7))
    monkeypatch.setattr(api, "goals", lambda _req, _params: {"data": {"items": []}})
    calls = []

    def _limits(_req, params):
        calls.append(params)
        return {"data": {"items": [{
            "id": "category:month:Food",
            "title": "Food",
            "category": "Food",
            "percent": 60,
            "spent": "18000.00",
            "amount": "30000.00",
            "period": "month",
        }]}}

    monkeypatch.setattr(api, "limits", _limits)

    item = api._home_focus(api.request(42), {"workspace_id": 10, "period": "current_week", "operation_type": "expense", "category": "Food"}, _focus_tx("Food"))

    assert item["id"] == "category:month:Food"
    assert item["percent"] == 60
    assert calls[0]["period"] == "current_week"


def test_home_insight_does_not_mix_currencies():
    api = MiniAppAPI()
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 5), "current_month", "all", None, "", ())
    data = api._home_insight(
        api.request(42),
        tx,
        {
            "RUB": {"income": Decimal("0.00"), "expense": Decimal("100.00"), "count": 1},
            "USD": {"income": Decimal("0.00"), "expense": Decimal("10.00"), "count": 1},
        },
    )

    assert data["kind"] == "currency_mix"


def test_profile_setters_and_quiet_hours_update(monkeypatch):
    api = _api(monkeypatch)
    events = []
    monkeypatch.setattr(api, "_track", lambda _req, event_name, **kwargs: events.append((event_name, kwargs)))
    monkeypatch.setattr("miniapp.api.set_user_preferred_name", lambda _user_id, value: value.strip())
    monkeypatch.setattr("miniapp.api.set_user_currency", lambda _user_id, value: value)
    monkeypatch.setattr("miniapp.api.set_notification_timezone", lambda _user_id, value: {"timezone": value})
    monkeypatch.setattr("miniapp.api.set_quiet_hours", lambda _user_id, enabled, start, end: {"quiet_hours_enabled": enabled, "quiet_hours_start": start, "quiet_hours_end": end})
    monkeypatch.setattr("miniapp.api.get_notification_preferences", lambda _user_id: {"quiet_hours_enabled": True})

    assert api.set_profile_preferred_name(api.request(42), {"preferred_name": " Маша "})["data"]["preferred_name"] == "Маша"
    assert api.set_profile_currency(api.request(42), {"currency": "USD"})["data"]["currency"] == "USD"
    assert api.set_profile_timezone(api.request(42), {"timezone": "Europe/Moscow"})["data"]["timezone"] == "Europe/Moscow"
    data = api.update_notification_preferences(api.request(42), {"action": "quiet_hours_update", "enabled": True, "start": "22:30", "end": "08:00"})["data"]

    assert data["quiet_hours_enabled"] is True
    assert events[-1][0] == "mini_app_profile_setting_changed"


def test_miniapp_display_name_uses_verified_telegram_first_name(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_workspace_rows", lambda _user_id: [])
    monkeypatch.setattr(api, "_profile_theme", lambda _user_id: "telegram")
    monkeypatch.setattr("miniapp.api.user_timezone_name", lambda _user_id: ("Europe/Moscow", "user"))
    req = api.request(42, telegram_first_name=" Максим ")

    data = api.bootstrap(req)["data"]

    assert data["user"]["display_name"] == "Максим"


def test_miniapp_display_name_combines_first_and_last(monkeypatch):
    api = _api(monkeypatch)
    req = api.request(42, telegram_first_name="Максим", telegram_last_name="Иванов")

    assert api._display_name(req, None) == "Максим Иванов"


def test_miniapp_preferred_name_has_priority_over_telegram_name(monkeypatch):
    api = _api(monkeypatch)
    req = api.request(42, telegram_first_name="Максим", telegram_username="fin")

    assert api._display_name(req, " Леонель Месси ") == "Леонель Месси"


def test_miniapp_display_name_uses_username_then_neutral_fallback(monkeypatch):
    api = _api(monkeypatch)

    assert api._display_name(api.request(42, telegram_username="fin_user"), None) == "fin_user"
    assert api._display_name(api.request(42), None) == "Пользователь"


def test_clearing_preferred_name_returns_telegram_display_name(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr("miniapp.api.set_user_preferred_name", lambda _user_id, _value: None)
    req = api.request(42, telegram_first_name="Максим")

    data = api.set_profile_preferred_name(req, {"preferred_name": ""})["data"]

    assert data["preferred_name"] is None
    assert data["display_name"] == "Максим"


def test_http_request_uses_only_verified_initdata_names(monkeypatch):
    from miniapp import http

    monkeypatch.setattr(
        http,
        "verify_telegram_init_data",
        lambda *_args, **_kwargs: MiniAppUser(
            user_id=42,
            auth_date=1000,
            first_name="Verified",
            last_name="User",
            username="verified_user",
            language_code="ru",
        ),
    )

    req = http._request(
        {
            "HTTP_AUTHORIZATION": "tma signed",
            "QUERY_STRING": "first_name=Fake&username=fake",
            "wsgi.input": None,
        },
        "req-1",
    )

    assert req.telegram_first_name == "Verified"
    assert req.telegram_last_name == "User"
    assert req.telegram_username == "verified_user"


def test_product_event_failures_do_not_fail_request(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr("miniapp.api.track_product_event", lambda _event: (_ for _ in ()).throw(RuntimeError("boom")))

    api._track(api.request(42), "mini_app_opened", properties={"tab": "home"})
