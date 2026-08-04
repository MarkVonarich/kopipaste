from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from services.workspaces import WorkspaceContext


def _json_value(value):
    return getattr(value, "adapted", value)


class _FakeDB:
    def __init__(self) -> None:
        self.idempotency: dict[tuple[int, str], dict] = {}
        self.operations: dict[int, dict] = {}
        self.next_operation_id = 1
        self.fail_complete = False


class _FakeConn:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db
        self.idempotency = deepcopy(db.idempotency)
        self.operations = deepcopy(db.operations)
        self.next_operation_id = db.next_operation_id

    def cursor(self):
        return _FakeCursor(self)

    def commit(self) -> None:
        self.db.idempotency = deepcopy(self.idempotency)
        self.db.operations = deepcopy(self.operations)
        self.db.next_operation_id = self.next_operation_id

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeCursor:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self._next = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchone(self):
        value = self._next
        self._next = None
        return value

    def execute(self, sql: str, params=()) -> None:
        compact = " ".join(sql.split())
        self.rowcount = 0
        self._next = None

        if compact.startswith("INSERT INTO public.miniapp_idempotency_keys"):
            user_id, key, request_hash, _lease_seconds = params
            idem_key = (int(user_id), str(key))
            if idem_key not in self.conn.idempotency:
                self.conn.idempotency[idem_key] = {
                    "request_hash": request_hash,
                    "status": "pending",
                    "response_json": None,
                    "operation_id": None,
                    "lease_expired": False,
                    "attempt_count": 1,
                }
                self._next = ("pending",)
            return

        if compact.startswith("SELECT request_hash, status, response_json"):
            user_id, key = params
            row = self.conn.idempotency.get((int(user_id), str(key)))
            if row is not None:
                self._next = (
                    row["request_hash"],
                    row["status"],
                    row.get("response_json"),
                    row.get("operation_id"),
                    None,
                    bool(row.get("lease_expired")),
                )
            return

        if compact.startswith("UPDATE public.miniapp_idempotency_keys") and "SET status='pending'" in compact:
            _lease_seconds, user_id, key, request_hash = params
            row = self.conn.idempotency[(int(user_id), str(key))]
            assert row["request_hash"] == request_hash
            row["status"] = "pending"
            row["lease_expired"] = False
            row["attempt_count"] += 1
            row["last_error_code"] = None
            self.rowcount = 1
            return

        if compact.startswith("INSERT INTO public.operations"):
            (
                chat_id, user_id, op_date, op_type, category, amount, comment,
                _week_start, _iso_year, _iso_week, _weekday,
                workspace_id, actor_user_id, source, currency, raw_text,
            ) = params
            operation_id = self.conn.next_operation_id
            self.conn.next_operation_id += 1
            self.conn.operations[operation_id] = {
                "id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "op_date": op_date,
                "type": op_type,
                "category": category,
                "amount": amount,
                "comment": comment,
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "source": source,
                "currency": currency,
                "raw_text": raw_text,
            }
            self._next = (operation_id,)
            return

        if compact.startswith("UPDATE public.miniapp_idempotency_keys") and "SET operation_id=%s" in compact:
            if self.conn.db.fail_complete:
                raise RuntimeError("complete failed")
            operation_id, response, user_id, key, request_hash = params
            row = self.conn.idempotency[(int(user_id), str(key))]
            if row["request_hash"] == request_hash and row["status"] == "pending":
                row["operation_id"] = operation_id
                row["status"] = "completed"
                row["response_json"] = _json_value(response)
                row["lease_expired"] = False
                row["last_error_code"] = None
                self.rowcount = 1
            return

        if compact.startswith("SELECT o.id, o.op_date"):
            _currency, operation_id, workspace_id, user_id = params
            op = self.conn.operations.get(int(operation_id))
            if op and op["workspace_id"] == workspace_id and (op["workspace_id"] is not None or op["user_id"] == user_id):
                self._next = (
                    op["id"], op["op_date"], op["type"], op["category"], op["amount"],
                    op["currency"], op["comment"], op["workspace_id"], op["actor_user_id"],
                    None, "Family",
                )
            return

        raise AssertionError(f"Unexpected SQL: {compact}")


