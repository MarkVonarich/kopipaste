from datetime import date
from decimal import Decimal

import pytest

import services.reminders as reminders
from services.operations import RecordedOperation
from services.workspaces import WorkspaceContext


class _ReminderCursor:
    def __init__(self, *, currency: str = "EUR", event_exists: bool = False, repeat_rule: str = "none") -> None:
        self.currency = currency
        self.event_exists = event_exists
        self.repeat_rule = repeat_rule
        self.inserts = 0
        self.updated_event_date = date(2026, 8, 2)
        self._next = None

    def fetchone(self):
        row = self._next
        self._next = None
        return row

    def execute(self, sql: str, params=()) -> None:
        compact = " ".join(sql.split())
        self._next = None
        if compact.startswith("SELECT id, title, rem_type"):
            event_date = self.updated_event_date if "LIMIT 1" in compact else date(2026, 8, 2)
            self._next = (7, "Internet", "Расходы", "Subscriptions", Decimal("100.00"), self.currency, event_date, self.repeat_rule, None, 1, True)
            return
        if compact.startswith("SELECT 1 FROM public.user_reminder_events"):
            self._next = (1,) if self.event_exists else None
            return
        if compact.startswith("INSERT INTO public.user_reminder_events"):
            self.inserts += 1
            return
        if compact.startswith("UPDATE public.user_reminders SET event_date"):
            self.updated_event_date = params[0]
            return
        if compact.startswith("UPDATE public.user_reminders SET is_active"):
            return
        raise AssertionError(compact)


def _workspace() -> WorkspaceContext:
    return WorkspaceContext(10, -100, 42, "group", "member", "Family", True)


def test_monthly_reminder_month_end_semantics():
    assert reminders._next_monthly_date(date(2026, 8, 31)) == date(2026, 9, 30)
    assert reminders._next_monthly_date(date(2026, 9, 30)) == date(2026, 10, 30)
    assert reminders._next_monthly_date(date(2026, 1, 31)) == date(2026, 2, 28)
    assert reminders._next_monthly_date(date(2028, 1, 31)) == date(2028, 2, 29)
    assert reminders._next_monthly_date(date(2026, 8, 15)) == date(2026, 9, 15)


def test_yearly_feb_29_semantics_stays_safe():
    assert reminders._advance_date(date(2028, 2, 29), "yearly") == date(2029, 2, 28)


def test_record_reminder_passes_saved_eur_currency_to_operation(monkeypatch):
    cur = _ReminderCursor(currency="EUR")
    captured = {}

    def _insert(*_args, **kwargs):
        captured.update(kwargs)
        return RecordedOperation(
            operation_id=1,
            workspace_id=10,
            actor_user_id=42,
            user_id=42,
            chat_id=-100,
            amount=Decimal("100.00"),
            currency=kwargs["currency"],
            type="Расходы",
            category="Subscriptions",
            operation_date=date(2026, 8, 2),
            source="reminder",
            comment="Internet",
        )

    monkeypatch.setattr(reminders, "insert_financial_operation_tx", _insert)
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")

    result = reminders.record_reminder_tx(cur, user_id=42, reminder_id=7, workspace=_workspace(), chat_type="group")

    assert result.status == "recorded"
    assert result.operation is not None
    assert result.operation.currency == "EUR"
    assert captured["currency"] == "EUR"


def test_record_reminder_keeps_rub_currency(monkeypatch):
    cur = _ReminderCursor(currency="RUB")
    monkeypatch.setattr(reminders, "insert_financial_operation_tx", lambda *_args, **kwargs: RecordedOperation(
        operation_id=1,
        workspace_id=10,
        actor_user_id=42,
        user_id=42,
        chat_id=-100,
        amount=Decimal("100.00"),
        currency=kwargs["currency"],
        type="Расходы",
        category="Subscriptions",
        operation_date=date(2026, 8, 2),
        source="reminder",
        comment="Internet",
    ))

    result = reminders.record_reminder_tx(cur, user_id=42, reminder_id=7, workspace=_workspace(), chat_type="group")

    assert result.operation is not None
    assert result.operation.currency == "RUB"


def test_recurring_record_preserves_reminder_currency_for_next_occurrence(monkeypatch):
    cur = _ReminderCursor(currency="EUR", repeat_rule="weekly")
    monkeypatch.setattr(reminders, "insert_financial_operation_tx", lambda *_args, **kwargs: RecordedOperation(
        operation_id=1,
        workspace_id=10,
        actor_user_id=42,
        user_id=42,
        chat_id=-100,
        amount=Decimal("100.00"),
        currency=kwargs["currency"],
        type="Расходы",
        category="Subscriptions",
        operation_date=date(2026, 8, 2),
        source="reminder",
        comment="Internet",
    ))

    result = reminders.record_reminder_tx(cur, user_id=42, reminder_id=7, workspace=_workspace(), chat_type="group")

    assert result.reminder is not None
    assert result.reminder["event_date"] == date(2026, 8, 9)
    assert result.reminder["currency"] == "EUR"


def test_idempotent_repeated_reminder_record_does_not_insert_operation(monkeypatch):
    cur = _ReminderCursor(currency="EUR", event_exists=True)
    monkeypatch.setattr(reminders, "insert_financial_operation_tx", lambda *_args, **_kwargs: pytest.fail("duplicate record must not insert"))

    result = reminders.record_reminder_tx(cur, user_id=42, reminder_id=7, workspace=_workspace(), chat_type="group")

    assert result.status == "already_recorded"
    assert result.operation is None
    assert cur.inserts == 0
