from datetime import date, datetime, time

from services.budgeting import (
    build_budget_status,
    current_alert_milestone,
    period_bounds,
    render_limit_alert,
    top_category_contribution,
)
from services.message_cleanup import BotMessageRef, should_cleanup
from services.notification_engine import NotificationFact, NotificationPreferences, choose_best_fact, is_quiet_time
from services.recurring_spend import detect_recurring_spend
from services.subscriptions import detect_upcoming_subscriptions
from ui.keyboards import limits_budgets_hub_kb, main_menu_kb, settings_menu_kb


def test_canonical_main_menu_routes_to_limits_budgets_hub():
    callbacks = [button.callback_data for row in main_menu_kb("ru").inline_keyboard for button in row]
    assert "lb_hub" in callbacks
    assert "settings_budgets" not in callbacks
    assert "lim_list" not in callbacks
    assert all(callbacks)


def test_settings_menu_has_no_separate_limits_button():
    callbacks = [button.callback_data for row in settings_menu_kb("ru").inline_keyboard for button in row]
    assert "lb_hub" in callbacks
    assert "lim_list" not in callbacks
    assert "settings_budgets" not in callbacks


def test_limits_budgets_hub_has_existing_compact_callbacks():
    callbacks = [button.callback_data for row in limits_budgets_hub_kb("en").inline_keyboard for button in row]
    assert callbacks == ["lim_list", "gl_menu", "cbg_menu", "lb_status", "menu_notifications", "start_main"]
    assert all(len(c) <= 64 for c in callbacks)


def test_general_limit_status_and_alert_renderer_under_and_over():
    period = period_bounds("month", date(2026, 7, 19))
    ops = [
        {"op_date": date(2026, 7, 2), "type": "Расходы", "category": "Продукты", "amount": 4000},
        {"op_date": date(2026, 7, 3), "type": "Доходы", "category": "Зарплата", "amount": 9000},
        {"op_date": date(2026, 7, 4), "type": "Расходы", "category": "Такси", "amount": 500},
    ]
    status = build_budget_status("Общий лимит", 5000, "RUB", period, ops)
    assert status.spent == 4500
    assert status.remaining == 500
    assert status.percentage == 90
    assert current_alert_milestone(status.percentage) == 90
    text = render_limit_alert(status, locale="ru")
    assert "Лимит: 5 000 RUB" in text
    assert "Потрачено: 4 500 RUB" in text
    assert "Осталось: 500 RUB" in text

    over = build_budget_status("Общий лимит", 5000, "RUB", period, ops + [{"op_date": date(2026, 7, 5), "type": "Расходы", "category": "Такси", "amount": 1750}])
    assert over.overage == 1250
    over_text = render_limit_alert(over, locale="ru")
    assert "Превышение: 1 250 RUB" in over_text
    assert "Использовано: 125%" in over_text
    assert current_alert_milestone(over.percentage) == 125


def test_combined_budget_counts_categories_once_and_top_contributor():
    period = period_bounds("month", date(2026, 7, 19))
    ops = [
        {"op_date": date(2026, 7, 1), "type": "Расходы", "category": "Продукты", "amount": 1000},
        {"op_date": date(2026, 7, 2), "type": "Расходы", "category": "Заведения", "amount": 2000},
        {"op_date": date(2026, 7, 3), "type": "Расходы", "category": "Такси", "amount": 9000},
    ]
    status = build_budget_status("Повседневные", 42000, "RUB", period, ops, ["Продукты", "Заведения"])
    assert status.spent == 3000
    assert status.categories == ("Продукты", "Заведения")
    assert top_category_contribution(ops, period, status.categories) == ("Заведения", 2000)


def test_notification_priority_preferences_and_quiet_hours():
    prefs = NotificationPreferences(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    assert is_quiet_time(datetime(2026, 7, 19, 23, 30), prefs)
    facts = [
        NotificationFact("fallback", "f", "fallback", 99),
        NotificationFact("subscription_upcoming", "s", "subscription", 10),
        NotificationFact("limit_near", "l", "limit", 30),
    ]
    assert choose_best_fact(facts, prefs, datetime(2026, 7, 19, 12, 0)).notification_type == "subscription_upcoming"
    assert choose_best_fact(facts, prefs, datetime(2026, 7, 19, 23, 30)) is None
    blocked = NotificationPreferences(subscription_alerts_enabled=False)
    assert choose_best_fact(facts, blocked, datetime(2026, 7, 19, 12, 0)).notification_type == "limit_near"


def test_subscription_prediction_two_days_before_and_workspace_isolation_shape():
    ops = [
        {"id": 1, "op_date": date(2026, 6, 28), "type": "Расходы", "category": "Подписки", "comment": "Yota", "amount": 750, "currency": "RUB", "workspace_id": 10},
    ]
    predictions = detect_upcoming_subscriptions(ops, date(2026, 7, 26))
    assert len(predictions) == 1
    assert predictions[0].expected_date == date(2026, 7, 28)
    assert "yota" in predictions[0].dedupe_key


def test_recurring_spend_detects_repeated_small_merchant():
    ops = [
        {"op_date": date(2026, 7, 1), "type": "Расходы", "category": "Кофе", "comment": "Coffee", "amount": 300, "currency": "RUB"},
        {"op_date": date(2026, 7, 8), "type": "Расходы", "category": "Кофе", "comment": "Coffee", "amount": 320, "currency": "RUB"},
        {"op_date": date(2026, 7, 15), "type": "Расходы", "category": "Кофе", "comment": "Coffee", "amount": 310, "currency": "RUB"},
        {"op_date": date(2026, 7, 16), "type": "Расходы", "category": "Такси", "comment": "Taxi", "amount": 2000, "currency": "RUB"},
    ]
    insights = detect_recurring_spend(ops, window_days=30)
    assert len(insights) == 1
    assert insights[0].merchant == "coffee"
    assert insights[0].count == 3


def test_message_cleanup_preserves_financial_and_report_messages():
    assert should_cleanup(BotMessageRef(1, 10, "transient_ui"))
    assert not should_cleanup(BotMessageRef(1, 11, "operation_confirmation"))
    assert not should_cleanup(BotMessageRef(1, 12, "report"))
    assert not should_cleanup(BotMessageRef(1, 13, "alert"))
