from datetime import date, datetime, timezone

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from services.workspaces import WorkspaceContext
from services.automatic_notifications import _vacation_delivery_skip_reason, _vacation_pauses_notification
from services.category_preferences import CategoryPreference, apply_category_preferences, apply_suggestion_preferences, migrate_category_preferences_cur
from services.notification_preferences import set_vacation_mode, vacation_mode_state
from services.personal_data_deletion import DeletionResult, HistoryDeletionPreview


def test_vacation_defaults_disabled():
    state = vacation_mode_state(enabled=False, start_date=None, end_date=None, today=date(2026, 8, 13))
    assert state == {"enabled": False, "active": False, "status": "disabled", "start_date": None, "end_date": None}


@pytest.mark.parametrize(
    ("today", "status", "active"),
    [
        (date(2026, 8, 12), "scheduled", False),
        (date(2026, 8, 13), "active", True),
        (date(2026, 8, 20), "active", True),
        (date(2026, 8, 21), "completed", False),
    ],
)
def test_vacation_state_is_inclusive_and_local_date_driven(today, status, active):
    state = vacation_mode_state(enabled=True, start_date=date(2026, 8, 13), end_date=date(2026, 8, 20), today=today)
    assert state["status"] == status
    assert state["active"] is active


def test_vacation_pauses_proactive_but_not_explicit_reminders():
    assert _vacation_pauses_notification("evening_reminder") is True
    assert _vacation_pauses_notification("weekly_report") is True
    assert _vacation_pauses_notification("goal_planned_contribution") is True
    assert _vacation_pauses_notification("user_reminder") is False


def test_completed_vacation_does_not_release_stale_backlog(monkeypatch):
    monkeypatch.setattr("services.notification_preferences.get_vacation_mode", lambda _user_id: {"enabled": True, "active": False, "status": "completed", "start_date": "2026-08-10", "end_date": "2026-08-12"})
    monkeypatch.setattr("services.automatic_notifications.resolve_user_timezone", lambda _user_id: type("Timezone", (), {"timezone_name": "UTC"})())
    row = {"user_id": 42, "notification_type": "weekly_report", "original_scheduled_at": datetime(2026, 8, 11, 12, tzinfo=timezone.utc)}
    assert _vacation_delivery_skip_reason(row) == "vacation_mode_stale"
    assert _vacation_delivery_skip_reason({**row, "notification_type": "user_reminder"}) is None


def test_invalid_vacation_range_is_rejected_before_persistence():
    with pytest.raises(ValueError, match="invalid_vacation_range"):
        set_vacation_mode(42, enabled=True, start_date="2026-08-20", end_date="2026-08-13")


@pytest.mark.parametrize(("start", "expects_suppression"), [("2026-08-13", True), ("2026-08-14", False)])
def test_vacation_suppresses_pending_only_when_already_active(monkeypatch, start, expects_suppression):
    class Cursor:
        def __init__(self):
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, _params):
            self.statements.append(" ".join(sql.split()))

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()

        def cursor(self):
            return self.cursor_value

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    connection = Connection()
    monkeypatch.setattr("services.notification_preferences.get_conn", lambda: connection)
    monkeypatch.setattr("services.notification_preferences.user_local_date", lambda _user_id: date(2026, 8, 13))
    monkeypatch.setattr("services.notification_preferences.get_vacation_mode", lambda _user_id, today=None: {"enabled": True, "active": expects_suppression, "status": "active" if expects_suppression else "scheduled"})

    set_vacation_mode(42, enabled=True, start_date=start, end_date="2026-08-20")

    suppression_sql = [sql for sql in connection.cursor_value.statements if "UPDATE public.automatic_notifications" in sql]
    assert bool(suppression_sql) is expects_suppression
    if suppression_sql:
        assert "notification_type <> 'user_reminder'" in suppression_sql[0]


def test_category_preferences_filter_hidden_and_order_priority():
    items = [
        {"name": "Food", "normalized_name": "food"},
        {"name": "Taxi", "normalized_name": "taxi"},
        {"name": "Travel", "normalized_name": "travel"},
    ]
    preferences = {
        "taxi": CategoryPreference("taxi", "normal", False),
        "travel": CategoryPreference("travel", "high", True),
    }
    result = apply_category_preferences(items, preferences, include_irrelevant=False)
    assert [item["name"] for item in result] == ["Travel", "Food"]


def test_hidden_current_category_is_preserved_for_editing():
    items = [{"name": "Taxi", "normalized_name": "taxi"}, {"name": "Food", "normalized_name": "food"}]
    result = apply_category_preferences(
        items,
        {"taxi": CategoryPreference("taxi", "normal", False)},
        include_irrelevant=False,
        preserve_key="Taxi",
    )
    assert [item["name"] for item in result] == ["Taxi", "Food"]
    assert result[0]["relevant"] is False


def test_exact_source_order_remains_stronger_than_priority():
    suggestions = [{"cat": "Exact", "score": 0.92}, {"cat": "Preferred", "score": 0.08}]
    prefs = {"preferred": CategoryPreference("preferred", "high", True)}
    result = apply_suggestion_preferences(suggestions, prefs, preserve_source_order=True)
    assert [item["cat"] for item in result] == ["Exact", "Preferred"]


