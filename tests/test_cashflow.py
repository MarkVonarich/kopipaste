from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from services.cashflow import CashEvent, CashflowRepository, calculate_cashflow, forecast_end, reminder_occurrences

TODAY = date(2026, 10, 4)


def forecast(**changes):
    values = dict(today=TODAY, end=TODAY + timedelta(days=9), balance=D('10000'), reserve=D('1000'),
                  events=(), daily_pace=D('100'), currency='RUB')
    values.update(changes)
    return calculate_cashflow(**values)


def reminder(**changes):
    values = dict(id=1, title='Счёт', rem_type='Расходы', event_date=TODAY, amount='100',
                  repeat_rule='weekly', repeat_interval_days=None, recorded_dates=[])
    values.update(changes)
    return values


def test_formula_separates_future_income_cash_and_goal_reserves():
    events = (
        CashEvent('expense', TODAY, D('2000'), 'expense', 'Платёж'),
        CashEvent('income', TODAY + timedelta(days=2), D('5000'), 'income', 'Доход'),
        CashEvent('goal', TODAY + timedelta(days=3), D('1500'), 'goal', 'Цель'),
    )
    result = forecast(events=events)
    assert result['current_balance'] == D('10000')
    assert result['can_spend'] == D('4500')
    assert result['daily_allowance'] == D('550')
    assert result['trajectory'][-1]['without_income'] == D('7000')
    assert result['trajectory'][-1]['with_expected_income'] == D('12000')


def test_paid_occurrence_is_excluded_but_next_recurrence_is_retained():
    events = reminder_occurrences([reminder(recorded_dates=[TODAY])], today=TODAY, end=TODAY + timedelta(days=8))
    assert [event.due for event in events] == [TODAY + timedelta(days=7)]
    assert forecast(events=events)['planned_expense'] == D('100')


def test_monthly_expansion_reuses_existing_end_of_month_rule():
    events = reminder_occurrences([reminder(event_date=date(2026, 1, 31), repeat_rule='monthly')], today=date(2026, 1, 1), end=date(2026, 3, 31))
    assert [item.due for item in events] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 28)]


def test_identical_occurrences_deduplicate_but_distinct_equal_payments_do_not():
    events = reminder_occurrences([reminder(), reminder(), reminder(id=2)], today=TODAY, end=TODAY)
    assert len(events) == 2
    assert forecast(events=events + events)['planned_expense'] == D('200')


def test_internal_transfers_are_neither_income_nor_expense():
    transfers = (
        CashEvent('out', TODAY, D('3000'), 'transfer', 'Из своего счёта'),
        CashEvent('in', TODAY, D('3000'), 'transfer', 'На свой счёт'),
    )
    assert forecast(events=transfers) == forecast()
    assert reminder_occurrences([reminder(rem_type='Перевод')], today=TODAY, end=TODAY) == ()


def test_overdue_expense_is_due_now_and_overdue_income_is_not_spendable():
    events = reminder_occurrences([
        reminder(event_date=TODAY - timedelta(days=2), repeat_rule='none'),
        reminder(id=2, rem_type='Доходы', event_date=TODAY - timedelta(days=1), repeat_rule='none'),
    ], today=TODAY, end=TODAY)
    result = forecast(events=events)
    assert result['planned_expense'] == D('100')
    assert result['expected_income'] == D('0')
    assert result['overdue_income_count'] == 1
    assert result['trajectory'][0]['without_income'] == D('9800')


@pytest.mark.parametrize('balance', ['0', '-100'])
def test_zero_and_negative_balance_are_not_hidden(balance):
    result = forecast(balance=D(balance))
    assert result['current_balance'] == D(balance)
    assert result['can_spend'] == 0
    assert result['exhaustion_without_income'] == TODAY


@pytest.mark.parametrize(('overrides', 'reason'), [({'balance': None}, 'balance'), ({'end': None}, 'next_income_date'), ({'daily_pace': None}, 'spending_history')])
def test_missing_evidence_has_no_invented_spendable_amount(overrides, reason):
    result = forecast(**overrides)
    assert result['can_spend'] is None
    assert reason in result['missing']
    assert result['daily_allowance'] is None


