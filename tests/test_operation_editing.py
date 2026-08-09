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
        return SimpleNamespace(message_id=len(self.replies))


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


def test_new_operation_category_selection_ignores_stale_edit_state_and_preserves_comment(monkeypatch):
    from routers import callbacks

    recorded = []

    async def _record_operation(cat, amt, dt, typ, update, context, note=None):
        pending = context.user_data.get("pending") or {}
        recorded.append({
            "cat": cat,
            "amount": amt,
            "type": typ,
            "note": note,
            "comment": pending.get("merch") or note or "",
        })

    monkeypatch.setattr(callbacks, "record_category_confirmation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(callbacks, "log_category_feedback", lambda **_kwargs: None)
    monkeypatch.setattr("services.records.record_operation", _record_operation)

    context = SimpleNamespace(user_data={
        "edit_mode": True,
        "edit_operation_id": 7,
        "pending": {
            "type": "Расходы",
            "merch": "coffee near office",
            "amt": 250,
            "time": date(2026, 7, 20),
            "note": "receipt",
        },
    })
    message = _Message(chat_id=55, text="кофе у офиса 250")

    from routers import messages

    monkeypatch.setattr(messages, "parse_user_input", lambda _text: ("кофе у офиса", 250, date(2026, 7, 20), None))
    monkeypatch.setattr(messages, "convert_amount_if_needed", lambda *_args, **_kwargs: (250, None))
    monkeypatch.setattr(messages, "get_user_alias", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(messages, "get_top2_suggestions", lambda *_args, **_kwargs: ([{"cat": "Кафе", "score": 0.9}, {"cat": "Другое", "score": 0.1}], {"source": "test"}))
    monkeypatch.setattr(messages, "insert_ml_observation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(messages, "track_product_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(messages, "get_user_currency", lambda _user_id: "RUB")

    asyncio.run(messages.handle_text(_text_update(message), context))
    query = _CallbackQuery("use_cat|Кафе", _Message(chat_id=55))
    asyncio.run(callbacks.callback_handler(_callback_update(query), context))

    assert "edit_mode" not in context.user_data
    assert recorded == [{
        "cat": "Кафе",
        "amount": 250,
        "type": "Расходы",
        "note": None,
        "comment": "кофе у офиса",
    }]


def test_alias_hit_passes_merchant_to_final_operation_commit(monkeypatch):
    from routers import messages

    recorded = []

    async def _record_operation(cat, amt, dt, typ, update, context, note=None, merchant=None):
        recorded.append({
            "cat": cat,
            "amount": amt,
            "type": typ,
            "note": note,
            "merchant": merchant,
            "pending": dict(context.user_data.get("pending") or {}),
        })

    monkeypatch.setattr(messages, "parse_user_input", lambda _text: ("Дринкит", 285, date(2026, 8, 4), None))
    monkeypatch.setattr(messages, "convert_amount_if_needed", lambda *_args, **_kwargs: (285, None))
    monkeypatch.setattr(messages, "get_user_alias", lambda *_args, **_kwargs: ("Расходы", "Заведения"))
    monkeypatch.setattr(messages, "insert_ml_observation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(messages, "record_operation", _record_operation)

    context = SimpleNamespace(user_data={})
    message = _Message(chat_id=55, text="дринкит 285")

    asyncio.run(messages.handle_text(_text_update(message), context))

    assert recorded == [{
        "cat": "Заведения",
        "amount": 285,
        "type": "Расходы",
        "note": None,
        "merchant": "дринкит",
        "pending": {},
    }]
