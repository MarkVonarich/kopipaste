from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace


class _Message:
    def __init__(self, chat_id=55):
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.replies))


class _CallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = _Message()
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=query.message.chat,
        effective_user=SimpleNamespace(id=query.message.chat.id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_user_timezone_precedence_and_legacy_mapping(monkeypatch):
    from services import user_time

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [("Asia/Irkutsk", "Europe/Moscow", "Europe/Kaliningrad", 180)])
    resolved = user_time.resolve_user_timezone(55)
    assert resolved.timezone_name == "Asia/Irkutsk"
    assert resolved.source == "notification_preferences.timezone"

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [("Not/AZone", "Asia/Omsk", "Europe/Moscow", 180)])
    assert user_time.resolve_user_timezone(55).timezone_name == "Asia/Omsk"

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [(None, None, None, 600)])
    assert user_time.resolve_user_timezone(55).timezone_name == "Asia/Vladivostok"

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [])
    assert user_time.resolve_user_timezone(55).timezone_name == "Europe/Moscow"


def test_user_local_date_is_independent_from_server_timezone(monkeypatch):
    from services import user_time

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [("Asia/Vladivostok", None, None, 180)])
    now_utc = datetime(2026, 8, 3, 15, 30, tzinfo=timezone.utc)
    assert user_time.user_local_date(55, now_utc=now_utc) == date(2026, 8, 4)


def test_local_datetime_to_utc_respects_dst_and_moscow_no_dst(monkeypatch):
    from services import user_time

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [("Europe/Stockholm", None, None, 180)])
    winter = user_time.local_datetime_to_utc(55, datetime(2026, 1, 15, 9, 0))
    summer = user_time.local_datetime_to_utc(55, datetime(2026, 7, 15, 9, 0))
    assert winter.hour == 8
    assert summer.hour == 7

    monkeypatch.setattr(user_time, "pg_fetchall", lambda *_args, **_kwargs: [("Europe/Moscow", None, None, 180)])
    assert user_time.local_datetime_to_utc(55, datetime(2026, 1, 15, 9, 0)).hour == 6
    assert user_time.local_datetime_to_utc(55, datetime(2026, 7, 15, 9, 0)).hour == 6


def test_quiet_hours_cross_midnight_uses_canonical_window():
    from services.user_time import is_local_time_in_window

    assert is_local_time_in_window(datetime(2026, 8, 3, 23, 0), time(22, 30), time(8, 0))
    assert is_local_time_in_window(datetime(2026, 8, 4, 7, 59), time(22, 30), time(8, 0))
    assert not is_local_time_in_window(datetime(2026, 8, 4, 8, 0), time(22, 30), time(8, 0))


def test_evening_reminder_uses_exact_configured_hour(monkeypatch):
    from jobs import daily

    monkeypatch.setattr(daily, "user_local_now", lambda _uid: datetime(2026, 8, 3, 14, 10, tzinfo=timezone.utc))
    assert daily._local_now(55).hour == 14
    assert daily._local_now(55).hour != 20


def test_timezone_change_suppresses_stale_pending_notifications(monkeypatch):
    from services import automatic_notifications as mod

    executed = {}

    class _Cursor:
        rowcount = 3

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            executed["committed"] = True

        def rollback(self):
            executed["rolled_back"] = True

        def close(self):
            executed["closed"] = True

    monkeypatch.setattr(mod, "get_conn", lambda: _Conn())
    assert mod.suppress_stale_timezone_sensitive_notifications(55) == 3
    assert executed["params"] == ("timezone_changed_stale_notification", 55)
    assert "status IN ('pending','claimed')" in executed["sql"]


def test_notification_timezone_screen_and_set_callback(monkeypatch):
    from routers import callbacks
    from services.user_time import ResolvedUserTimezone

    saved = {}
    monkeypatch.setattr(callbacks, "resolve_user_timezone", lambda _cid: ResolvedUserTimezone("Europe/Moscow", "test"))
    monkeypatch.setattr(callbacks, "set_notification_timezone", lambda _cid, tz_name: saved.setdefault("tz", tz_name) or {"timezone": tz_name})
    monkeypatch.setattr(callbacks, "suppress_stale_timezone_sensitive_notifications", lambda _cid: 2)
    monkeypatch.setattr(callbacks, "track_product_event", lambda _ev: None)

    context = SimpleNamespace(user_data={})
    query = _CallbackQuery("notif_tz")
    asyncio.run(callbacks.callback_handler(_update(query), context))
    displayed = _callbacks(query.edits[-1][1]["reply_markup"])
    assert "tz|set|Europe/Moscow" in displayed
    assert "tz|manual" in displayed
    assert "menu_notifications" in displayed

    set_query = _CallbackQuery("tz|set|Asia/Omsk")
    asyncio.run(callbacks.callback_handler(_update(set_query), context))
    assert saved["tz"] == "Asia/Omsk"
    assert set_query.answers[-1][0] == "Часовой пояс сохранён"