def test_priority_breaks_weak_candidate_order_only():
    suggestions = [{"cat": "Food", "score": 0.5}, {"cat": "Travel", "score": 0.5}]
    prefs = {"travel": CategoryPreference("travel", "high", True)}
    result = apply_suggestion_preferences(suggestions, prefs, preserve_source_order=False)
    assert [item["cat"] for item in result] == ["Travel", "Food"]


def test_category_preference_rename_is_collision_safe_and_user_scoped():
    class Cursor:
        def __init__(self):
            self.calls = []
            self.rowcount = 1

        def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))

    cur = Cursor()
    changed = migrate_category_preferences_cur(
        cur,
        user_id=42,
        workspace_id=None,
        operation_type="Расходы",
        source_key="old",
        destination_key="new",
        shared_workspace=False,
    )
    assert changed == 2
    assert "EXISTS" in cur.calls[0][0]
    assert "p.user_id=%s" in cur.calls[0][0]
    assert cur.calls[0][1] == (None, "Расходы", "old", 42, "new")


def test_shared_category_preference_rename_updates_identity_without_user_filter():
    class Cursor:
        rowcount = 0

        def __init__(self):
            self.sql = []

        def execute(self, sql, _params):
            self.sql.append(" ".join(sql.split()))

    cur = Cursor()
    migrate_category_preferences_cur(cur, user_id=42, workspace_id=9, operation_type="Расходы", source_key="old", destination_key=None, shared_workspace=True)
    assert "p.user_id=%s" not in cur.sql[0]
    assert "workspace_id IS NOT DISTINCT FROM %s" in cur.sql[0]


def test_history_preview_uses_user_local_date(monkeypatch):
    api = MiniAppAPI()
    api._check_write_rate = lambda _req: None
    captured = {}
    monkeypatch.setattr("miniapp.api.user_local_date", lambda _user_id: date(2026, 8, 13))
    monkeypatch.setattr("miniapp.api.preview_delete_financial_history", lambda user_id, start, end: captured.update({"user_id": user_id, "start": start, "end": end}) or HistoryDeletionPreview(user_id, start, end, 3, {"operations": 3, "financial_goals": 0}))

    data = api.preview_profile_history_deletion(api.request(42), {"period": "last7"})["data"]

    assert captured == {"user_id": 42, "start": date(2026, 8, 7), "end": date(2026, 8, 13)}
    assert data["summary"]["operations"] == 3


def test_unsupported_history_period_rejected(monkeypatch):
    api = MiniAppAPI()
    api._check_write_rate = lambda _req: None
    monkeypatch.setattr("miniapp.api.user_local_date", lambda _user_id: date(2026, 8, 13))
    with pytest.raises(MiniAppError) as exc:
        api.preview_profile_history_deletion(api.request(42), {"period": "forever"})
    assert exc.value.code == "unsupported_history_period"


def test_account_deletion_requires_typed_confirmation(monkeypatch):
    api = MiniAppAPI()
    api._check_write_rate = lambda _req: None
    monkeypatch.setattr("miniapp.api.apply_account_deletion", lambda _user_id, **_kwargs: pytest.fail("must not delete"))
    with pytest.raises(MiniAppError) as exc:
        api.delete_profile_account(api.request(42), {"confirmed": True, "confirmation_text": "удалить"})
    assert exc.value.code == "account_confirmation_required"


def test_account_deletion_uses_canonical_services_without_post_event(monkeypatch):
    api = MiniAppAPI()
    api._check_write_rate = lambda _req: None
    calls = []
    api._track = lambda *_args, **_kwargs: calls.append("event")
    monkeypatch.setattr("miniapp.api.apply_account_deletion", lambda user_id, **_kwargs: calls.append(("analytics", user_id)))
    monkeypatch.setattr("miniapp.api.delete_user_data", lambda user_id: calls.append(("data", user_id)) or DeletionResult(user_id, {}, deleted=True))

    data = api.delete_profile_account(api.request(42), {"confirmed": True, "confirmation_text": "УДАЛИТЬ"})["data"]

    assert data["terminal"] is True
    assert calls == [("analytics", 42), ("data", 42)]


def test_category_preference_api_uses_authenticated_user_and_exact_scope(monkeypatch):
    api = MiniAppAPI()
    api._check_write_rate = lambda _req: None
    api._write_scope = lambda _req, _workspace_id: WorkspaceContext(9, 42, 42, "shared", "member", "Team", True)
    api._category_by_token = lambda _req, _workspace_id, _op_type, _token: {
        "name": "Food", "normalized_name": "food", "token": "food", "priority": "high", "relevant": False,
    }
    captured = {}
    monkeypatch.setattr("miniapp.api.set_category_preference", lambda user_id, workspace_id, op_type, category, **values: captured.update({"user_id": user_id, "workspace_id": workspace_id, "op_type": op_type, "category": category, **values}) or CategoryPreference("food", values["priority"], values["relevant"]))
    api._track = lambda *_args, **_kwargs: None

    api.update_category_preference(api.request(42), "food", {"workspace_id": 9, "type": "expense", "priority": "high", "relevant": False})

    assert captured == {"user_id": 42, "workspace_id": 9, "op_type": "Расходы", "category": "food", "priority": "high", "relevant": False}