def test_horizons_require_a_real_future_income_date():
    past = CashEvent('old', TODAY - timedelta(days=1), D('500'), 'income', '')
    later = CashEvent('new', TODAY + timedelta(days=3), D('500'), 'income', '')
    assert forecast_end(TODAY, 'next_income', None, (past,)) is None
    assert forecast_end(TODAY, 'next_income', None, (past, later)) == later.due
    assert forecast_end(TODAY, 'month_end', None, ()) == date(2026, 10, 31)
    assert forecast_end(TODAY, 'date', '2026-10-10', ()) == date(2026, 10, 10)


@pytest.mark.parametrize('end', ['2026-10-03', '2028-01-01', 'wrong'])
def test_invalid_dates_are_rejected(end):
    with pytest.raises(ValueError):
        forecast_end(TODAY, 'date', end, ())


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1e30'])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        forecast(balance=D(value))


def test_recording_a_payment_does_not_reduce_forecast_twice():
    pending = reminder_occurrences([reminder(repeat_rule='none')], today=TODAY, end=TODAY)
    paid = reminder_occurrences([reminder(repeat_rule='none', recorded_dates=[TODAY])], today=TODAY, end=TODAY)
    assert forecast(events=pending)['can_spend'] == forecast(events=paid, balance=D('9900'))['can_spend']
    assert forecast(balance=D('9700'))['can_spend'] == forecast()['can_spend'] - D('300')


def test_history_does_not_fill_gaps_with_zero(monkeypatch):
    rows = [(TODAY - timedelta(days=day), D('100')) for day in range(40, 0, -3)]
    monkeypatch.setattr('services.cashflow.pg_fetchall', lambda *args: rows)
    assert CashflowRepository().daily_pace(1, 10, 'RUB', 'RUB', TODAY) == D('100')
    rows.pop()
    assert CashflowRepository().daily_pace(1, 10, 'RUB', 'RUB', TODAY) is None


def test_explicit_no_spend_days_can_support_zero_pace(monkeypatch):
    calls = []
    def fetch(sql, params):
        calls.append((sql, params))
        return [(TODAY - timedelta(days=day), D('0')) for day in range(14, 0, -1)]
    monkeypatch.setattr('services.cashflow.pg_fetchall', fetch)
    assert CashflowRepository().daily_pace(1, 10, 'EUR', 'RUB', TODAY) == 0
    sql, params = calls[0]
    assert "o.type='Расходы'" in sql and "o.type='noop'" in sql
    assert "COALESCE(o.source,'')<>'reminder'" in sql
    assert params[:3] == (10, 'RUB', 'EUR')


def test_personal_scope_and_currency_are_required(monkeypatch):
    calls = []
    monkeypatch.setattr('services.cashflow.pg_fetchall', lambda sql, params: calls.append((sql, params)) or [])
    CashflowRepository().reminders(1, 10, 'EUR', True)
    assert calls[0][1] == (1, 'EUR', 10, True)
    assert "event_type='recorded'" in calls[0][0]
    with pytest.raises(ValueError, match='personal_workspace_required'):
        CashflowRepository().preview(user_id=1, workspace_id=20, currency='EUR', default_currency='RUB', today=TODAY,
                                     horizon='month_end', end_date=None, balance=D('100'), reserve=D('0'), workspace_kind='group')


def test_api_does_not_trust_foreign_or_all_workspace(monkeypatch):
    from miniapp.api import MiniAppAPI, MiniAppError
    api = MiniAppAPI()
    monkeypatch.setattr(api, '_check_write_rate', lambda req: None)
    monkeypatch.setattr(api, '_read_scope', lambda req, scope: ([10], True))
    with pytest.raises(MiniAppError) as error:
        api.cashflow_preview(api.request(1), {'workspace_id': 'all', 'balance': '100'})
    assert error.value.code == 'forecast_workspace_required'
    def denied(*args):
        raise MiniAppError(403, 'workspace_access_denied', 'Denied')
    monkeypatch.setattr(api, '_read_scope', denied)
    with pytest.raises(MiniAppError) as error:
        api.cashflow_preview(api.request(1), {'workspace_id': 99})
    assert error.value.status == 403
