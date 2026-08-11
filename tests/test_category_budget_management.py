import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from services.categories import CategoryReferenceCounts, ManagedCategory


def test_custom_category_owner_filter_is_personal_only_and_workspace_shared(monkeypatch):
    from services import categories

    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (1,)

    monkeypatch.setattr(categories, "_table_columns", lambda _cur, table: {"id", "user_id", "workspace_id"} if table == "custom_categories" else set())
    personal = Cursor()
    workspace = Cursor()

    assert categories._category_exists(personal, user_id=55, workspace_id=None, op_type="Расходы", name="Food") is True
    assert categories._category_exists(workspace, user_id=55, workspace_id=10, op_type="Расходы", name="Food") is True

    personal_sql, personal_params = personal.calls[0]
    workspace_sql, workspace_params = workspace.calls[0]
    assert "(user_id=%s OR %s::boolean)" in personal_sql
    assert personal_params[-2:] == (55, False)
    assert "(user_id=%s OR %s::boolean)" in workspace_sql
    assert workspace_params[-2:] == (55, True)


def _category_scope_rows():
    return {
        "operations": [
            {"id": 1, "workspace_id": 10, "user_id": 55, "type": "Расходы", "category": "Прочее"},
            {"id": 2, "workspace_id": 10, "user_id": 55, "type": "Расходы", "category": "Прочее"},
            {"id": 3, "workspace_id": 10, "user_id": 55, "type": "Доходы", "category": "Прочее"},
        ],
        "user_reminders": [
            {"id": 11, "workspace_id": 10, "user_id": 55, "rem_type": "Расходы", "category": "Прочее"},
            {"id": 12, "workspace_id": 10, "user_id": 55, "rem_type": "Доходы", "category": "Прочее"},
        ],
        "category_limits": [
            {"id": 21, "workspace_id": 10, "user_id": 55, "period": "month", "category": "Прочее", "amount": 20000, "currency": "RUB"},
        ],
        "category_budget_groups": [
            {"id": 31, "workspace_id": 10, "owner_user_id": 55},
        ],
        "category_budget_group_members": [
            {"group_id": 31, "category_name": "Прочее", "normalized_category_name": "прочее"},
        ],
        "custom_categories": [
            {"id": 41, "workspace_id": 10, "user_id": 55, "type": "Расходы", "name": "Прочее", "normalized_name": "прочее", "archived_at": None},
            {"id": 42, "workspace_id": 10, "user_id": 55, "type": "Доходы", "name": "Прочее", "normalized_name": "прочее", "archived_at": None},
        ],
    }