def _call_create(api: MiniAppAPI, request_hash: str, *, key: str = "k1"):
    req = api.request(42)
    ctx = WorkspaceContext(10, -100, 42, "group", "member", "Family", True)
    return api._create_operation_atomically(
        req=req,
        ctx=ctx,
        idempotency_key=key,
        request_hash=request_hash,
        op_date=date(2026, 8, 4),
        op_type="Расходы",
        category="Food",
        amount=Decimal("216.34"),
        description="Lunch",
    )


def test_atomic_create_replay_does_not_duplicate_operation_or_side_effects(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")

    first_payload, first_recorded, first_created = _call_create(api, "hash-a")
    second_payload, second_recorded, second_created = _call_create(api, "hash-a")

    assert first_created is True
    assert first_recorded is not None
    assert second_created is False
    assert second_recorded is None
    assert first_payload["operation"]["id"] == second_payload["operation"]["id"]
    assert len(db.operations) == 1
    assert db.idempotency[(42, "k1")]["status"] == "completed"


def test_same_key_different_payload_conflicts_without_insert(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")

    _call_create(api, "hash-a")

    with pytest.raises(MiniAppError) as exc:
        _call_create(api, "hash-b")

    assert exc.value.status == 409
    assert exc.value.code == "idempotency_conflict"
    assert len(db.operations) == 1


def test_active_pending_is_not_processed_twice(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    db.idempotency[(42, "k1")] = {
        "request_hash": "hash-a",
        "status": "pending",
        "response_json": None,
        "operation_id": None,
        "lease_expired": False,
        "attempt_count": 1,
    }
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))

    with pytest.raises(MiniAppError) as exc:
        _call_create(api, "hash-a")

    assert exc.value.code == "idempotency_pending"
    assert db.operations == {}


def test_stale_pending_without_operation_is_reclaimed(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    db.idempotency[(42, "k1")] = {
        "request_hash": "hash-a",
        "status": "pending",
        "response_json": None,
        "operation_id": None,
        "lease_expired": True,
        "attempt_count": 1,
    }
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")

    _payload, recorded, created = _call_create(api, "hash-a")

    assert created is True
    assert recorded is not None
    assert len(db.operations) == 1
    assert db.idempotency[(42, "k1")]["attempt_count"] == 2
    assert db.idempotency[(42, "k1")]["status"] == "completed"


def test_stale_pending_with_operation_is_reconciled_without_duplicate(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    db.operations[7] = {
        "id": 7,
        "chat_id": -100,
        "user_id": 42,
        "op_date": date(2026, 8, 4),
        "type": "Расходы",
        "category": "Food",
        "amount": Decimal("216.34"),
        "comment": "Lunch",
        "workspace_id": 10,
        "actor_user_id": 42,
        "source": "miniapp",
        "currency": "RUB",
        "raw_text": None,
    }
    db.next_operation_id = 8
    db.idempotency[(42, "k1")] = {
        "request_hash": "hash-a",
        "status": "pending",
        "response_json": None,
        "operation_id": 7,
        "lease_expired": True,
        "attempt_count": 1,
    }
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda _user_id: "RUB")

    payload, recorded, created = _call_create(api, "hash-a")

    assert created is False
    assert recorded is None
    assert payload["operation"]["id"] == 7
    assert len(db.operations) == 1
    assert db.idempotency[(42, "k1")]["status"] == "completed"
    assert db.idempotency[(42, "k1")]["response_json"]["operation"]["id"] == 7


def test_failure_after_operation_insert_rolls_back_untracked_operation(monkeypatch):
    api = MiniAppAPI()
    db = _FakeDB()
    db.fail_complete = True
    monkeypatch.setattr("miniapp.api.get_conn", lambda: _FakeConn(db))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")

    with pytest.raises(RuntimeError):
        _call_create(api, "hash-a")

    assert db.operations == {}
    assert db.idempotency == {}
