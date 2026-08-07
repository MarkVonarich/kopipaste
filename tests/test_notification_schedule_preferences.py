from datetime import datetime, time

from services.automatic_notifications import suppress_pending_preference_notifications


def test_morning_disabled_is_not_due(monkeypatch):
    from jobs.daily import is_notification_due

    prefs = {"morning_enabled": False, "morning_time": "09:00", "quiet_hours_enabled": False}

    assert not is_notification_due(42, "morning", datetime(2026, 8, 7, 9, 0), prefs)


def test_evening_disabled_is_not_due(monkeypatch):
    from jobs.daily import is_notification_due

    prefs = {"evening_enabled": False, "evening_time": "20:00", "quiet_hours_enabled": False}

    assert not is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 0), prefs)


def test_evening_pref_time_beats_legacy_reminder_hour(monkeypatch):
    from jobs import daily

    prefs = {"evening_enabled": True, "evening_time": "20:00", "quiet_hours_enabled": False}
    monkeypatch.setattr(daily, "pg_fetchall", lambda *_args, **_kwargs: [("20:00",)])
    monkeypatch.setattr(daily, "_user_tz_and_hour", lambda _user_id: (0, 11))

    assert not daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 11, 0), prefs)
    assert daily.is_notification_due(42, "evening", datetime(2026, 8, 7, 20, 0), prefs)


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
    monkeypatch.setattr(mod, "_automatic_type_preference_enabled", lambda _user_id, _type: False)
    monkeypatch.setattr(mod, "mark_notification_skipped", lambda notification_id, reason: skipped.append((notification_id, reason)))

    import asyncio
    from types import SimpleNamespace

    counts = asyncio.run(mod.process_due_notifications(SimpleNamespace(bot=SimpleNamespace(send_message=None))))

    assert counts["skipped"] == 1
    assert skipped == [(7, "preference_disabled")]


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