class _CategoryCursor:
    def __init__(self, rows):
        self.rows = rows
        self.rowcount = 0
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.rowcount = 0
        if "COUNT(*)::int FROM public.operations" in compact:
            workspace_id, op_type, keys = params
            self._result = self._group_count("operations", workspace_id, op_type, keys)
            return
        if "COUNT(*)::int FROM public.user_reminders" in compact:
            workspace_id, op_type, keys = params
            self._result = self._group_count("user_reminders", workspace_id, op_type, keys)
            return
        if "COUNT(*)::int FROM public.category_limits" in compact:
            workspace_id, keys = params
            self._result = self._group_count("category_limits", workspace_id, "Расходы", keys)
            return
        if "COUNT(*)::int FROM public.category_budget_group_members m" in compact:
            workspace_id, keys = params
            counts = {}
            group_ids = {
                row["id"] for row in self.rows["category_budget_groups"]
                if row["workspace_id"] == workspace_id
            }
            for key in keys:
                counts[key] = len([
                    row for row in self.rows["category_budget_group_members"]
                    if row["group_id"] in group_ids and self._key(row["category_name"]) == key
                ])
            self._result = [(key, count) for key, count in counts.items() if count]
            return
        if "COUNT(*)::int FROM public.operation_drafts" in compact:
            workspace_id, op_type, _op_type_again, keys = params
            counts = {
                key: len([
                    row for row in self.rows.get("operation_drafts", [])
                    if row["workspace_id"] == workspace_id
                    and row["payload"].get("type", row["payload"].get("op_type", op_type)) == op_type
                    and self._key(row["payload"].get("category", row["payload"].get("merchant", ""))) == key
                ])
                for key in keys
            }
            self._result = [(key, count) for key, count in counts.items() if count]
            return
        if "COUNT(*)::int FROM public.user_aliases" in compact:
            workspace_id, op_type, keys = params
            self._result = self._group_count("user_aliases", workspace_id, op_type, keys)
            return
        for table in ("category_limit_state", "subscription_patterns", "recurring_spend_patterns"):
            if f"COUNT(*)::int FROM public.{table}" in compact:
                workspace_id, keys = params
                self._result = self._group_count(table, workspace_id, "Расходы", keys)
                return
        if compact.startswith("SELECT COUNT(*) FROM public.operations"):
            workspace_id, op_type, key = params
            self._result = [(len(self._matching("operations", workspace_id, op_type, key)),)]
            return
        if compact.startswith("SELECT COUNT(*) FROM public.user_reminders"):
            workspace_id, op_type, key = params
            self._result = [(len(self._matching("user_reminders", workspace_id, op_type, key)),)]
            return
        if compact.startswith("SELECT COUNT(*) FROM public.category_limits"):
            workspace_id, key = params
            self._result = [(len(self._matching("category_limits", workspace_id, "Расходы", key)),)]
            return
        if compact.startswith("SELECT COUNT(*) FROM category_budget_group_members"):
            workspace_id, key = params
            group_ids = {
                row["id"] for row in self.rows["category_budget_groups"]
                if row["workspace_id"] == workspace_id
            }
            count = len([
                row for row in self.rows["category_budget_group_members"]
                if row["group_id"] in group_ids and self._key(row["category_name"]) == key
            ])
            self._result = [(count,)]
            return
        if compact.startswith("SELECT COUNT(*) FROM public.operation_drafts"):
            workspace_id, op_type, _op_type_again, key = params
            count = len([
                row for row in self.rows.get("operation_drafts", [])
                if row["workspace_id"] == workspace_id
                and row["payload"].get("type", row["payload"].get("op_type", op_type)) == op_type
                and self._key(row["payload"].get("category", row["payload"].get("merchant", ""))) == key
            ])
            self._result = [(count,)]
            return
        if compact.startswith("SELECT COUNT(*) FROM public.user_aliases"):
            workspace_id, op_type, key = params
            self._result = [(len(self._matching("user_aliases", workspace_id, op_type, key)),)]
            return
        for table in ("category_limit_state", "subscription_patterns", "recurring_spend_patterns"):
            if compact.startswith(f"SELECT COUNT(*) FROM public.{table}"):
                workspace_id, key = params
                self._result = [(len(self._matching(table, workspace_id, "Расходы", key)),)]
                return
        if compact.startswith("UPDATE public.operations SET category=%s"):
            destination, workspace_id, op_type, key = params
            changed = 0
            for row in self._matching("operations", workspace_id, op_type, key):
                row["category"] = destination
                changed += 1
            self.rowcount = changed
            self._result = []
            return
        if compact.startswith("UPDATE public.operation_drafts SET payload=jsonb_set"):
            destination, workspace_id, op_type, _op_type_again, key = params
            changed = 0
            for row in self.rows.get("operation_drafts", []):
                payload = row["payload"]
                if (
                    row["workspace_id"] == workspace_id
                    and payload.get("type", payload.get("op_type", op_type)) == op_type
                    and self._key(payload.get("category", payload.get("merchant", ""))) == key
                ):
                    payload["category"] = destination
                    changed += 1
            self.rowcount = changed
            self._result = []
            return
        if compact.startswith("UPDATE public.user_reminders SET category=%s"):
            destination, workspace_id, op_type, key = params
            changed = 0
            for row in self._matching("user_reminders", workspace_id, op_type, key):
                row["category"] = destination
                changed += 1
            self.rowcount = changed
            self._result = []
            return
        if compact.startswith("UPDATE public.user_aliases SET category=%s"):
            destination, workspace_id, op_type, key = params
            changed = 0
            for row in self._matching("user_aliases", workspace_id, op_type, key):
                row["category"] = destination
                changed += 1
            self.rowcount = changed
            self._result = []
            return
        for table in ("category_limit_state", "subscription_patterns", "recurring_spend_patterns"):
            if compact.startswith(f"UPDATE public.{table} SET category=%s"):
                destination, workspace_id, key = params
                matching = self._matching(table, workspace_id, "Расходы", key)
                for row in matching:
                    row["category"] = destination
                self.rowcount = len(matching)
                self._result = []
                return
        if compact.startswith("UPDATE public.category_limits SET category=%s"):
            if "WHERE id=%s" in compact:
                destination, limit_id = params
                matching = [row for row in self.rows["category_limits"] if row["id"] == limit_id]
            else:
                destination, workspace_id, key = params
                matching = self._matching("category_limits", workspace_id, "Расходы", key)
            changed = 0
            for row in matching:
                row["category"] = destination
                changed += 1
            self.rowcount = changed
            self._result = []
            return
        if compact.startswith("UPDATE public.category_budget_group_members SET category_name=%s"):
            if "group_id IN" not in compact:
                destination, destination_key, group_id, source_key = params
                group_ids = {group_id}
            else:
                destination, destination_key, source_key, workspace_id = params
                group_ids = {
                    row["id"] for row in self.rows["category_budget_groups"]
                    if row["workspace_id"] == workspace_id
                }
            changed = 0
            for row in self.rows["category_budget_group_members"]:
                if row["group_id"] in group_ids and self._key(row["category_name"]) == source_key:
                    row["category_name"] = destination
                    row["normalized_category_name"] = destination_key
                    changed += 1
            self.rowcount = changed
            self._result = []
            return
        if compact.startswith("SELECT 1 FROM public.category_limits"):
            workspace_id, owner_user_id, period, key = params
            self._result = [(1,)] if any(
                row["workspace_id"] == workspace_id
                and row["user_id"] == owner_user_id
                and row["period"] == period
                and self._key(row["category"]) == key
                for row in self.rows["category_limits"]
            ) else []
            return
        if compact.startswith("UPDATE public.custom_categories SET name=%s"):
            destination, destination_key, workspace_id, op_type, source_key, user_id, _workspace_id_again = params
            for row in self.rows["custom_categories"]:
                if row["workspace_id"] == workspace_id and row["type"] == op_type and row["normalized_name"] == source_key and row["archived_at"] is None:
                    row["name"] = destination
                    row["normalized_name"] = destination_key
                    self.rowcount = 1
                    self._result = [(row["id"],)]
                    return
            self._result = []
            return
        if compact.startswith("SELECT id, user_id, period, amount, currency FROM public.category_limits"):
            workspace_id, key = params
            self._result = [
                (row["id"], row["user_id"], row["period"], row["amount"], row["currency"])
                for row in self._matching("category_limits", workspace_id, "Расходы", key)
            ]
            return
        if compact.startswith("SELECT m.group_id FROM public.category_budget_group_members"):
            workspace_id, key = params
            group_ids = {
                row["id"] for row in self.rows["category_budget_groups"]
                if row["workspace_id"] == workspace_id
            }
            self._result = [
                (row["group_id"],)
                for row in self.rows["category_budget_group_members"]
                if row["group_id"] in group_ids and self._key(row["category_name"]) == key
            ]
            return
        if compact.startswith("SELECT 1 FROM public.category_budget_group_members"):
            group_id, key = params
            self._result = [(1,)] if any(row["group_id"] == group_id and self._key(row["category_name"]) == key for row in self.rows["category_budget_group_members"]) else []
            return
        if compact.startswith("DELETE FROM public.category_budget_group_members WHERE"):
            group_id, key = params
            before = len(self.rows["category_budget_group_members"])
            self.rows["category_budget_group_members"] = [
                row for row in self.rows["category_budget_group_members"]
                if not (row["group_id"] == group_id and self._key(row["category_name"]) == key)
            ]
            self.rowcount = before - len(self.rows["category_budget_group_members"])
            self._result = []
            return
        if compact.startswith("SELECT id FROM public.operations"):
            workspace_id, op_type, key = params
            self._result = [(row["id"],) for row in self._matching("operations", workspace_id, op_type, key)]
            return
        if compact.startswith("DELETE FROM public.operations WHERE id=ANY"):
            operation_ids = set(params[0])
            before = len(self.rows["operations"])
            self.rows["operations"] = [row for row in self.rows["operations"] if row["id"] not in operation_ids]
            self.rowcount = before - len(self.rows["operations"])
            self._result = []
            return
        if compact.startswith("DELETE FROM public.category_limits"):
            if "WHERE id=%s" in compact:
                limit_id = params[0]
                before = len(self.rows["category_limits"])
                self.rows["category_limits"] = [row for row in self.rows["category_limits"] if row["id"] != limit_id]
                self.rowcount = before - len(self.rows["category_limits"])
                self._result = []
                return
            if len(params) == 4:
                user_id, workspace_id, _period, key = params
            elif len(params) == 2:
                workspace_id, key = params
                user_id = None
            else:
                user_id, workspace_id, key = params
            before = len(self.rows["category_limits"])
            self.rows["category_limits"] = [
                row for row in self.rows["category_limits"]
                if row not in self._matching("category_limits", workspace_id, "Расходы", key, user_id=user_id)
            ]
            self.rowcount = before - len(self.rows["category_limits"])
            self._result = []
            return
        if compact.startswith("DELETE FROM public.category_budget_group_members m"):
            workspace_id, key = params
            group_ids = {
                row["id"] for row in self.rows["category_budget_groups"]
                if row["workspace_id"] == workspace_id
            }
            before = len(self.rows["category_budget_group_members"])
            self.rows["category_budget_group_members"] = [
                row for row in self.rows["category_budget_group_members"]
                if not (row["group_id"] in group_ids and self._key(row["category_name"]) == key)
            ]
            self.rowcount = before - len(self.rows["category_budget_group_members"])
            self._result = []
            return
        if compact.startswith("DELETE FROM public.user_reminders"):
            workspace_id, op_type, key = params
            before = len(self.rows["user_reminders"])
            self.rows["user_reminders"] = [
                row for row in self.rows["user_reminders"]
                if row not in self._matching("user_reminders", workspace_id, op_type, key)
            ]
            self.rowcount = before - len(self.rows["user_reminders"])
            self._result = []
            return
        if compact.startswith("UPDATE public.custom_categories SET archived_at"):
            workspace_id, op_type, key, user_id, _workspace_id_again = params
            for row in self.rows["custom_categories"]:
                if row["workspace_id"] == workspace_id and row["type"] == op_type and row["normalized_name"] == key and row["archived_at"] is None:
                    row["archived_at"] = "now"
                    self.rowcount = 1
                    self._result = [(row["id"],)]
                    return
            self._result = []
            return
        self._result = []

    @staticmethod
    def _key(value):
        return " ".join(str(value or "").split()).casefold().replace("ё", "е")

    def _group_count(self, table, workspace_id, op_type, keys, *, user_id=None):
        counts = {}
        for key in keys:
            counts[key] = len(self._matching(table, workspace_id, op_type, key, user_id=user_id))
        return [(key, count) for key, count in counts.items() if count]

    def _matching(self, table, workspace_id, op_type, key, *, user_id=None):
        if table in {"category_limits", "category_limit_state", "subscription_patterns", "recurring_spend_patterns"}:
            type_field = None
        else:
            type_field = "type" if table in {"operations", "user_aliases"} else "rem_type"
        return [
            row for row in self.rows[table]
            if row.get("workspace_id") == workspace_id
            and (user_id is None or row.get("user_id") == user_id)
            and (type_field is None or row.get(type_field) == op_type)
            and self._key(row.get("category")) == key
        ]


