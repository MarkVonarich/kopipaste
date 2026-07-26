import asyncio
from datetime import datetime, time
from types import SimpleNamespace

from services.budgeting import BudgetCategoryOption
from services.notification_engine import NotificationPreferences, is_quiet_time, quiet_hours_end_datetime, should_send_now
from ui.keyboards import category_budget_picker_kb


class _Message:
    def __init__(self, chat_id=55, chat_type="private", text=""):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.text = text
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


def _text_update(message, user_id=55):
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=message.chat,
        effective_user=SimpleNamespace(id=user_id),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _last_callbacks(query):
    return _callbacks(query.edits[-1][1]["reply_markup"])


def _category_options(workspace_marker="personal"):
    return [
        BudgetCategoryOption("c1", f"{workspace_marker} Продукты", "prod", 1, "custom"),
        BudgetCategoryOption("c2", f"{workspace_marker} Заведения", "zav", 2, "custom"),
        BudgetCategoryOption("c3", f"{workspace_marker} Аптеки", "apt", 3, "custom"),
    ]


def _patch_common(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "get_user_locale", lambda _cid: "ru")
    monkeypatch.setattr(callbacks, "get_user_currency", lambda _cid: "RUB")
    monkeypatch.setattr(callbacks, "resolve_workspace", lambda chat_id, actor_user_id, chat_type: SimpleNamespace(workspace_id=10 if chat_type == "private" else 20))
    return callbacks


def test_combined_budget_picker_selection_deselection_select_all_clear(monkeypatch):
    callbacks = _patch_common(monkeypatch)
    monkeypatch.setattr(callbacks, "list_active_expense_categories", lambda **kwargs: _category_options("personal"))
    context = SimpleNamespace(user_data={"cbg_draft": {"name": "Everyday", "workspace_id": 10, "selected_tokens": [], "selected_categories": {}, "page": 0}})
    msg = _Message()

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cbgp|t|c1", msg)), context))
    assert context.user_data["cbg_draft"]["selected_tokens"] == ["c1"]
    assert "✅ personal Продукты" in msg.replies[0][1]["reply_markup"].inline_keyboard[0][0].text if msg.replies else True

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cbgp|t|c1", msg)), context))
    assert context.user_data["cbg_draft"]["selected_tokens"] == []

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cbgp|all", msg)), context))
    assert context.user_data["cbg_draft"]["selected_tokens"] == ["c1", "c2", "c3"]

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cbgp|clear", msg)), context))
    assert context.user_data["cbg_draft"]["selected_tokens"] == []


def test_combined_budget_picker_pagination_and_back_preserves_name(monkeypatch):
    callbacks = _patch_common(monkeypatch)
    many = [BudgetCategoryOption(f"c{i}", f"Категория {i}", f"cat{i}", i, "custom") for i in range(1, 13)]
    monkeypatch.setattr(callbacks, "list_active_expense_categories", lambda **kwargs: many)
    context = SimpleNamespace(user_data={"cbg_draft": {"name": "Travel", "workspace_id": 10, "selected_tokens": [], "selected_categories": {}, "page": 0}})
    query = _CallbackQuery("cbgp|p|1")

    asyncio.run(callbacks.callback_handler(_update(query), context))
    assert context.user_data["cbg_draft"]["page"] == 1
    assert "2/2" in [button.text for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row]

    back = _CallbackQuery("cbgp|back")
    asyncio.run(callbacks.callback_handler(_update(back), context))
    assert context.user_data["cbg_draft"]["name"] == "Travel"
    assert context.user_data["cbg_draft"]["step"] == "name"


def test_combined_budget_workspace_isolation(monkeypatch):
    callbacks = _patch_common(monkeypatch)
    seen = []

    def _list(**kwargs):
        seen.append(kwargs["workspace_id"])
        return _category_options("group" if kwargs["workspace_id"] == 20 else "personal")

    monkeypatch.setattr(callbacks, "list_active_expense_categories", _list)
    context = SimpleNamespace(user_data={})
    query = _CallbackQuery("cbg_create", _Message(chat_id=-100, chat_type="group"))

    asyncio.run(callbacks.callback_handler(_update(query, user_id=77), context))
    assert context.user_data["cbg_draft"]["workspace_id"] == 20

    context.user_data["cbg_draft"].update({"name": "Group budget", "step": "categories"})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cbgp|t|c1", query.message), user_id=77), context))
    assert seen[-1] == 20


