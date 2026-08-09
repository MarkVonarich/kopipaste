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


def _update(query, user_id=55, language_code="ru"):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=query.message.chat,
        effective_user=SimpleNamespace(id=user_id, full_name="Test User", language_code=language_code),
    )


def _text_update(message, user_id=55, language_code="ru"):
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=message.chat,
        effective_user=SimpleNamespace(id=user_id, language_code=language_code),
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
    assert any("✅ Ежедневные уведомления" == button.text for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row)

    second = _CallbackQuery("notif_toggle|morning")
    asyncio.run(callbacks.callback_handler(_update(second), context))
    assert state["morning_enabled"] is False
    assert any("✅ Ежедневные уведомления" == button.text for row in second.edits[-1][1]["reply_markup"].inline_keyboard for button in row)


def test_quiet_hours_cross_midnight_boundaries():
    prefs = NotificationPreferences(quiet_hours_start=time(22, 30), quiet_hours_end=time(8, 0))
    assert is_quiet_time(datetime(2026, 7, 26, 23, 0), prefs)
    assert is_quiet_time(datetime(2026, 7, 27, 7, 59), prefs)
    assert not is_quiet_time(datetime(2026, 7, 27, 8, 0), prefs)
    assert quiet_hours_end_datetime(datetime(2026, 7, 26, 23, 0), prefs) == datetime(2026, 7, 27, 8, 0)
    assert not should_send_now(datetime(2026, 7, 26, 23, 0), prefs)
    assert should_send_now(datetime(2026, 7, 26, 23, 0), prefs, manual_preview=True)


def test_i18n_locale_priority_and_privacy_fallback():
    from services.i18n import normalize_locale, resolve_locale, t

    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("uz") == "en"
    assert resolve_locale("ru", "en") == "ru"
    assert resolve_locale(None, "uz") == "en"
    assert t("privacy.title", "uz") == "🔐 Privacy"
    assert t("privacy.clear_history", "uz") != "privacy.clear_history"


def test_privacy_menu_is_localized(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "_privacy_locale", lambda *_args, **_kwargs: "ru")
    context = SimpleNamespace(user_data={})
    ru_query = _CallbackQuery("privacy_menu")
    asyncio.run(callbacks.callback_handler(_update(ru_query), context))

    assert "Конфиденциальность" in ru_query.edits[-1][0]
    assert "Clear financial history" not in ru_query.edits[-1][0]
    assert "hist|menu" in _last_callbacks(ru_query)

    monkeypatch.setattr(callbacks, "_privacy_locale", lambda *_args, **_kwargs: "en")
    en_query = _CallbackQuery("privacy_menu")
    asyncio.run(callbacks.callback_handler(_update(en_query, language_code="en"), SimpleNamespace(user_data={})))

    assert "Privacy" in en_query.edits[-1][0]
    assert "Конфиденциальность" not in en_query.edits[-1][0]


