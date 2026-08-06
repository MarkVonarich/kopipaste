import asyncio
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

from telegram import InlineKeyboardMarkup

from services.automatic_notifications import DeliveryPolicy, QuietHoursWindow, dispatch_automatic_notification, is_quiet_local
from services.challenges import ChallengeCard, ChallengeDefinition, ChallengePrompt
from services.product_events import ProductEvent


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
    monkeypatch.setattr(mod, "_claim_immediate_send", lambda **_kwargs: 101)
    monkeypatch.setattr(mod, "mark_notification_sent", lambda _notification_id: None)
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


def test_dispatcher_immediate_send_is_deduped(monkeypatch):
    from services import automatic_notifications as mod

    window = QuietHoursWindow(False, None, None, "Europe/Moscow")
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    claims = [101, None]
    monkeypatch.setattr(mod, "quiet_context", lambda user_id, now_utc=None: (window, now, False))
    monkeypatch.setattr(mod, "_claim_immediate_send", lambda **_kwargs: claims.pop(0))
    monkeypatch.setattr(mod, "mark_notification_sent", lambda _notification_id: None)
    monkeypatch.setattr(mod, "track_product_event", lambda _ev: None)

    context = SimpleNamespace(bot=_Bot())
    first = asyncio.run(dispatch_automatic_notification(
        context,
        user_id=55,
        notification_type="challenge_prompt",
        dedupe_key="challenge:2026-07-31:daily_progress_prompt",
        policy=DeliveryPolicy.SKIP,
        text="prompt",
    ))
    second = asyncio.run(dispatch_automatic_notification(
        context,
        user_id=55,
        notification_type="challenge_prompt",
        dedupe_key="challenge:2026-07-31:daily_progress_prompt",
        policy=DeliveryPolicy.SKIP,
        text="prompt",
    ))

    assert first.status == "sent"
    assert second.status == "duplicate"
    assert len(context.bot.sent) == 1


def test_queue_claim_is_durable_idempotent_and_skip_locked():
    import inspect
    from services.automatic_notifications import claim_due_notifications, _insert_deferred

    claim_source = inspect.getsource(claim_due_notifications)
    insert_source = inspect.getsource(_insert_deferred)
    assert "FOR UPDATE SKIP LOCKED" in claim_source
    assert "ON CONFLICT (user_id, notification_type, dedupe_key)" in insert_source


def test_challenge_home_navigation_has_no_duplicate_notifications_button(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "track_product_event", lambda _ev: None)
    context = SimpleNamespace(user_data={})
    home = _CallbackQuery("chal|home")
    asyncio.run(callbacks.callback_handler(_update(home), context))
    displayed = _callbacks(home.edits[-1][1]["reply_markup"])
    assert displayed == ["chal|sec|today", "chal|sec|week", "chal|sec|month", "chal|sec|onboarding", "chal|ach", "chal|how", "start_main"]
    assert "chal|notif" not in displayed


def test_challenge_notification_settings_remain_available_from_settings(monkeypatch):
    from routers import callbacks

    prefs = {
        "morning_enabled": True,
        "evening_enabled": True,
        "limit_alerts_enabled": True,
        "budget_alerts_enabled": True,
        "subscription_alerts_enabled": True,
        "recurring_spend_alerts_enabled": True,
        "weekly_reports_enabled": True,
        "monthly_reports_enabled": True,
        "challenge_notifications_enabled": False,
        "morning_time": "09:00",
        "evening_time": "21:00",
        "quiet_hours_enabled": True,
    }
    monkeypatch.setattr(callbacks, "get_notification_preferences", lambda _cid: prefs)

    context = SimpleNamespace(user_data={"notification_back": "menu_settings"})
    query = _CallbackQuery("menu_notifications")
    asyncio.run(callbacks.callback_handler(_update(query), context))

    displayed = _callbacks(query.edits[-1][1]["reply_markup"])
    assert "notif_challenges" in displayed
    assert "menu_settings" in displayed
    assert any(button.text == "🏆 Челленджи: выключены" for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row)

    detail = _CallbackQuery("notif_challenges")
    asyncio.run(callbacks.callback_handler(_update(detail), context))
    assert "Уведомления о челленджах выключены по умолчанию" in detail.edits[-1][0]
    assert "notif_toggle|challenges" in _callbacks(detail.edits[-1][1]["reply_markup"])


