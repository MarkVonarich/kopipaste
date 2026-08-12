from datetime import date, datetime, timezone

import pytest

from miniapp.api import MiniAppAPI, MiniAppError
from miniapp.api import TransactionFilters
from services.announcements import Announcement, resolve_announcement_candidates, resolve_announcements
from services.home_preferences import HOME_WIDGET_KEYS, home_widget_registry, reconcile_home_preferences, validate_home_preferences
from services.shopping import ShoppingItem, ShoppingSummary, normalize_item_text, shopping_summary


def test_home_widget_registry_is_canonical_and_reconciliation_is_forward_compatible():
    assert tuple(item["key"] for item in home_widget_registry()) == HOME_WIDGET_KEYS
    prefs = reconcile_home_preferences(
        ["activity", "retired_widget", "activity", "financial_result"],
        ["activity", "retired_widget"],
    )
    assert prefs["order"][:2] == ["activity", "financial_result"]
    assert "retired_widget" not in prefs["order"]
    assert prefs["order"][-1] == "recent_operations"
    assert prefs["enabled"][0] == "activity"
    assert "whats_new" in prefs["enabled"]


def test_home_preferences_allow_all_widgets_disabled():
    prefs = reconcile_home_preferences(list(HOME_WIDGET_KEYS), [])
    assert prefs["enabled"] == []
    assert prefs["order"] == list(HOME_WIDGET_KEYS)


def test_no_saved_home_preferences_uses_registry_defaults(monkeypatch):
    from services.home_preferences import get_home_preferences

    monkeypatch.setattr("services.home_preferences.pg_fetchall", lambda *_args: [])
    prefs = get_home_preferences(42)
    assert prefs["order"] == list(HOME_WIDGET_KEYS)
    assert prefs["enabled"] == list(HOME_WIDGET_KEYS)


def test_home_preference_mutation_rejects_unknown_duplicates_and_partial_order():
    with pytest.raises(ValueError):
        validate_home_preferences([*HOME_WIDGET_KEYS, HOME_WIDGET_KEYS[0]], list(HOME_WIDGET_KEYS))
    with pytest.raises(ValueError):
        validate_home_preferences([*HOME_WIDGET_KEYS[:-1], "unknown"], list(HOME_WIDGET_KEYS))
    with pytest.raises(ValueError):
        validate_home_preferences(list(HOME_WIDGET_KEYS[:-1]), list(HOME_WIDGET_KEYS[:-1]))


def test_home_preferences_save_and_reload_preserves_order(monkeypatch):
    from services.home_preferences import get_home_preferences, save_home_preferences

    stored = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql, params):
            stored["order"] = params[1].adapted
            stored["enabled"] = params[2].adapted

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    order = list(reversed(HOME_WIDGET_KEYS))
    enabled = ["goals", "reminders"]
    monkeypatch.setattr("services.home_preferences.get_conn", lambda: Connection())
    save_home_preferences(42, order, enabled)
    monkeypatch.setattr("services.home_preferences.pg_fetchall", lambda *_args: [(stored["order"], stored["enabled"])])
    assert get_home_preferences(42) == {"order": order, "enabled": [key for key in order if key in enabled]}


def test_announcement_resolver_applies_ttl_family_and_dismissal(monkeypatch):
    monkeypatch.setattr("services.announcements.pg_fetchall", lambda *_args: [("shopping-list-v1",)])
    visible = resolve_announcements(42, today=date(2026, 8, 11))
    assert [item["id"] for item in visible] == ["custom-home-v1", "plans-v2"]
    assert all(item["action"]["type"].startswith("OPEN_") for item in visible)
    assert resolve_announcements(42, today=date(2026, 9, 1)) == []