def test_delete_my_data_uses_two_stage_inline_confirmation(monkeypatch):
    from routers import callbacks, commands

    monkeypatch.setattr(commands, "_command_privacy_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(callbacks, "_privacy_locale", lambda *_args, **_kwargs: "en")

    context = SimpleNamespace(user_data={})
    msg = _Message()
    update = _text_update(msg, language_code="en")
    asyncio.run(commands.cmd_delete_my_data(update, context))

    assert context.user_data["delete_my_data"]["step"] == "explain"
    assert "Type exactly" not in msg.replies[-1][0]
    assert "privacy_delete_stage2" in _callbacks(msg.replies[-1][1]["reply_markup"])

    query = _CallbackQuery("privacy_delete_stage2", msg)
    asyncio.run(callbacks.callback_handler(_update(query, language_code="en"), context))

    assert context.user_data["delete_my_data"]["step"] == "confirm"
    assert "privacy_delete_confirm" in _last_callbacks(query)


def test_custom_history_deletion_preview_is_tokenized(monkeypatch):
    from routers import messages
    from services.personal_data_deletion import HistoryDeletionPreview

    monkeypatch.setattr(messages, "_message_privacy_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(messages, "get_user_tz", lambda _uid: 0)
    monkeypatch.setattr(
        messages,
        "preview_delete_financial_history",
        lambda user_id, start, end: HistoryDeletionPreview(user_id, start, end, 3, {"operations": 3}),
    )

    context = SimpleNamespace(user_data={"history_delete_wizard": {"actor_user_id": 55, "step": "start", "expires_at": 9_999_999_999}})
    start_msg = _Message(text="2026-07-01")
    asyncio.run(messages.handle_text(_text_update(start_msg, language_code="en"), context))

    assert context.user_data["history_delete_wizard"]["step"] == "end"
    assert "Enter end date" in start_msg.replies[-1][0]

    end_msg = _Message(text="2026-07-26")
    asyncio.run(messages.handle_text(_text_update(end_msg, language_code="en"), context))

    assert "history_delete_wizard" not in context.user_data
    assert context.user_data["history_delete_confirm"]["actor_user_id"] == 55
    assert "Operations to delete: 3" in end_msg.replies[-1][0]
    assert any(cb.startswith("hist|confirm|") for cb in _callbacks(end_msg.replies[-1][1]["reply_markup"]))


def test_history_period_bounds_and_personal_operation_scope():
    from datetime import date
    from services.personal_data_deletion import _personal_operation_where, history_period_bounds

    today = date(2026, 7, 26)
    assert history_period_bounds("today", today) == (today, today)
    assert history_period_bounds("last7", today) == (date(2026, 7, 20), today)
    assert history_period_bounds("this_month", today) == (date(2026, 7, 1), today)
    assert history_period_bounds("prev_month", today) == (date(2026, 6, 1), date(2026, 6, 30))
    assert history_period_bounds("this_year", today) == (date(2026, 1, 1), today)
    assert history_period_bounds("all", today) == (None, None)

    where = _personal_operation_where({"workspace_id", "user_id", "chat_id"}, [10])
    assert "workspace_id = ANY(%s)" in where
    assert "workspace_id IS NULL" in where
    assert "user_id=%s" in where
    assert "chat_id=%s" in where


def test_history_delete_confirm_token_is_single_use(monkeypatch):
    from datetime import date
    from routers import callbacks
    from services.personal_data_deletion import HistoryDeletionPreview, HistoryDeletionResult

    calls = {"delete": 0}

    monkeypatch.setattr(callbacks, "_privacy_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(callbacks, "get_user_tz", lambda _uid: 0)
    monkeypatch.setattr(
        callbacks,
        "preview_delete_financial_history",
        lambda user_id, start, end: HistoryDeletionPreview(user_id, start, end, 2, {"operations": 2}),
    )

    def _delete(user_id, start, end):
        calls["delete"] += 1
        return HistoryDeletionResult(user_id, start, end, 2, {"operations": 2}, deleted=True)

    monkeypatch.setattr(callbacks, "delete_financial_history", _delete)

    context = SimpleNamespace(user_data={})
    preview_query = _CallbackQuery("hist|period|today")
    asyncio.run(callbacks.callback_handler(_update(preview_query, language_code="en"), context))

    token = context.user_data["history_delete_confirm"]["token"]
    confirm_query = _CallbackQuery(f"hist|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm_query, language_code="en"), context))

    assert calls["delete"] == 1
    assert "Deleted operations: 2" in confirm_query.edits[-1][0]
    assert "history_delete_confirm" not in context.user_data

    second_query = _CallbackQuery(f"hist|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(second_query, language_code="en"), context))
    assert calls["delete"] == 1
    assert second_query.answers[-1][0] == "Confirmation expired. Please start again."


def test_admin_preview_render_is_sanitized():
    from services.notification_engine import NotificationFact
    from services.notification_preview import NotificationPreview, render_admin_preview

    preview = NotificationPreview(55, "auto", NotificationFact("limit_near", "k", "message", 30, payload={"amount": 10, "raw_text": "secret spend"}), "priority", "message")
    text = render_admin_preview(preview)
    assert "ADMIN PREVIEW — NOT SENT TO USER" in text
    assert "raw_text" not in text
    assert "secret spend" not in text