def test_limit_alert_copy_bands_buttons_and_safe_analytics():
    from services.limit_alerts import (
        EXCEEDED_BAND,
        build_category_limit_dedupe_key,
        category_limit_alert_markup,
        render_category_limit_alert,
        safe_limit_threshold_event_properties,
        threshold_band,
    )

    assert threshold_band(Decimal("790"), Decimal("1000")) is None
    assert threshold_band(Decimal("800"), Decimal("1000")) == 80
    assert threshold_band(Decimal("900"), Decimal("1000")) == 90
    assert threshold_band(Decimal("1000"), Decimal("1000")) == 100
    assert threshold_band(Decimal("1001"), Decimal("1000")) == EXCEEDED_BAND

    alert = render_category_limit_alert(category="<Food_&_Cafe>", period="month", spent=Decimal("901"), limit=Decimal("1000"), currency="RUB")
    assert alert is not None
    assert alert.status == "approaching"
    assert "ЛИМИТ_ПО_СТРОКА" not in alert.text
    assert "&lt;Food" in alert.text
    assert "901 RUB" in alert.text

    callbacks = [button.callback_data for row in category_limit_alert_markup().inline_keyboard for button in row]
    assert callbacks == ["lim_list", "lim_list", "menu_notifications"]

    key_a = build_category_limit_dedupe_key(user_id=55, workspace_id=7, period="month", period_start=date(2026, 8, 1), category_key="food", band=90)
    key_b = build_category_limit_dedupe_key(user_id=55, workspace_id=7, period="month", period_start=date(2026, 8, 1), category_key="food", band=90)
    assert key_a == key_b
    assert "2026-08-01" in key_a

    props = safe_limit_threshold_event_properties(band=90, period="month", status="approaching", currency="RUB", source="operation_commit")
    assert props == {"threshold_band": 90, "period": "month", "status": "approaching", "currency": "RUB", "source": "operation_commit"}


def test_category_limit_warning_uses_dispatcher_and_safe_event(monkeypatch):
    from services import records

    queries = []
    execs = []
    dispatched = []
    events = []

    def _fetch(sql, params=()):
        queries.append((sql, params))
        if "FROM public.category_limits" in sql:
            return [(Decimal("1000"),)]
        if "SUM(amount)" in sql:
            return [(Decimal("1000"),)]
        if "FROM public.category_limit_state" in sql:
            return [(90, date(2026, 8, 1))]
        return []

    async def _dispatch(_context, **kwargs):
        dispatched.append(kwargs)
        return SimpleNamespace(status="sent")

    monkeypatch.setattr(records, "pg_fetchall", _fetch)
    monkeypatch.setattr(records, "pg_exec", lambda sql, params=(): execs.append((sql, params)))
    monkeypatch.setattr(records, "get_user_currency", lambda _uid: "RUB")
    monkeypatch.setattr(records, "user_local_date", lambda _uid: date(2026, 8, 3))
    monkeypatch.setattr(records, "dispatch_automatic_notification", _dispatch)
    monkeypatch.setattr(records, "track_product_event", lambda ev: events.append(ev))

    asyncio.run(records._check_category_limits_and_warn(55, "Продукты", datetime(2026, 8, 3), SimpleNamespace(), workspace_id=7))
    assert len(dispatched) == 2
    assert all(item["notification_type"] == "category_limit_warning" for item in dispatched)
    assert all("category_limit:55:7:" in item["dedupe_key"] for item in dispatched)
    assert all(item["parse_mode"] == "HTML" for item in dispatched)
    assert events
    assert all("category" not in ev.properties for ev in events)
    assert all("amount" not in ev.properties for ev in events)
