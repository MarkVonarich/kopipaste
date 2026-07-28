import asyncio
from datetime import date
from types import SimpleNamespace

from services.categories import CategoryReferenceCounts, ManagedCategory


class _Message:
    def __init__(self, chat_id=55, chat_type="private"):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.text = ""
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


def _message_update(text, user_id=55, chat_id=55, chat_type="private"):
    message = _Message(chat_id=chat_id, chat_type=chat_type)
    message.text = text
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=message.chat,
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_category_menu_starts_with_type_selector(monkeypatch):
    from routers import callbacks

    called = {"list": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: called.__setitem__("list", called["list"] + 1))

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat_menu")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "Категории" in menu.edits[-1][0]
    assert "Выберите тип, категории которого хотите настроить." in menu.edits[-1][0]
    assert {"cat|type|expense", "cat|type|income", "cat|goals", "menu_settings", "start_main"} <= set(_callbacks(menu.edits[-1][1]["reply_markup"]))
    assert called["list"] == 0


def test_category_type_list_preserves_income_add_type(monkeypatch):
    from routers import callbacks

    cats = [
        ManagedCategory("Зарплата", "зарплата", "Доходы", category_id=1, source="custom", operation_count=2),
    ]
    seen = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **kwargs: seen.append(kwargs["op_type"]) or cats)

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat|type|income")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "Категории доходов" in menu.edits[-1][0]
    assert "Зарплата" in menu.edits[-1][0]
    assert {"cat|add|income", "cat|move_start|income"} <= set(_callbacks(menu.edits[-1][1]["reply_markup"]))
    assert seen == ["Доходы"]

    add = _CallbackQuery("cat|add|income")
    asyncio.run(callbacks.callback_handler(_update(add), context))

    assert context.user_data["await_category_create"]["op_type"] == "Доходы"
    assert context.user_data["await_category_create"]["type_key"] == "income"


def test_category_goals_screen_is_informational(monkeypatch):
    from routers import callbacks

    called = {"list": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: called.__setitem__("list", called["list"] + 1))

    context = SimpleNamespace(user_data={})
    goals = _CallbackQuery("cat|goals")
    asyncio.run(callbacks.callback_handler(_update(goals), context))

    assert "Категории целей" in goals.edits[-1][0]
    assert "ничего не создаётся" in goals.edits[-1][0]
    assert called["list"] == 0


def test_category_card_and_delete_inspection_offer_distinct_paths(monkeypatch):
    from routers import callbacks

    cats = [
        ManagedCategory("Стоматология", "стоматология", "Расходы", category_id=1, source="custom", operation_count=2, has_budget=True),
        ManagedCategory("Здоровье", "здоровье", "Расходы", category_id=2, source="custom", operation_count=0),
    ]
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2, category_limits=1, reminders=1, aliases=1))

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat|type|expense")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    card = _CallbackQuery("cat|open|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(card), context))

    assert "Тип: расход" in card.edits[-1][0]
    assert {"cat|rename|expense|k0", "cat|move_from|expense|k0", "cat|delete|expense|k0"} <= set(_callbacks(card.edits[-1][1]["reply_markup"]))

    delete = _CallbackQuery("cat|delete|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(delete), context))

    callbacks_data = set(_callbacks(delete.edits[-1][1]["reply_markup"]))
    assert "Операций: 2" in delete.edits[-1][0]
    assert "cat|delete_transfer|expense|k0" in callbacks_data
    assert "cat|hard1|expense|k0" in callbacks_data


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


def test_income_transfer_uses_selected_type(monkeypatch):
    from routers import callbacks
    from services.categories import CategoryTransferResult

    cats = [
        ManagedCategory("Зарплата", "зарплата", "Доходы", category_id=1, source="custom", operation_count=2),
        ManagedCategory("Бонус", "бонус", "Доходы", category_id=2, source="custom", operation_count=0),
    ]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **kwargs: cats if kwargs["op_type"] == "Доходы" else [])
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2))

    def _transfer(**kwargs):
        calls.append(kwargs)
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=2),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={})

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|income")), context))
    preview = _CallbackQuery("cat|move_to|income|k0|k1")
    asyncio.run(callbacks.callback_handler(_update(preview), context))
    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls[0]["op_type"] == "Доходы"
    assert calls[0]["source"] == "Зарплата"
    assert calls[0]["destination"] == "Бонус"
    assert "cat|post_delete|" in "|".join(_callbacks(confirm.edits[-1][1]["reply_markup"]))


