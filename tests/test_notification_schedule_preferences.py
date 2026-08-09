import asyncio
from datetime import datetime, timezone, time
from types import SimpleNamespace

from services.automatic_notifications import suppress_pending_preference_notifications


def test_morning_disabled_is_not_due(monkeypatch):
    from jobs.daily import is_notification_due

    prefs = {"morning_enabled": False, "morning_time": "09:00", "quiet_hours_enabled": False}

    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 9, 0), prefs)


def test_morning_notifications_are_retired_even_when_legacy_pref_enabled():
    from jobs.daily import is_notification_due

    prefs = {"morning_enabled": True, "morning_time": "08:30", "quiet_hours_enabled": False}

    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 8, 5), prefs)
    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 8, 29), prefs)
    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 8, 30), prefs)
    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 8, 34), prefs)
    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 8, 35), prefs)


def test_evening_disabled_is_not_due(monkeypatch):
    from jobs.daily import is_notification_due

    prefs = {"evening_enabled": False, "evening_time": "20:00", "quiet_hours_enabled": False}

    assert not is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 0), prefs)


def test_evening_pref_time_beats_legacy_reminder_hour(monkeypatch):
    from jobs import daily

    prefs = {"evening_enabled": True, "evening_time": "20:30", "quiet_hours_enabled": False}
    monkeypatch.setattr(daily, "pg_fetchall", lambda *_args, **_kwargs: [("20:30",)])
    monkeypatch.setattr(daily, "_user_tz_and_hour", lambda _user_id: (0, 11))

    assert not daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 11, 0), prefs)
    assert not daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 0), prefs)
    assert daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 30), prefs)
    assert daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 34), prefs)
    assert not daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 35), prefs)


def test_due_window_handles_cross_hour_edge():
    from jobs.daily import notification_due_in_window

    configured = time(23, 58)

    assert notification_due_in_window(datetime(2026, 8, 7, 23, 59), configured)
    assert notification_due_in_window(datetime(2026, 8, 8, 0, 2), configured)
    assert not notification_due_in_window(datetime(2026, 8, 8, 0, 3), configured)


def test_evening_template_105_is_not_in_morning_pool():
    from jobs.daily import EVENING_TEMPLATES, MORNING_TEMPLATES

    assert any(template["id"] == 105 for template in EVENING_TEMPLATES)
    assert all(template["id"] != 105 for template in MORNING_TEMPLATES)


def test_pending_preference_notifications_are_suppressed(monkeypatch):
    from services import automatic_notifications as mod

    executed = {}

    class _Cursor:
        rowcount = 2

        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

    class _Conn:
        def cursor(self):
            return self

        def __enter__(self):
            return _Cursor()

        def __exit__(self, *_args):
            return False

        def commit(self):
            executed["committed"] = True

        def rollback(self):
            executed["rolled_back"] = True

        def close(self):
            executed["closed"] = True

    monkeypatch.setattr(mod, "get_conn", lambda: _Conn())

    changed = suppress_pending_preference_notifications(42, "evening")

    assert changed == 2
    assert executed["params"][0] == "preference_disabled"
    assert executed["params"][2] == ["evening_reminder"]


def test_deferred_delivery_rechecks_disabled_preference(monkeypatch):
    from services import automatic_notifications as mod

    skipped = []
    monkeypatch.setattr(mod, "release_stale_deferred_claims", lambda: 0)
    monkeypatch.setattr(mod, "claim_due_notifications", lambda limit=50: [{
        "id": 7,
        "user_id": 42,
        "workspace_id": None,
        "notification_type": "day_nudge",
        "dedupe_key": "day_nudge:42:2026-08-07",
        "template_key": "day_nudge",
        "payload": {"text": "hello"},
        "original_scheduled_at": None,
        "timezone_name": "Europe/Moscow",
        "attempts": 0,
    }])
    monkeypatch.setattr(
        "services.notification_preferences.get_notification_preferences",
        lambda _user_id: {"morning_enabled": False, "quiet_hours_enabled": False, "timezone": "Europe/Moscow"},
    )
    monkeypatch.setattr(mod, "mark_notification_skipped", lambda notification_id, reason: skipped.append((notification_id, reason)))

    counts = asyncio.run(mod.process_due_notifications(SimpleNamespace(bot=SimpleNamespace(send_message=None))))

    assert counts["skipped"] == 1
    assert skipped == [(7, "preference_disabled")]


def test_deferred_day_nudge_is_retired_even_with_legacy_enabled_preference(monkeypatch):
    from services import automatic_notifications as mod

    monkeypatch.setattr(
        "services.notification_preferences.get_notification_preferences",
        lambda _user_id: {"morning_enabled": True, "quiet_hours_enabled": False, "timezone": "Europe/Moscow"},
    )

    reason = mod._automatic_delivery_skip_reason({
        "id": 7,
        "user_id": 42,
        "notification_type": "day_nudge",
    })

    assert reason == "preference_disabled"


