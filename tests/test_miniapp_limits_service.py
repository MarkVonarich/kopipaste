from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from services.miniapp_limits import MiniAppLimitError, delete_limit, replace_category_limit


class _LimitDB:
    def __init__(self) -> None:
        self.rows = {
            (42, 10, "month", "Food"): {
                "amount": Decimal("1000.00"),
                "currency": "RUB",
                "title": "Food",
                "alerts_enabled": True,
            },
        }
        self.fail_update = False


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
        if compact.startswith("SELECT currency, COALESCE(display_name, category), alerts_enabled"):
            user_id, workspace_id, period, category = params
            row = self.conn.rows.get((int(user_id), workspace_id, period, category))
            if row:
                self._next = (row["currency"], row["title"], row["alerts_enabled"])
            return
        if compact.startswith("SELECT pg_advisory_xact_lock"):
            return
        if compact.startswith("SELECT 1 FROM public.category_limits"):
            user_id, workspace_id, period, category = params[:4]
            key = (int(user_id), workspace_id, period, category)
            old_key = (int(user_id), workspace_id, params[4], params[5]) if len(params) == 6 else None
            if key in self.conn.rows and key != old_key:
                self._next = (1,)
            return
        if compact.startswith("DELETE FROM public.category_limits"):
            user_id, workspace_id, period, category = params
            key = (int(user_id), workspace_id, period, category)
            if key in self.conn.rows:
                del self.conn.rows[key]
                self.rowcount = 1
            return
        if compact.startswith("UPDATE public.category_limits"):
            if self.conn.db.fail_update:
                raise RuntimeError("update failed")
            period, category, amount, currency, title, alerts_enabled, user_id, workspace_id, old_period, old_category = params
            old_key = (int(user_id), workspace_id, old_period, old_category)
            if old_key in self.conn.rows:
                del self.conn.rows[old_key]
                self.conn.rows[(int(user_id), workspace_id, period, category)] = {
                    "amount": amount,
                    "currency": currency,
                    "title": title,
                    "alerts_enabled": bool(alerts_enabled),
                }
                self._next = (period, category, amount, currency, workspace_id, title, bool(alerts_enabled))
            return
        if compact.startswith("INSERT INTO public.category_limits"):
            user_id, workspace_id, period, category, amount, currency, title, alerts_enabled = params
            self.conn.rows[(int(user_id), workspace_id, period, category)] = {
                "amount": amount,
                "currency": currency,
                "title": title,
                "alerts_enabled": bool(alerts_enabled),
            }
            self._next = (period, category, amount, currency, workspace_id, title, bool(alerts_enabled))
            return
        raise AssertionError(compact)


def test_category_limit_replace_rolls_back_when_update_fails(monkeypatch):
    db = _LimitDB()
    db.fail_update = True
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

    assert db.rows[(42, 10, "month", "Food")]["amount"] == Decimal("1000.00")


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
        title="Coffee out",
        alerts_enabled=False,
        require_existing=True,
    )

    assert stored.identifier == "category:week:Cafe"
    assert (42, 10, "month", "Food") not in db.rows
    assert db.rows[(42, 10, "week", "Cafe")]["amount"] == Decimal("800.00")
    assert stored.title == "Coffee out"
    assert stored.alerts_enabled is False


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


def test_category_limit_update_without_alert_choice_preserves_existing_value(monkeypatch):
    db = _LimitDB()
    db.rows[(42, 10, "month", "Food")]["alerts_enabled"] = False
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    stored = replace_category_limit(
        user_id=42, workspace_id=10, old_period="month", old_category="Food",
        period="month", category="Food", amount="900.00", currency="RUB",
        alerts_enabled=None, require_existing=True,
    )

    assert stored.alerts_enabled is False
    assert db.rows[(42, 10, "month", "Food")]["alerts_enabled"] is False


def test_category_limit_rename_rejects_target_collision(monkeypatch):
    db = _LimitDB()
    db.rows[(42, 10, "week", "Cafe")] = {
        "amount": Decimal("500.00"), "currency": "RUB", "title": "Cafe", "alerts_enabled": True,
    }
    monkeypatch.setattr("services.miniapp_limits.get_conn", lambda: _Conn(db))

    with pytest.raises(MiniAppLimitError) as exc:
        replace_category_limit(
            user_id=42, workspace_id=10, old_period="month", old_category="Food",
            period="week", category="Cafe", amount="800.00", currency="RUB", require_existing=True,
        )

    assert exc.value.code == "limit_conflict"
    assert len(db.rows) == 2