class _CategoryConn:
    def __init__(self, rows):
        self.cursor_obj = _CategoryCursor(rows)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _patch_category_scope(monkeypatch, rows):
    from services import categories

    schemas = {
        "operations": {"id", "workspace_id", "user_id", "chat_id", "type", "category", "updated_at"},
        "user_reminders": {"id", "workspace_id", "user_id", "rem_type", "category", "updated_at"},
        "category_limits": {"id", "workspace_id", "user_id", "period", "category", "amount", "currency", "updated_at"},
        "category_budget_groups": {"id", "workspace_id", "owner_user_id"},
        "category_budget_group_members": {"group_id", "category_name", "normalized_category_name"},
        "custom_categories": {"id", "workspace_id", "user_id", "type", "name", "normalized_name", "archived_at", "updated_at"},
    }
    if "operation_drafts" in rows:
        schemas["operation_drafts"] = {"draft_id", "workspace_id", "actor_user_id", "payload", "updated_at"}
    if "user_aliases" in rows:
        schemas["user_aliases"] = {"user_id", "workspace_id", "type", "category", "updated_at"}
    if "ml_observations" in rows:
        schemas["ml_observations"] = {"user_id", "chosen_category", "chosen_type"}
    for table in ("category_limit_state", "subscription_patterns", "recurring_spend_patterns"):
        if table in rows:
            schemas[table] = {"id", "workspace_id", "user_id", "category", "updated_at"}
    monkeypatch.setattr(categories, "get_conn", lambda: _CategoryConn(rows))
    monkeypatch.setattr(categories, "_table_columns", lambda _cur, table: schemas.get(table, set()))
    monkeypatch.setattr(categories, "_ensure_category_exists", lambda *_args, **_kwargs: None)
    if "operation_drafts" not in rows:
        monkeypatch.setattr(categories, "_update_draft_category", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(categories, "track_product_event", lambda _event: None)


class _Message:
    def __init__(self, chat_id=55, chat_type="private"):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.text = ""
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
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


def _message_update(text, user_id=55, chat_id=55, chat_type="private"):
    message = _Message(chat_id=chat_id, chat_type=chat_type)
    message.text = text
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=message.chat,
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_category_menu_starts_with_type_selector(monkeypatch):
    from routers import callbacks

    called = {"list": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: called.__setitem__("list", called["list"] + 1))

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat_menu")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "Категории" in menu.edits[-1][0]
    assert "Выберите тип, категории которого хотите настроить." in menu.edits[-1][0]
    callbacks_data = set(_callbacks(menu.edits[-1][1]["reply_markup"]))
    assert {"cat|type|expense", "cat|type|income", "menu_settings", "start_main"} <= callbacks_data
    assert "cat|goals" not in callbacks_data
    assert called["list"] == 0


def test_category_type_list_preserves_income_add_type(monkeypatch):
    from routers import callbacks

    cats = [
        ManagedCategory("Зарплата", "зарплата", "Доходы", category_id=1, source="custom", operation_count=2),
    ]
    seen = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **kwargs: seen.append(kwargs["op_type"]) or cats)

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat|type|income")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "Категории доходов" in menu.edits[-1][0]
    assert "Зарплата" in menu.edits[-1][0]
    assert {"cat|add|income", "cat|move_start|income"} <= set(_callbacks(menu.edits[-1][1]["reply_markup"]))
    assert seen == ["Доходы"]

    add = _CallbackQuery("cat|add|income")
    asyncio.run(callbacks.callback_handler(_update(add), context))

    assert context.user_data["await_category_create"]["op_type"] == "Доходы"
    assert context.user_data["await_category_create"]["type_key"] == "income"


