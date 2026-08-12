import { describe, expect, it } from 'vitest';

import { ReportsScreen } from '../src/components/ReportsScreen';
import type { FinancialReport } from '../src/types';


function report(overrides: Partial<FinancialReport> = {}): FinancialReport {
  return {
    kind: 'selected',
    period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-12' },
    comparison_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-12' },
    workspace: { scope: 10, name: 'Семья', type: 'group', read_only: false },
    filters: { operation_type: 'all', category: 'Продукты' },
    available_currencies: ['EUR', 'RUB'],
    selected_currency: 'RUB',
    data_state: 'complete',
    summary: { currency: 'RUB', income: '1000.00', expense: '400.00', result: '600.00', operation_count: 4 },
    comparison: {
      income: { current: '1000.00', previous: '0.00', delta: '1000.00', pct: null, state: 'zero_baseline' },
      expense: { current: '400.00', previous: '300.00', delta: '100.00', pct: '33.33', state: 'ok' },
      result: { current: '600.00', previous: '-300.00', delta: '900.00', pct: null, state: 'sign_change' },
      count: 4,
      previous_count: 2,
    },
    structure_type: 'expense',
    categories: [{
      key: 'food', category: 'Продукты', currency: 'RUB', total: '300.00', previous_total: '100.00', delta: '200.00', count: 2, previous_count: 1, share: 75, drillable: true,
      operation_scope: { workspace_id: 10, period: 'current_month', start_date: '2026-08-01', end_date: '2026-08-12', operation_type: 'expense', category: 'all', scope_category: 'Продукты', currency: 'RUB', category_key: 'food', merchant_key: null },
    }, {
      key: '__synthetic_other_category__', category: 'Остальные', currency: 'RUB', total: '100.00', count: 1, share: 25, drillable: false, synthetic: true, operation_scope: null,
    }],
    merchants: [{
      key: 'яндекс лавка', merchant: 'Яндекс Лавка', currency: 'RUB', total: '240.00', count: 2, share: 60, average_check: '120.00', drillable: true,
      operation_scope: { workspace_id: 10, period: 'current_month', start_date: '2026-08-01', end_date: '2026-08-12', operation_type: 'expense', category: 'Продукты', currency: 'RUB', category_key: null, merchant_key: 'яндекс лавка' },
    }, {
      key: '__empty_merchant__', merchant: 'Без описания', currency: 'RUB', total: '160.00', count: 1, share: 40, drillable: false, fallback: true, operation_scope: null,
    }],
    observations: [{ kind: 'category_change', title: 'Заметное изменение по категории', description: 'Продукты', delta: '200.00', currency: 'RUB', drilldown: { workspace_id: 10, period: 'current_month', start_date: '2026-08-01', end_date: '2026-08-12', operation_type: 'expense', category: 'all', scope_category: 'Продукты', currency: 'RUB', category_key: 'food', merchant_key: null } }],
    export_available: false,
    export_reason: 'Для отчёта с несколькими валютами экспорт недоступен.',
    ...overrides,
  };
}


describe('ReportsScreen', () => {
  it('renders selected, completed weekly and completed monthly navigation', () => {
    const html = ReportsScreen(report(), 'selected');
    expect(html).toContain('data-kind="selected"');
    expect(html).toContain('data-kind="completed_week"');
    expect(html).toContain('data-kind="completed_month"');
    expect(html).toContain('Выбранный период');
  });

  it('shows exact period, workspace, operation scope and active currency', () => {
    const html = ReportsScreen(report());
    expect(html).toContain('01.08.2026 - 12.08.2026');
    expect(html).toContain('Семья · Все операции · Продукты');
    expect(html).toContain('data-action="report-currency"');
    expect(html).toContain('<option value="RUB" selected>RUB</option>');
  });

  it('renders only backend-selected currency values and no combined total', () => {
    const html = ReportsScreen(report());
    expect(html).toContain('1 000 ₽');
    expect(html).toContain('400 ₽');
    expect(html).toContain('600 ₽');
    expect(html).not.toContain('1 080');
    expect(html).not.toContain('Общий итог');
  });

  it('preserves zero-baseline and sign-change comparison copy', () => {
    const html = ReportsScreen(report());
    expect(html).toContain('было 0');
    expect(html).toContain('смена знака');
  });

  it('renders an explicit no-data state without manufactured zero totals', () => {
    const html = ReportsScreen(report({ data_state: 'no_data', summary: null, categories: [], merchants: [], observations: [] }));
    expect(html).toContain('Нет операций');
    expect(html).not.toContain('0 ₽');
  });

  it('renders neutral income-only and expense-only notes', () => {
    expect(ReportsScreen(report({ data_state: 'income_only' }))).toContain('В периоде есть только доходы.');
    expect(ReportsScreen(report({ data_state: 'expense_only' }))).toContain('В периоде есть только расходы.');
  });

  it('makes canonical category, merchant and observation scopes drillable', () => {
    const root = document.createElement('div');
    root.innerHTML = ReportsScreen(report());
    const category = root.querySelector<HTMLButtonElement>('[data-action="report-drill"][data-kind="category"]')!;
    const merchant = root.querySelector<HTMLButtonElement>('[data-action="report-drill"][data-kind="merchant"]')!;
    const observation = root.querySelector<HTMLButtonElement>('[data-action="report-drill"][data-kind="observation"]')!;
    expect(JSON.parse(category.dataset.scope || '{}')).toMatchObject({ workspace_id: 10, start_date: '2026-08-01', end_date: '2026-08-12', operation_type: 'expense', currency: 'RUB', category_key: 'food' });
    expect(JSON.parse(merchant.dataset.scope || '{}')).toMatchObject({ merchant_key: 'яндекс лавка', currency: 'RUB' });
    expect(JSON.parse(observation.dataset.scope || '{}')).toMatchObject({ category_key: 'food' });
  });

  it('keeps synthetic and empty-merchant fallback rows non-clickable', () => {
    const root = document.createElement('div');
    root.innerHTML = ReportsScreen(report());
    expect(root.textContent).toContain('Остальные');
    expect(root.textContent).toContain('Без описания');
    expect([...root.querySelectorAll('[data-action="report-drill"]')].some((node) => node.textContent?.includes('Остальные'))).toBe(false);
    expect([...root.querySelectorAll('[data-action="report-drill"]')].some((node) => node.textContent?.includes('Без описания'))).toBe(false);
  });

  it('renders backend average check and bounded observations', () => {
    const html = ReportsScreen(report());
    expect(html).toContain('средний чек 120 ₽');
    expect(html).toContain('Заметное изменение по категории');
  });

  it('only enables exact export and otherwise explains the unavailable state', () => {
    expect(ReportsScreen(report())).toContain('Экспорт не будет подменён другим набором операций.');
    expect(ReportsScreen(report())).not.toContain('data-action="report-export"');
    const exact = ReportsScreen(report({ available_currencies: ['RUB'], export_available: true, export_reason: null }));
    expect(exact).toContain('data-action="report-export"');
  });

  it('uses responsive classes without fixed-width report markup', () => {
    const html = ReportsScreen(report());
    expect(html).toContain('report-kind-selector');
    expect(html).toContain('report-summary');
    expect(html).not.toMatch(/style="[^"]*width:\s*\d+px/);
  });
});
