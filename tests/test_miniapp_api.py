from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError, _WRITE_RATE
from services.operations import RecordedOperation
from services.workspaces import WorkspaceContext


def _api(monkeypatch) -> MiniAppAPI:
    _WRITE_RATE.clear()
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr("miniapp.api.get_user_locale", lambda _user_id: "ru")
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


def test_create_operation_uses_authenticated_actor_and_decimal_amount(monkeypatch):
    api = _api(monkeypatch)
    ctx = WorkspaceContext(10, -100, 42, "group", "member", "Family", True)
    captured = {}
    saved = []

    monkeypatch.setattr(api, "_write_workspace", lambda _req, workspace_id: ctx if workspace_id == 10 else pytest.fail("bad workspace"))
    monkeypatch.setattr(api, "_idempotency_get", lambda *_args: None)
    monkeypatch.setattr(api, "_idempotency_save", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setattr(api, "_operation_row", lambda _req, _operation_id: (
        777, date(2026, 8, 4), "Расходы", "Food", Decimal("216.34"), "RUB", "Lunch", 10, 42, None, "Family"
    ))

    def _record(**kwargs):
        captured.update(kwargs)
        return RecordedOperation(
            operation_id=777,
            workspace_id=10,
            actor_user_id=kwargs["actor_user_id"],
            user_id=42,
            chat_id=-100,
            amount=kwargs["amount"],
            currency="RUB",
            type=kwargs["op_type"],
            category=kwargs["category"],
            operation_date=kwargs["op_date"],
            source="miniapp",
            comment=kwargs["comment"],
        )

    monkeypatch.setattr("miniapp.api.record_financial_operation", _record)

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

    assert captured["actor_user_id"] == 42
    assert captured["amount"] == Decimal("216.34")
    assert response["data"]["operation"]["id"] == 777
    assert saved


def test_duplicate_create_returns_idempotent_response_without_insert(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_idempotency_get", lambda *_args: {"operation": {"id": 777}})
    monkeypatch.setattr("miniapp.api.record_financial_operation", lambda **_kwargs: pytest.fail("duplicate must not insert"))

    response = api.create_operation(api.request(42), {"idempotency_key": "k1"})

    assert response["data"]["operation"]["id"] == 777


def test_all_workspaces_are_read_only_for_writes(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, "_idempotency_get", lambda *_args: None)

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


def test_product_event_failures_do_not_fail_request(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr("miniapp.api.track_product_event", lambda _event: (_ for _ in ()).throw(RuntimeError("boom")))

    api._track(api.request(42), "mini_app_opened", properties={"tab": "home"})
