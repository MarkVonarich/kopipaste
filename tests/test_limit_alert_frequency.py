from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services.operations import RecordedOperation


def test_limit_sequence_restores_50_and_repeats_distinct_exceeded(monkeypatch):
    from services import records

    spent_by_operation = {
        1: Decimal("1000"),
        2: Decimal("1600"),
        3: Decimal("1800"),
        4: Decimal("2000"),
        5: Decimal("2130"),
        6: Decimal("2280"),
    }
    state = {"last_band": 0, "updated_at": None}
    dispatched = []
    events = []
    seen = set()
    current_op = {"id": 1}

    def _fetch(sql, params=()):
        if "FROM public.category_limits" in sql:
            return [(Decimal("2000"),)] if params[1] == "week" else []
        if "SUM(amount)" in sql:
            return [(spent_by_operation[current_op["id"]],)]
        if "FROM public.category_limit_state" in sql:
            return [(state["last_band"], state["updated_at"])] if state["updated_at"] else []
        if "FROM public.automatic_notifications" in sql:
            prefix = params[1].removesuffix("%")
            return [(1,)] if any(key.startswith(prefix) for key in seen) else []
        return []

    def _exec(sql, params=()):
        state["last_band"] = max(state["last_band"], int(params[3]))
        state["updated_at"] = date(2026, 8, 3)

    async def _dispatch(_context, **kwargs):
        if kwargs["dedupe_key"] in seen:
            return SimpleNamespace(status="duplicate")
        seen.add(kwargs["dedupe_key"])
        dispatched.append(kwargs)
        return SimpleNamespace(status="sent")

    monkeypatch.setattr(records, "pg_fetchall", _fetch)
    monkeypatch.setattr(records, "pg_exec", _exec)
    monkeypatch.setattr(records, "get_user_currency", lambda _uid: "RUB")
    monkeypatch.setattr(records, "user_local_date", lambda _uid: date(2026, 8, 3))
    monkeypatch.setattr(records, "dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(records, "track_product_event", lambda ev: events.append(ev))

    for op_id in range(1, 7):
        current_op["id"] = op_id
        recorded = RecordedOperation(op_id, 7, 55, 55, 55, 0, "RUB", "Расходы", "Продукты", date(2026, 8, 3), "text", "")
        asyncio.run(records.send_operation_limit_alert(recorded, SimpleNamespace()))

    texts = [item["text"] for item in dispatched]
    assert len(texts) == 6
    assert "Половина лимита использована" in texts[0]
    assert "Использовано: <b>50%</b>" in texts[0]
    assert "Лимит почти израсходован" in texts[1]
    assert "Использовано: <b>80%</b>" in texts[1]
    assert "До лимита осталось совсем немного" in texts[2]
    assert "Использовано: <b>90%</b>" in texts[2]
    assert "Лимит исчерпан" in texts[3]
    assert "Лимит превышен" in texts[4]
    assert "2 130 RUB" in texts[4]
    assert "Лимит превышен ещё сильнее" in texts[5]
    assert "2 280 RUB" in texts[5]
    assert all(item["force_immediate"] is True for item in dispatched)
    assert dispatched[4]["dedupe_key"] != dispatched[5]["dedupe_key"]
    assert ":5" in dispatched[4]["dedupe_key"]
    assert ":6" in dispatched[5]["dedupe_key"]
    assert all("category" not in ev.properties for ev in events)
    assert all("amount" not in ev.properties for ev in events)


def test_same_exceeded_operation_dedupes_and_scheduler_scan_does_not_exceed(monkeypatch):
    from services import records

    dispatched = []
    seen = set()

    def _fetch(sql, params=()):
        if "FROM public.category_limits" in sql:
            return [(Decimal("2000"),)] if params[1] == "week" else []
        if "SUM(amount)" in sql:
            return [(Decimal("2130"),)]
        if "FROM public.category_limit_state" in sql:
            return [(100, date(2026, 8, 3))]
        if "FROM public.automatic_notifications" in sql:
            prefix = params[1].removesuffix("%")
            return [(1,)] if any(key.startswith(prefix) for key in seen) else []
        return []

    async def _dispatch(_context, **kwargs):
        if kwargs["dedupe_key"] in seen:
            return SimpleNamespace(status="duplicate")
        seen.add(kwargs["dedupe_key"])
        dispatched.append(kwargs)
        return SimpleNamespace(status="sent")

    monkeypatch.setattr(records, "pg_fetchall", _fetch)
    monkeypatch.setattr(records, "pg_exec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(records, "get_user_currency", lambda _uid: "RUB")
    monkeypatch.setattr(records, "user_local_date", lambda _uid: date(2026, 8, 3))
    monkeypatch.setattr(records, "dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(records, "track_product_event", lambda _ev: None)

    recorded = RecordedOperation(42, 7, 55, 55, 55, 130, "RUB", "Расходы", "Продукты", date(2026, 8, 3), "text", "")
    asyncio.run(records.send_operation_limit_alert(recorded, SimpleNamespace()))
    asyncio.run(records.send_operation_limit_alert(recorded, SimpleNamespace()))
    asyncio.run(records._check_category_limits_and_warn(55, "Продукты", date(2026, 8, 3), SimpleNamespace(), workspace_id=7))

    assert len(dispatched) == 1
    assert "category_limit_exceeded" in dispatched[0]["dedupe_key"]


def test_non_expense_operations_do_not_trigger_limit_alert(monkeypatch):
    from services import records

    called = []
    monkeypatch.setattr(records, "_check_category_limits_and_warn", lambda *_args, **_kwargs: called.append(True))
    for op_type in ["Доходы", "noop", "Цель"]:
        recorded = RecordedOperation(1, 7, 55, 55, 55, 100, "RUB", op_type, "Продукты", date(2026, 8, 3), "text", "")
        asyncio.run(records.send_operation_limit_alert(recorded, SimpleNamespace()))
    assert called == []


def test_limit_alert_html_escaping_and_decimal_money():
    from services.limit_alerts import render_category_limit_alert

    alert = render_category_limit_alert(category="<Продукты&Дом>", period="week", spent=Decimal("1000.50"), limit=Decimal("2000.00"), currency="RUB")
    assert alert is not None
    assert "&lt;Продукты&amp;Дом&gt;" in alert.text
    assert "1 000,50 RUB" in alert.text
