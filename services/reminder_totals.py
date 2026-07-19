from __future__ import annotations

from collections import defaultdict

from services.i18n import format_money, t

AVERAGE_DAYS_PER_MONTH = 30.4375


def monthly_equivalent(amount: float, repeat_rule: str | None, repeat_interval_days: int | None = None) -> float | None:
    rule = repeat_rule or 'none'
    if rule == 'weekly':
        return amount * 52 / 12
    if rule == 'monthly':
        return amount
    if rule == 'yearly':
        return amount / 12
    if rule == 'custom_days':
        days = int(repeat_interval_days or 0)
        if days <= 0:
            return None
        return amount * AVERAGE_DAYS_PER_MONTH / days
    return None


def reminder_totals(rows: list[dict]) -> dict:
    totals = {
        'recurring_expenses_monthly': defaultdict(float),
        'recurring_income_monthly': defaultdict(float),
        'one_time_expenses': defaultdict(float),
        'one_time_income': defaultdict(float),
    }
    for row in rows:
        amount = float(row.get('amount') or 0)
        currency = row.get('currency') or 'RUB'
        rem_type = row.get('rem_type') or 'Расходы'
        repeat_rule = row.get('repeat_rule') or 'none'
        eq = monthly_equivalent(amount, repeat_rule, row.get('repeat_interval_days'))
        if eq is None:
            key = 'one_time_income' if rem_type == 'Доходы' else 'one_time_expenses'
            totals[key][currency] += amount
        else:
            key = 'recurring_income_monthly' if rem_type == 'Доходы' else 'recurring_expenses_monthly'
            totals[key][currency] += eq
    return {key: dict(value) for key, value in totals.items()}


def render_reminder_totals(rows: list[dict], locale: str = 'ru') -> str:
    totals = reminder_totals(rows)

    def lines_for(key: str) -> list[str]:
        values = totals.get(key) or {}
        if not values:
            return ['—']
        return [format_money(round(amount), currency, locale) for currency, amount in sorted(values.items())]

    return (
        f"{t('reminders.recurring_per_month', locale)}:\n"
        f"{t('reminders.expenses', locale)}: {', '.join(lines_for('recurring_expenses_monthly'))}\n"
        f"{t('reminders.income', locale)}: {', '.join(lines_for('recurring_income_monthly'))}\n\n"
        f"{t('reminders.one_time_upcoming', locale)}:\n"
        f"{t('reminders.expenses', locale)}: {', '.join(lines_for('one_time_expenses'))}\n"
        f"{t('reminders.income', locale)}: {', '.join(lines_for('one_time_income'))}"
    )
