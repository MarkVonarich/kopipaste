from datetime import date
from decimal import Decimal


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _Conn:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_update_financial_operation_tracks_side_effect(monkeypatch):
    from services import operations

    conn = _Conn([(7, date(2026, 8, 4), "Расходы", "Food", Decimal("120.00"), "RUB", "Lunch", 10, 42, None)])
    events = []
    monkeypatch.setattr(operations, "get_conn", lambda: conn)
    monkeypatch.setattr(operations, "get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(operations, "track_product_event", lambda event: events.append(event))

    result = operations.update_financial_operation(actor_user_id=42, operation_id=7, amount="120.00", source="miniapp")

    assert result["id"] == 7
    assert conn.committed is True
    assert "UPDATE public.operations" in conn.cursor_obj.executed[0][0]
    assert events[0].event_name == "operation_edited"


def test_delete_financial_operation_tracks_side_effect(monkeypatch):
    from services import operations

    conn = _Conn([(7, date(2026, 8, 4), "Расходы", "Food", Decimal("120.00"), "RUB", "Lunch", 10, 42, None)])
    events = []
    monkeypatch.setattr(operations, "get_conn", lambda: conn)
    monkeypatch.setattr(operations, "get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(operations, "track_product_event", lambda event: events.append(event))

    result = operations.delete_financial_operation(actor_user_id=42, operation_id=7, source="miniapp")

    assert result["id"] == 7
    assert conn.committed is True
    assert "DELETE FROM public.operations" in conn.cursor_obj.executed[0][0]
    assert events[0].event_name == "operation_deleted"
