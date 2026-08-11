import { describe, expect, it } from 'vitest';
import { HomeScreen, InsightDetail } from '../src/components/HomeScreen';
import type { Insight } from '../src/types';
import { OperationsScreen } from '../src/components/OperationsScreen';
import { TransactionForm } from '../src/components/TransactionForm';
import { ConfirmDialog } from '../src/components/ConfirmDialog';
import { ErrorState, LoadingState, EmptyState, AccessDeniedState } from '../src/components/States';
import { CategoryBudgetForm, CategoryDetail, GoalDetail, GoalForm, LimitForm, PlansScreen, ReminderForm } from '../src/components/PlansScreen';
import { AnalyticsScreen, contributionRows, deltaText } from '../src/components/AnalyticsScreen';
import { ActivityCalendarView } from '../src/components/ActivityCalendar';
import { AdditionalMenu, ExportForm, ProfileScreen, QuietHoursForm } from '../src/components/ProfileScreen';

const overview = {
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-04' },
  workspace_scope: 10,
  aggregation_available: true,
  totals_by_currency: { RUB: { income: '1000.00', expense: '350.25', count: 2 } },
  recent_operations: [],
};

function insight(overrides: Partial<Insight> = {}): Insight {
  return {
    id: 'a'.repeat(64),
    type: 'category_contribution',
    detector: 'category_contribution',
    tone: 'warning',
    severity: 'high',
    title: 'Расходы на Продукты выросли на 4 100 ₽',
    summary: '+29% к сопоставимому периоду',
    currency: 'RUB',
    period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-10' },
    comparison_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-10' },
    evidence: [{ kind: 'amount_comparison', label: 'Продукты', current_amount: '18400.00', previous_amount: '14300.00', delta_amount: '4100.00', currency: 'RUB' }],
    actions: [{ type: 'OPEN_CATEGORY', label: 'Посмотреть категорию', params: { category_key: 'продукты', currency: 'RUB' } }],
    feedback: null,
    ...overrides,
  };
}

