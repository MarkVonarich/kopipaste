import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services.goal_planning import (
    ScheduleConfig,
    calculate_contribution_first,
    calculate_deadline_first,
    progress_percent,
    status_for_goal,
)
from ui.keyboards import main_menu_kb


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
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_main_menu_contains_financial_goals_next_to_challenges():
    rows = main_menu_kb("ru").inline_keyboard
    assert any([button.callback_data for button in row] == ["goal|home", "chal|home"] for row in rows)
    assert all(len(cb.encode("utf-8")) <= 64 for cb in _callbacks(main_menu_kb("ru")))


def test_deadline_first_monthly_rounds_up_and_includes_deadline_occurrence():
    plan = calculate_deadline_first(
        target_amount=Decimal("100000"),
        current_balance=Decimal("0"),
        deadline=date(2026, 4, 5),
        schedule=ScheduleConfig(frequency="monthly", day=5),
        today=date(2026, 1, 6),
    )
    assert plan.occurrence_count == 3
    assert plan.next_occurrence == date(2026, 2, 5)
    assert plan.projected_completion_date == date(2026, 4, 5)
    assert plan.recommended_amount == Decimal("33334")


def test_contribution_first_without_schedule_returns_required_count_not_fake_date():
    plan = calculate_contribution_first(
        target_amount=Decimal("45000"),
        current_balance=Decimal("10000"),
        comfortable_amount=Decimal("15000"),
        schedule=ScheduleConfig(frequency="none"),
        today=date(2026, 1, 1),
    )
    assert plan.required_contributions == 3
    assert plan.projected_completion_date is None
    assert plan.reason == "no_schedule"


def test_goal_statuses_are_deterministic():
    no_plan = status_for_goal(
        status="active",
        target_amount=Decimal("100"),
        current_balance=Decimal("10"),
        deadline=None,
        plan=None,
        today=date(2026, 1, 1),
    )
    achieved = status_for_goal(
        status="active",
        target_amount=Decimal("100"),
        current_balance=Decimal("100"),
        deadline=None,
        plan=None,
        today=date(2026, 1, 1),
    )
    overdue = status_for_goal(
        status="active",
        target_amount=Decimal("100"),
        current_balance=Decimal("50"),
        deadline=date(2025, 12, 31),
        plan=None,
        today=date(2026, 1, 1),
    )
    assert no_plan == "no_plan"
    assert achieved == "achieved"
    assert overdue == "overdue"
    assert progress_percent(Decimal("100"), Decimal("150")) == 100


def test_goal_notifications_default_off_when_column_missing(monkeypatch):
    from psycopg2 import errors
    from services import notification_preferences

    monkeypatch.setattr(notification_preferences, "pg_fetchall", lambda *_args, **_kwargs: (_ for _ in ()).throw(errors.UndefinedColumn()))
    prefs = notification_preferences.get_notification_preferences(55)
    assert prefs["goal_notifications_enabled"] is False
    assert prefs["challenge_notifications_enabled"] is False


def test_goals_home_renders_empty_state_without_dead_end(monkeypatch):
    from routers import callbacks

    monkeypatch.setattr(callbacks, "resolve_workspace", lambda *_args, **_kwargs: SimpleNamespace(workspace_id=10))
    monkeypatch.setattr(callbacks, "list_goals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(callbacks, "track_product_event", lambda *_args, **_kwargs: None)

    query = _CallbackQuery("goal|home")
    context = SimpleNamespace(user_data={})
    asyncio.run(callbacks.callback_handler(_update(query), context))

    assert query.answers
    assert "Финансовые цели" in query.edits[-1][0]
    data = set(_callbacks(query.edits[-1][1]["reply_markup"]))
    assert {"goal|new", "goal|how", "start_main"} <= data


def test_salary_suggestion_accept_uses_operation_idempotency(monkeypatch):
    from routers import callbacks

    goal = SimpleNamespace(
        id=7,
        owner_user_id=55,
        workspace_id=10,
        currency="RUB",
        planned_contribution_amount=Decimal("25000"),
        comfortable_amount=None,
        display_name="Отпуск",
    )
    calls = []
    monkeypatch.setattr(callbacks, "resolve_workspace", lambda *_args, **_kwargs: SimpleNamespace(workspace_id=10))
    monkeypatch.setattr(callbacks, "get_goal", lambda *_args, **_kwargs: goal)
    monkeypatch.setattr(callbacks, "add_goal_movement", lambda **kwargs: calls.append(kwargs) or (goal, SimpleNamespace(id=1), True))
    monkeypatch.setattr(callbacks, "_render_goal_card", lambda *args, **kwargs: args[0].edit_message_text("card"))
    monkeypatch.setattr(callbacks, "track_product_event", lambda *_args, **_kwargs: None)

    query = _CallbackQuery("goal|sal|a|7|99")
    asyncio.run(callbacks.callback_handler(_update(query), SimpleNamespace(user_data={})))

    assert calls[0]["linked_operation_id"] == 99
    assert calls[0]["idempotency_key"] == "goal:7:income:99:accepted"
    assert calls[0]["movement_type"] == "contribution"


def test_privacy_tables_include_goal_owned_data():
    from services.personal_data_deletion import PERSONAL_TABLES

    assert "financial_goals" in PERSONAL_TABLES
    assert "goal_drafts" in PERSONAL_TABLES
    assert "automatic_notifications" in PERSONAL_TABLES


def test_goal_events_registered_and_do_not_need_raw_properties():
    from services.event_registry import PRODUCT_EVENT_GROUPS

    for name in [
        "goal_created",
        "goal_plan_updated",
        "goal_contribution_added",
        "goal_withdrawal_added",
        "goal_progress_adjusted",
        "goal_income_suggestion_accepted",
        "goal_deleted",
    ]:
        assert PRODUCT_EVENT_GROUPS[name] == "goals"