def test_announcement_ttl_boundary_family_dedupe_and_top_five(monkeypatch):
    candidates = tuple(
        Announcement(f"item-{index}", f"family-{index}", "feature", date(2026, 8, index + 1), f"T{index}", "S", "OPEN_DETAIL", "Открыть")
        for index in range(6)
    ) + (
        Announcement("item-5-new", "family-5", "fix", date(2026, 8, 10), "Newest family", "S", "OPEN_DETAIL", "Открыть"),
    )
    monkeypatch.setattr("services.announcements.ANNOUNCEMENTS", candidates)
    monkeypatch.setattr("services.announcements.pg_fetchall", lambda *_args: [])
    visible = resolve_announcements(42, today=date(2026, 8, 11))
    assert len(visible) == 5
    assert visible[0]["id"] == "item-5-new"
    assert "item-5" not in {item["id"] for item in visible}
    boundary = (Announcement("boundary", "boundary", "fix", date(2026, 7, 20), "Boundary", "S", "OPEN_DETAIL", "Открыть"),)
    monkeypatch.setattr("services.announcements.ANNOUNCEMENTS", boundary)
    assert [item["id"] for item in resolve_announcements(42, today=date(2026, 8, 9))] == ["boundary"]
    assert resolve_announcements(42, today=date(2026, 8, 10)) == []


def test_announcement_dismissal_is_user_scoped_on_reload(monkeypatch):
    def _rows(_sql, params):
        return [("custom-home-v1",)] if params == (1,) else []

    monkeypatch.setattr("services.announcements.pg_fetchall", _rows)
    assert "custom-home-v1" not in {item["id"] for item in resolve_announcements(1, today=date(2026, 8, 11))}
    assert "custom-home-v1" in {item["id"] for item in resolve_announcements(2, today=date(2026, 8, 11))}


def test_announcement_dismiss_event_includes_safe_candidate_metadata(monkeypatch):
    api = MiniAppAPI()
    candidate = Announcement("fix-v1", "fix", "fix", date(2026, 8, 11), "Fix", "Summary", "OPEN_DETAIL", "Details", "Исправили.")
    events = []
    monkeypatch.setattr(api, "_check_write_rate", lambda *_args: None)
    monkeypatch.setattr(api, "_track", lambda _req, name, **kwargs: events.append((name, kwargs)))
    monkeypatch.setattr("miniapp.api.announcement_candidate", lambda *_args: candidate)
    monkeypatch.setattr("miniapp.api.dismiss_announcement", lambda *_args: True)

    api.dismiss_announcement(api.request(42), candidate.id)

    assert events == [("mini_app_announcement_dismissed", {"properties": {"result": "success", "source": "mini_app", "update_key": "fix-v1", "update_kind": "fix"}})]


def test_open_detail_candidate_serializes_safe_plain_detail():
    candidate = Announcement(
        "fix-v1",
        "fix",
        "fix",
        date(2026, 8, 11),
        "Исправление",
        "Короткое описание",
        "OPEN_DETAIL",
        "Подробнее",
        "Исправили отображение списка.",
    )

    visible = resolve_announcement_candidates((candidate,), set(), today=date(2026, 8, 11))

    assert visible[0]["kind"] == "fix"
    assert visible[0]["detail"] == "Исправили отображение списка."
    assert visible[0]["action"]["type"] == "OPEN_DETAIL"


def test_shopping_text_is_normalized_and_bounded():
    assert normalize_item_text("  Молоко   и хлеб ") == "Молоко и хлеб"
    with pytest.raises(ValueError):
        normalize_item_text(" ")
    with pytest.raises(ValueError):
        normalize_item_text("x" * 201)
    with pytest.raises(ValueError):
        normalize_item_text("Молоко\nХлеб")


def test_shopping_write_uses_workspace_role_and_never_tracks_item_text(monkeypatch):
    api = MiniAppAPI()
    req = api.request(42)
    events = []
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    monkeypatch.setattr(api, "_write_scope", lambda *_args: type("Ctx", (), {"workspace_id": 10})())
    monkeypatch.setattr(api, "_track", lambda _req, name, **kwargs: events.append((name, kwargs)))
    item = ShoppingItem(7, 10, "Секретный товар", None, datetime.now(timezone.utc), datetime.now(timezone.utc))
    monkeypatch.setattr("miniapp.api.create_shopping_item", lambda *_args: item)

    result = api.create_shopping_item(req, {"workspace_id": 10, "text": item.text})["data"]

    assert result["item"]["id"] == 7
    assert events[0][0] == "mini_app_shopping_item_created"
    assert item.text not in repr(events)