def test_category_goals_placeholder_removed_and_real_goals_entry_is_main_menu(monkeypatch):
    from routers import callbacks
    from ui.keyboards import main_menu_kb

    called = {"list": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: called.__setitem__("list", called["list"] + 1))

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat_menu")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    assert "cat|goals" not in _callbacks(menu.edits[-1][1]["reply_markup"])
    assert "goal|home" in _callbacks(main_menu_kb("ru"))
    assert called["list"] == 0


def test_category_card_and_delete_inspection_offer_distinct_paths(monkeypatch):
    from routers import callbacks

    cats = [
        ManagedCategory("Стоматология", "стоматология", "Расходы", category_id=1, source="custom", operation_count=2, has_budget=True),
        ManagedCategory("Здоровье", "здоровье", "Расходы", category_id=2, source="custom", operation_count=0),
    ]
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2, category_limits=1, reminders=1, aliases=1))

    context = SimpleNamespace(user_data={})
    menu = _CallbackQuery("cat|type|expense")
    asyncio.run(callbacks.callback_handler(_update(menu), context))

    card = _CallbackQuery("cat|open|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(card), context))

    assert "Тип: расход" in card.edits[-1][0]
    assert {"cat|rename|expense|k0", "cat|move_from|expense|k0", "cat|delete|expense|k0"} <= set(_callbacks(card.edits[-1][1]["reply_markup"]))

    delete = _CallbackQuery("cat|delete|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(delete), context))

    callbacks_data = set(_callbacks(delete.edits[-1][1]["reply_markup"]))
    assert "Операций: 2" in delete.edits[-1][0]
    assert "cat|delete_transfer|expense|k0" in callbacks_data
    assert "cat|hard1|expense|k0" in callbacks_data


def test_category_transfer_confirmation_is_single_use(monkeypatch):
    from routers import callbacks
    from services.categories import CategoryTransferResult

    calls = {"transfer": 0}
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)

    def _transfer(**kwargs):
        calls["transfer"] += 1
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=3),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={
        "category_action": {
            "token": "tok",
            "actor_user_id": 55,
            "workspace_id": 10,
            "op_type": "Расходы",
            "source": "A",
            "destination": "B",
            "mode": "move",
            "expires_at": 9_999_999_999,
            "used": False,
        }
    })

    first = _CallbackQuery("cat|confirm|tok")
    asyncio.run(callbacks.callback_handler(_update(first), context))

    assert calls["transfer"] == 1
    assert "Обновлено операций: 3" in first.edits[-1][0]

    second = _CallbackQuery("cat|confirm|tok")
    asyncio.run(callbacks.callback_handler(_update(second), context))

    assert calls["transfer"] == 1
    assert second.answers[-1][1].get("show_alert") is True


