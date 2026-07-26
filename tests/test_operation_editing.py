import asyncio
from datetime import date
from types import SimpleNamespace


class _Message:
    def __init__(self, chat_id: int, text: str = ""):
        self.chat = SimpleNamespace(id=chat_id)
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class _CallbackQuery:
    def __init__(self, data: str, message: _Message):
        self.data = data
        self.message = message
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def edit_message_reply_markup(self, **kwargs):
        self.edits.append(("", kwargs))


def _callback_update(query: _CallbackQuery):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=query.message.chat.id, type="private"),
        effective_user=SimpleNamespace(id=query.message.chat.id),
    )


def _text_update(message: _Message):
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=message.chat.id, type="private"),
        effective_user=SimpleNamespace(id=message.chat.id),
    )


def test_operation_edit_amount_uses_captured_operation_id(monkeypatch):
    from routers import callbacks, messages

    operations = {
        7: {"id": 7, "category": "Coffee", "amount": 100, "type": "Расходы", "op_date": date(2026, 7, 20)},
        9: {"id": 9, "category": "Taxi", "amount": 900, "type": "Расходы", "op_date": date(2026, 7, 21)},
    }
    updated = []

    def _fake_pg_fetchall(sql, params=()):
        normalized = " ".join(sql.split())
        if "WHERE chat_id=%s AND id=%s" in normalized:
            row = operations.get(params[1])
        else:
            row = operations[9]
        return [(row["id"], row["category"], row["amount"], row["type"], row["op_date"])]

    def _fake_update_by_id(user_id, operation_id, **fields):
        updated.append((user_id, operation_id, fields))
        row = operations.get(operation_id)
        if not row:
            return None
        row.update(fields)
        return row

    def _must_not_update_last(*_args, **_kwargs):
        raise AssertionError("edit flow must not fall back to the latest operation")

    monkeypatch.setattr(callbacks, "pg_fetchall", _fake_pg_fetchall)
    monkeypatch.setattr(messages, "update_operation_fields_by_id", _fake_update_by_id)
    monkeypatch.setattr(messages, "update_last_operation_fields", _must_not_update_last)

    context = SimpleNamespace(user_data={})
    message = _Message(chat_id=55)

    asyncio.run(callbacks._op_edit_router(_callback_update(_CallbackQuery("op_edit|7", message)), context))
    asyncio.run(callbacks._op_edit_router(_callback_update(_CallbackQuery("op_e_amt", message)), context))

    text_message = _Message(chat_id=55, text="250")
    asyncio.run(messages.handle_text(_text_update(text_message), context))

    assert updated == [(55, 7, {"amount": 250})]
    assert operations[7]["amount"] == 250
    assert operations[9]["amount"] == 900
    assert text_message.replies[-1][0] == "✅ Сумма обновлена."


def test_operation_edit_category_updates_selected_operation_without_reinsert(monkeypatch):
    from routers import callbacks

    updated = []

    def _fake_update_by_id(user_id, operation_id, **fields):
        updated.append((user_id, operation_id, fields))
        return {"id": operation_id, "category": fields["category"], "type": fields["op_type"], "amount": 100, "comment": ""}

    def _must_not_record(*_args, **_kwargs):
        raise AssertionError("category edit must not record a new operation")

    def _must_not_delete(*_args, **_kwargs):
        raise AssertionError("category edit must not delete the latest operation")

    monkeypatch.setattr(callbacks, "update_operation_fields_by_id", _fake_update_by_id)
    monkeypatch.setattr(callbacks, "delete_last_operation", _must_not_delete)
    monkeypatch.setattr(callbacks, "record_category_confirmation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(callbacks, "log_category_feedback", lambda **_kwargs: None)
    monkeypatch.setattr("services.records.record_operation", _must_not_record)

    context = SimpleNamespace(user_data={
        "edit_mode": True,
        "edit_operation_id": 7,
        "pending": {
            "edit_operation_id": 7,
            "type": "Расходы",
            "merch": "Coffee",
            "amt": 100,
            "time": date(2026, 7, 20),
        },
    })
    query = _CallbackQuery("use_cat|Food", _Message(chat_id=55))

    asyncio.run(callbacks.callback_handler(_callback_update(query), context))

    assert updated == [(55, 7, {"category": "Food", "op_type": "Расходы"})]
    assert "edit_mode" not in context.user_data
    assert "edit_operation_id" not in context.user_data