def test_duplicate_rename_offers_merge_retry_open_and_confirm(monkeypatch):
    from routers import callbacks, messages
    from services.categories import CategoryTransferResult

    cats = [
        ManagedCategory("A", "a", "Расходы", category_id=1, source="custom", operation_count=3),
        ManagedCategory("B", "b", "Расходы", category_id=2, source="custom", operation_count=1),
    ]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=3, category_limits=1))

    def _transfer(**kwargs):
        calls.append(kwargs)
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=3),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|rename|expense|k0")), context))

    msg_update = _message_update("B")
    asyncio.run(messages.handle_text(msg_update, context))

    text, kwargs = msg_update.message.replies[-1]
    data = _callbacks(kwargs["reply_markup"])
    assert "уже есть" in text
    assert any(cb.startswith("cat|confirm|") for cb in data)
    assert any(cb.startswith("cat|rename_again|") for cb in data)
    assert any(cb.startswith("cat|open_dup|") for cb in data)

    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls[0]["source"] == "A"
    assert calls[0]["destination"] == "B"
    assert calls[0]["archive_source"] is False


def test_normal_rename_confirms_service_call(monkeypatch):
    from routers import callbacks, messages
    from services.categories import CategoryRenameResult

    cats = [ManagedCategory("Food", "food", "Расходы", category_id=1, source="custom", operation_count=2)]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2))

    def _rename(**kwargs):
        calls.append(kwargs)
        return CategoryRenameResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=2),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "rename_category", _rename)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|rename|expense|k0")), context))
    msg_update = _message_update("food")
    asyncio.run(messages.handle_text(msg_update, context))
    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls == [{
        "user_id": 55,
        "workspace_id": 10,
        "op_type": "Расходы",
        "source": "Food",
        "destination": "food",
    }]
    assert "переименована" in confirm.edits[-1][0]


def test_hard_delete_requires_second_confirmation(monkeypatch):
    from routers import callbacks

    cats = [ManagedCategory("Trips", "trips", "Расходы", category_id=1, source="custom", operation_count=4)]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=4, aliases=1))

    def _hard_delete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(source=kwargs["category"], deleted_operation_count=4)

    monkeypatch.setattr(callbacks, "hard_delete_category_with_operations", _hard_delete)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))

    first = _CallbackQuery("cat|hard1|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(first), context))
    assert calls == []
    assert "cat|hard2|expense|k0" in _callbacks(first.edits[-1][1]["reply_markup"])

    second = _CallbackQuery("cat|hard2|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(second), context))
    assert calls == []
    token = context.user_data["category_action"]["token"]

    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))
    assert calls == [{"user_id": 55, "workspace_id": 10, "op_type": "Расходы", "category": "Trips"}]
    assert "Удалено операций: 4" in confirm.edits[-1][0]


def test_protected_category_cannot_be_renamed_or_deleted(monkeypatch):
    from routers import callbacks

    cats = [ManagedCategory("Без операций", "без операций", "Расходы", category_id=None, source="operation", operation_count=1)]
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))

    rename = _CallbackQuery("cat|rename|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(rename), context))
    delete = _CallbackQuery("cat|delete|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(delete), context))

    assert rename.answers[-1][1]["show_alert"] is True
    assert delete.answers[-1][1]["show_alert"] is True


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