def test_deferred_evening_delivery_outside_new_time_window_is_detected(monkeypatch):
    from services import automatic_notifications as mod

    monkeypatch.setattr(mod, "resolve_user_timezone", lambda _user_id: SimpleNamespace(timezone_name="UTC"))
    monkeypatch.setattr(
        "services.notification_preferences.get_notification_preferences",
        lambda _user_id: {
            "evening_enabled": True,
            "evening_time": "20:30",
            "quiet_hours_enabled": False,
            "timezone": "UTC",
        },
    )
    monkeypatch.setattr("jobs.daily.pg_fetchall", lambda *_args, **_kwargs: [("20:30",)])

    reason = mod._automatic_delivery_skip_reason(
        {
        "id": 8,
        "user_id": 42,
        "workspace_id": None,
        "notification_type": "evening_reminder",
        "dedupe_key": "evening:42:2026-08-07",
        "template_key": "evening_reminder",
        "payload": {"text": "hello"},
        "original_scheduled_at": datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc),
        "timezone_name": "UTC",
        "attempts": 0,
        },
        now_utc=datetime(2026, 8, 7, 11, 1, tzinfo=timezone.utc),
    )

    assert reason == "outside_delivery_window"


def test_grouped_daily_toggle_forces_morning_off_and_sets_evening(monkeypatch):
    from services import notification_preferences as prefs

    executed = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            executed.append(("commit", ()))

        def rollback(self):
            executed.append(("rollback", ()))

        def close(self):
            executed.append(("close", ()))

    monkeypatch.setattr(prefs, "get_conn", lambda: _Conn())
    monkeypatch.setattr(prefs, "grouped_notification_preferences", lambda _user_id: {"daily_notifications": {"enabled": False}})
    monkeypatch.setattr("services.automatic_notifications.suppress_pending_preference_notifications", lambda *_args, **_kwargs: 0)

    result = prefs.set_grouped_notification_preference(42, "daily", False)

    assert result["daily_notifications"]["enabled"] is False
    assert "morning_enabled" in executed[0][0]
    assert "evening_enabled" in executed[0][0]
    assert executed[0][1] == (42, False, False)


def test_grouped_plans_toggle_updates_hidden_financial_control_fields(monkeypatch):
    from services import notification_preferences as prefs

    executed = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))

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
    monkeypatch.setattr(prefs, "grouped_notification_preferences", lambda _user_id: {"plans_control": {"enabled": False}})

    result = prefs.set_grouped_notification_preference(42, "plans", False)

    assert result["plans_control"]["enabled"] is False
    sql, params = executed[0]
    assert "limit_alerts_enabled" in sql
    assert "budget_alerts_enabled" in sql
    assert "goal_notifications_enabled" in sql
    assert "subscription_alerts_enabled" in sql
    assert "recurring_spend_alerts_enabled" in sql
    assert params == (42, False, False, False, False, False, False, False, False, False, False)


def _notification_row(notification_type: str) -> dict:
    return {
        "id": 99,
        "user_id": 42,
        "workspace_id": None,
        "notification_type": notification_type,
        "dedupe_key": f"{notification_type}:42",
        "template_key": notification_type,
        "payload": {"text": "hello"},
        "original_scheduled_at": datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc),
        "timezone_name": "UTC",
        "attempts": 0,
    }


def test_grouped_delivery_mapping_skips_disabled_groups(monkeypatch):
    from services import automatic_notifications as mod

    prefs = {
        "morning_enabled": False,
        "evening_enabled": False,
        "limit_alerts_enabled": False,
        "budget_alerts_enabled": False,
        "goal_notifications_enabled": False,
        "subscription_alerts_enabled": False,
        "recurring_spend_alerts_enabled": False,
        "weekly_reports_enabled": False,
        "monthly_reports_enabled": False,
        "quiet_hours_enabled": False,
        "timezone": "UTC",
    }
    monkeypatch.setattr("services.notification_preferences.get_notification_preferences", lambda _user_id: prefs)

    for notification_type in ["day_nudge", "evening_reminder", "category_limit_warning", "budget_near", "goal_planned_contribution", "subscription_upcoming", "recurring_spend_detected", "weekly_report", "monthly_report", "challenge_prompt"]:
        assert mod._automatic_delivery_skip_reason(_notification_row(notification_type), now_utc=datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)) == "preference_disabled"