def test_income_transfer_uses_selected_type(monkeypatch):
    from routers import callbacks
    from services.categories import CategoryTransferResult

    cats = [
        ManagedCategory("Зарплата", "зарплата", "Доходы", category_id=1, source="custom", operation_count=2),
        ManagedCategory("Бонус", "бонус", "Доходы", category_id=2, source="custom", operation_count=0),
    ]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **kwargs: cats if kwargs["op_type"] == "Доходы" else [])
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2))

    def _transfer(**kwargs):
        calls.append(kwargs)
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=2),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={})

    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|income")), context))
    preview = _CallbackQuery("cat|move_to|income|k0|k1")
    asyncio.run(callbacks.callback_handler(_update(preview), context))
    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls[0]["op_type"] == "Доходы"
    assert calls[0]["source"] == "Зарплата"
    assert calls[0]["destination"] == "Бонус"
    assert "cat|post_delete|" in "|".join(_callbacks(confirm.edits[-1][1]["reply_markup"]))


def test_duplicate_rename_offers_merge_retry_open_and_confirm(monkeypatch):
    from routers import callbacks, messages
    from services.categories import CategoryTransferResult

    cats = [
        ManagedCategory("A", "a", "Расходы", category_id=1, source="custom", operation_count=3),
        ManagedCategory("B", "b", "Расходы", category_id=2, source="custom", operation_count=1),
    ]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=3, category_limits=1))

    def _transfer(**kwargs):
        calls.append(kwargs)
        return CategoryTransferResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=3),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "transfer_category", _transfer)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|rename|expense|k0")), context))

    msg_update = _message_update("B")
    asyncio.run(messages.handle_text(msg_update, context))

    text, kwargs = msg_update.message.replies[-1]
    data = _callbacks(kwargs["reply_markup"])
    assert "уже есть" in text
    assert any(cb.startswith("cat|confirm|") for cb in data)
    assert any(cb.startswith("cat|rename_again|") for cb in data)
    assert any(cb.startswith("cat|open_dup|") for cb in data)

    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls[0]["source"] == "A"
    assert calls[0]["destination"] == "B"
    assert calls[0]["archive_source"] is False


def test_normal_rename_confirms_service_call(monkeypatch):
    from routers import callbacks, messages
    from services.categories import CategoryRenameResult

    cats = [ManagedCategory("Food", "food", "Расходы", category_id=1, source="custom", operation_count=2)]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(messages, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=2))

    def _rename(**kwargs):
        calls.append(kwargs)
        return CategoryRenameResult(
            source=kwargs["source"],
            destination=kwargs["destination"],
            op_type=kwargs["op_type"],
            counts=CategoryReferenceCounts(operations=2),
            changed=True,
        )

    monkeypatch.setattr(callbacks, "rename_category", _rename)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|rename|expense|k0")), context))
    msg_update = _message_update("food")
    asyncio.run(messages.handle_text(msg_update, context))
    token = context.user_data["category_action"]["token"]
    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert calls == [{
        "user_id": 55,
        "workspace_id": 10,
        "op_type": "Расходы",
        "source": "Food",
        "destination": "food",
    }]
    assert "переименована" in confirm.edits[-1][0]


