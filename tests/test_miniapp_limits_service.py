from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from services.miniapp_limits import MiniAppLimitError, delete_limit, replace_category_limit


class _LimitDB:
    def __init__(self) -> None:
        self.rows = {
            (42, 10, "month", "Food"): {"amount": Decimal("1000.00"), "currency": "RUB"},
        }
        self.fail_insert = False


class _Conn:
    def __init__(self, db: _LimitDB) -> None:
        self.db = db
        self.rows = deepcopy(db.rows)

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.db.rows = deepcopy(self.rows)

    def rollback(self):
        pass

    def close(self):
        pass


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
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
        if compact.startswith("SELECT currency FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            row = self.conn.rows.get((int(user_id), workspace_id, period, category))
            if row:
                self._next = (row["currency"],)
            return
        if compact.startswith("SELECT 1 FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            if (int(user_id), workspace_id, period, category) in self.conn.rows:
                self._next = (1,)
            return
        if compact.startswith("DELETE FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            key = (int(user_id), workspace_id, period, category)
            if key in self.conn.rows:
                del self.conn.rows[key]
                self.rowcount = 1
            return
        if compact.startswith("INSERT INTO public.category_limits"):
            if self.conn.db.fail_insert:
                raise RuntimeError("insert failed")
            user_id, workspace_id, period, category, amount, currency = params
            self.conn.rows[(int(user_id), workspace_id, period, category)] = {"amount": amount, "currency": currency}
            self._next = (period, category, amount, currency, workspace_id)
            return
        raise AssertionError(compact)


def test_category_limit_replace_rolls_back_when_insert_fails(monkeypatch):
    db = _LimitDB()
    db.fail_insert = True
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    with pytest.raises(RuntimeError):
        replace_category_limit(
            user_id=42,
            workspace_id=10,
            old_period="month",
            old_category="Food",
            period="week",
            category="Food",
            amount="800.00",
            currency="RUB",
            require_existing=True,
        )

    assert db.rows == {(42, 10, "month", "Food"): {"amount": Decimal("1000.00"), "currency": "RUB"}}


def test_category_limit_replace_changes_period_and_category_atomically(monkeypatch):
    db = _LimitDB()
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    stored = replace_category_limit(
        user_id=42,
        workspace_id=10,
        old_period="month",
        old_category="Food",
        period="week",
        category="Cafe",
        amount="800.00",
        currency="RUB",
        require_existing=True,
    )

    assert stored.identifier == "category:week:Cafe"
    assert (42, 10, "month", "Food") not in db.rows
    assert db.rows[(42, 10, "week", "Cafe")]["amount"] == Decimal("800.00")


def test_category_limit_foreign_workspace_update_and_missing_delete_are_safe(monkeypatch):
    db = _LimitDB()
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    with pytest.raises(MiniAppLimitError) as exc:
        replace_category_limit(
            user_id=42,
            workspace_id=99,
            old_period="month",
            old_category="Food",
            period="week",
            category="Food",
            amount="800.00",
            currency="RUB",
            require_existing=True,
        )
    assert exc.value.code == "limit_not_found"
    assert delete_limit(user_id=42, workspace_id=99, limit_id="category:month:Food") is False
    assert delete_limit(user_id=42, workspace_id=10, limit_id="category:month:Food") is True
    assert delete_limit(user_id=42, workspace_id=10, limit_id="category:month:Food") is False


def test_category_limit_update_without_currency_preserves_existing_currency(monkeypatch):
    db = _LimitDB()
    db.rows[(42, 10, "month", "Food")]["currency"] = "EUR"
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    stored = replace_category_limit(
        user_id=42,
        workspace_id=10,
        old_period="month",
        old_category="Food",
        period="month",
        category="Food",
        amount="900.00",
        currency=None,
        require_existing=True,
    )

    assert stored.currency == "EUR"
    assert db.rows[(42, 10, "month", "Food")]["currency"] == "EUR"
