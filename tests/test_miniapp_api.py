from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from services.operations import RecordedOperation
from services.workspaces import WorkspaceContext


def _api(monkeypatch) -> MiniAppAPI:
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_track", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
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


def test_product_event_failures_do_not_fail_request(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr("miniapp.api.track_product_event", lambda _event: (_ for _ in ()).throw(RuntimeError("boom")))

    api._track(api.request(42), "mini_app_opened", properties={"tab": "home"})
