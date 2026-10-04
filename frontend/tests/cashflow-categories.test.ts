import { describe, expect, it } from 'vitest';
import { CashflowView, CashflowForm, type CashflowResult } from '../src/components/Cashflow';
import { CategoryAppearance, CategoryBadge } from '../src/components/CategoryAppearance';

export const scenario: CashflowResult = {
  currency: 'RUB', as_of: '2026-10-04', end_date: '2026-10-31', current_balance: '10000.00',
  protected_reserve: '1000.00', goal_reserve: '500.00', planned_expense: '2000.00', expected_income: '30000.00',
  ordinary_expense: '2800.00', can_spend: '3700.00', daily_allowance: '232.14', daily_pace: '100.00',
  exhaustion_without_income: null, missing: [], overdue_income_count: 0,
  trajectory: [{ date: '2026-10-04', without_income: '7900.00', with_expected_income: '7900.00' }],
  events: [{ date: '2026-10-04', amount: '2000.00', kind: 'expense', label: 'Жильё', overdue: false }],
};

describe('cashflow and category presentation', () => {
  it('separates confirmed money, expected income, reserves and approximate spending', () => {
    const html = CashflowView(scenario);
    expect(html).toContain('~3 700 ₽');
    expect(html).toContain('Ожидаемые доходы, ещё не доступны');
    expect(html).toContain('Деньги сейчас, подтверждено вами');
    expect(html).toContain('Как рассчитано');
    expect(html).toContain('Остаток по дням');
  });

  it('does not display false zero when balance or history is missing', () => {
    const html = CashflowView({ ...scenario, current_balance: null, can_spend: null, daily_allowance: null, missing: ['balance', 'spending_history'] });
    expect(html).toContain('Не хватает данных');
    expect(html).toContain('Укажите фактический остаток сейчас');
    expect(html).not.toContain('~0 ₽');
  });

  it('shows overdue income exclusion and zero as a valid known result', () => {
    const html = CashflowView({ ...scenario, current_balance: '-100.00', can_spend: '0.00', overdue_income_count: 1 });
    expect(html).toContain('~0 ₽');
    expect(html).toContain('−100'.replace('−', '-'));
    expect(html).toContain('Он исключён из ожидаемых поступлений');
  });

  it('keeps negative entered balance and date fields after a validation error', () => {
    const html = CashflowForm('EUR', { balance: '-100', reserve: '10', horizon: 'date', end_date: '2026-10-10' }, null, false, 'Проверьте дату');
    expect(html).toContain('value="-100"');
    expect(html).toContain('value="2026-10-10" required');
    expect(html).toContain('Проверьте дату');
  });

  it('offers accessible icon and color choices and previews escaped category text', () => {
    const html = CategoryAppearance({ name: '<Жильё>', icon: 'home', color: '#2563EB' });
    expect(html).toContain('&lt;Жильё&gt;');
    expect(html).toContain('name="icon" value="home" aria-label="Дом" checked');
    expect(html).toContain('name="color" value="#2563EB" aria-label="Синий" checked');
  });

  it('never renders unvalidated CSS or remote icon identifiers', () => {
    const html = CategoryBadge({ icon: 'https://example.test/icon', color: 'red;position:fixed' });
    expect(html).not.toContain('example.test');
    expect(html).not.toContain('position:fixed');
    expect(html).toContain('#187568');
  });
});
