import { describe, expect, it } from 'vitest';
import { HomeScreen } from '../src/components/HomeScreen';
import { OperationsScreen } from '../src/components/OperationsScreen';
import { TransactionForm } from '../src/components/TransactionForm';
import { ConfirmDialog } from '../src/components/ConfirmDialog';
import { ErrorState, LoadingState, EmptyState, AccessDeniedState } from '../src/components/States';
import { CategoryBudgetForm, GoalForm, LimitForm, PlansScreen, ReminderForm } from '../src/components/PlansScreen';
import { AnalyticsScreen } from '../src/components/AnalyticsScreen';
import { ActivityCalendarView } from '../src/components/ActivityCalendar';
import { AdditionalMenu, ExportForm, ProfileScreen, QuietHoursForm } from '../src/components/ProfileScreen';

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
      insight: { kind: 'expense_down', tone: 'positive', title: 'Расходы ниже', text: 'На 10% меньше' },
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
      insight: { kind: 'period', tone: 'neutral', title: 'Период', text: 'Есть данные' },
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
      insight: { kind: 'expense_up', tone: 'warning', title: 'Расходы выше', text: 'На 564% больше, чем в прошлом сопоставимом периоде.' },
    }, [], 'RUB', true);

    expect(html).toContain('На 564% больше прошлого периода');
    expect(html).not.toContain('в прошлом сопоставимом периоде');
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
        metric: 'absolute_amount',
        max_axes: 6,
        scale: { max: '0.00', step: '0.00', ticks: ['0.00'] },
        insufficient_data: true,
        explanation: 'Недостаточно данных',
        axes: [],
      },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 2, days: [{ date: '2026-08-01', count: 0 }, { date: '2026-08-02', count: 1 }, { date: '2026-08-03', count: 2 }, { date: '2026-08-04', count: 1 }] },
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' }, { period: 'current_month', operation_type: 'all', category: 'all' });

    expect(html).toContain('categoryChart');
    expect(html).toContain('dynamicsChart');
    expect(html).toContain('data-chart="category"');
    expect(html).toContain('Недостаточно данных');
    expect(html).toContain('data-action="export-open"');
    expect(html).toContain('Открыть экспорт');
  });

  it('renders money radar long labels and keeps activity out of Analytics', () => {
    const longCategory = 'Фиксированные расходы на коммунальные услуги';
    const html = AnalyticsScreen({
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
        currency_groups: { RUB: { income: '0.00', expense: '22000.00', result: '-22000.00', count: 4 } },
        totals_by_currency: { RUB: { income: '0.00', expense: '22000.00', count: 4 } },
        result_by_currency: { RUB: '-22000.00' },
      },
      category_structure: { type: 'expense', top_n: 5, currency_groups: {}, items: [] },
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
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' }, { period: 'current_month', operation_type: 'expense', category: 'all' });

    expect(html).toContain('<details class="chart-details">');
    expect(html).toContain('<tspan');
    expect(html).toContain('Фиксированные');
    expect(html).toContain('расходы');
    expect(html).toContain('коммунальные');
    expect(html).toContain('услуги');
    expect(html).toContain(`<title>${longCategory}: текущий период 15000.00 RUB`);
    expect(html).not.toContain('...');
    expect(html).toContain('5к');
    expect(html).toContain('Текущий период');
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
        metric: 'absolute_amount',
        max_axes: 6,
        scale: { max: '0.00', step: '0.00', ticks: ['0.00'] },
        insufficient_data: true,
        reason: 'mixed_currencies',
        explanation: 'mixed',
        axes: [],
      },
      activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-04', max_count: 0, days: [] },
      top_expense_categories: [],
    }, { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', categoryCurrency: 'EUR', dynamicsCurrency: 'RUB', radarCurrency: 'RUB' }, { period: 'current_month', operation_type: 'all', category: 'all' });

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
