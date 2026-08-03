import asyncio
from decimal import Decimal
from types import SimpleNamespace


class _Message:
    def __init__(self, chat_id=55):
        self.chat = SimpleNamespace(id=chat_id)
        self.edits = []
        self.replies = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class _Query:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.message.edits.append((text, kwargs))


def _update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=query.message.chat.id, type="private"),
        effective_user=SimpleNamespace(id=query.message.chat.id, full_name="Tester"),
    )


def test_receipt_confirm_all_records_decimal_and_integer_rows(monkeypatch):
    from routers import callbacks

    recorded = []

    def _record(**kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(type=kwargs["op_type"], operation_id=len(recorded), category=kwargs["category"], user_id=kwargs["chat_id"], workspace_id=None, source="ocr")

    async def _confirm(*_args, **_kwargs):
        return None

    async def _limit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(callbacks, "record_financial_operation", _record)
    monkeypatch.setattr(callbacks, "_send_standard_op_confirmation", _confirm)
    monkeypatch.setattr(callbacks, "send_operation_limit_alert", _limit)

    context = SimpleNamespace(user_data={
        "receipt_candidates": [
            {"amount": "216.34", "category": "Продукты", "type": "Расходы", "date": "2026-08-03", "merchant": "Чижик", "raw_text": "Чижик -216,34 ₽"},
            {"amount": "285.00", "category": "Заведения", "type": "Расходы", "date": "2026-08-03", "merchant": "Дринкит", "raw_text": "Дринкит -285 ₽"},
        ],
    })
    message = _Message()
    asyncio.run(callbacks.callback_handler(_update(_Query("receipt_confirm_all", message)), context))

    assert [call["amount"] for call in recorded] == [Decimal("216.34"), Decimal("285.00")]
    assert [call["comment"] for call in recorded] == ["Чижик", "Дринкит"]
    assert "✅ Готово: записано 2, пропущено 0. Сумма: 501,34 ₽" in message.edits[-1][0]
    assert "receipt_candidates" not in context.user_data