def test_grouped_plans_on_allows_financial_control_notifications(monkeypatch):
    from services import automatic_notifications as mod

    prefs = {
        "limit_alerts_enabled": True,
        "budget_alerts_enabled": True,
        "goal_notifications_enabled": True,
        "subscription_alerts_enabled": True,
        "recurring_spend_alerts_enabled": True,
        "quiet_hours_enabled": False,
        "timezone": "UTC",
    }
    monkeypatch.setattr("services.notification_preferences.get_notification_preferences", lambda _user_id: prefs)

    for notification_type in ["category_limit_warning", "budget_near", "goal_planned_contribution", "subscription_upcoming", "recurring_spend_detected"]:
        assert mod._automatic_delivery_skip_reason(_notification_row(notification_type)) is None


def test_grouped_plans_delivery_respects_each_legacy_subtype_field(monkeypatch):
    from services import automatic_notifications as mod

    prefs = {
        "limit_alerts_enabled": False,
        "budget_alerts_enabled": True,
        "goal_notifications_enabled": False,
        "subscription_alerts_enabled": True,
        "recurring_spend_alerts_enabled": False,
        "quiet_hours_enabled": False,
        "timezone": "UTC",
    }
    monkeypatch.setattr("services.notification_preferences.get_notification_preferences", lambda _user_id: prefs)

    assert mod._automatic_delivery_skip_reason(_notification_row("category_limit_warning")) == "preference_disabled"
    assert mod._automatic_delivery_skip_reason(_notification_row("limit_near")) == "preference_disabled"
    assert mod._automatic_delivery_skip_reason(_notification_row("budget_near")) is None
    assert mod._automatic_delivery_skip_reason(_notification_row("goal_planned_contribution")) == "preference_disabled"
    assert mod._automatic_delivery_skip_reason(_notification_row("subscription_upcoming")) is None
    assert mod._automatic_delivery_skip_reason(_notification_row("recurring_spend_detected")) == "preference_disabled"


def test_queued_delivery_skips_disabled_legacy_plan_subtype(monkeypatch):
    from services import automatic_notifications as mod

    skipped = []
    row = _notification_row("category_limit_warning")
    monkeypatch.setattr(mod, "release_stale_deferred_claims", lambda: 0)
    monkeypatch.setattr(mod, "claim_due_notifications", lambda limit=50: [row])
    monkeypatch.setattr(
        "services.notification_preferences.get_notification_preferences",
        lambda _user_id: {
            "limit_alerts_enabled": False,
            "budget_alerts_enabled": True,
            "goal_notifications_enabled": False,
            "subscription_alerts_enabled": True,
            "recurring_spend_alerts_enabled": False,
            "quiet_hours_enabled": False,
            "timezone": "UTC",
        },
    )
    monkeypatch.setattr(mod, "mark_notification_skipped", lambda notification_id, reason: skipped.append((notification_id, reason)))

    class _Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("disabled subtype must not be sent")

    counts = asyncio.run(mod.process_due_notifications(SimpleNamespace(bot=_Bot())))

    assert counts["skipped"] == 1
    assert skipped == [(99, "preference_disabled")]


def test_user_reminder_is_independent_of_grouped_preferences(monkeypatch):
    from services import automatic_notifications as mod

    sent = []
    row = _notification_row("user_reminder")
    row["template_key"] = "generic"
    monkeypatch.setattr(mod, "release_stale_deferred_claims", lambda: 0)
    monkeypatch.setattr(mod, "claim_due_notifications", lambda limit=50: [row])
    monkeypatch.setattr(mod, "mark_notification_sent", lambda notification_id: sent.append(notification_id))
    monkeypatch.setattr(mod, "track_product_event", lambda _ev: None)

    class _Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    counts = asyncio.run(mod.process_due_notifications(SimpleNamespace(bot=_Bot())))

    assert counts["sent"] == 1
    assert sent == ["hello", 99]


def test_delivery_validation_exception_fails_closed(monkeypatch):
    from services import automatic_notifications as mod

    skipped = []
    monkeypatch.setattr(mod, "release_stale_deferred_claims", lambda: 0)
    monkeypatch.setattr(mod, "claim_due_notifications", lambda limit=50: [_notification_row("evening_reminder")])
    monkeypatch.setattr("services.notification_preferences.get_notification_preferences", lambda _user_id: (_ for _ in ()).throw(RuntimeError("temporary")))
    monkeypatch.setattr(mod, "mark_notification_skipped", lambda notification_id, reason: skipped.append((notification_id, reason)))

    class _Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("notification must not be sent")

    counts = asyncio.run(mod.process_due_notifications(SimpleNamespace(bot=_Bot())))

    assert counts["skipped"] == 1
    assert skipped == [(99, "delivery_validation_failed")]


def test_quiet_hours_still_block_due_window(monkeypatch):
    from jobs.daily import is_notification_due

    prefs = {
        "morning_enabled": True,
        "morning_time": "09:00",
        "quiet_hours_enabled": True,
        "quiet_hours_start": time(8, 0),
        "quiet_hours_end": time(10, 0),
    }

    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 9, 0), prefs)
