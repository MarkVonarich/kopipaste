import asyncio
from datetime import date
from types import SimpleNamespace

from services.categories import CategoryReferenceCounts, ManagedCategory


class _Message:
    def __init__(self, chat_id=55, chat_type="private"):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.replies))


class _CallbackQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or _Message()
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _update(query, user_id=55):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=query.message.chat,
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_category_menu_lists_actions_and_delete_requires_destination(monkeypatch):
    from routers import callbacks

    cats = [
        ManagedCategory("Стоматология", "стоматология", "Расходы", category_id=1, source="custom", operation_count=2, has_budget=True),
        ManagedCategory("Здоровье", "здоровье", "Расходы", category_id=2, source="custom", operation_count=0),
    ]
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(
        callbacks,
        "category_reference_counts",
        lambda **_kwargs: CategoryReferenceCounts(operations=2, category_limits=1),
    )

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat_menu")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "Стоматология" in menu.edits[-1][0]
    assert {"cat|add_type", "cat|move_start"} <= set(_callbacks(menu.edits[-1][1]["reply_markup"]))

    delete = _CallbackQuery("cat|delete|k0")
    asyncio.run(callbacks.callback_handler(_update(delete), context))

    assert "есть 2 записей" in delete.edits[-1][0]
    assert any(cb.startswith("cat|delete_to|k0|") for cb in _callbacks(delete.edits[-1][1]["reply_markup"]))


def test_category_transfer_confirmation_is_single_use(monkeypatch):
    from routers import callbacks
    from services.categories import CategoryTransferResult

    calls = {"transfer": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)

    def _transfer(**kwargs):
        calls["transfer"] += 1
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=3),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={
        "category_action": {
            "token": "tok",
            "actor_user_id": 55,
            "workspace_id": 10,
            "op_type": "Расходы",
            "source": "A",
            "destination": "B",
            "mode": "move",
            "expires_at": 9_999_999_999,
            "used": False,
        }
    })

    first = _CallbackQuery("cat|confirm|tok")
    asyncio.run(callbacks.callback_handler(_update(first), context))

    assert calls["transfer"] == 1
    assert "Обновлено операций: 3" in first.edits[-1][0]

    second = _CallbackQuery("cat|confirm|tok")
    asyncio.run(callbacks.callback_handler(_update(second), context))

    assert calls["transfer"] == 1
    assert second.answers[-1][1].get("show_alert") is True


def test_budget_edit_requires_confirmation(monkeypatch):
    from routers import callbacks

    saved = []
    monkeypatch.setattr(callbacks, "get_user_budgets", lambda _cid: (10_000, 50_000))
    monkeypatch.setattr(callbacks, "set_budget", lambda user_id, **kwargs: saved.append((user_id, kwargs)))
    monkeypatch.setattr(callbacks, "_budget_spent", lambda *_args: 0)

    context = SimpleNamespace(user_data={})
    adjust = _CallbackQuery("bud_adj|month|1000")
    asyncio.run(callbacks.callback_handler(_update(adjust), context))

    assert saved == []
    token = context.user_data["budget_pending_edit"]["token"]
    assert f"bud_confirm|{token}" in _callbacks(adjust.edits[-1][1]["reply_markup"])

    confirm = _CallbackQuery(f"bud_confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert saved == [(55, {"month": 51_000})]
    assert "Бюджет обновлён" in confirm.edits[-1][0]


def test_evening_tip_has_working_bot_cta_and_no_miniapp_link():
    from jobs.daily import _evening_reply_markup, _with_evening_tip

    text = _with_evening_tip("Напоминание", 55, date(2026, 7, 28))
    markup = _evening_reply_markup(55, date(2026, 7, 28))
    callbacks = _callbacks(markup)

    assert "💡 Возможность бота" in text
    assert "Mini App" not in text
    assert "noop_today" in callbacks
    assert any(cb in {"lb_hub", "rem_menu", "exp_menu", "cat_menu", "menu_settings", "menu_report", "start_main"} for cb in callbacks)


def test_report_export_buttons_use_exact_periods():
    from jobs.daily import monthly_report_kb, weekly_report_kb

    weekly = _callbacks(weekly_report_kb(date(2026, 7, 20), date(2026, 7, 26)))
    monthly = _callbacks(monthly_report_kb(date(2026, 7, 1), date(2026, 7, 31)))

    assert "rep_export|w|2026-07-20|2026-07-26" in weekly
    assert "rep_export|m|2026-07-01|2026-07-31" in monthly