def test_viewer_cannot_write_shopping_item(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_check_write_rate", lambda _req: None)
    monkeypatch.setattr(
        api,
        "_write_scope",
        lambda *_args: (_ for _ in ()).throw(MiniAppError(403, "workspace_read_only", "read only")),
    )
    with pytest.raises(MiniAppError) as exc:
        api.create_shopping_item(api.request(42), {"workspace_id": 10, "text": "Milk"})
    assert exc.value.code == "workspace_read_only"


def test_shopping_reads_are_bounded_and_workspace_isolated(monkeypatch):
    from services.shopping import list_shopping_items

    calls = []
    monkeypatch.setattr("services.shopping.pg_fetchall", lambda sql, params: calls.append((sql, params)) or [])
    assert list_shopping_items(10, limit=500) == []
    assert list_shopping_items(20, limit=5) == []
    assert calls[0][1] == (10, 200)
    assert calls[1][1] == (20, 5)
    assert "WHERE workspace_id=%s" in calls[0][0]


def test_shopping_summary_counts_full_list_and_keeps_preview_bounded(monkeypatch):
    now = datetime.now(timezone.utc)
    preview_rows = [(item_id, 10, f"Item {item_id}", None, now, now) for item_id in range(1, 6)]
    calls = []

    def _fetch(sql, params):
        calls.append((sql, params))
        return [(12, 4)] if "COUNT(*) FILTER" in sql else preview_rows

    monkeypatch.setattr("services.shopping.pg_fetchall", _fetch)

    summary = shopping_summary(10, preview_limit=5)

    assert summary.active_count == 12
    assert summary.completed_count == 4
    assert len(summary.items) == 5
    assert calls[0][1] == (10,)
    assert calls[1][1] == (10, 5)
    assert all("period" not in sql.lower() and "category" not in sql.lower() and "currency" not in sql.lower() for sql, _params in calls)


def test_home_shopping_uses_full_counts_and_ignores_financial_filters(monkeypatch):
    api = MiniAppAPI()
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 11), "current_month", "expense", "Food", "workspace_id=%s", (10,))
    now = datetime.now(timezone.utc)
    items = [ShoppingItem(item_id, 10, f"Item {item_id}", None, now, now) for item_id in range(1, 6)]
    calls = []
    monkeypatch.setattr(api, "_transaction_filters", lambda *_args: tx)
    monkeypatch.setattr(api, "operations", lambda *_args: {"data": {"items": []}})
    monkeypatch.setattr(api, "_home_challenges", lambda *_args: [])
    monkeypatch.setattr(api, "_home_focus_items", lambda *_args: [])
    monkeypatch.setattr(api, "_home_reminders", lambda *_args: [])
    monkeypatch.setattr(api, "_home_insights", lambda *_args: [])
    monkeypatch.setattr(api, "_workspace_rows", lambda *_args: [{"workspace_id": 10, "role": "member"}])
    monkeypatch.setattr("miniapp.api.pg_fetchall", lambda *_args: [])
    monkeypatch.setattr("miniapp.api.get_user_currency", lambda *_args: "RUB")
    monkeypatch.setattr("miniapp.api.get_home_preferences", lambda *_args: {"order": [], "enabled": []})
    monkeypatch.setattr("miniapp.api.resolve_announcements", lambda *_args: [])
    monkeypatch.setattr(
        "miniapp.api.shopping_summary",
        lambda workspace_id, **kwargs: calls.append((workspace_id, kwargs)) or ShoppingSummary(items, 12, 4),
    )

    data = api.overview(api.request(42), {"workspace_id": 10, "period": "current_month", "operation_type": "expense", "category": "Food"})["data"]

    assert data["shopping"]["active_count"] == 12
    assert data["shopping"]["completed_count"] == 4
    assert len(data["shopping"]["items"]) == 5
    assert calls == [(10, {"preview_limit": 5})]


def test_all_workspace_scope_never_merges_shopping_lists(monkeypatch):
    api = MiniAppAPI()
    monkeypatch.setattr(api, "_read_scope", lambda *_args: ([10, 20], True))
    monkeypatch.setattr("miniapp.api.shopping_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query")))
    data = api.shopping_items(api.request(42), {"workspace_id": "all"})["data"]
    assert data["items"] == []
    assert data["read_only"] is True
    assert data["note"] == "Выберите одно пространство для списка покупок."