def test_challenge_notification_preference_toggle_still_works(monkeypatch):
    from routers import callbacks

    state = {"enabled": True}

    def _prefs(_cid):
        return {
            "morning_enabled": True,
            "evening_enabled": True,
            "limit_alerts_enabled": True,
            "budget_alerts_enabled": True,
            "subscription_alerts_enabled": True,
            "recurring_spend_alerts_enabled": True,
            "weekly_reports_enabled": True,
            "monthly_reports_enabled": True,
            "challenge_notifications_enabled": state["enabled"],
            "morning_time": "09:00",
            "evening_time": "21:00",
            "quiet_hours_enabled": True,
        }

    def _toggle(_cid, key):
        assert key == "challenges"
        state["enabled"] = not state["enabled"]
        return state["enabled"]

    monkeypatch.setattr(callbacks, "get_notification_preferences", _prefs)
    monkeypatch.setattr(callbacks, "toggle_notification_preference", _toggle)
    monkeypatch.setattr(callbacks, "track_product_event", lambda _ev: None)

    context = SimpleNamespace(user_data={"notification_back": "menu_settings"})
    query = _CallbackQuery("notif_toggle|challenges")
    asyncio.run(callbacks.callback_handler(_update(query), context))

    assert query.answers[-1][0] == "Челленджи выключены"
    assert "🏆 Челленджи: выключены" in query.edits[-1][0]
    assert any(button.text == "Включить" for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row)


def test_default_challenge_preference_is_false_for_missing_rows(monkeypatch):
    from services import notification_preferences as prefs

    monkeypatch.setattr(prefs, "pg_fetchall", lambda *_args, **_kwargs: [])
    values = prefs.get_notification_preferences(55)

    assert values["challenge_notifications_enabled"] is False
    assert values["morning_enabled"] is True


def test_challenge_preference_sql_resolves_null_to_false():
    import inspect
    from services.notification_preferences import _preferences_rows

    source = inspect.getsource(_preferences_rows)
    assert "COALESCE(challenge_notifications_enabled, false)" in source


def test_explicit_challenge_toggle_uses_false_default(monkeypatch):
    from services import notification_preferences as prefs

    statements = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            statements.append((sql, params))

        def fetchone(self):
            return (True,)

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(prefs, "get_conn", lambda: _Conn())

    assert prefs.toggle_notification_preference(55, "challenges") is True
    assert statements[0][1] == (55, True, False)


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


def _prompt_for(key="daily_two_operations", period_key="2026-07-31"):
    definition = ChallengeDefinition(key, "Две записи за день", "Запишите две реальные операции за сегодня.", "day", "operation_count", 2, "daily", "Добавить операцию", "menu_examples", "Готово.")
    card = ChallengeCard(definition, 1, 2, False, period_key, date(2026, 7, 31))
    return ChallengePrompt(card=card, text="prompt")


def test_challenge_prompt_disabled_user_creates_no_notification(monkeypatch):
    from services import challenges

    monkeypatch.setattr(challenges, "challenge_prompt_candidates", lambda: [55])
    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: False)
    monkeypatch.setattr(challenges, "build_challenge_prompt", lambda _uid: (_ for _ in ()).throw(AssertionError("prompt should not be built")))

    counts = asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))

    assert counts == {"sent": 0, "deferred": 0, "skipped": 0}


def test_challenge_prompt_uses_stable_daily_dedupe_and_never_posthog_as_progress_source(monkeypatch):
    from services import challenges

    calls = []

    async def _dispatch(_context, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="skipped", reason="quiet_hours")

    monkeypatch.setattr(challenges, "challenge_prompt_candidates", lambda: [55])
    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: True)
    monkeypatch.setattr(challenges, "user_local_today", lambda _uid: date(2026, 7, 31))
    monkeypatch.setattr(challenges, "build_challenge_prompt", lambda _uid: _prompt_for())
    monkeypatch.setattr("services.automatic_notifications.dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(challenges, "track_product_event", lambda _ev: None)

    counts = asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))

    assert counts["skipped"] == 1
    assert calls[0]["policy"] == DeliveryPolicy.SKIP
    assert calls[0]["notification_type"] == "challenge_prompt"
    assert calls[0]["dedupe_key"] == "challenge:2026-07-31:daily_progress_prompt"
    assert calls[0]["payload"]["challenge_key"] == "daily_two_operations"
    assert "posthog" not in challenges.calculate_progress.__code__.co_names