describe('acceptance components', () => {
  it('renders Home result card and recent operations controls', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html).toContain('Доходы − Расходы');
    expect(html).toContain('+649,75 ₽');
    expect(html).toContain('Последние операции');
    expect(html).toContain('data-action="open-actions"');
    expect(html).toContain('Все операции');
  });

  it('uses neutral Home activity copy for zero streak in historical periods', () => {
    const html = HomeScreen({
      ...overview,
      period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
      activity: {
        start_date: '2026-07-01',
        end_date: '2026-07-31',
        max_count: 0,
        current_streak: 0,
        active_days: 0,
        days_in_period: 31,
        operations_count: 0,
        label: 'Активность',
        days: [{ date: '2026-07-01', count: 0 }],
      },
    }, [], 'RUB', true, { period: 'previous_month', operation_type: 'all', category: 'all' });

    expect(html).toContain('Нет серии без пропусков');
    expect(html).not.toContain('Серия начнётся сегодня');
  });

  it('renders aligned Home action columns and at most three recent operations', () => {
    const recent = [1, 2, 3, 4].map((id) => ({
      id,
      op_date: '2026-08-04',
      type: 'Расходы' as const,
      category: `Food ${id}`,
      amount: '100.00',
      amount_text: '100 ₽',
      currency: 'RUB',
      description: 'Lunch',
      workspace_id: 10,
    }));
    const html = HomeScreen({
      ...overview,
      challenge: { key: 'daily', title: 'Две записи за день', description: 'Запишите две реальные операции за сегодня.', progress: 1, target: 2, completed: false, cta_label: 'Добавить', period_key: '2026-08-04' },
      focus: { kind: 'limit', title: 'Food', description: 'Лимит почти исчерпан', target_mode: 'limits', percent: 90 },
      insight: insight({ tone: 'positive', title: 'Расходы ниже', summary: 'На 10% меньше' }),
    }, recent, 'RUB', true);
    const incomeColumn = html.slice(html.indexOf('data-testid="income-column"'), html.indexOf('data-testid="expense-column"'));
    const expenseColumn = html.slice(html.indexOf('data-testid="expense-column"'), html.indexOf('data-testid="smart-home-grid"'));
    expect(incomeColumn).toContain('Доходы');
    expect(incomeColumn).toContain('data-kind="income"');
    expect(expenseColumn).toContain('Расходы');
    expect(expenseColumn).toContain('data-kind="expense"');
    expect(html).toContain('Челлендж · Сегодня');
    expect(html).toContain('2 записи за день');
    expect(html).toContain('1/2');
    expect(html).toContain('Запишите 2 операции сегодня.');
    expect(html).not.toContain('реальные операции');
    expect(html).toContain('Фокус');
    expect(html).toContain('Инсайт периода');
    expect(html).not.toContain('Food 4');
  });

  it('renders smart Home carousel dots inside card shells without nested buttons', () => {
    const html = HomeScreen({
      ...overview,
      challenges: [
        { key: 'daily', title: 'Сегодня', description: 'Запишите операцию', progress: 1, target: 2, completed: false, cta_label: 'Добавить', period_type: 'day', period_key: '2026-08-04' },
        { key: 'weekly', title: 'Неделя', description: 'Пять дней', progress: 2, target: 5, completed: false, cta_label: 'Добавить', period_type: 'week', period_key: '2026-08-03' },
      ],
      focus_items: [
        { kind: 'limit', title: 'Food', description: 'Лимит', target_mode: 'limits', percent: 60 },
        { kind: 'goal', title: 'Trip', description: 'Цель', target_mode: 'goals', percent: 25 },
      ],
      reminders: [
        { state: 'upcoming', id: 1, title: 'Internet', event_date: '2026-08-10', status_text: 'Скоро', overdue_days: 0 },
        { state: 'empty', title: 'Нет событий', status_text: 'Добавьте напоминание', overdue_days: 0 },
      ],
      insight: insight({ tone: 'neutral', title: 'Период', summary: 'Есть данные' }),
    }, [], 'RUB', true);

    expect(html).toContain('class="smart-card home-carousel"');
    expect(html.match(/class="smart-card home-carousel"/g)).toHaveLength(3);
    expect(html).toContain('data-action="carousel-dot"');
    expect(html).toContain('class="smart-card insight-card');
    expect(html).not.toContain('<button class="smart-card"');
  });

  it('renders compact Home reminder copy in all reminder states', () => {
    const empty = HomeScreen({
      ...overview,
      reminders: [{ state: 'empty', title: 'Нет запланированных событий', status_text: 'Добавьте напоминание в боте.', overdue_days: 0 }],
    }, [], 'RUB', true);
    const active = HomeScreen({
      ...overview,
      reminders: [{ state: 'upcoming', id: 9, title: 'ChatGPT', amount_text: '1 990 ₽', event_date: '19 августа', status_text: 'Через 10 дней', overdue_days: 0 }],
    }, [], 'RUB', true);

    expect(empty).toContain('Напоминание');
    expect(empty).toContain('Нет событий');
    expect(empty).toContain('Добавьте в Планах.');
    expect(empty).not.toContain('Ближайшее напоминание');
    expect(empty).not.toContain('Нет запланированных событий');
    expect(active).toContain('Напоминание');
    expect(active).toContain('ChatGPT · 1 990 ₽');
    expect(active).toContain('19 августа');
    expect(active).toContain('Через 10 дней');
    expect(active).not.toContain('Ближайшее напоминание');
  });

  it('renders the current Home reminder slide id on the action button', () => {
    const second = HomeScreen({
      ...overview,
      reminders: [
        { state: 'upcoming', id: 10, title: 'A', event_date: '2026-08-10', status_text: 'A status', overdue_days: 0 },
        { state: 'overdue', id: 20, title: 'B', event_date: '2026-08-11', status_text: 'B status', overdue_days: 1 },
        { state: 'upcoming', id: 30, title: 'C', event_date: '2026-08-12', status_text: 'C status', overdue_days: 0 },
      ],
    }, [], 'RUB', true, { period: 'current_month', operation_type: 'all', category: 'all' }, { challenge: 0, focus: 0, reminder: 1 });
    const third = HomeScreen({
      ...overview,
      reminders: [
        { state: 'upcoming', id: 10, title: 'A', event_date: '2026-08-10', status_text: 'A status', overdue_days: 0 },
        { state: 'overdue', id: 20, title: 'B', event_date: '2026-08-11', status_text: 'B status', overdue_days: 1 },
        { state: 'upcoming', id: 30, title: 'C', event_date: '2026-08-12', status_text: 'C status', overdue_days: 0 },
      ],
    }, [], 'RUB', true, { period: 'current_month', operation_type: 'all', category: 'all' }, { challenge: 0, focus: 0, reminder: 2 });

    expect(second).toContain('data-action="home-reminder" type="button" data-id="20" data-state="overdue"');
    expect(second).toContain('<strong>B</strong>');
    expect(second).not.toContain('<strong>A</strong>');
    expect(third).toContain('data-action="home-reminder" type="button" data-id="30" data-state="upcoming"');
    expect(third).toContain('<strong>C</strong>');
  });

  it('renders focus projected risk without replacing actual progress', () => {
    const html = HomeScreen({
      ...overview,
      focus: { kind: 'limit', title: 'Food', description: 'При текущем темпе лимит может быть превышен.', target_mode: 'limits', percent: 60, projected_percent: 145, severity: 'high', status: 'warning' },
    }, [], 'RUB', true);

    expect(html).toContain('aria-valuenow="60"');
    expect(html).toContain('Лимит под риском');
    expect(html).toContain('Прогноз: 145%');
    expect(html).not.toContain('При текущем темпе лимит может быть превышен.');
    expect(html).not.toContain('Прогноз к концу периода');
  });

  it('uses compact Financial Result comparison text on Home', () => {
    const html = HomeScreen({
      ...overview,
      insight: insight({ title: 'Расходы выше', summary: 'На 564% больше, чем в прошлом сопоставимом периоде.' }),
    }, [], 'RUB', true);

    expect(html).toContain('На 564% больше прошлого периода');
    expect(html).not.toContain('в прошлом сопоставимом периоде');
  });

  it('renders up to three structured insights and keeps Home clean without them', () => {
    const clean = HomeScreen(overview, [], 'RUB', true);
    const populated = HomeScreen({
      ...overview,
      insights: [insight(), insight({ id: 'b'.repeat(64), type: 'limit_pace', detector: 'limit_pace', title: 'Рестораны близки к лимиту', summary: 'Использовано 82%' }), insight({ id: 'c'.repeat(64), type: 'merchant_frequency', detector: 'merchant_frequency', title: 'В Лавке стало больше покупок', summary: '12 вместо 7' })],
    }, [], 'RUB', true);

    expect(clean).not.toContain('Инсайт периода');
    expect(populated.match(/data-action="home-insight"/g)).toHaveLength(3);
    expect(populated).toContain('Продукты выросли');
    expect(populated).toContain('Рестораны близки');
    expect(populated).toContain('12 вместо 7');
  });

  it('renders insight evidence, contextual actions, and feedback controls', () => {
    const html = InsightDetail(insight({
      evidence: [
        { kind: 'amount_comparison', label: 'Продукты', current_amount: '18400.00', previous_amount: '14300.00', currency: 'RUB' },
        { kind: 'merchant_contribution', label: 'Яндекс Лавка', delta_amount: '2800.00', currency: 'RUB', share_pct: 63, current_count: 12, previous_count: 7 },
      ],
      actions: [
        { type: 'OPEN_MERCHANT', label: 'Посмотреть Лавку', params: { merchant_key: 'яндекс лавка' } },
        { type: 'OPEN_OPERATIONS', label: 'Посмотреть операции', params: { merchant_key: 'яндекс лавка' } },
        { type: 'CREATE_LIMIT', label: 'Установить лимит', params: { category: 'Продукты' } },
      ],
    }));

    expect(html).toContain('18 400 ₽');
    expect(html).toContain('14 300 ₽');
    expect(html).toContain('Яндекс Лавка');
    expect(html).toContain('12 покупок вместо 7');
    expect(html).toContain('data-action="insight-action"');
    expect(html).toContain('data-feedback="useful"');
    expect(html).toContain('data-feedback="not_useful"');
  });

  it('formats signed analytics deltas without adding a false percentage sign', () => {
    expect(deltaText({ delta: '150.00', pct: '30.00', state: 'ok' }, 'RUB')).toBe('+150 ₽ · +30,00%');
    expect(deltaText({ delta: '-250.00', pct: '-25.00', state: 'ok' }, 'RUB')).toBe('-250 ₽ · -25,00%');
    expect(deltaText({ delta: '850.00', pct: null, state: 'sign_change' }, 'RUB')).toBe('смена знака · +850 ₽');
    expect(deltaText({ delta: '100.00', pct: null, state: 'zero_baseline' }, 'RUB')).toBe('было 0 · +100 ₽');
  });

  it('scales contribution bars by visible max absolute delta', () => {
    const html = contributionRows([
      { category: 'Small', currency: 'RUB', total: '100.00', previous_total: '0.00', delta: '100.00', count: 1, share: 10 },
      { category: 'Large', currency: 'RUB', total: '10000.00', previous_total: '0.00', delta: '10000.00', count: 1, share: 90 },
    ], 'RUB');

    expect(html).toContain('width:8%');
    expect(html).toContain('width:100%');
    expect(html).toContain('Small');
    expect(html).toContain('Large');
  });

  it('renders synthetic and fallback analytics rows without drill actions', () => {
    const categoryHtml = AnalyticsScreen({
      period: overview.period,
      overview,
      aggregation_available: true,
      available_currencies: ['RUB'],
      radar_available_currencies: ['RUB'],
      selected_currency: 'RUB',
      currency_groups: {},
      summary: {
        aggregation_available: true,
        available_currencies: ['RUB'],
        currency_groups: { RUB: { income: '0.00', expense: '1500.00', result: '-1500.00', count: 7 } },
        totals_by_currency: { RUB: { income: '0.00', expense: '1500.00', count: 7 } },
        result_by_currency: { RUB: '-1500.00' },
      },
      category_structure: { type: 'expense', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '1500.00', items: [
        { key: 'food', category: 'Food', currency: 'RUB', total: '1000.00', previous_total: '800.00', delta: '200.00', count: 5, share: 67, drillable: true },
        { key: '__synthetic_other_category__', category: 'Остальные', currency: 'RUB', total: '500.00', previous_total: '0.00', delta: '500.00', count: 2, share: 33, synthetic: true, drillable: false },
      ] } }, items: [] },
      merchant_structure: { type: 'expense', dimension: 'merchant', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '1500.00', items: [
        { key: '__empty_merchant__', merchant: 'Без описания', currency: 'RUB', total: '500.00', previous_total: '0.00', delta: '500.00', count: 2, share: 33, fallback: true, drillable: false },
        { key: '__synthetic_other_merchant__', merchant: 'Остальные', currency: 'RUB', total: '300.00', previous_total: '0.00', delta: '300.00', count: 1, share: 20, synthetic: true, drillable: false },
      ] } }, items: [] },
      change_contribution: { type: 'expense', currency_groups: { RUB: { currency: 'RUB', type: 'expense', current_total: '1500.00', previous_total: '800.00', total_delta: '700.00', reconciles: true, items: [
        { key: 'food', category: 'Food', currency: 'RUB', total: '1000.00', previous_total: '800.00', delta: '200.00', count: 5, share: 67, drillable: true },
        { key: '__synthetic_other_contribution__', category: 'Остальные', currency: 'RUB', total: '500.00', previous_total: '0.00', delta: '500.00', count: 2, share: 33, synthetic: true, drillable: false },
      ] } }, items: [] },
      time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
      radar: { type: 'expense', currency: 'RUB', current_period: overview.period, previous_period: overview.period, metric: 'absolute_amount', max_axes: 6, scale: { max: '0.00', step: '0.00', ticks: [] }, insufficient_data: true, explanation: '', axes: [] },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 0, days: [] },
      search: { query: '', items: [] },
      selected_detail: null,
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', analyticsCurrency: 'RUB' }, { period: 'current_month', operation_type: 'all', category: 'all' });

    expect(categoryHtml).toContain('data-action="analytics-drill" data-kind="category" data-value="Food"');
    expect(categoryHtml).not.toContain('data-value="Остальные"');
    expect(categoryHtml).not.toContain('data-kind="merchant" data-value="Без описания"');
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

  it('renders categories as a simple first-level list and keeps details in the sheet view', () => {
    const category = {
      name: 'Продукты', normalized_name: 'продукты', token: 'продукты', type: 'Расходы' as const,
      source: 'custom', operation_count: 12, has_budget: true, protected: false,
      references: { operations: 12, drafts: 0, category_limits: 1, category_budget_groups: 2, reminders: 1, aliases: 3, ml_observations: 4, total: 23 },
    };
    const list = PlansScreen({ goals: [], limits: [], categories: [category], category_type: 'expense' }, 'categories', true);

    expect(list).toContain('data-action="category-open"');
    expect(list).toContain('Продукты');
    expect(list).not.toContain('Автокатегоризация');
    expect(list).not.toContain('12 операций');

    const detail = CategoryDetail(category, 'expense', true);
    expect(detail).toContain('Автокатегоризация');
    expect(detail).toContain('data-action="category-rename"');
    expect(detail).toContain('data-action="category-delete"');
    expect(CategoryDetail(category, 'expense', false)).not.toContain('data-action="category-delete"');
  });

  it('renders a goal archive entry, archived goals, and protected destructive actions', () => {
    const archived = {
      id: 7, title: 'Отпуск', target: '100000.00', current: '25000.00', remaining: '75000.00', percent: 25,
      currency: 'RUB', status: 'archived', deadline: null, strategy: 'none', frequency: 'none', reminders_enabled: false,
      next_action: 'Цель в архиве', movement_count: 2,
    };
    const active = PlansScreen({ goals: [], archived_goals: [archived], limits: [] }, 'goals', true);
    const archive = PlansScreen({ goals: [], archived_goals: [archived], limits: [] }, 'goals', true, 'archive');
    const detail = GoalDetail(archived, true);

    expect(active).toContain('data-action="goal-archive-open"');
    expect(archive).toContain('Архив целей');
    expect(archive).toContain('Отпуск');
    expect(detail).toContain('Восстановить');
    expect(detail).toContain('Удалить навсегда');
    expect(GoalDetail(archived, false)).not.toContain('Удалить навсегда');
  });

  it('opens limit forms with correct scopes and preserves edit currency', () => {
    const general = LimitForm(null, [{ name: 'Food' }], false, '', 'all_expenses');
    const category = LimitForm(null, [{ name: 'Food' }], false, '', 'category');
    const edit = LimitForm({
      id: 'general:1',
      kind: 'general',
      title: 'All',
      category: null,
      scope: 'all_expenses',
      amount: '1000.00',
      spent: '0.00',
      remaining: '1000.00',
      percent: 0,
      period: 'month',
      status: 'normal',
      currency: 'EUR',
      alerts_enabled: true,
      workspace_id: 10,
      icon: 'wallet',
    }, []);

    expect(general).toContain('<option value="all_expenses" selected>');
    expect(general).toContain('data-field="limit-category" hidden');
    expect(category).toContain('<option value="category" selected>');
    expect(category).not.toContain('data-field="limit-category" hidden');
    expect(edit).toContain('name="currency" value="EUR"');

    const card = PlansScreen({
      goals: [],
      limits: [],
      general_limits: [{
        id: 'general:1',
        kind: 'general',
        title: 'All',
        category: null,
        scope: 'all_expenses',
        amount: '1000.00',
        spent: '0.00',
        remaining: '1000.00',
        percent: 0,
        period: 'month',
        status: 'normal',
        currency: 'EUR',
        alerts_enabled: true,
        enabled: true,
        workspace_id: 10,
        icon: 'wallet',
      }],
    }, 'limits', true);
    expect(card).toContain('data-action="limit-toggle"');
    expect(card).toContain('Выключить');
  });

  it('renders category budget currency selector with existing edit currency', () => {
    const html = CategoryBudgetForm({
      id: 2,
      kind: 'category_budget',
      title: 'Food',
      amount: '300.00',
      currency: 'EUR',
      spent: '0.00',
      remaining: '300.00',
      percent: 0,
      period: 'month',
      status: 'normal',
      categories: ['Food'],
      enabled: true,
      alerts_enabled: true,
      workspace_id: 10,
    }, [{ name: 'Food' }], false, '', ['RUB', 'EUR'], 'RUB');

    expect(html).toContain('name="currency"');
    expect(html).toContain('<option value="EUR" selected>');
  });

  it('renders reminder form with switched category options without losing draft fields', () => {
    const html = ReminderForm(null, [{ name: 'Salary' }], false, '', {
      title: 'Payroll',
      amount: '1000',
      category: 'Salary',
      rem_type: 'income',
      event_date: '2026-08-20',
      repeat_rule: 'monthly',
      notify_days_before: '2',
      is_active: true,
    });

    expect(html).toContain('value="Payroll"');
    expect(html).toContain('value="1000"');
    expect(html).toContain('<option value="Salary" selected>');
    expect(html).toContain('<option value="income" selected>');
    expect(html).toContain('value="2026-08-20"');
  });

  it('renders analytics charts with one currency context and no primary radar section', () => {
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
      previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-04' },
      currency_groups: {
        RUB: {
          summary: { income: '1000.00', expense: '350.25', result: '649.75', count: 2 },
          category_structure: { currency: 'RUB', total: '350.25', items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
          merchant_structure: { currency: 'RUB', total: '350.25', items: [{ merchant: 'Lavka', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
          time_dynamics: { currency: 'RUB', datasets: [{ kind: 'expense', items: [{ date: '2026-08-04', amount: '350.25', count: 2 }] }, { kind: 'income', items: [{ date: '2026-08-04', amount: '1000.00', count: 2 }] }, { kind: 'result', items: [{ date: '2026-08-04', amount: '649.75', count: 2 }] }] }
        }
      },
      overview_metrics: { RUB: { income: { current: '1000.00', previous: '800.00', delta: '200.00', pct: '25.00', state: 'ok' }, expense: { current: '350.25', previous: '300.00', delta: '50.25', pct: '16.75', state: 'ok' }, result: { current: '649.75', previous: '500.00', delta: '149.75', pct: '29.95', state: 'ok' }, count: 2, previous_count: 1 } },
      category_structure: { type: 'expense', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '350.25', items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] } }, items: [{ category: 'Food', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
      merchant_structure: { type: 'expense', dimension: 'merchant', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '350.25', items: [{ merchant: 'Lavka', currency: 'RUB', total: '350.25', count: 2, share: 70 }] } }, items: [{ merchant: 'Lavka', currency: 'RUB', total: '350.25', count: 2, share: 70 }] },
      change_contribution: { type: 'expense', currency_groups: { RUB: { currency: 'RUB', type: 'expense', current_total: '350.25', previous_total: '300.00', total_delta: '50.25', reconciles: true, items: [{ category: 'Food', currency: 'RUB', total: '350.25', previous_total: '300.00', delta: '50.25', count: 2, share: 100 }] } }, items: [] },
      time_dynamics: { grouping: 'day', currency_groups: { RUB: { currency: 'RUB', datasets: [{ kind: 'expense', items: [{ date: '2026-08-04', amount: '350.25', count: 2 }] }, { kind: 'income', items: [{ date: '2026-08-04', amount: '1000.00', count: 2 }] }, { kind: 'result', items: [{ date: '2026-08-04', amount: '649.75', count: 2 }] }] } }, items: [{ date: '2026-08-04', currency: 'RUB', income: '1000.00', expense: '350.25', result: '649.75', count: 2 }] },
      radar: {
        type: 'expense',
        currency: 'RUB',
        aggregation_available: true,
        current_period: overview.period,
        previous_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
        metric: 'absolute_amount',
        max_axes: 6,
        scale: { max: '0.00', step: '0.00', ticks: ['0.00'] },
        insufficient_data: true,
        explanation: 'Недостаточно данных',
        axes: [],
      },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 2, days: [{ date: '2026-08-01', count: 0 }, { date: '2026-08-02', count: 1 }, { date: '2026-08-03', count: 2 }, { date: '2026-08-04', count: 1 }] },
      search: { query: '', items: [] },
      selected_detail: null,
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' }, { period: 'current_month', operation_type: 'all', category: 'all' });

    expect(html).toContain('categoryChart');
    expect(html).toContain('dynamicsChart');
    expect(html).toContain('Финрезультат');
    expect(html).toContain('data-chart="category"');
    expect(html).not.toContain('Radar');
    expect(html).toContain('data-action="export-open"');
    expect(html).toContain('Открыть экспорт');
    expect(html).toContain('Что изменилось');
    expect(html).toContain('data-action="analytics-drill"');
  });

  it('keeps legacy radar data out of primary Analytics and keeps activity out', () => {
    const longCategory = 'Фиксированные расходы на коммунальные услуги';
    const html = AnalyticsScreen({
      period: overview.period,
      overview,
      aggregation_available: true,
      available_currencies: ['RUB'],
      radar_available_currencies: ['RUB'],
      selected_currency: 'RUB',
      currency_groups: {},
      previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-04' },
      summary: {
        aggregation_available: true,
        available_currencies: ['RUB'],
        currency_groups: { RUB: { income: '0.00', expense: '22000.00', result: '-22000.00', count: 4 } },
        totals_by_currency: { RUB: { income: '0.00', expense: '22000.00', count: 4 } },
        result_by_currency: { RUB: '-22000.00' },
      },
      category_structure: { type: 'expense', top_n: 5, currency_groups: {}, items: [] },
      merchant_structure: { type: 'expense', dimension: 'merchant', top_n: 5, currency_groups: {}, items: [] },
      change_contribution: { type: 'expense', currency_groups: {}, items: [] },
      time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
      radar: {
        type: 'expense',
        currency: 'RUB',
        aggregation_available: true,
        current_period: overview.period,
        previous_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
        metric: 'absolute_amount',
        max_axes: 6,
        scale: { max: '20000.00', step: '5000.00', ticks: ['0.00', '5000.00', '10000.00', '15000.00', '20000.00'] },
        insufficient_data: false,
        explanation: 'Radar сравнивает абсолютные суммы категорий в выбранной валюте.',
        axes: [
          { category: longCategory, current_amount: '15000.00', previous_amount: '12000.00' },
          { category: 'Заведения', current_amount: '7000.00', previous_amount: '10000.00' },
          { category: 'Такси', current_amount: '3000.00', previous_amount: '2500.00' },
        ],
      },
      activity_calendar: { start_date: '2026-07-31', end_date: '2026-08-03', max_count: 4, days: [{ date: '2026-07-31', count: 0 }, { date: '2026-08-01', count: 2 }, { date: '2026-08-02', count: 4 }, { date: '2026-08-03', count: 1 }] },
      search: { query: '', items: [] },
      selected_detail: null,
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' }, { period: 'current_month', operation_type: 'expense', category: 'all' });

    expect(html).toContain('<details class="chart-details">');
    expect(html).not.toContain('<svg class="radar"');
    expect(html).not.toContain(`<title>${longCategory}: текущий период 15000.00 RUB`);
    expect(html).not.toContain('Текущий период');
    expect(html).not.toContain('Количество операций по дням');
    expect(html).not.toContain('activity-calendar');
  });

  it('aligns activity calendar Monday and Friday starts', () => {
    const monday = ActivityCalendarView({ start_date: '2026-08-03', end_date: '2026-08-04', max_count: 1, days: [{ date: '2026-08-03', count: 1 }, { date: '2026-08-04', count: 0 }] });
    const friday = ActivityCalendarView({ start_date: '2026-08-07', end_date: '2026-08-08', max_count: 1, days: [{ date: '2026-08-07', count: 1 }, { date: '2026-08-08', count: 0 }] });

    expect(monday).toContain('3 августа — 1 операций');
    expect(monday).toContain('data-weekday-row="1"');
    expect(friday).toContain('7 августа — 1 операций');
    expect(friday).toContain('data-weekday-row="5"');
    const beforeFriday = friday.slice(0, friday.indexOf('7 августа'));
    expect((beforeFriday.match(/activity-cell empty/g) || [])).toHaveLength(4);
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
      previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-04' },
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
      merchant_structure: {
        type: 'expense',
        dimension: 'merchant',
        top_n: 5,
        currency_groups: { EUR: { currency: 'EUR', total: '40.00', items: [{ merchant: 'Cafe', currency: 'EUR', total: '40.00', count: 1, share: 100 }] } },
        items: [{ merchant: 'Cafe', currency: 'EUR', total: '40.00', count: 1, share: 100 }],
      },
      change_contribution: { type: 'expense', currency_groups: { EUR: { currency: 'EUR', type: 'expense', current_total: '40.00', previous_total: '0.00', total_delta: '40.00', reconciles: true, items: [eurItem] } }, items: [] },
      time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
      radar: {
        type: 'expense',
        currency: null,
        aggregation_available: false,
        current_period: overview.period,
        previous_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
        metric: 'absolute_amount',
        max_axes: 6,
        scale: { max: '0.00', step: '0.00', ticks: ['0.00'] },
        insufficient_data: true,
        reason: 'mixed_currencies',
        explanation: 'mixed',
        axes: [],
      },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 0, days: [] },
      search: { query: '', items: [] },
      selected_detail: null,
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', analyticsCurrency: 'EUR' }, { period: 'current_month', operation_type: 'all', category: 'all' });

    expect(html).toContain('Валюты показаны отдельно. Автоматическая конвертация не выполняется.');
    expect(html).toContain('data-action="chart-currency"');
    expect(html).toContain('data-chart="analytics"');
    expect(html).toContain('40 €');
    expect(html).toContain('Cafe');
    expect(html).not.toContain('Food</span><strong>350,25 ₽');
  });

  it('renders analytics search, merchant drilldown and operation navigation', () => {
    const html = AnalyticsScreen({
      period: overview.period,
      previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-04' },
      overview,
      aggregation_available: true,
      available_currencies: ['RUB'],
      radar_available_currencies: ['RUB'],
      selected_currency: 'RUB',
      currency_groups: {},
      summary: {
        aggregation_available: true,
        available_currencies: ['RUB'],
        currency_groups: { RUB: { income: '0.00', expense: '500.00', result: '-500.00', count: 2 } },
        totals_by_currency: { RUB: { income: '0.00', expense: '500.00', count: 2 } },
        result_by_currency: { RUB: '-500.00' },
      },
      overview_metrics: { RUB: { income: { current: '0.00', previous: '0.00', delta: '0.00', pct: null, state: 'empty_previous' }, expense: { current: '500.00', previous: '300.00', delta: '200.00', pct: '66.67', state: 'ok' }, result: { current: '-500.00', previous: '-300.00', delta: '-200.00', pct: '66.67', state: 'ok' }, count: 2, previous_count: 1 } },
      category_structure: { type: 'expense', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '500.00', items: [{ category: 'Food', currency: 'RUB', total: '500.00', previous_total: '300.00', delta: '200.00', count: 2, share: 100 }] } }, items: [] },
      merchant_structure: { type: 'expense', dimension: 'merchant', top_n: 5, currency_groups: { RUB: { currency: 'RUB', total: '500.00', items: [{ key: 'lavka', merchant: 'Lavka', currency: 'RUB', total: '500.00', previous_total: '300.00', delta: '200.00', count: 2, share: 100, raw_aliases: ['Lavka', 'LAVKA'] }] } }, items: [] },
      change_contribution: { type: 'expense', currency_groups: { RUB: { currency: 'RUB', type: 'expense', current_total: '500.00', previous_total: '300.00', total_delta: '200.00', reconciles: true, items: [{ category: 'Food', currency: 'RUB', total: '500.00', previous_total: '300.00', delta: '200.00', count: 2, share: 100 }] } }, items: [] },
      time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
      radar: { type: 'expense', currency: 'RUB', aggregation_available: true, current_period: overview.period, previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-04' }, metric: 'absolute_amount', max_axes: 6, scale: { max: '0.00', step: '0.00', ticks: ['0.00'] }, insufficient_data: true, explanation: 'Недостаточно данных', axes: [] },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 0, days: [] },
      search: { query: 'Lav', items: [{ kind: 'merchant', title: 'Lavka', subtitle: '2 операций', currency: 'RUB', amount: '500.00', params: { detail_kind: 'merchant', detail_value: 'lavka', detail_currency: 'RUB' } }, { kind: 'operation', title: 'Lavka', subtitle: 'Food', currency: 'RUB', amount: '250.00', operation_id: 7 }] },
      selected_detail: {
        kind: 'merchant',
        title: 'Lavka',
        merchant_key: 'lavka',
        currency: 'RUB',
        operation_type: 'expense',
        total: '500.00',
        operation_count: 2,
        previous_operation_count: 1,
        previous_total: '300.00',
        delta: '200.00',
        pct: '66.67',
        state: 'ok',
        average_check: '250.00',
        previous_average_check: '300.00',
        frequency_delta: 1,
        frequency_pct: '100.00',
        average_check_delta: '-50.00',
        average_check_pct: '-16.67',
        merchant_share_of_category: '80.00',
        merchant_share_of_total: '25.00',
        primary_category: { category_key: 'food', category: 'Food', category_total: '625.00', merchant_total: '500.00', merchant_count: 2, merchant_share_of_category: '80.00' },
        baseline: { method: 'trailing_median', periods_used: 3, amount: '450.00', count: '2.00', average_check: '225.00', sufficient_data: true },
        raw_aliases: ['Lavka', 'LAVKA'],
        operations: [{ id: 7, op_date: '2026-08-02', type: 'Расходы', category: 'Food', amount: '250.00', amount_text: '250 ₽', currency: 'RUB', description: 'Lavka', workspace_id: 10 }],
        operation_scope: { workspace_id: 10, period: 'custom', start_date: '2026-08-01', end_date: '2026-08-04', operation_type: 'expense', category: 'all', currency: 'RUB', merchant_key: 'lavka' },
      },
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', analyticsCurrency: 'RUB', structureMode: 'merchant', search: 'Lav', detailKind: 'merchant', detailValue: 'Lavka', detailCurrency: 'RUB', detailOperationType: 'expense' }, { period: 'current_month', operation_type: 'all', category: 'all' });

    expect(html).toContain('Категория, магазин или операция');
    expect(html).toContain('data-action="analytics-structure" data-mode="merchant"');
    expect(html).toContain('data-action="analytics-drill" data-kind="merchant" data-value="lavka"');
    expect(html).toContain('Средний чек');
    expect(html).toContain('Частота');
    expect(html).toContain('Учтённые записи');
    expect(html).toContain('+200 ₽ · +66,67%');
    expect(html).toContain('data-action="operation-detail" data-id="7"');
    expect(html).toContain('data-action="analytics-back"');
    expect(html).toContain('data-action="analytics-open-operations"');
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

  it('keeps existing goal balance ledger-derived during edit', () => {
    const goal = {
      id: 7, title: 'Trip', target: '1000.00', current: '250.00', remaining: '750.00', percent: 25,
      currency: 'RUB', status: 'active', deadline: '2026-12-31', strategy: 'deadline', frequency: 'monthly',
      schedule_config: { day: 5 }, reminders_enabled: false, next_action: 'Пополнить', movement_count: 1,
    };
    const html = GoalForm(goal);

    expect(html).toContain('Прогресс меняется через пополнение цели');
    expect(html).not.toContain('name="current_amount"');
  });

  it('does not render invalid repository profile document links', () => {
    const html = ProfileScreen({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test', links: { privacy: null, terms: null } }, [], 'telegram', 'legal');
    expect(html).toContain('Документ пока недоступен');
    expect(html).not.toContain('docs/MINI_APP_AUTH.md');
  });

  it('renders accordion profile sections and hides closed controls', () => {
    const html = ProfileScreen({
      theme: 'telegram',
      preferred_name: 'Мария',
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
    }, [], 'telegram', 'user');
    expect(html).toContain('aria-expanded="true" aria-controls="profile-panel-user"');
    expect(html).toContain('id="profile-panel-notifications" hidden');
    expect(html).toContain('Как к вам обращаться?');
    expect(html).toContain('Уведомления');
    expect(html).toContain('Premium');
    expect(html).not.toContain('Экспорт и данные');
    expect(html).toContain('profile-name-open');
  });

  it('keeps notification switches in settings and quiet hours as editor row', () => {
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
        challenge_notifications_enabled: true,
        goal_notifications_enabled: false,
        morning_time: '08:30',
        evening_time: '20:30',
        quiet_hours_enabled: true,
        quiet_hours_start: '22:30',
        quiet_hours_end: '08:00',
        timezone: 'Europe/Moscow',
      },
      categories: { expense: [], income: [] },
      premium: { available: false, title: 'Premium', status: 'info_only', description: 'Информационный раздел', features: [] },
      export: { available: true, status: 'ready', presets: ['month'], privacy_note: 'Существующий flow' },
    }, [], 'telegram', 'notifications');
    expect(html).toContain('role="switch"');
    expect(html).toContain('data-key="daily"');
    expect(html).toContain('data-key="plans"');
    expect(html).toContain('data-key="reports"');
    expect(html).not.toContain('data-key="challenges"');
    expect(html).toContain('data-action="quiet-hours-open"');
    expect(html).not.toContain('data-action="notification-quiet"');

    const menu = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, 'unsupported');
    expect(menu).toContain('Поделиться Finuchet');
    expect(menu).not.toContain('Добавить на главный экран');
  });

  it('shows custom export date fields immediately and preserves draft dates', () => {
    const html = ExportForm({ preset: 'custom', start_date: '2026-08-01', end_date: '2026-08-09' }, undefined);

    expect(html).toContain('class="custom-export-fields" >');
    expect(html).toContain('value="2026-08-01"');
    expect(html).toContain('value="2026-08-09"');
  });

  it('renders platform-aware Add to Home unsupported fallback copy', () => {
    const ios = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, 'unsupported', 'ios');
    const android = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, 'unsupported', 'android');
    const desktop = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, 'unsupported', 'tdesktop');
    const added = AdditionalMenu({ theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test' }, 'added', 'ios');

    expect(ios).toContain('Обновите Telegram');
    expect(android).toContain('Telegram для Android');
    expect(desktop).toContain('поддерживаемых мобильных версиях Telegram');
    expect(added).toContain('Добавлено');
    expect(added).not.toContain('data-action="add-to-home"');
  });

  it('shows Telegram display name before preferred name, then returns to it after clear', () => {
    const telegramName = ProfileScreen({
      theme: 'telegram',
      preferred_name: null,
      display_name: 'Максим',
      currency: 'RUB',
      timezone: 'Europe/Moscow',
      version: 'test',
    }, [], 'telegram', 'user');
    expect(telegramName).toContain('Как к вам обращаться?');
    expect(telegramName).toContain('Максим');

    const preferred = ProfileScreen({
      theme: 'telegram',
      preferred_name: 'Леонель Месси',
      display_name: 'Леонель Месси',
      currency: 'RUB',
      timezone: 'Europe/Moscow',
      version: 'test',
    }, [], 'telegram', 'user');
    expect(preferred).toContain('Леонель Месси');

    const cleared = ProfileScreen({
      theme: 'telegram',
      preferred_name: null,
      display_name: 'Максим',
      currency: 'RUB',
      timezone: 'Europe/Moscow',
      version: 'test',
    }, [], 'telegram', 'user');
    expect(cleared).toContain('Максим');
    expect(cleared).not.toContain('Леонель Месси');
  });

  it('quiet-hours editor preserves displayed times while disabled and re-enabled', () => {
    const disabled = QuietHoursForm({
      morning_enabled: true,
      evening_enabled: true,
      limit_alerts_enabled: true,
      budget_alerts_enabled: true,
      weekly_reports_enabled: true,
      monthly_reports_enabled: true,
      challenge_notifications_enabled: true,
      goal_notifications_enabled: false,
      morning_time: '08:30',
      evening_time: '20:30',
      quiet_hours_enabled: false,
      quiet_hours_start: '23:00',
      quiet_hours_end: '09:00',
      timezone: 'Europe/Moscow',
    }, false);
    const enabled = QuietHoursForm({
      morning_enabled: true,
      evening_enabled: true,
      limit_alerts_enabled: true,
      budget_alerts_enabled: true,
      weekly_reports_enabled: true,
      monthly_reports_enabled: true,
      challenge_notifications_enabled: true,
      goal_notifications_enabled: false,
      morning_time: '08:30',
      evening_time: '20:30',
      quiet_hours_enabled: true,
      quiet_hours_start: '23:00',
      quiet_hours_end: '09:00',
      timezone: 'Europe/Moscow',
    }, false);

    expect(disabled).toContain('value="23:00"');
    expect(disabled).toContain('value="09:00"');
    expect(enabled).toContain('value="23:00"');
    expect(enabled).toContain('value="09:00"');
  });
});