def test_hard_delete_requires_second_confirmation(monkeypatch):
    from routers import callbacks

    cats = [ManagedCategory("Trips", "trips", "Расходы", category_id=1, source="custom", operation_count=4)]
    calls = []
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    monkeypatch.setattr(callbacks, "category_reference_counts", lambda **_kwargs: CategoryReferenceCounts(operations=4, aliases=1))

    def _hard_delete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(source=kwargs["category"], deleted_operation_count=4)

    monkeypatch.setattr(callbacks, "hard_delete_category_with_operations", _hard_delete)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))

    first = _CallbackQuery("cat|hard1|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(first), context))
    assert calls == []
    assert "cat|hard2|expense|k0" in _callbacks(first.edits[-1][1]["reply_markup"])

    second = _CallbackQuery("cat|hard2|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(second), context))
    assert calls == []
    token = context.user_data["category_action"]["token"]

    confirm = _CallbackQuery(f"cat|confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))
    assert calls == [{"user_id": 55, "workspace_id": 10, "op_type": "Расходы", "category": "Trips"}]
    assert "Удалено операций: 4" in confirm.edits[-1][0]


def test_protected_category_cannot_be_renamed_or_deleted(monkeypatch):
    from routers import callbacks

    cats = [ManagedCategory("Без операций", "без операций", "Расходы", category_id=None, source="operation", operation_count=1)]
    monkeypatch.setattr(callbacks, "_category_workspace", lambda _update: 10)
    monkeypatch.setattr(callbacks, "list_managed_categories", lambda **_kwargs: cats)
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(_CallbackQuery("cat|type|expense")), context))

    rename = _CallbackQuery("cat|rename|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(rename), context))
    delete = _CallbackQuery("cat|delete|expense|k0")
    asyncio.run(callbacks.callback_handler(_update(delete), context))

    assert rename.answers[-1][1]["show_alert"] is True
    assert delete.answers[-1][1]["show_alert"] is True


def test_budget_edit_requires_confirmation(monkeypatch):
    from routers import callbacks

    saved = []
    monkeypatch.setattr(callbacks, "get_user_budgets", lambda _cid: (10_000, 50_000))
    monkeypatch.setattr(callbacks, "set_budget", lambda user_id, **kwargs: saved.append((user_id, kwargs)))
    monkeypatch.setattr(callbacks, "_budget_spent", lambda *_args: 0)

    context = SimpleNamespace(user_data={})
    adjust = _CallbackQuery("bud_adj|month|1000")
    asyncio.run(callbacks.callback_handler(_update(adjust), context))

    assert saved == []
    token = context.user_data["budget_pending_edit"]["token"]
    assert f"bud_confirm|{token}" in _callbacks(adjust.edits[-1][1]["reply_markup"])

    confirm = _CallbackQuery(f"bud_confirm|{token}")
    asyncio.run(callbacks.callback_handler(_update(confirm), context))

    assert saved == [(55, {"month": 51_000})]
    assert "Бюджет обновлён" in confirm.edits[-1][0]


def test_evening_tip_has_working_bot_cta_and_no_miniapp_link():
    from jobs.daily import _evening_reply_markup, _with_evening_tip

    text = _with_evening_tip("Напоминание", 55, date(2026, 7, 28))
    markup = _evening_reply_markup(55, date(2026, 7, 28))
    callbacks = _callbacks(markup)

    assert "💡 Возможность бота" in text
    assert "Mini App" not in text
    assert "noop_today" in callbacks
    assert any(cb in {"lb_hub", "rem_menu", "exp_menu", "cat_menu", "menu_settings", "menu_report", "start_main"} for cb in callbacks)


def test_report_export_buttons_use_exact_periods():
    from jobs.daily import monthly_report_kb, weekly_report_kb

    weekly = _callbacks(weekly_report_kb(date(2026, 7, 20), date(2026, 7, 26)))
    monthly = _callbacks(monthly_report_kb(date(2026, 7, 1), date(2026, 7, 31)))

    assert "rep_export|w|2026-07-20|2026-07-26" in weekly
    assert "rep_export|m|2026-07-01|2026-07-31" in monthly


def test_category_reference_counts_isolate_user_reminders_by_rem_type(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    _patch_category_scope(monkeypatch, rows)

    expense = categories.category_reference_counts_many(
        user_id=55,
        workspace_id=10,
        op_type="Расходы",
        category_keys=["Прочее"],
    )["прочее"]
    income = categories.category_reference_counts(
        user_id=55,
        workspace_id=10,
        op_type="Доходы",
        category="Прочее",
    )

    assert expense.operations == 2
    assert expense.reminders == 1
    assert expense.category_limits == 1
    assert expense.category_budget_groups == 1
    assert income.operations == 1
    assert income.reminders == 1
    assert income.category_limits == 0
    assert income.category_budget_groups == 0


def test_rename_income_category_preserves_expense_spending_configuration(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    _patch_category_scope(monkeypatch, rows)
    monkeypatch.setattr(categories, "_category_exists", lambda *_args, **_kwargs: False)

    result = categories.rename_category(
        user_id=55,
        workspace_id=10,
        op_type="Доходы",
        source="Прочее",
        destination="Доп. доход",
    )

    assert result.counts.operations == 1
    assert result.counts.reminders == 1
    assert result.counts.category_limits == 0
    assert result.counts.category_budget_groups == 0
    assert [row["category"] for row in rows["operations"]] == ["Прочее", "Прочее", "Доп. доход"]
    assert [row["category"] for row in rows["user_reminders"]] == ["Прочее", "Доп. доход"]
    assert rows["category_limits"] == [{"id": 21, "workspace_id": 10, "user_id": 55, "period": "month", "category": "Прочее", "amount": 20000, "currency": "RUB"}]
    assert rows["category_budget_group_members"] == [{"group_id": 31, "category_name": "Прочее", "normalized_category_name": "прочее"}]
    assert rows["custom_categories"][1]["name"] == "Доп. доход"
    assert rows["custom_categories"][1]["normalized_name"] == "доп. доход"


def test_rename_expense_category_updates_spending_configuration(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    _patch_category_scope(monkeypatch, rows)
    monkeypatch.setattr(categories, "_category_exists", lambda *_args, **_kwargs: False)

    result = categories.rename_category(
        user_id=55,
        workspace_id=10,
        op_type="Расходы",
        source="Прочее",
        destination="Отдых",
    )

    assert result.counts.operations == 2
    assert result.counts.reminders == 1
    assert result.counts.category_limits == 1
    assert result.counts.category_budget_groups == 1
    assert [row["category"] for row in rows["operations"]] == ["Отдых", "Отдых", "Прочее"]
    assert [row["category"] for row in rows["user_reminders"]] == ["Отдых", "Прочее"]
    assert rows["category_limits"][0]["category"] == "Отдых"
    assert rows["category_budget_group_members"] == [{"group_id": 31, "category_name": "Отдых", "normalized_category_name": "отдых"}]


def test_transfer_category_isolates_operations_and_reminders_by_op_type(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    _patch_category_scope(monkeypatch, rows)
    monkeypatch.setattr(categories, "_category_exists", lambda *_args, **_kwargs: True)

    result = categories.transfer_category(
        user_id=55,
        workspace_id=10,
        op_type="Расходы",
        source="Прочее",
        destination="Отдых",
    )

    assert result.counts.operations == 2
    assert result.counts.reminders == 1
    assert result.counts.category_limits == 1
    assert result.counts.category_budget_groups == 1
    assert [row["category"] for row in rows["operations"]] == ["Отдых", "Отдых", "Прочее"]
    assert [row["category"] for row in rows["user_reminders"]] == ["Отдых", "Прочее"]
    assert rows["category_limits"] == []
    assert rows["category_budget_group_members"] == [{"group_id": 31, "category_name": "Отдых", "normalized_category_name": "отдых"}]


def test_direct_delete_rejects_a_non_operation_reference(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    rows["operations"] = [row for row in rows["operations"] if row["type"] == "Расходы"]
    _patch_category_scope(monkeypatch, rows)

    import pytest

    with pytest.raises(ValueError, match="category_has_references"):
        categories.delete_category_without_operations(
            user_id=55,
            workspace_id=10,
            op_type="Доходы",
            category="Прочее",
        )

    assert rows["operations"] == [
        {"id": 1, "workspace_id": 10, "user_id": 55, "type": "Расходы", "category": "Прочее"},
        {"id": 2, "workspace_id": 10, "user_id": 55, "type": "Расходы", "category": "Прочее"},
    ]
    assert rows["category_limits"] == [{"id": 21, "workspace_id": 10, "user_id": 55, "period": "month", "category": "Прочее", "amount": 20000, "currency": "RUB"}]
    assert rows["category_budget_group_members"] == [{"group_id": 31, "category_name": "Прочее", "normalized_category_name": "прочее"}]
    assert rows["custom_categories"][1]["archived_at"] is None


def test_direct_delete_archives_a_truly_empty_custom_category(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    rows["operations"] = [row for row in rows["operations"] if row["type"] == "Расходы"]
    rows["user_reminders"] = [row for row in rows["user_reminders"] if row["rem_type"] == "Расходы"]
    _patch_category_scope(monkeypatch, rows)

    result = categories.delete_category_without_operations(
        user_id=55,
        workspace_id=10,
        op_type="Доходы",
        category="Прочее",
    )

    assert result.counts.total == 0
    assert result.changed is True
    assert rows["custom_categories"][1]["archived_at"] == "now"


@pytest.mark.parametrize("reference", ["category_limit", "reminder", "grouped_budget"])
def test_direct_delete_requires_transfer_for_every_known_reference(monkeypatch, reference):
    from services import categories

    rows = _category_scope_rows()
    rows["operations"] = []
    rows["category_limits"] = rows["category_limits"] if reference == "category_limit" else []
    rows["user_reminders"] = rows["user_reminders"][:1] if reference == "reminder" else []
    rows["category_budget_group_members"] = rows["category_budget_group_members"] if reference == "grouped_budget" else []
    _patch_category_scope(monkeypatch, rows)

    before = {name: [dict(row) for row in rows[name]] for name in ("category_limits", "user_reminders", "category_budget_group_members")}
    with pytest.raises(ValueError, match="category_has_references"):
        categories.delete_category_without_operations(
            user_id=55,
            workspace_id=10,
            op_type="Расходы",
            category="Прочее",
        )

    assert rows["category_limits"] == before["category_limits"]
    assert rows["user_reminders"] == before["user_reminders"]
    assert rows["category_budget_group_members"] == before["category_budget_group_members"]
    assert rows["custom_categories"][0]["archived_at"] is None


def test_workspace_transfer_migrates_all_members_and_isolates_other_scopes(monkeypatch):
    from services import categories

    rows = {
        "operations": [
            {"id": 1, "workspace_id": 10, "user_id": 55, "type": "Расходы", "category": "Прочее"},
            {"id": 2, "workspace_id": 10, "user_id": 66, "type": "Расходы", "category": "  ПРОЧЕЕ  "},
            {"id": 3, "workspace_id": 20, "user_id": 66, "type": "Расходы", "category": "Прочее"},
            {"id": 4, "workspace_id": None, "user_id": 66, "type": "Расходы", "category": "Прочее"},
        ],
        "category_limits": [
            {"id": 11, "workspace_id": 10, "user_id": 55, "period": "month", "category": "Прочее", "amount": 100, "currency": "RUB"},
            {"id": 12, "workspace_id": 10, "user_id": 66, "period": "month", "category": " прочее ", "amount": 200, "currency": "RUB"},
            {"id": 13, "workspace_id": 20, "user_id": 66, "period": "month", "category": "Прочее", "amount": 300, "currency": "RUB"},
            {"id": 14, "workspace_id": None, "user_id": 66, "period": "month", "category": "Прочее", "amount": 400, "currency": "RUB"},
        ],
        "category_limit_state": [
            {"id": 15, "workspace_id": 10, "user_id": 55, "category": "Прочее"},
            {"id": 16, "workspace_id": 10, "user_id": 66, "category": " ПРОЧЕЕ "},
            {"id": 17, "workspace_id": 20, "user_id": 66, "category": "Прочее"},
        ],
        "subscription_patterns": [
            {"id": 18, "workspace_id": 10, "user_id": 55, "category": "Прочее"},
            {"id": 19, "workspace_id": 10, "user_id": 66, "category": "ПРОЧЕЕ"},
            {"id": 20, "workspace_id": 20, "user_id": 66, "category": "Прочее"},
        ],
        "recurring_spend_patterns": [
            {"id": 24, "workspace_id": 10, "user_id": 55, "category": "Прочее"},
            {"id": 25, "workspace_id": 10, "user_id": 66, "category": "Прочее   "},
            {"id": 26, "workspace_id": None, "user_id": 66, "category": "Прочее"},
        ],
        "user_reminders": [
            {"id": 21, "workspace_id": 10, "user_id": 55, "rem_type": "Расходы", "category": "Прочее"},
            {"id": 22, "workspace_id": 10, "user_id": 66, "rem_type": "Расходы", "category": "Прочее   "},
            {"id": 23, "workspace_id": 20, "user_id": 66, "rem_type": "Расходы", "category": "Прочее"},
        ],
        "category_budget_groups": [
            {"id": 31, "workspace_id": 10, "owner_user_id": 55},
            {"id": 32, "workspace_id": 10, "owner_user_id": 66},
            {"id": 33, "workspace_id": 20, "owner_user_id": 66},
        ],
        "category_budget_group_members": [
            {"group_id": 31, "category_name": "Прочее", "normalized_category_name": "прочее"},
            {"group_id": 32, "category_name": " ПРОЧЕЕ ", "normalized_category_name": " прочее "},
            {"group_id": 33, "category_name": "Прочее", "normalized_category_name": "прочее"},
        ],
        "operation_drafts": [
            {"draft_id": "a", "workspace_id": 10, "actor_user_id": 55, "payload": {"type": "Расходы", "category": "Прочее"}},
            {"draft_id": "b", "workspace_id": 10, "actor_user_id": 66, "payload": {"type": "Расходы", "category": " ПРОЧЕЕ "}},
            {"draft_id": "c", "workspace_id": 20, "actor_user_id": 66, "payload": {"type": "Расходы", "category": "Прочее"}},
        ],
        "user_aliases": [
            {"user_id": 55, "workspace_id": 10, "type": "Расходы", "category": "Прочее"},
            {"user_id": 66, "workspace_id": 10, "type": "Расходы", "category": "ПРОЧЕЕ"},
            {"user_id": 66, "workspace_id": 20, "type": "Расходы", "category": "Прочее"},
        ],
        "ml_observations": [
            {"user_id": 55, "chosen_type": "Расходы", "chosen_category": "Прочее"},
            {"user_id": 66, "chosen_type": "Расходы", "chosen_category": "Прочее"},
        ],
        "custom_categories": [
            {"id": 41, "workspace_id": 10, "user_id": 55, "type": "Расходы", "name": "Прочее", "normalized_name": "прочее", "archived_at": None},
            {"id": 42, "workspace_id": 10, "user_id": 55, "type": "Расходы", "name": "Другое", "normalized_name": "другое", "archived_at": None},
        ],
    }
    _patch_category_scope(monkeypatch, rows)
    monkeypatch.setattr(categories, "_category_exists", lambda *_args, **_kwargs: True)

    result = categories.transfer_category(
        user_id=55,
        workspace_id=10,
        op_type="Расходы",
        source="Прочее",
        destination="Другое",
        archive_source=True,
        budget_resolution="transfer_source",
    )

    key = categories.normalized_category_key
    assert result.counts.category_limits == 2
    assert all(key(row["category"]) == "другое" for row in rows["operations"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["category_limits"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["category_limit_state"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["subscription_patterns"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["recurring_spend_patterns"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["user_reminders"] if row["workspace_id"] == 10)
    assert all(key(row["category_name"]) == "другое" for row in rows["category_budget_group_members"] if row["group_id"] in {31, 32})
    assert all(key(row["payload"]["category"]) == "другое" for row in rows["operation_drafts"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "другое" for row in rows["user_aliases"] if row["workspace_id"] == 10)
    assert all(key(row["category"]) == "прочее" for row in rows["operations"] if row["workspace_id"] != 10)
    assert all(key(row["category"]) == "прочее" for row in rows["category_limits"] if row["workspace_id"] != 10)
    assert all(key(row["category"]) == "прочее" for row in rows["category_limit_state"] if row["workspace_id"] != 10)
    assert all(key(row["category"]) == "прочее" for row in rows["subscription_patterns"] if row["workspace_id"] != 10)
    assert all(key(row["category"]) == "прочее" for row in rows["recurring_spend_patterns"] if row["workspace_id"] != 10)
    assert rows["ml_observations"] == [
        {"user_id": 55, "chosen_type": "Расходы", "chosen_category": "Прочее"},
        {"user_id": 66, "chosen_type": "Расходы", "chosen_category": "Прочее"},
    ]
    assert rows["custom_categories"][0]["archived_at"] == "now"


def test_category_key_sql_matches_whitespace_case_and_yo_contract():
    from services.categories import category_key_sql, normalized_category_key

    sql = category_key_sql("category")

    assert "btrim" in sql
    assert "regexp_replace" in sql
    assert "lower" in sql
    assert "'ё', 'е'" in sql
    assert {normalized_category_key(value) for value in ("Прочее", " ПРОЧЕЕ ", "Прочее   ")} == {"прочее"}
    assert normalized_category_key("Ёлка") == normalized_category_key("елка") == "елка"


def test_personal_reference_scope_keeps_another_user_isolated():
    from services.categories import _owned_reference_scope

    columns = {"workspace_id", "user_id", "category"}

    assert _owned_reference_scope(columns, 55, None, owner_column="user_id") == (
        ["workspace_id IS NULL", "user_id=%s"],
        [55],
    )
    assert _owned_reference_scope(
        columns,
        55,
        10,
        owner_column="user_id",
        shared_workspace=False,
    ) == (["workspace_id=%s", "user_id=%s"], [10, 55])


def test_hard_delete_category_keeps_same_name_income_category_usable(monkeypatch):
    from services import categories

    rows = _category_scope_rows()
    _patch_category_scope(monkeypatch, rows)

    result = categories.hard_delete_category_with_operations(
        user_id=55,
        workspace_id=10,
        op_type="Расходы",
        category="Прочее",
    )

    assert result.deleted_operation_count == 2
    assert result.counts.operations == 2
    assert result.counts.reminders == 1
    assert result.counts.category_limits == 1
    assert result.counts.category_budget_groups == 1
    assert rows["operations"] == [{"id": 3, "workspace_id": 10, "user_id": 55, "type": "Доходы", "category": "Прочее"}]
    assert rows["user_reminders"] == [{"id": 12, "workspace_id": 10, "user_id": 55, "rem_type": "Доходы", "category": "Прочее"}]
    assert rows["category_limits"] == []
    assert rows["category_budget_group_members"] == []
