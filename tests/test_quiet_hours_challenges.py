import asyncio
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

from telegram import InlineKeyboardMarkup

from services.automatic_notifications import DeliveryPolicy, QuietHoursWindow, dispatch_automatic_notification, is_quiet_local


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


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
        effective_user=SimpleNamespace(id=user_id, full_name="Test User", language_code="ru"),
    )


def _callbacks(markup: InlineKeyboardMarkup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_quiet_hours_local_boundaries_and_disabled_equal_window():
    assert is_quiet_local(datetime(2026, 7, 31, 22, 30), time(22, 30), time(8, 0))
    assert is_quiet_local(datetime(2026, 8, 1, 7, 59), time(22, 30), time(8, 0))
    assert not is_quiet_local(datetime(2026, 8, 1, 8, 0), time(22, 30), time(8, 0))
    assert is_quiet_local(datetime(2026, 7, 31, 13, 0), time(12, 0), time(14, 0))
    assert not is_quiet_local(datetime(2026, 7, 31, 14, 0), time(12, 0), time(14, 0))
    assert not is_quiet_local(datetime(2026, 7, 31, 22, 30), time(22, 30), time(22, 30))


def test_dispatcher_defers_or_skips_automatic_messages_during_quiet_hours(monkeypatch):
    from services import automatic_notifications as mod

    inserted = []
    skipped = []
    events = []
    window = QuietHoursWindow(True, time(22, 30), time(8, 0), "Europe/Moscow")
    now = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(mod, "quiet_context", lambda user_id, now_utc=None: (window, now, True))
    monkeypatch.setattr(mod, "_insert_deferred", lambda **kwargs: inserted.append(kwargs) or 101)
    monkeypatch.setattr(mod, "_mark_skip", lambda **kwargs: skipped.append(kwargs))
    monkeypatch.setattr(mod, "track_product_event", lambda ev: events.append(ev))

    context = SimpleNamespace(bot=_Bot())
    deferred = asyncio.run(dispatch_automatic_notification(
        context,
        user_id=55,
        notification_type="weekly_report",
        dedupe_key="weekly:2026-07-27",
        policy=DeliveryPolicy.DEFER,
        text="report",
    ))
    skipped_result = asyncio.run(dispatch_automatic_notification(
        context,
        user_id=55,
        notification_type="evening_reminder",
        dedupe_key="evening:2026-07-31",
        policy=DeliveryPolicy.SKIP,
        text="prompt",
    ))

    assert deferred.status == "deferred"
    assert inserted[0]["notification_type"] == "weekly_report"
    assert skipped_result.status == "skipped"
    assert skipped[0]["notification_type"] == "evening_reminder"
    assert context.bot.sent == []
    assert {ev.event_name for ev in events} == {"automatic_notification_deferred", "automatic_notification_skipped_quiet_hours"}


def test_dispatcher_sends_immediately_outside_quiet_hours(monkeypatch):
    from services import automatic_notifications as mod

    window = QuietHoursWindow(True, time(22, 30), time(8, 0), "Europe/Moscow")
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    events = []
    monkeypatch.setattr(mod, "quiet_context", lambda user_id, now_utc=None: (window, now, False))
    monkeypatch.setattr(mod, "track_product_event", lambda ev: events.append(ev))

    context = SimpleNamespace(bot=_Bot())
    result = asyncio.run(dispatch_automatic_notification(
        context,
        user_id=55,
        notification_type="category_limit_warning",
        dedupe_key="limit:55:2026-07-31",
        policy=DeliveryPolicy.DEFER,
        text="limit",
    ))

    assert result.status == "sent"
    assert context.bot.sent[0]["chat_id"] == 55
    assert events[0].event_name == "automatic_notification_sent"


def test_queue_claim_is_durable_idempotent_and_skip_locked():
    import inspect
    from services.automatic_notifications import claim_due_notifications, _insert_deferred

    claim_source = inspect.getsource(claim_due_notifications)
    insert_source = inspect.getsource(_insert_deferred)
    assert "FOR UPDATE SKIP LOCKED" in claim_source
    assert "ON CONFLICT (user_id, notification_type, dedupe_key)" in insert_source


def test_challenge_home_navigation_and_notifications_back(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "track_product_event", lambda _ev: None)
    monkeypatch.setattr(callbacks, "get_notification_preferences", lambda _cid: {
        "morning_enabled": True,
        "evening_enabled": True,
        "limit_alerts_enabled": True,
        "budget_alerts_enabled": True,
        "subscription_alerts_enabled": True,
        "recurring_spend_alerts_enabled": True,
        "weekly_reports_enabled": True,
        "monthly_reports_enabled": True,
        "challenge_notifications_enabled": True,
        "morning_time": "09:00",
        "evening_time": "21:00",
        "quiet_hours_enabled": True,
    })
    context = SimpleNamespace(user_data={})
    home = _CallbackQuery("chal|home")
    asyncio.run(callbacks.callback_handler(_update(home), context))
    assert {"chal|sec|today", "chal|sec|week", "chal|sec|month", "chal|sec|onboarding", "chal|ach", "chal|notif", "chal|how"} <= set(_callbacks(home.edits[-1][1]["reply_markup"]))

    notif = _CallbackQuery("chal|notif")
    asyncio.run(callbacks.callback_handler(_update(notif), context))
    assert context.user_data["notification_back"] == "chal|home"
    assert "chal|home" in _callbacks(notif.edits[-1][1]["reply_markup"])


def test_reminder_created_inside_quiet_hours_requires_confirmation(monkeypatch):
    from routers import callbacks

    saved = []
    monkeypatch.setattr(callbacks, "pg_fetchall", lambda *args, **kwargs: [(23,)])
    monkeypatch.setattr(callbacks, "quiet_hours_window", lambda _uid: QuietHoursWindow(True, time(22, 30), time(8, 0), "Europe/Moscow"))
    monkeypatch.setattr(callbacks, "_save_reminder_draft", lambda user_id, draft: saved.append((user_id, draft)) or 777)

    context = SimpleNamespace(user_data={"rem_draft": {
        "title": "Subscription",
        "rem_type": "Расходы",
        "category": "Подписки",
        "amount": 1000,
        "event_date": date(2026, 8, 2),
        "repeat_rule": "none",
        "notify_days_before": 1,
    }})
    save = _CallbackQuery("rem_save")
    asyncio.run(callbacks.callback_handler(_update(save), context))

    assert saved == []
    assert "тихие часы" in save.edits[-1][0]
    assert {"rem_quiet_save", "rem_quiet_time", "rem_menu"} <= set(_callbacks(save.edits[-1][1]["reply_markup"]))

    confirm = _CallbackQuery("rem_quiet_save")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))
    assert saved[0][0] == 55
    assert context.user_data.get("rem_draft") is None


def test_challenge_prompt_uses_dispatcher_and_never_posthog_as_progress_source(monkeypatch):
    from services import challenges

    calls = []

    async def _dispatch(_context, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="skipped", reason="quiet_hours")

    monkeypatch.setattr(challenges, "challenge_prompt_candidates", lambda: [55])
    monkeypatch.setattr(challenges, "get_notification_preferences", lambda _uid: {"challenge_notifications_enabled": True})
    monkeypatch.setattr(challenges, "user_local_today", lambda _uid: date(2026, 7, 31))
    monkeypatch.setattr(challenges, "build_challenge_prompt", lambda _uid: "prompt")
    monkeypatch.setattr("services.automatic_notifications.dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(challenges, "track_product_event", lambda _ev: None)

    counts = asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))

    assert counts["skipped"] == 1
    assert calls[0]["policy"] == DeliveryPolicy.SKIP
    assert calls[0]["notification_type"] == "challenge_prompt"
    assert "posthog" not in challenges.calculate_progress.__code__.co_names