def test_limits_home_collection_reuses_grouped_budget_read_model(monkeypatch):
    api = MiniAppAPI()
    tx = TransactionFilters([10], False, date(2026, 8, 1), date(2026, 8, 11), "current_month", "all", None, "workspace_id=%s", (10,))
    monkeypatch.setattr(api, "goals", lambda *_args: {"data": {"items": []}})
    monkeypatch.setattr(api, "limits", lambda *_args: {"data": {"items": []}})
    monkeypatch.setattr("miniapp.api.user_local_date", lambda *_args: date(2026, 8, 11))
    monkeypatch.setattr("miniapp.api.list_category_budget_groups", lambda *_args: [{"id": 9, "period_type": "month", "enabled": True}])
    monkeypatch.setattr(api, "_category_budget_dict", lambda *_args: {
        "id": 9,
        "kind": "category_budget",
        "title": "Дом",
        "amount": "1000",
        "spent": "900",
        "currency": "RUB",
        "period": "month",
        "percent": 90,
        "status": "warning",
        "categories": ["Дом"],
        "enabled": True,
    })
    items = api._home_focus_items(api.request(42), {"workspace_id": 10}, tx)
    assert items[0]["kind"] == "limit"
    assert items[0]["budget_kind"] == "category_budget"
    assert items[0]["title"] == "Дом"


def test_privacy_deletion_anonymizes_shared_shopping_without_second_member(monkeypatch):
    from services.personal_data_deletion import _anonymize_shared_shopping_attribution

    class Cursor:
        rowcount = 1

        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    cur = Cursor()
    monkeypatch.setattr("services.personal_data_deletion._table_columns", lambda *_args: {"workspace_id", "created_by", "updated_by"})
    monkeypatch.setattr("services.personal_data_deletion._table_exists", lambda *_args: True)

    assert _anonymize_shared_shopping_attribution(cur, 42) == 1
    sql, params = cur.calls[0]
    assert "FROM public.workspaces w" in sql
    assert "w.kind<>'personal'" in sql
    assert "workspace_members" not in sql
    assert params == (42, 42, 42, 42)


def test_privacy_deletion_preserves_other_creator_and_nulls_deleting_updater(monkeypatch):
    from services.personal_data_deletion import _anonymize_shared_shopping_attribution

    class Cursor:
        rowcount = 1

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

    cur = Cursor()
    monkeypatch.setattr("services.personal_data_deletion._table_columns", lambda *_args: {"workspace_id", "created_by", "updated_by"})
    monkeypatch.setattr("services.personal_data_deletion._table_exists", lambda *_args: True)

    _anonymize_shared_shopping_attribution(cur, 42)

    assert "created_by=CASE WHEN i.created_by=%s THEN NULL ELSE i.created_by END" in cur.sql
    assert "updated_by=CASE WHEN i.updated_by=%s THEN NULL ELSE i.updated_by END" in cur.sql
    assert cur.params == (42, 42, 42, 42)


def test_privacy_deletion_declares_user_owned_state_and_personal_workspace_cascade():
    from services.personal_data_deletion import PERSONAL_TABLES

    source = open("/root/bot_finuchet/services/personal_data_deletion.py", encoding="utf-8").read()
    assert PERSONAL_TABLES["user_home_preferences"] == "user_id=%s"
    assert PERSONAL_TABLES["user_announcement_state"] == "user_id=%s"
    assert "UPDATE public.shopping_items" in source
    assert "created_by=CASE" in source
    assert "DELETE FROM public.workspaces WHERE kind='personal' AND owner_user_id=%s" in source


def test_migration_uses_workspace_cascade_and_nullable_actor_attribution():
    source = open("/root/bot_finuchet/migrations/20260811_022_custom_home_shopping_announcements.sql", encoding="utf-8").read()
    assert "workspace_id BIGINT NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE" in source
    assert "created_by BIGINT" in source
    assert "created_by BIGINT NOT NULL" not in source
    assert "user_announcement_state" in source
