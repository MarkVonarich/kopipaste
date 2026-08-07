import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services.operations import RecordedOperation
from services.reminders import ReminderRecordResult
from services.workspaces import WorkspaceContext


class _Bot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _Message:
    def __init__(self, chat_id=55) -> None:
        self.chat = SimpleNamespace(id=chat_id)


class _Query:
    def __init__(self, data: str, message: _Message) -> None:
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def _update(query: _Query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=query.message.chat.id, type="private"),
        effective_user=SimpleNamespace(id=query.message.chat.id, full_name="Tester"),
    )


def test_reminder_record_confirmation_uses_recorded_currency(monkeypatch):
    from routers import callbacks

    recorded = RecordedOperation(
        operation_id=77,
        workspace_id=None,
        actor_user_id=55,
        user_id=55,
        chat_id=55,
        amount=Decimal("100.00"),
        currency="EUR",
        type="Расходы",
        category="Subscriptions",
        operation_date=date(2026, 8, 31),
        source="reminder",
        comment="Internet",
    )
    seen = {}

    monkeypatch.setattr(callbacks, "get_user_currency", lambda _chat_id: "RUB")
    monkeypatch.setattr(callbacks, "resolve_workspace", lambda *_args: WorkspaceContext(None, 55, 55, "legacy_personal", "owner", "Личное", True))
    monkeypatch.setattr(callbacks, "record_shared_reminder", lambda **_kwargs: ReminderRecordResult("recorded", {}, recorded))
    async def _limit_alert(*_args, **_kwargs):
        return None

    monkeypatch.setattr(callbacks, "send_operation_limit_alert", _limit_alert)

    def _render(**kwargs):
        seen.update(kwargs)
        return f"{kwargs['amount']} {kwargs['currency']}"

    monkeypatch.setattr(callbacks, "render_operation_confirmation", _render)

    bot = _Bot()
    context = SimpleNamespace(bot=bot, user_data={})
    query = _Query("rem_rec|7", _Message())

    asyncio.run(callbacks.callback_handler(_update(query), context))

    assert seen["currency"] == "EUR"
    assert bot.messages[-1]["text"] == "100.00 EUR"
    assert query.answers[-1][0] == "Записано"
