from __future__ import annotations

from datetime import time

from services.automatic_notifications import quiet_hours_window
from services.notification_engine import NotificationPreferences, is_quiet_time, preferences_from_dict


def _fmt(value):
    return value.strftime("%H:%M") if isinstance(value, time) else value


def test_set_quiet_hours_preserves_saved_times_when_disabled(monkeypatch):
    from services import notification_preferences as prefs

    state = {"enabled": False, "start": None, "end": None}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, params=()):
            _user_id, enabled, start, end, start_provided, end_provided = params
            state["enabled"] = bool(enabled)
            if start_provided:
                state["start"] = start
            elif enabled and state["start"] is None:
                state["start"] = time(22, 30)
            if end_provided:
                state["end"] = end
            elif enabled and state["end"] is None:
                state["end"] = time(8, 0)

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
    monkeypatch.setattr(prefs, "get_notification_preferences", lambda _uid: {
        "quiet_hours_enabled": state["enabled"],
        "quiet_hours_start": _fmt(state["start"]),
        "quiet_hours_end": _fmt(state["end"]),
    })

    assert prefs.set_quiet_hours(55, enabled=True, start="23:00", end="09:00") == {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "09:00",
    }
    assert prefs.set_quiet_hours(55, enabled=False) == {
        "quiet_hours_enabled": False,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "09:00",
    }
    assert prefs.set_quiet_hours(55, enabled=True) == {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "09:00",
    }


def test_set_quiet_hours_rolls_back_on_error(monkeypatch):
    from services import notification_preferences as prefs

    calls = {"rollback": 0}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, _params=()):
            raise RuntimeError("db_failed")

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            raise AssertionError("commit should not run")

        def rollback(self):
            calls["rollback"] += 1

        def close(self):
            pass

    monkeypatch.setattr(prefs, "get_conn", lambda: _Conn())

    try:
        prefs.set_quiet_hours(55, enabled=True, start="23:00", end="09:00")
    except RuntimeError:
        pass

    assert calls["rollback"] == 1


def test_quiet_hours_window_respects_enabled_flag(monkeypatch):
    from services import automatic_notifications as mod

    monkeypatch.setattr(mod, "resolve_user_timezone", lambda _uid: type("Tz", (), {"timezone_name": "Europe/Moscow", "fallback_reason": None})())
    monkeypatch.setattr(mod, "pg_fetchall", lambda *_args, **_kwargs: [(False, "23:00", "09:00")])

    window = quiet_hours_window(55)

    assert window.enabled is False
    assert window.start is None
    assert window.end is None


def test_notification_engine_enabled_flag_and_cross_midnight():
    disabled = preferences_from_dict({"quiet_hours_enabled": False, "quiet_hours_start": "23:00", "quiet_hours_end": "09:00"})
    enabled = NotificationPreferences(quiet_hours_enabled=True, quiet_hours_start=time(23, 0), quiet_hours_end=time(9, 0))

    assert not is_quiet_time(__import__("datetime").datetime(2026, 8, 6, 23, 30), disabled)
    assert is_quiet_time(__import__("datetime").datetime(2026, 8, 6, 23, 30), enabled)


def test_quiet_hours_migrations_are_separate():
    migration_019 = open("/root/bot_finuchet/migrations/20260806_019_user_preferred_name.sql", encoding="utf-8").read()
    migration_020 = open("/root/bot_finuchet/migrations/20260806_020_quiet_hours_enabled.sql", encoding="utf-8").read()

    assert "preferred_name" in migration_019
    assert "quiet_hours_enabled" not in migration_019
    assert "ADD COLUMN IF NOT EXISTS quiet_hours_enabled" in migration_020
    assert "quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL" in migration_020