def test_repeated_hourly_scans_keep_same_daily_dedupe_key(monkeypatch):
    from services import challenges

    calls = []

    async def _dispatch(_context, **kwargs):
        calls.append(kwargs["dedupe_key"])
        return SimpleNamespace(status="duplicate", reason="dedupe")

    monkeypatch.setattr(challenges, "challenge_prompt_candidates", lambda: [55])
    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: True)
    monkeypatch.setattr(challenges, "user_local_today", lambda _uid: date(2026, 7, 31))
    monkeypatch.setattr(challenges, "build_challenge_prompt", lambda _uid: _prompt_for())
    monkeypatch.setattr("services.automatic_notifications.dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(challenges, "track_product_event", lambda _ev: None)

    asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))
    asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))

    assert calls == ["challenge:2026-07-31:daily_progress_prompt", "challenge:2026-07-31:daily_progress_prompt"]


def test_next_local_day_gets_new_daily_dedupe_key(monkeypatch):
    from services import challenges

    dates = [date(2026, 7, 31), date(2026, 8, 1)]
    calls = []

    async def _dispatch(_context, **kwargs):
        calls.append(kwargs["dedupe_key"])
        return SimpleNamespace(status="sent", reason=None)

    monkeypatch.setattr(challenges, "challenge_prompt_candidates", lambda: [55])
    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: True)
    monkeypatch.setattr(challenges, "user_local_today", lambda _uid: dates.pop(0))
    monkeypatch.setattr(challenges, "build_challenge_prompt", lambda _uid: _prompt_for())
    monkeypatch.setattr("services.automatic_notifications.dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(challenges, "track_product_event", lambda _ev: None)

    asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))
    asyncio.run(challenges.challenge_daily_prompt_job(SimpleNamespace(bot=_Bot())))

    assert calls == ["challenge:2026-07-31:daily_progress_prompt", "challenge:2026-08-01:daily_progress_prompt"]


def test_completion_and_achievement_progress_persist_without_notifications_when_disabled(monkeypatch):
    from services import challenges

    queued = []
    card = _prompt_for().card
    completed_card = ChallengeCard(card.definition, 2, 2, True, "2026-07-31", date(2026, 7, 31))

    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: False)
    monkeypatch.setattr(challenges, "grant_achievement", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(challenges, "upsert_assignments", lambda *_args, **_kwargs: [completed_card])
    monkeypatch.setattr("services.automatic_notifications.queue_automatic_notification", lambda **kwargs: queued.append(kwargs))

    challenges.process_product_event(ProductEvent(event_name="operation_created", user_id=55, properties={"operation_type": "Расходы"}))

    assert queued == []


def test_completion_and_achievement_notifications_are_deduped_when_enabled(monkeypatch):
    from services import challenges

    queued = []
    seen = set()
    card = _prompt_for().card
    completed_card = ChallengeCard(card.definition, 2, 2, True, "2026-07-31", date(2026, 7, 31))

    monkeypatch.setattr(challenges, "challenge_notifications_enabled", lambda _uid: True)
    monkeypatch.setattr(challenges, "grant_achievement", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(challenges, "upsert_assignments", lambda *_args, **_kwargs: [completed_card])
    def _queue(**kwargs):
        if kwargs["dedupe_key"] in seen:
            return SimpleNamespace(status="duplicate")
        seen.add(kwargs["dedupe_key"])
        queued.append(kwargs)
        return SimpleNamespace(status="queued")

    monkeypatch.setattr("services.automatic_notifications.queue_automatic_notification", _queue)

    challenges.process_product_event(ProductEvent(event_name="operation_created", user_id=55, properties={"operation_type": "Расходы"}))
    challenges.process_product_event(ProductEvent(event_name="operation_created", user_id=55, properties={"operation_type": "Расходы"}))

    dedupe_keys = [item["dedupe_key"] for item in queued]
    assert "achievement:first_step:achievement_granted" in dedupe_keys
    assert "challenge:daily_two_operations:2026-07-31:challenge_completed" in dedupe_keys
    assert len(set(dedupe_keys)) == 2


def test_rollout_migration_defaults_false_and_suppresses_only_unsent_challenge_rows():
    sql = open("migrations/20260801_013_challenge_notifications_opt_in.sql", encoding="utf-8").read()

    assert "ALTER COLUMN challenge_notifications_enabled SET DEFAULT FALSE" in sql
    assert "challenge_notifications_enabled = FALSE" in sql
    assert "challenge_notifications_default_off_rollout" in sql
    assert "notification_type IN ('challenge_prompt', 'challenge_completed', 'achievement_granted')" in sql
    assert "status IN ('pending', 'claimed')" in sql
    assert "user_challenge_assignments" not in sql
    assert "user_achievement_grants" not in sql
