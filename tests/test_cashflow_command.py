import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from routers.cashflow import cmd_forecast


def test_group_command_does_not_read_or_show_financial_data(monkeypatch):
    def unexpected(*args):
        raise AssertionError('Financial service must not be called from group chat')
    monkeypatch.setattr('routers.cashflow._preview', unexpected)
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(type='group'), message=SimpleNamespace(reply_text=reply))
    asyncio.run(cmd_forecast(update, SimpleNamespace(args=['10000'])))
    assert 'личном чате' in reply.call_args.args[0]


def test_private_command_uses_shared_preview_and_separates_expected_income(monkeypatch):
    from services.cashflow import calculate_cashflow, CashEvent
    from datetime import date
    from decimal import Decimal
    calls = []
    def preview(user_id, args):
        calls.append((user_id, args))
        return calculate_cashflow(today=date(2026, 10, 4), end=date(2026, 10, 4), balance=Decimal('10000'),
                                  reserve=Decimal('2000'), daily_pace=Decimal('100'), currency='RUB',
                                  events=(CashEvent('income', date(2026, 10, 4), Decimal('50000'), 'income', 'Доход'),))
    monkeypatch.setattr('routers.cashflow._preview', preview)
    monkeypatch.setattr('routers.cashflow.MINIAPP_PUBLIC_URL', '')
    worker = AsyncMock(side_effect=lambda function, *args: function(*args))
    monkeypatch.setattr('routers.cashflow.asyncio.to_thread', worker)
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=SimpleNamespace(id=1), message=SimpleNamespace(reply_text=reply))
    asyncio.run(cmd_forecast(update, SimpleNamespace(args=['10000', 'месяц', '2000'])))
    worker.assert_awaited_once_with(preview, 1, ['10000', 'месяц', '2000'])
    assert calls == [(1, ['10000', 'месяц', '2000'])]
    text = reply.call_args.args[0]
    assert 'примерно 7 900' in text and 'Ожидаемые доходы: 50 000' in text
    assert 'Будущие доходы не включены' in text