def test_category_picker_keyboard_uses_compact_callbacks_and_balanced_rows():
    cats = [item.__dict__ for item in _category_options()]
    markup = category_budget_picker_kb(cats, {"c2"})
    callbacks = _callbacks(markup)
    assert "cbgp|t|c2" in callbacks
    assert all("Заведения" not in cb for cb in callbacks)
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)
    assert len(markup.inline_keyboard[0]) == 2


def test_notification_toggle_refreshes_same_message_and_label(monkeypatch):
    callbacks = _patch_common(monkeypatch)
    state = {"morning_enabled": False}

    def _prefs(_cid):
        return {
            "morning_enabled": state["morning_enabled"],
            "evening_enabled": True,
            "limit_alerts_enabled": True,
            "budget_alerts_enabled": True,
            "subscription_alerts_enabled": True,
            "recurring_spend_alerts_enabled": True,
            "weekly_reports_enabled": True,
            "monthly_reports_enabled": True,
            "morning_time": "08:30",
            "evening_time": "20:30",
            "quiet_hours_enabled": False,
        }

    def _toggle(_cid, key):
        assert key == "morning"
        state["morning_enabled"] = not state["morning_enabled"]
        return state["morning_enabled"]

    monkeypatch.setattr(callbacks, "get_notification_preferences", _prefs)
    monkeypatch.setattr(callbacks, "toggle_notification_preference", _toggle)
    context = SimpleNamespace(user_data={"notification_back": "menu_settings"})
    query = _CallbackQuery("notif_toggle|morning")
    asyncio.run(callbacks.callback_handler(_update(query), context))

    assert query.answers[-1][0] == "Утренние уведомления включены"
    assert any("✅ Утро: включено" == button.text for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row)

    second = _CallbackQuery("notif_toggle|morning")
    asyncio.run(callbacks.callback_handler(_update(second), context))
    assert state["morning_enabled"] is False
    assert any("⛔ Утро: выключено" == button.text for row in second.edits[-1][1]["reply_markup"].inline_keyboard for button in row)


def test_quiet_hours_cross_midnight_boundaries():
    prefs = NotificationPreferences(quiet_hours_start=time(22, 30), quiet_hours_end=time(8, 0))
    assert is_quiet_time(datetime(2026, 7, 26, 23, 0), prefs)
    assert is_quiet_time(datetime(2026, 7, 27, 7, 59), prefs)
    assert not is_quiet_time(datetime(2026, 7, 27, 8, 0), prefs)
    assert quiet_hours_end_datetime(datetime(2026, 7, 26, 23, 0), prefs) == datetime(2026, 7, 27, 8, 0)
    assert not should_send_now(datetime(2026, 7, 26, 23, 0), prefs)
    assert should_send_now(datetime(2026, 7, 26, 23, 0), prefs, manual_preview=True)


def test_delete_my_data_phrase_and_final_confirmation(monkeypatch):
    from routers import messages

    context = SimpleNamespace(user_data={"delete_my_data": {"actor_user_id": 55, "step": "phrase", "expires_at": 9_999_999_999, "phrase": "УДАЛИТЬ МОИ ДАННЫЕ"}})
    msg = _Message(text="УДАЛИТЬ МОИ ДАННЫЕ")
    asyncio.run(messages.handle_text(_text_update(msg), context))

    assert context.user_data["delete_my_data"]["step"] == "confirmed"
    assert "privacy_delete_confirm" in _callbacks(msg.replies[-1][1]["reply_markup"])


def test_admin_preview_render_is_sanitized():
    from services.notification_engine import NotificationFact
    from services.notification_preview import NotificationPreview, render_admin_preview

    preview = NotificationPreview(55, "auto", NotificationFact("limit_near", "k", "message", 30, payload={"amount": 10, "raw_text": "secret spend"}), "priority", "message")
    text = render_admin_preview(preview)
    assert "ADMIN PREVIEW — NOT SENT TO USER" in text
    assert "raw_text" not in text
    assert "secret spend" not in text
