from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from services.i18n import format_money, t
from utils.money import to_decimal_money

AVERAGE_DAYS_PER_MONTH = Decimal("30.4375")


def monthly_equivalent(amount, repeat_rule: str | None, repeat_interval_days: int | None = None) -> Decimal | None:
    amount_dec = to_decimal_money(amount)
    rule = repeat_rule or 'none'
    if rule == 'weekly':
        return (amount_dec * Decimal(52) / Decimal(12)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rule == 'monthly':
        return amount_dec
    if rule == 'yearly':
        return (amount_dec / Decimal(12)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rule == 'custom_days':
        days = int(repeat_interval_days or 0)
        if days <= 0:
            return None
        return (amount_dec * AVERAGE_DAYS_PER_MONTH / Decimal(days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


def reminder_totals(rows: list[dict]) -> dict:
    totals = {
        'recurring_expenses_monthly': defaultdict(lambda: Decimal("0.00")),
        'recurring_income_monthly': defaultdict(lambda: Decimal("0.00")),
        'one_time_expenses': defaultdict(lambda: Decimal("0.00")),
        'one_time_income': defaultdict(lambda: Decimal("0.00")),
    }
    for row in rows:
        amount = to_decimal_money(row.get('amount') or 0)
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
        return [format_money(amount, currency, locale) for currency, amount in sorted(values.items())]

    return (
        f"{t('reminders.recurring_per_month', locale)}:\n"
        f"{t('reminders.expenses', locale)}: {', '.join(lines_for('recurring_expenses_monthly'))}\n"
        f"{t('reminders.income', locale)}: {', '.join(lines_for('recurring_income_monthly'))}\n\n"
        f"{t('reminders.one_time_upcoming', locale)}:\n"
        f"{t('reminders.expenses', locale)}: {', '.join(lines_for('one_time_expenses'))}\n"
        f"{t('reminders.income', locale)}: {', '.join(lines_for('one_time_income'))}"
    )
