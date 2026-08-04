import { describe, expect, it } from 'vitest';
import { HomeScreen } from '../src/components/HomeScreen';
import { OperationsScreen } from '../src/components/OperationsScreen';
import { TransactionForm } from '../src/components/TransactionForm';
import { ConfirmDialog } from '../src/components/ConfirmDialog';
import { ErrorState, LoadingState, EmptyState, AccessDeniedState } from '../src/components/States';
import { PlansScreen } from '../src/components/PlansScreen';
import { ProfileScreen } from '../src/components/ProfileScreen';

const overview = {
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-04' },
  workspace_scope: 10,
  aggregation_available: true,
  totals_by_currency: { RUB: { income: '1000.00', expense: '350.25', count: 2 } },
  recent_operations: [],
};

describe('acceptance components', () => {
  it('renders Home result card and recent operations controls', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html).toContain('Доходы − Расходы');
    expect(html).toContain('+649,75 ₽');
    expect(html).toContain('Последние операции');
    expect(html).toContain('data-action="open-actions"');
    expect(html).toContain('Все операции');
  });

  it('renders multiple currencies without false aggregation', () => {
    const html = HomeScreen({
      ...overview,
      aggregation_available: false,
      totals_by_currency: {
        RUB: { income: '1000.00', expense: '350.00', count: 2 },
        USD: { income: '10.00', expense: '2.00', count: 1 },
      },
    }, [], 'RUB', true);
    expect(html).toContain('Валюты показаны отдельно');
    expect(html).toContain('+650 ₽');
    expect(html).toContain('+8 $');
  });

  it('renders category picker instead of free text category input', () => {
    const html = TransactionForm([
      { name: 'Food', normalized_name: 'food', type: 'Расходы', source: 'custom', operation_count: 1, has_budget: false },
    ], { action: 'create-operation', type: 'Расходы', saving: false });
    expect(html).toContain('<select');
    expect(html).toContain('Food');
    expect(html).not.toContain('name="category" maxlength');
  });

  it('renders operation list empty/load/error/access states', () => {
    expect(OperationsScreen({ items: [], has_more: false, limit: 30, offset: 0, period: overview.period }, true, '')).toContain('Список пуст');
    expect(LoadingState()).toContain('data-state="loading"');
    expect(EmptyState('Нет данных')).toContain('Нет данных');
    expect(ErrorState('Ошибка')).toContain('Повторить');
    expect(AccessDeniedState()).toContain('Нет доступа');
  });

  it('renders delete confirmation before destructive action', () => {
    const html = ConfirmDialog(7, 'Food · 120 ₽');
    expect(html).toContain('Удалить операцию?');
    expect(html).toContain('data-action="confirm-delete"');
    expect(html).toContain('data-action="cancel-delete"');
  });

  it('renders read-only plans with actual goal and limit values', () => {
    const html = PlansScreen({
      goals: [{ title: 'Trip', target: '1000.00', current: '250.00', percent: 25, currency: 'RUB', status: 'active', deadline: '2026-12-31' }],
      limits: [{ category: 'Food', amount: '1000.00', spent: '750.00', remaining: '250.00', percent: 75, period: 'month', status: 'ok', currency: 'RUB' }],
    });
    expect(html).toContain('Trip');
    expect(html).toContain('Food');
    expect(html).toContain('750 ₽ / 1 000 ₽');
  });

  it('does not render invalid repository profile document links', () => {
    const html = ProfileScreen({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test', links: { privacy: null, terms: null } }, [], 'telegram');
    expect(html).toContain('Документ пока недоступен');
    expect(html).not.toContain('docs/MINI_APP_AUTH.md');
  });
});
