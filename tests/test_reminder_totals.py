from services.reminder_totals import monthly_equivalent, reminder_totals, render_reminder_totals


def test_monthly_equivalent_rules():
    assert round(monthly_equivalent(120, "weekly"), 2) == 520.0
    assert monthly_equivalent(120, "monthly") == 120
    assert monthly_equivalent(120, "yearly") == 10
    assert round(monthly_equivalent(100, "custom_days", 10), 2) == 304.38
    assert monthly_equivalent(100, "none") is None


def test_reminder_totals_separate_type_and_currency():
    totals = reminder_totals([
        {"amount": 1200, "currency": "RUB", "rem_type": "Расходы", "repeat_rule": "monthly"},
        {"amount": 3000, "currency": "USD", "rem_type": "Расходы", "repeat_rule": "yearly"},
        {"amount": 5000, "currency": "RUB", "rem_type": "Доходы", "repeat_rule": "weekly"},
        {"amount": 700, "currency": "RUB", "rem_type": "Расходы", "repeat_rule": "none"},
        {"amount": 900, "currency": "USD", "rem_type": "Доходы", "repeat_rule": "none"},
    ])
    assert totals["recurring_expenses_monthly"]["RUB"] == 1200
    assert totals["recurring_expenses_monthly"]["USD"] == 250
    assert round(totals["recurring_income_monthly"]["RUB"], 2) == 21666.67
    assert totals["one_time_expenses"]["RUB"] == 700
    assert totals["one_time_income"]["USD"] == 900


def test_reminder_total_headings_are_localized_ru_en():
    rows = [{"amount": 1200, "currency": "RUB", "rem_type": "Расходы", "repeat_rule": "monthly"}]
    ru = render_reminder_totals(rows, "ru")
    en = render_reminder_totals(rows, "en")
    assert "Повторяющиеся в месяц" in ru
    assert "Расходы:" in ru
    assert "Recurring per month" in en
    assert "Expenses:" in en
