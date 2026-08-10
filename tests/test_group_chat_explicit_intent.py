import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace


class _ReplyMessage:
    def __init__(self, text="", *, entities=None, reply_to_message=None):
        self.text = text
        self.entities = entities or []
        self.reply_to_message = reply_to_message
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.replies))


def _update(*, text, chat_type="group", chat_id=-100, user_id=22, entities=None, reply_to_message=None):
    message = _ReplyMessage(text, entities=entities, reply_to_message=reply_to_message)
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id, language_code="ru"),
    )


def _context():
    return SimpleNamespace(
        bot=SimpleNamespace(id=777, username="uchet_finbot"),
        user_data={},
    )


def _mention(text="@uchet_finbot", offset=0):
    return SimpleNamespace(type="mention", offset=offset, length=len(text))


def _text_mention(user_id=777):
    return SimpleNamespace(type="text_mention", offset=0, length=5, user=SimpleNamespace(id=user_id))


def test_random_group_text_is_ignored(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "_process_group_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("group parser must stay silent")))
    update = _update(text="в 8 идём в бар")

    result = asyncio.run(messages.handle_text(update, _context()))

    assert result is None
    assert update.effective_message.replies == []


def test_random_group_text_with_numbers_is_ignored(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "_process_group_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("group parser must stay silent")))

    for text in ["скинь мне 500 завтра", "билеты 3200", "купил вчера за 1200", "500 рублей с тебя"]:
        update = _update(text=text)
        result = asyncio.run(messages.handle_text(update, _context()))
        assert result is None
        assert update.effective_message.replies == []


def test_financial_group_text_without_explicit_intent_is_ignored(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "_process_group_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("group parser must stay silent")))
    update = _update(text="такси 890")

    result = asyncio.run(messages.handle_text(update, _context()))

    assert result is None
    assert update.effective_message.replies == []


def test_correct_bot_mention_is_processed_and_stripped(monkeypatch):
    from routers import messages

    calls = []

    async def _fake_group_text(_update, _context, input_text):
        calls.append(input_text)

    monkeypatch.setattr(messages, "_process_group_text", _fake_group_text)
    update = _update(text="@uchet_finbot такси 890", entities=[_mention()])

    asyncio.run(messages.handle_text(update, _context()))

    assert calls == ["такси 890"]


def test_another_username_mention_is_ignored(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "_process_group_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wrong mention must not route")))
    update = _update(text="@another_bot такси 890", entities=[_mention("@another_bot")])

    result = asyncio.run(messages.handle_text(update, _context()))

    assert result is None
    assert update.effective_message.replies == []


def test_text_mention_entity_for_bot_is_processed(monkeypatch):
    from routers import messages

    calls = []

    async def _fake_group_text(_update, _context, input_text):
        calls.append(input_text)

    monkeypatch.setattr(messages, "_process_group_text", _fake_group_text)
    update = _update(text="бот такси 890", entities=[_text_mention(777)])

    asyncio.run(messages.handle_text(update, _context()))

    assert calls == ["бот такси 890"]


def test_group_mention_preserves_workspace_actor_and_source(monkeypatch):
    from routers import messages

    calls = {}
    workspace = SimpleNamespace(
        workspace_id=10,
        chat_id=-100,
        actor_user_id=22,
        kind="group",
        role="member",
        name="Family",
        is_configured=True,
    )

    def _resolve_workspace(chat_id, actor_user_id, chat_type):
        calls["resolve_workspace"] = {"chat_id": chat_id, "actor_user_id": actor_user_id, "chat_type": chat_type}
        return workspace

    monkeypatch.setattr(messages, "resolve_workspace", _resolve_workspace)
    monkeypatch.setattr(messages, "parse_user_input", lambda _text: ("Такси", Decimal("890"), date(2026, 8, 10), None))
    monkeypatch.setattr(messages, "convert_amount_if_needed", lambda *_args, **_kwargs: (Decimal("890"), None))
    monkeypatch.setattr(messages, "get_top2_suggestions", lambda *_args, **_kwargs: ([{"cat": "Транспорт"}, {"cat": "Другое"}], {"source": "test"}))
    monkeypatch.setattr(messages, "create_operation_draft", lambda **kwargs: calls.setdefault("draft", kwargs) or "draft-1")
    monkeypatch.setattr(messages, "get_user_currency", lambda _user_id: "RUB")

    update = _update(text="@uchet_finbot такси 890", entities=[_mention()])

    asyncio.run(messages.handle_text(update, _context()))

    assert calls["resolve_workspace"] == {"chat_id": -100, "actor_user_id": 22, "chat_type": "group"}
    assert calls["draft"]["workspace"] is workspace
    assert calls["draft"]["source"] == "text"
    assert calls["draft"]["raw_text"] == "такси 890"
    assert calls["draft"]["amount"] == Decimal("890")
    assert update.effective_message.replies


def test_reply_to_bot_is_processed(monkeypatch):
    from routers import messages

    calls = []

    async def _fake_group_text(_update, _context, input_text):
        calls.append(input_text)

    monkeypatch.setattr(messages, "_process_group_text", _fake_group_text)
    bot_message = SimpleNamespace(from_user=SimpleNamespace(id=777, username="uchet_finbot", is_bot=True))
    update = _update(text="такси 890", reply_to_message=bot_message)

    asyncio.run(messages.handle_text(update, _context()))

    assert calls == ["такси 890"]


def test_reply_to_another_user_is_ignored(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "_process_group_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("foreign reply must not route")))
    user_message = SimpleNamespace(from_user=SimpleNamespace(id=42, username="friend", is_bot=False))
    update = _update(text="такси 890", reply_to_message=user_message)

    result = asyncio.run(messages.handle_text(update, _context()))

    assert result is None
    assert update.effective_message.replies == []


def test_private_financial_text_still_uses_private_flow(monkeypatch):
    from routers import messages

    calls = []

    async def _fake_free_text(_update, _context, input_text):
        calls.append(input_text)

    monkeypatch.setattr(messages, "_process_free_text", _fake_free_text)
    monkeypatch.setattr(messages, "parse_day_list", lambda _text: [])
    update = _update(text="лавка 726", chat_type="private", chat_id=22)

    asyncio.run(messages.handle_text(update, _context()))

    assert calls == ["лавка 726"]


def test_group_photo_and_voice_are_silent_without_intent(monkeypatch):
    from routers import messages

    monkeypatch.setattr(messages, "parse_receipt_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OCR must not run")))
    monkeypatch.setattr(messages, "transcribe_telegram_voice", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("voice must not run")))
    photo_update = _update(text="", chat_type="group")
    photo_update.message.photo = [SimpleNamespace(get_file=lambda: None)]
    voice_update = _update(text="", chat_type="group")
    voice_update.message.voice = SimpleNamespace(duration=3)

    assert asyncio.run(messages.handle_photo(photo_update, _context())) is None
    assert asyncio.run(messages.handle_voice(voice_update, _context())) is None
    assert photo_update.effective_message.replies == []
    assert voice_update.effective_message.replies == []


def test_explicit_commands_still_reply_in_group(monkeypatch):
    from routers import commands

    monkeypatch.setattr(commands, "get_user_locale", lambda _user_id: "ru")
    update = _update(text="/help", chat_type="group")

    asyncio.run(commands.cmd_help(update, _context()))

    assert update.effective_message.replies
    assert "Помощь" in update.effective_message.replies[0][0]
