import asyncio
from types import SimpleNamespace

from ui.keyboards import (
    export_menu_kb,
    help_menu_kb,
    limits_budgets_hub_kb,
    main_menu_kb,
    reminders_menu_kb,
    settings_menu_kb,
)


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

    async def edit_message_reply_markup(self, **kwargs):
        self.edits.append(("", kwargs))


def _update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=query.message.chat,
        effective_user=SimpleNamespace(id=query.message.chat.id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _last_text(query):
    return query.edits[-1][0]


def _last_callbacks(query):
    return _callbacks(query.edits[-1][1]["reply_markup"])


def _patch_nav_deps(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "get_user_locale", lambda _cid: "ru")
    monkeypatch.setattr(callbacks, "get_user_currency", lambda _cid: "RUB")
    monkeypatch.setattr(callbacks, "reminders_list", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(callbacks, "list_general_limits", lambda _cid: [])
    monkeypatch.setattr(callbacks, "list_category_budget_groups", lambda _cid: [])
    monkeypatch.setattr(callbacks, "_export_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(callbacks, "get_notification_preferences", lambda _cid: {
        "morning_enabled": True,
        "evening_enabled": True,
        "limit_alerts_enabled": True,
        "budget_alerts_enabled": True,
        "subscription_alerts_enabled": True,
        "recurring_spend_alerts_enabled": True,
        "morning_time": "09:00",
        "evening_time": "21:00",
    })
    return callbacks


def _run_callback(monkeypatch, data, context=None):
    callbacks = _patch_nav_deps(monkeypatch)
    query = _CallbackQuery(data)
    context = context or SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(query), context))
    return query, context


def test_main_menu_limits_and_budgets_button_opens_hub(monkeypatch):
    assert "lb_hub" in _callbacks(main_menu_kb("ru"))
    query, _context = _run_callback(monkeypatch, "lb_hub")
    assert query.answers
    assert "Лимиты" in _last_text(query)
    assert {"lim_list", "gl_menu", "cbg_menu", "lb_status", "menu_notifications", "start_main"} <= set(_last_callbacks(query))


def test_main_menu_settings_button_opens_settings(monkeypatch):
    assert "menu_settings" in _callbacks(main_menu_kb("ru"))
    query, context = _run_callback(monkeypatch, "menu_settings")
    assert query.answers
    assert "Настройки" in _last_text(query)
    assert {"menu_currency", "menu_reminder", "menu_notifications", "menu_tz", "workspace_menu", "privacy_menu", "cat_menu", "start_main"} <= set(_last_callbacks(query))
    assert context.user_data["notification_back"] == "menu_settings"


def test_export_back_returns_to_main(monkeypatch):
    query, _context = _run_callback(monkeypatch, "exp_menu")
    assert "start_main" in _last_callbacks(query)
    back, _context = _run_callback(monkeypatch, "start_main")
    assert "Добавить" in _last_callbacks(back)[0] or "menu_examples" in _last_callbacks(back)


def test_reminders_back_returns_to_main(monkeypatch):
    query, context = _run_callback(monkeypatch, "rem_menu")
    assert "start_main" in _last_callbacks(query)
    assert context.user_data["notification_back"] == "rem_menu"
    back, _context = _run_callback(monkeypatch, "start_main", context)
    assert "menu_settings" in _last_callbacks(back)


def test_currency_back_returns_to_settings(monkeypatch):
    query, _context = _run_callback(monkeypatch, "menu_currency")
    assert "menu_settings" in _last_callbacks(query)
    back, _context = _run_callback(monkeypatch, "menu_settings")
    assert "menu_currency" in _last_callbacks(back)


def test_notifications_back_returns_to_settings(monkeypatch):
    context = SimpleNamespace(user_data={"notification_back": "menu_settings"})
    query, _context = _run_callback(monkeypatch, "menu_notifications", context)
    assert "menu_settings" in _last_callbacks(query)
    back, _context = _run_callback(monkeypatch, "menu_settings", context)
    assert "menu_notifications" in _last_callbacks(back)


def test_timezone_back_returns_to_settings(monkeypatch):
    query, _context = _run_callback(monkeypatch, "menu_tz")
    assert "menu_settings" in _last_callbacks(query)
    back, _context = _run_callback(monkeypatch, "menu_settings")
    assert "menu_tz" in _last_callbacks(back)


def test_unknown_callback_answers_and_falls_back_to_main(monkeypatch):
    query, _context = _run_callback(monkeypatch, "removed_legacy_menu")
    assert query.answers
    assert query.answers[-1][0] == "Кнопка устарела. Открываю главное меню."
    assert "menu_settings" in _last_callbacks(query)


def test_public_keyboards_emit_handled_callbacks_and_no_legacy_main_menu():
    public_markups = [
        main_menu_kb("ru"),
        settings_menu_kb("ru"),
        limits_budgets_hub_kb("ru"),
        export_menu_kb(),
        reminders_menu_kb(False),
        reminders_menu_kb(True),
        help_menu_kb("ru"),
    ]
    emitted = {cb for markup in public_markups for cb in _callbacks(markup)}
    direct_handlers = {
        "menu_examples", "lb_hub", "rem_menu", "exp_menu", "menu_settings", "menu_help",
        "lim_list", "gl_menu", "cbg_menu", "lb_status", "menu_notifications", "start_main",
        "menu_currency", "menu_reminder", "workspace_menu", "privacy_menu", "cat_menu", "menu_tz", "menu_support",
        "exp_today", "exp_7", "exp_14", "exp_m", "exp_pm", "exp_y", "exp_py", "exp_custom",
        "rem_add", "rem_all",
    }
    assert emitted <= direct_handlers
    assert "menu_report" not in _callbacks(main_menu_kb("ru"))
    assert "menu_analytics" not in _callbacks(main_menu_kb("ru"))
    assert all(len(cb.encode("utf-8")) <= 64 for cb in emitted)


def test_back_and_cancel_callbacks_have_valid_public_destinations():
    valid = {
        "start_main", "menu_settings", "lb_hub", "rem_menu", "exp_menu", "exp_custom",
        "menu_currency", "menu_reminder", "menu_tz",
    }
    public_markups = [
        main_menu_kb("ru"),
        settings_menu_kb("ru"),
        limits_budgets_hub_kb("ru"),
        export_menu_kb(),
        reminders_menu_kb(False),
        reminders_menu_kb(True),
        help_menu_kb("ru"),
    ]
    nav_callbacks = {
        button.callback_data
        for markup in public_markups
        for row in markup.inline_keyboard
        for button in row
        if any(word in button.text for word in ("Назад", "В меню", "Главное меню", "Отмена", "Cancel"))
    }
    assert nav_callbacks <= valid


def test_export_keyboard_row_layout_is_balanced():
    lengths = [len(row) for row in export_menu_kb().inline_keyboard]
    assert lengths == [2, 2, 2, 2, 1]


def test_reminder_keyboard_row_layout_is_balanced():
    assert [len(row) for row in reminders_menu_kb(False).inline_keyboard] == [2, 1, 1]
    assert [len(row) for row in reminders_menu_kb(True).inline_keyboard] == [2, 1, 1]
