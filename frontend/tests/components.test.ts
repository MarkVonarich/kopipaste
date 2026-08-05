import { describe, expect, it } from 'vitest';
import { HomeScreen } from '../src/components/HomeScreen';
import { OperationsScreen } from '../src/components/OperationsScreen';
import { TransactionForm } from '../src/components/TransactionForm';
import { ConfirmDialog } from '../src/components/ConfirmDialog';
import { ErrorState, LoadingState, EmptyState, AccessDeniedState } from '../src/components/States';
import { GoalForm, PlansScreen } from '../src/components/PlansScreen';
import { AnalyticsScreen } from '../src/components/AnalyticsScreen';
import { AdditionalMenu, ProfileScreen } from '../src/components/ProfileScreen';

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
      goals: [{
        id: 1,
        title: 'Trip',
        target: '1000.00',
        current: '250.00',
        remaining: '750.00',
        percent: 25,
        currency: 'RUB',
        status: 'active',
        deadline: '2026-12-31',
        strategy: 'deadline',
        frequency: 'monthly',
        reminders_enabled: false,
        next_action: 'Пополнить 250 ₽',
        movement_count: 1,
      }],
      limits: [{
        id: 'category:month:Food',
        kind: 'category',
        title: 'Food',
        category: 'Food',
        scope: 'category',
        amount: '1000.00',
        spent: '750.00',
        remaining: '250.00',
        percent: 75,
        period: 'month',
        status: 'half_used',
        currency: 'RUB',
        alerts_enabled: true,
        workspace_id: 10,
        icon: 'category',
      }],
    });
    expect(html).toContain('Trip');
    const limitsHtml = PlansScreen({
      goals: [],
      limits: [{
        id: 'category:month:Food',
        kind: 'category',
        title: 'Food',
        category: 'Food',
        scope: 'category',
        amount: '1000.00',
        spent: '750.00',
        remaining: '250.00',
        percent: 75,
        period: 'month',
        status: 'half_used',
        currency: 'RUB',
        alerts_enabled: true,
        workspace_id: 10,
        icon: 'category',
      }],
    }, 'limits');
    expect(limitsHtml).toContain('Food');
    expect(limitsHtml).toContain('750 ₽ / 1 000 ₽');
  });

  it('renders analytics charts with local filter controls and radar empty state', () => {
    const html = AnalyticsScreen({
      period: overview.period,
      overview,
      summary: {
        aggregation_available: true,
        available_currencies: ['RUB'],
        currency_groups: { RUB: { income: '1000.00', expense: '350.25', result: '649.75', count: 2 } },
        totals_by_currency: overview.totals_by_currency,
        result_by_currency: { RUB: '649.75' },
      },
      aggregation_available: true,
      available_currencies: ['RUB'],
      radar_available_currencies: ['RUB'],
      selected_currency: null,
      currency_groups: {
        RUB: {
          summary: { income: '1000.00', expense: '350.25', result: '649.75', count: 2 },
          category_structure: { currency: 'RUB', total: '350.25', items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
          time_dynamics: { currency: 'RUB', datasets: [{ kind: 'expense', items: [{ date: '2026-08-04', amount: '350.25', count: 2 }] }, { kind: 'income', items: [{ date: '2026-08-04', amount: '1000.00', count: 2 }] }] }
        }
      },
      category_structure: { type: 'expense', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '350.25', items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] } }, items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
      time_dynamics: { grouping: 'day', currency_groups: { RUB: { currency: 'RUB', datasets: [{ kind: 'expense', items: [{ date: '2026-08-04', amount: '350.25', count: 2 }] }, { kind: 'income', items: [{ date: '2026-08-04', amount: '1000.00', count: 2 }] }] } }, items: [{ date: '2026-08-04', currency: 'RUB', income: '1000.00', expense: '350.25', count: 2 }] },
      radar: {
        type: 'expense',
        currency: 'RUB',
        aggregation_available: true,
        current_period: overview.period,
        previous_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
        metric: 'normalized_category_share_percent',
        max_axes: 6,
        insufficient_data: true,
        explanation: 'Значения нормализованы',
        axes: [],
      },
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' });

    expect(html).toContain('categoryChart');
    expect(html).toContain('dynamicsChart');
    expect(html).toContain('data-chart="category"');
    expect(html).toContain('Недостаточно данных');
  });

  it('renders mixed-currency analytics with explicit currency selector', () => {
    const rubItem = { category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 };
    const eurItem = { category: 'Cafe', currency: 'EUR', total: '40.00', count: 1, share: 100 };
    const html = AnalyticsScreen({
      period: overview.period,
      overview: { ...overview, aggregation_available: false, totals_by_currency: { RUB: overview.totals_by_currency.RUB, EUR: { income: '0.00', expense: '40.00', count: 1 } } },
      aggregation_available: false,
      available_currencies: ['RUB', 'EUR'],
      radar_available_currencies: ['RUB', 'EUR'],
      selected_currency: 'RUB',
      currency_groups: {},
      summary: {
        aggregation_available: false,
        available_currencies: ['RUB', 'EUR'],
        currency_groups: {
          RUB: { income: '1000.00', expense: '350.25', result: '649.75', count: 2 },
          EUR: { income: '0.00', expense: '40.00', result: '-40.00', count: 1 },
        },
        totals_by_currency: { RUB: overview.totals_by_currency.RUB, EUR: { income: '0.00', expense: '40.00', count: 1 } },
        result_by_currency: { RUB: '649.75', EUR: '-40.00' },
      },
      category_structure: {
        type: 'expense',
        top_n: 5,
        currency_groups: {
          RUB: { currency: 'RUB', total: '350.25', items: [rubItem] },
          EUR: { currency: 'EUR', total: '40.00', items: [eurItem] },
        },
        items: [rubItem, eurItem],
      },
      time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
      radar: {
        type: 'expense',
        currency: null,
        aggregation_available: false,
        current_period: overview.period,
        previous_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
        metric: 'normalized_category_share_percent',
        max_axes: 6,
        insufficient_data: true,
        reason: 'mixed_currencies',
        explanation: 'mixed',
        axes: [],
      },
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', categoryCurrency: 'EUR', dynamicsCurrency: 'RUB', radarCurrency: 'RUB' });

    expect(html).toContain('Валюты показаны отдельно. Автоматическая конвертация не выполняется.');
    expect(html).toContain('data-action="chart-currency"');
    expect(html).toContain('40 €');
    expect(html).toContain('Cafe');
    expect(html).not.toContain('Food</span><strong>350,25 ₽');
  });

  it('renders goal preview before confirm save and visible schedule controls', () => {
    const html = GoalForm(null, false, '', {
      strategy: 'deadline',
      frequency: 'monthly',
      remaining_amount: '750.00',
      occurrence_count: 5,
      recommended_amount: '150.00',
      next_occurrence: '2026-09-05',
      projected_completion_date: '2026-12-05',
      required_contributions: null,
      feasible: true,
      reason: null,
      schedule_config: { day: 5 },
      preview_payload_hash: 'preview-hash',
    }, { title: 'Trip', target_amount: '1000.00', current_amount: '250.00', strategy: 'deadline', frequency: 'monthly', day: 5 });

    expect(html).toContain('data-testid="goal-plan-preview"');
    expect(html).toContain('Подтвердить сохранение');
    expect(html).toContain('name="day"');
    expect(html).toContain('Например, 5');
  });

  it('does not render invalid repository profile document links', () => {
    const html = ProfileScreen({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test', links: { privacy: null, terms: null } }, [], 'telegram');
    expect(html).toContain('Документ пока недоступен');
    expect(html).not.toContain('docs/MINI_APP_AUTH.md');
  });

  it('renders full profile sections and hides unsupported add-to-home', () => {
    const html = ProfileScreen({
      theme: 'telegram',
      currency: 'RUB',
      timezone: 'Europe/Moscow',
      version: 'test',
      notifications: {
        morning_enabled: true,
        evening_enabled: true,
        limit_alerts_enabled: true,
        budget_alerts_enabled: true,
        weekly_reports_enabled: true,
        monthly_reports_enabled: true,
        challenge_notifications_enabled: false,
        goal_notifications_enabled: false,
        morning_time: '08:30',
        evening_time: '20:30',
        quiet_hours_enabled: true,
        quiet_hours_start: '22:30',
        quiet_hours_end: '08:00',
        timezone: 'Europe/Moscow',
      },
      premium: { available: false, title: 'Premium', status: 'info_only', description: 'Информационный раздел', features: [] },
      export: { available: true, status: 'ready', presets: ['month'], privacy_note: 'Существующий flow' },
      categories: { expense: [], income: [] },
    }, [], 'telegram');
    expect(html).toContain('Уведомления');
    expect(html).toContain('Premium');
    expect(html).toContain('Экспорт и данные');

    const menu = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, false);
    expect(menu).toContain('Поделиться Finuchet');
    expect(menu).not.toContain('Добавить на главный экран');
  });
});
