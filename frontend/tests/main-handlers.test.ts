import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const plansData: any = {
  read_only: false,
  goals: [],
  archived_goals: [],
  general_limits: [{
    id: 'general:1',
    kind: 'general',
    title: 'All expenses',
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
  }],
  limits: [{
    id: 'category:month:Food',
    kind: 'category',
    title: 'Food',
    category: 'Food',
    scope: 'category',
    amount: '500.00',
    spent: '0.00',
    remaining: '500.00',
    percent: 0,
    period: 'month',
    status: 'normal',
    currency: 'RUB',
    alerts_enabled: true,
    workspace_id: 10,
    icon: 'category',
  }],
  category_budgets: [],
  reminders: [],
};

function goalData(status: 'active' | 'archived' = 'active') {
  return {
    id: 7,
    title: 'Trip',
    target: '1000.00',
    current: '250.00',
    remaining: '750.00',
    percent: 25,
    currency: 'RUB',
    status,
    deadline: '2026-12-31',
    strategy: 'deadline',
    frequency: 'monthly',
    schedule_config: { day: 5 },
    reminders_enabled: false,
    next_action: status === 'archived' ? 'Цель в архиве' : 'Пополнить 150 ₽',
    movement_count: 2,
  };
}

function analyticsData(currencies: string[], selectedCurrency: string | null = null): any {
  const totals = Object.fromEntries(currencies.map((currency) => [currency, { income: '1000.00', expense: currency === 'EUR' ? '20.00' : '500.00', count: 2 }]));
  const result = Object.fromEntries(currencies.map((currency) => [currency, currency === 'EUR' ? '980.00' : '500.00']));
  const categoryGroups = Object.fromEntries(currencies.map((currency) => [currency, {
    currency,
    total: totals[currency].expense,
    items: [],
  }]));
  return {
    period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
    previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-07' },
    overview: { period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' }, workspace_scope: 10, aggregation_available: currencies.length <= 1, totals_by_currency: totals, recent_operations: [] },
    aggregation_available: currencies.length <= 1,
    available_currencies: currencies,
    radar_available_currencies: currencies,
    selected_currency: selectedCurrency,
    currency_groups: {},
    summary: {
      aggregation_available: currencies.length <= 1,
      available_currencies: currencies,
      currency_groups: Object.fromEntries(currencies.map((currency) => [currency, { ...totals[currency], result: result[currency] }])),
      totals_by_currency: totals,
      result_by_currency: result,
    },
    overview_metrics: Object.fromEntries(currencies.map((currency) => [currency, {
      income: { current: totals[currency].income, previous: '0.00', delta: totals[currency].income, pct: null, state: 'zero_baseline' },
      expense: { current: totals[currency].expense, previous: '0.00', delta: totals[currency].expense, pct: null, state: 'zero_baseline' },
      result: { current: result[currency], previous: '0.00', delta: result[currency], pct: null, state: 'zero_baseline' },
      count: 2,
      previous_count: 0,
    }])),
    category_structure: { type: 'expense', top_n: 5, currency_groups: categoryGroups, items: [] },
    merchant_structure: { type: 'expense', dimension: 'merchant', top_n: 5, currency_groups: {}, items: [] },
    change_contribution: { type: 'expense', currency_groups: {}, items: [] },
    time_dynamics: { grouping: 'day', currency_groups: {}, items: [] },
    radar: { type: 'expense', currency: selectedCurrency, current_period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' }, previous_period: { key: 'previous_month_to_date', start_date: '2026-07-01', end_date: '2026-07-07' }, metric: 'absolute_amount', max_axes: 6, scale: { max: '0.00', step: '0.00', ticks: [] }, insufficient_data: true, explanation: '', axes: [] },
    activity_calendar: { start_date: '2026-08-01', end_date: '2026-08-07', max_count: 0, days: [] },
    search: { query: '', items: [] },
    selected_detail: null,
    top_expense_categories: [],
  };
}

function reportData(kind: 'selected' | 'completed_week' | 'completed_month' = 'selected', currency = 'RUB'): any {
  const period = kind === 'completed_month'
    ? { key: kind, start_date: '2026-07-01', end_date: '2026-07-31' }
    : kind === 'completed_week'
      ? { key: kind, start_date: '2026-08-03', end_date: '2026-08-09' }
      : { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' };
  return {
    kind,
    period,
    comparison_period: { key: 'previous_equal_period', start_date: '2026-07-27', end_date: '2026-08-02' },
    workspace: { scope: 10, name: 'Family', type: 'group', read_only: false },
    filters: { operation_type: 'all', category: 'all' },
    available_currencies: ['RUB', 'EUR'],
    selected_currency: currency,
    data_state: 'complete',
    summary: { currency: 'RUB', income: '1000.00', expense: '400.00', result: '600.00', operation_count: 4 },
    comparison: {
      income: { current: '1000.00', previous: '800.00', delta: '200.00', pct: '25.00', state: 'ok' },
      expense: { current: '400.00', previous: '300.00', delta: '100.00', pct: '33.33', state: 'ok' },
      result: { current: '600.00', previous: '500.00', delta: '100.00', pct: '20.00', state: 'ok' },
      count: 4,
      previous_count: 3,
    },
    structure_type: 'expense',
    categories: [{
      key: 'food', category: 'Food', currency: 'RUB', total: '400.00', count: 4, share: 100, drillable: true,
      operation_scope: { workspace_id: 10, period: 'custom', start_date: period.start_date, end_date: period.end_date, operation_type: 'expense', category: 'all', scope_category: null, currency: 'RUB', category_key: 'food', merchant_key: null },
    }],
    merchants: [],
    observations: [],
    export_available: false,
    export_reason: 'Экспорт доступен для выбранного периода.',
  };
}

function installAppMocks(homeInsights: any[] = [], workspaces: any[] = [{ workspace_id: 10, name: 'Family', kind: 'group', role: 'member', active: true, read_only: false }], homeAnnouncements: any[] = []) {
  const api = {
    bootstrap: vi.fn(async () => ({
      user: { currency: 'RUB', timezone: 'Europe/Moscow' },
      workspaces,
      theme: 'telegram',
      notifications: {},
      version: 'test'
    })),
    overview: vi.fn(async (): Promise<any> => ({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
      workspace_scope: 10 as number | 'all' | null,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      insights: homeInsights,
      insight: homeInsights[0] || null,
      announcements: homeAnnouncements,
    })),
    operations: vi.fn(async (_workspaceId: any, _filters: any, _offset = 0, _search = '') => ({ items: [], has_more: false, limit: 20, offset: 0, period: { key: 'custom', start_date: '2026-08-03', end_date: '2026-08-09' } })),
    analytics: vi.fn(async (_workspaceId: any, _filters: any) => analyticsData(['RUB'], 'RUB')),
    report: vi.fn(async (_workspaceId: any, filters: any) => ({ report: reportData(filters.report_kind, filters.currency || 'RUB') })),
    plans: vi.fn(async () => plansData),
    profile: vi.fn(async () => ({
      theme: 'telegram', currency: 'RUB', timezone: 'Europe/Moscow', version: 'test', workspaces,
      notifications: {
        morning_enabled: true, evening_enabled: true, limit_alerts_enabled: true, budget_alerts_enabled: true,
        weekly_reports_enabled: true, monthly_reports_enabled: true, challenge_notifications_enabled: false,
        goal_notifications_enabled: true, morning_time: '08:30', evening_time: '20:30', quiet_hours_enabled: false,
        timezone: 'Europe/Moscow', daily_notifications: { enabled: true, evening_time: '20:30' }, plans_control: { enabled: true }, reports: { enabled: true },
      },
      vacation_mode: { enabled: false, active: false, status: 'disabled', start_date: null, end_date: null },
      premium: { available: false, title: 'Premium', status: 'info', description: '', features: [] },
      export: { available: true, status: 'ready', presets: [], privacy_note: '' },
      categories: { expense: [], income: [] }, home_preferences: { widgets: [], order: [], enabled: [] }, links: {}, help_url: '',
    })),
    categories: vi.fn(async (_workspaceId, type) => ({
      items: type === 'income'
        ? [{ name: 'Salary', normalized_name: 'salary', type: 'Доходы', source: 'custom', operation_count: 0, has_budget: false }]
        : [{ name: 'Food', normalized_name: 'food', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false }],
      read_only: false,
    })),
    managedCategories: vi.fn(async () => ({
      items: [
        { name: 'Food', normalized_name: 'food', token: 'food', type: 'Расходы', source: 'custom', operation_count: 2, has_budget: false, protected: false, references: { operations: 2, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 2 } },
        { name: 'Other', normalized_name: 'other', token: 'other', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false, protected: false, references: { operations: 0, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 0 } },
      ],
      read_only: false,
    })),
    deleteCategory: vi.fn(async () => ({ deleted: true, references: {} })),
    updateCategoryPreference: vi.fn(async (_token, payload) => ({ category: { name: 'Food', normalized_name: 'food', token: 'food', type: 'Расходы', source: 'custom', operation_count: 2, has_budget: false, protected: false, priority: payload.priority, relevant: payload.relevant, references: { operations: 2, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 2 } } })),
    renameCategory: vi.fn(),
    createCategory: vi.fn(),
    goalPlanPreview: vi.fn(async () => ({ plan_preview: { strategy: 'deadline', frequency: 'monthly', remaining_amount: '950.00', occurrence_count: 5, recommended_amount: '190.00', next_occurrence: '2026-09-05', projected_completion_date: '2026-12-05', required_contributions: null, feasible: true, reason: null, schedule_config: { day: 5 }, preview_payload_hash: 'preview-hash' } })),
    planningEstimate: vi.fn(async (payload: any): Promise<any> => ({
      estimate: {
        kind: payload.kind,
        scope: { workspace_id: 10, currency: payload.currency || 'RUB', period: payload.period || 'month', categories: payload.categories || ['food'] },
        history: [
          { start_date: '2026-04-01', end_date: '2026-04-30', label: 'Апрель', amount: '15400.00', income: '0.00', expense: '15400.00', net: '-15400.00', operation_count: 2, expense_count: 2, income_count: 0 },
          { start_date: '2026-05-01', end_date: '2026-05-31', label: 'Май', amount: '17900.00', income: '0.00', expense: '17900.00', net: '-17900.00', operation_count: 2, expense_count: 2, income_count: 0 },
          { start_date: '2026-06-01', end_date: '2026-06-30', label: 'Июнь', amount: '12300.00', income: '0.00', expense: '12300.00', net: '-12300.00', operation_count: 2, expense_count: 2, income_count: 0 },
          { start_date: '2026-07-01', end_date: '2026-07-31', label: 'Июль', amount: '21400.00', income: '0.00', expense: '21400.00', net: '-21400.00', operation_count: 2, expense_count: 2, income_count: 0 },
        ],
        periods_requested: 4,
        valid_periods: 4,
        history_confidence: 'good',
        baseline_average: '16750.00',
        recommendation: '16750.00',
        conflicts: [],
        read_only: false,
        can_apply: true,
      },
    })),
    updateGoal: vi.fn(async () => ({ goal: {}, plan_preview: {} })),
    setGoalStatus: vi.fn(async (_id, _workspace, status) => ({ goal: { status } })),
    deleteGoal: vi.fn(async (id) => ({ deleted: true, goal_id: id, deleted_movement_count: 2 })),
    reminderDetail: vi.fn(async () => ({
      reminder: {
        id: 7,
        title: 'Internet',
        amount: '100.00',
        amount_text: '100 €',
        currency: 'EUR',
        category: 'Food',
        rem_type: 'Расходы',
        event_date: '2026-08-05',
        status: 'overdue',
        repeat_rule: 'monthly',
        repeat_interval_days: null,
        notify_days_before: 1,
        is_active: true,
      }
    })),
    recordReminder: vi.fn(async () => ({ result: 'recorded', reminder: null, operation: { id: 9 } })),
    createLimit: vi.fn(async () => ({ limit: plansData.limits[0] })),
    updateLimit: vi.fn(async () => ({ limit: plansData.general_limits[0] })),
    deleteLimit: vi.fn(),
    shoppingItems: vi.fn(async (): Promise<{ items: any[]; read_only: boolean; active_count: number; completed_count: number; note?: string }> => ({ items: [], read_only: false, active_count: 0, completed_count: 0 })),
    createShoppingItem: vi.fn(),
    updateShoppingItem: vi.fn(),
    deleteShoppingItem: vi.fn(),
    clearCompletedShoppingItems: vi.fn(),
    dismissAnnouncement: vi.fn(async (id) => ({ dismissed: true, candidate_id: id })),
    insightImpression: vi.fn(async () => ({ recorded: true })),
    insightFeedback: vi.fn(async (_id, _workspace, feedbackType) => ({ recorded: true, feedback_type: feedbackType })),
    forecastFeedback: vi.fn(async (_fingerprint, _workspace, feedbackType) => ({ recorded: true, feedback_type: feedbackType })),
    forecastExposure: vi.fn(async () => ({ recorded: true })),
    spendableForecast: vi.fn(async () => ({})),
    track: vi.fn(async (_name: string, _properties: Record<string, any> = {}) => undefined),
    exportInfo: vi.fn(async () => ({ available: true, status: 'ready', presets: [], privacy_note: '' })),
    setVacation: vi.fn(async (payload) => ({ vacation_mode: { ...payload, active: true, status: 'active' } })),
    previewHistoryDeletion: vi.fn(async (period) => ({ period, start_date: '2026-08-01', end_date: '2026-08-13', summary: { operations: 3, drafts: 1, goals: period === 'all' ? 2 : 0, related_records: 1 } })),
    deleteHistory: vi.fn(async (period) => ({ deleted: true, period, summary: { operations: 3, drafts: 1, goals: 0, related_records: 1 } })),
    previewAccountDeletion: vi.fn(async () => ({ summary: { financial_records: 3, preferences: 2, personal_workspaces: 1 }, confirmation_text: 'УДАЛИТЬ', shared_workspace_note: 'Общие данные сохранятся.' })),
    deleteAccount: vi.fn(async () => ({ deleted: true, terminal: true, message: 'Данные удалены. Вы можете закрыть КопиPaste.' })),
  };
  vi.doMock('../src/api', () => ({ api, requestId: () => 'request-id' }));
  return api;
}

function homeInsight(actions: any[] = [{ type: 'OPEN_MERCHANT', label: 'Посмотреть Лавку', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', scope_category: null, target_category: 'Продукты', category_key: 'продукты', merchant_key: 'яндекс лавка', currency: 'RUB' } }]) {
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
    evidence: [
      { kind: 'amount_comparison', label: 'Продукты', current_amount: '18400.00', previous_amount: '14300.00', currency: 'RUB' },
      { kind: 'merchant_contribution', label: 'Яндекс Лавка', delta_amount: '2800.00', currency: 'RUB', share_pct: 63, current_count: 12, previous_count: 7 },
    ],
    actions,
    feedback: null,
  };
}

function announcement(id: string, action: string = 'OPEN_DETAIL', detail: string | null = 'Исправили отображение списка.') {
  return {
    id,
    family: id,
    kind: action === 'OPEN_DETAIL' ? 'fix' : 'feature',
    released_on: '2026-08-11',
    title: `Update ${id}`,
    description: 'Короткое описание',
    detail,
    action: { type: action, label: 'Открыть' },
  };
}

async function openPlansLimits() {
  document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
  await Promise.resolve();
  await Promise.resolve();
  document.querySelector<HTMLButtonElement>('[data-action="plans-mode"][data-mode="limits"]')?.click();
  await Promise.resolve();
}

async function flush(times = 4) {
  for (let index = 0; index < times; index += 1) await Promise.resolve();
}

function dispatchPointer(target: EventTarget, type: string, clientX: number, clientY: number, pointerId = 7): void {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    clientX: { value: clientX },
    clientY: { value: clientY },
  });
  target.dispatchEvent(event);
}

function planningDropZone(): HTMLElement {
  const zone = document.querySelector<HTMLElement>('[data-planning-drop-zone]')!;
  zone.getBoundingClientRect = vi.fn(() => ({
    x: 100,
    y: 100,
    left: 100,
    top: 100,
    right: 220,
    bottom: 220,
    width: 120,
    height: 120,
    toJSON: () => ({}),
  } as DOMRect));
  return zone;
}

describe('main plan handlers', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    document.body.innerHTML = '<div id="app">Загрузка КопиPaste…</div>';
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.doUnmock('../src/api');
    delete window.Telegram;
  });

  it('keeps the same three primary filters on every tab and applies secondary filters from Еще', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await flush();

    const expectCompactFilters = () => {
      const primary = document.querySelector('.primary-filter-strip');
      expect(primary?.children).toHaveLength(3);
      expect(primary?.querySelector('[aria-label="Пространство"]')).not.toBeNull();
      expect(primary?.querySelector('[aria-label="Период"]')).not.toBeNull();
      expect(primary?.querySelector('[data-action="global-filters-open"]')?.textContent).toContain('Еще');
      expect(primary?.querySelector('[data-action="global-currency"]')).toBeNull();
      expect(primary?.querySelector('[data-action="operation-type"]')).toBeNull();
      expect(primary?.querySelector('[data-action="category-filter"]')).toBeNull();
    };
    expectCompactFilters();

    document.querySelector<HTMLButtonElement>('[data-action="global-filters-open"]')?.click();
    expect(document.querySelector('[data-sheet]')?.getAttribute('aria-label')).toBe('Еще');
    expect(document.querySelector('[data-action="global-currency"]')).not.toBeNull();
    expect(document.querySelector('[data-action="operation-type"]')).not.toBeNull();
    expect(document.querySelector('[data-action="category-filter"]')).not.toBeNull();

    const currency = document.querySelector<HTMLSelectElement>('[data-action="global-currency"]')!;
    currency.value = 'EUR';
    currency.dispatchEvent(new Event('change', { bubbles: true }));
    await flush(8);

    expect(api.overview).toHaveBeenLastCalledWith(10, expect.objectContaining({ currency: 'EUR' }));
    expect(document.querySelector<HTMLSelectElement>('[data-action="global-currency"]')?.value).toBe('EUR');
    expect(document.querySelector('[data-sheet]')?.getAttribute('aria-label')).toBe('Еще');

    const back = (window.Telegram!.WebApp!.BackButton!.onClick as any).mock.calls[0][0];
    back();
    expect(document.querySelector('[data-sheet]')).toBeNull();
    expect(document.querySelector<HTMLSelectElement>('[aria-label="Пространство"]')?.value).toBe('10');
    expect(document.querySelector<HTMLSelectElement>('[aria-label="Период"]')?.value).toBe('current_month');

    for (const tab of ['analytics', 'operations', 'plans', 'profile']) {
      document.querySelector<HTMLButtonElement>(`[data-tab="${tab}"]`)?.click();
      await flush(8);
      expectCompactFilters();
    }
  });

  it('opens escaped OPEN_DETAIL copy in a sheet, stays on Home, and closes with Telegram Back', async () => {
    let backHandler: (() => void) | undefined;
    window.Telegram!.WebApp!.BackButton!.onClick = vi.fn((callback: () => void) => {
      backHandler = callback;
    });
    const candidate = announcement('fix-v1', 'OPEN_DETAIL', '<b>Исправили карточку</b>');
    const api = installAppMocks([], undefined, [candidate]);

    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush();

    const sheet = document.querySelector<HTMLElement>('[data-sheet]');
    expect(sheet?.textContent).toContain('Исправление');
    expect(sheet?.innerHTML).toContain('&lt;b&gt;Исправили карточку&lt;/b&gt;');
    expect(document.querySelector('[data-tab="home"]')?.getAttribute('aria-current')).toBe('page');
    expect(api.analytics).not.toHaveBeenCalled();

    backHandler?.();
    expect(document.querySelector('[data-sheet]')).toBeNull();
    expect(document.querySelector('[data-tab="home"]')?.getAttribute('aria-current')).toBe('page');
  });

  it('safely ignores OPEN_DETAIL without usable detail', async () => {
    const api = installAppMocks([], undefined, [announcement('empty-detail', 'OPEN_DETAIL', null)]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush();

    expect(document.querySelector('[data-sheet]')).toBeNull();
    expect(document.querySelector('[data-tab="home"]')?.getAttribute('aria-current')).toBe('page');
    expect(api.analytics).not.toHaveBeenCalled();
  });

  it.each([
    ['OPEN_PROFILE', 'profile'],
    ['OPEN_ANALYTICS', 'analytics'],
    ['OPEN_PLANS', 'plans'],
  ])('keeps typed announcement navigation for %s', async (target, tab) => {
    installAppMocks([], undefined, [announcement(`target-${target}`, target, null)]);
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush(8);

    expect(document.querySelector(`[data-tab="${tab}"]`)?.getAttribute('aria-current')).toBe('page');
  });

  it('opens Reports from Analytics and switches selected, weekly, monthly and currency modes', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="reports-open"]')?.click();
    await flush(8);

    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: 'selected', currency: 'RUB' }));
    expect(document.querySelector('.reports-screen')).not.toBeNull();

    document.querySelector<HTMLButtonElement>('[data-action="report-kind"][data-kind="completed_week"]')?.click();
    await flush(8);
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: 'completed_week', currency: 'RUB' }));

    document.querySelector<HTMLButtonElement>('[data-action="report-kind"][data-kind="completed_month"]')?.click();
    await flush(8);
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: 'completed_month', currency: 'RUB' }));

    const currency = document.querySelector<HTMLSelectElement>('[data-action="report-currency"]')!;
    currency.value = 'EUR';
    currency.dispatchEvent(new Event('change'));
    await flush(8);
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: 'completed_month', currency: 'EUR' }));

    document.querySelector<HTMLButtonElement>('[data-action="reports-close"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="reports-open"]')?.click();
    await flush(8);
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: 'selected', currency: 'EUR' }));
  });

  it.each([
    ['OPEN_REPORTS', 'selected'],
    ['OPEN_REPORT_WEEKLY', 'completed_week'],
    ['OPEN_REPORT_MONTHLY', 'completed_month'],
  ])('routes report-ready action %s through the typed Reports mode', async (target, reportKind) => {
    const api = installAppMocks([], undefined, [announcement(`target-${target}`, target, null)]);
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush(8);

    expect(document.querySelector('[data-tab="analytics"]')?.getAttribute('aria-current')).toBe('page');
    expect(document.querySelector('.reports-screen')).not.toBeNull();
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({ report_kind: reportKind }));
  });

  it('preserves canonical report drill scope and returns with Telegram Back', async () => {
    let backHandler: (() => void) | undefined;
    window.Telegram!.WebApp!.BackButton!.onClick = vi.fn((callback: () => void) => {
      backHandler = callback;
    });
    const api = installAppMocks();
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="reports-open"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="report-kind"][data-kind="completed_month"]')?.click();
    await flush(8);

    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({
      period: 'current_month',
      operation_type: 'all',
      category: 'all',
      report_kind: 'completed_month',
      currency: 'RUB',
    }));

    document.querySelector<HTMLButtonElement>('[data-action="report-drill"][data-kind="category"]')?.click();
    await flush(8);

    expect(document.querySelector('[data-tab="operations"]')?.getAttribute('aria-current')).toBe('page');
    expect(api.operations).toHaveBeenLastCalledWith(10, expect.objectContaining({
      period: 'custom',
      start_date: '2026-07-01',
      end_date: '2026-07-31',
      operation_type: 'expense',
      currency: 'RUB',
      category_key: 'food',
    }), 0, '');
    expect(api.track).toHaveBeenCalledWith('report_drilldown_opened', expect.objectContaining({ report_kind: 'completed_month', kind: 'category', currency: 'RUB' }));

    backHandler?.();
    await flush(8);
    expect(document.querySelector('.reports-screen')).not.toBeNull();
    expect(api.report).toHaveBeenLastCalledWith(10, expect.objectContaining({
      period: 'current_month',
      operation_type: 'all',
      category: 'all',
      report_kind: 'completed_month',
      currency: 'RUB',
    }));
    expect(document.querySelector<HTMLSelectElement>('[data-action="period"]')?.value).toBe('current_month');
    document.querySelector<HTMLButtonElement>('[data-action="global-filters-open"]')?.click();
    expect(document.querySelector<HTMLSelectElement>('[data-action="operation-type"]')?.value).toBe('all');
    expect(document.querySelector<HTMLSelectElement>('[data-action="category-filter"]')?.value).toBe('all');
    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();

    backHandler?.();
    await flush(8);
    expect(document.querySelector('.analytics-screen')).not.toBeNull();
  });

  it('keeps Home settings and Shopping List typed announcement targets working', async () => {
    const api = installAppMocks([], undefined, [announcement('settings', 'OPEN_HOME_SETTINGS', null)]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush();
    expect(document.querySelector('[data-sheet]')?.getAttribute('aria-label')).toBe('Настройка главной');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    api.overview.mockResolvedValue({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
      workspace_scope: 10,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      announcements: [announcement('shopping', 'OPEN_SHOPPING_LIST', null)],
    });
    document.querySelector<HTMLSelectElement>('[data-action="period"]')?.dispatchEvent(new Event('change'));
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.click();
    await flush();
    expect(api.shoppingItems).toHaveBeenCalled();
    expect(document.querySelector('[data-sheet]')?.getAttribute('aria-label')).toBe('Список покупок');
  });

  it('tracks only the compact visible announcement once per session', async () => {
    const api = installAppMocks([], undefined, [announcement('a'), announcement('b'), announcement('c')]);
    await import('../src/main');
    await flush();
    const impressionIds = () => api.track.mock.calls
      .filter(([name]) => name === 'mini_app_announcement_impression')
      .map(([, properties]) => properties?.update_key);

    expect(impressionIds()).toEqual(['a']);
    expect(document.querySelectorAll('[data-action="carousel-dot"]')).toHaveLength(3);
    document.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.dispatchEvent(new Event('focus'));
    expect(impressionIds()).toEqual(['a']);
  });

  it('browses compact announcements with dots and swipe', async () => {
    const api = installAppMocks([], undefined, [announcement('a'), announcement('b'), announcement('c')]);
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="carousel-dot"][data-index="1"]')?.click();
    expect(document.querySelector('[data-announcement-target]')?.textContent).toContain('Update b');

    const card = document.querySelector<HTMLElement>('[data-carousel="announcement"]')!;
    dispatchPointer(card, 'pointerdown', 120, 10);
    dispatchPointer(card, 'pointerup', 20, 10);
    expect(document.querySelector('[data-announcement-target]')?.textContent).toContain('Update c');
    expect(api.track).toHaveBeenCalledWith('mini_app_announcement_carousel_changed', expect.objectContaining({ direction: 'next', position: '3', total: '3' }));
  });

  it('pages each compact planning card by dot and swipe while preserving tap navigation', async () => {
    const api = installAppMocks();
    api.overview.mockResolvedValue({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-31' },
      workspace_scope: 10,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      limit_items: [
        { kind: 'limit', title: 'Food', description: 'Норма', percent: 20, target_mode: 'limits' },
        { kind: 'limit', title: 'Cafe', description: 'Риск', percent: 90, target_mode: 'limits' },
      ],
      goal_items: [
        { kind: 'goal', title: 'Trip', description: 'В плане', percent: 20, target_mode: 'goals' },
        { kind: 'goal', title: 'Laptop', description: 'В плане', percent: 30, target_mode: 'goals' },
      ],
      reminders: [
        { state: 'upcoming', id: 10, title: 'Internet', status_text: 'Скоро', overdue_days: 0 },
        { state: 'upcoming', id: 20, title: 'Phone', status_text: 'Позже', overdue_days: 0 },
      ],
    });
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="carousel-dot"][data-carousel="limit"][data-index="1"]')?.click();
    expect(document.querySelector('[data-carousel="limit"]')?.textContent).toContain('Cafe');

    const goal = document.querySelector<HTMLElement>('[data-carousel="goal"]')!;
    dispatchPointer(goal, 'pointerdown', 120, 10);
    dispatchPointer(goal, 'pointerup', 20, 10);
    expect(document.querySelector('[data-carousel="goal"]')?.textContent).toContain('Laptop');

    document.querySelector<HTMLButtonElement>('[data-action="carousel-dot"][data-carousel="reminder"][data-index="1"]')?.click();
    expect(document.querySelector<HTMLButtonElement>('[data-action="home-reminder"]')?.dataset.id).toBe('20');

    document.querySelector<HTMLButtonElement>('[data-action="home-focus"][data-mode="limits"]')?.click();
    await flush(8);
    expect(api.plans).toHaveBeenCalled();
    expect(document.querySelector('[data-action="plans-mode"][data-mode="limits"]')?.getAttribute('aria-selected')).toBe('true');
  });

  it('keeps feedback acknowledgement on revisit and prompts for a new fingerprint', async () => {
    const oldFingerprint = 'f'.repeat(64);
    const newFingerprint = 'e'.repeat(64);
    let resolved = false;
    let currentFingerprint = oldFingerprint;
    const api = installAppMocks();
    api.overview.mockImplementation(async () => ({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-31' },
      workspace_scope: 10,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      spendable: {
        available: true,
        amount: '12000.00',
        currency: 'RUB',
        approximate: true,
        period_label: 'до конца месяца',
        quality_label: 'По истории',
        quality_tier: 'personal',
        risk_state: 'normal',
        fingerprint: currentFingerprint,
        feedback: currentFingerprint === oldFingerprint && resolved ? 'useful' : null,
        experiment: { enabled: true, variant: 'compact' },
      },
    }));
    api.forecastFeedback.mockImplementation(async (_fingerprint, _workspace, feedbackType) => {
      resolved = true;
      return { recorded: true, feedback_type: feedbackType };
    });
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="forecast-feedback"][data-feedback="useful"]')?.click();
    await flush();
    expect(document.body.textContent).toContain('Спасибо');
    expect(document.querySelector('[data-action="forecast-feedback"]')).toBeNull();

    document.querySelector<HTMLButtonElement>('[data-tab="operations"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-tab="home"]')?.click();
    await flush(8);
    expect(document.body.textContent).toContain('Спасибо');
    expect(document.querySelector('[data-action="forecast-feedback"]')).toBeNull();

    currentFingerprint = newFingerprint;
    document.querySelector<HTMLSelectElement>('[data-action="period"]')?.dispatchEvent(new Event('change'));
    await flush(8);
    expect(document.querySelector<HTMLButtonElement>('[data-action="forecast-feedback"]')?.dataset.fingerprint).toBe(newFingerprint);
  });

  it('keeps useful insight feedback resolved when a revisited overview response is stale', async () => {
    const api = installAppMocks([homeInsight()]);
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="home-insight-feedback"][data-feedback="useful"]')?.click();
    await flush(8);
    expect(api.insightFeedback).toHaveBeenCalledWith('a'.repeat(64), 10, 'useful');
    expect(document.querySelector('[data-action="home-insight-feedback"]')).toBeNull();
    expect(document.body.textContent).toContain('Спасибо');

    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-tab="home"]')?.click();
    await flush(8);

    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(document.querySelector('[data-action="home-insight-feedback"]')).toBeNull();
    expect(document.body.textContent).toContain('Спасибо');
  });

  it('keeps What’s New fixed even when legacy preferences disabled it', async () => {
    const api = installAppMocks([], undefined, [announcement('hidden')]);
    api.overview.mockResolvedValue({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
      workspace_scope: 10,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      home_widgets: [{ key: 'whats_new', title: 'Новое', description: 'Новости', layout: 'wide', default_enabled: true, default_order: 0 }],
      home_preferences: { order: ['whats_new'], enabled: [] },
      announcements: [announcement('hidden')],
    });
    await import('../src/main');
    await flush();

    expect(api.track.mock.calls.some(([name]) => name === 'mini_app_announcement_impression')).toBe(true);
    expect(document.querySelector('[data-action="announcement-open"]')).not.toBeNull();
  });

  it('tracks the next visible slide after dismissing the current announcement', async () => {
    const api = installAppMocks([], undefined, [announcement('a'), announcement('b')]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="announcement-dismiss"]')?.click();
    await flush();

    const impressions = api.track.mock.calls.filter(([name]) => name === 'mini_app_announcement_impression');
    expect(impressions.map(([, properties]) => properties?.update_key)).toEqual(['a', 'b']);
    expect(api.dismissAnnouncement).toHaveBeenCalledWith('a', 10);
  });

  it('edits a shopping item inline and cancel preserves the original text', async () => {
    const api = installAppMocks();
    const item = { id: 1, workspace_id: 10, text: 'Молоко', completed: false, created_at: '2026-08-11', updated_at: '2026-08-11' };
    api.shoppingItems.mockResolvedValue({ items: [item], read_only: false, active_count: 1, completed_count: 0 });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="shopping-open"]')?.click();
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="shopping-edit"]')?.click();
    const input = document.querySelector<HTMLInputElement>('form[data-action="shopping-edit-save"] input[name="text"]');
    expect(input?.maxLength).toBe(200);
    if (input) input.value = 'Хлеб';
    document.querySelector<HTMLButtonElement>('[data-action="shopping-edit-cancel"]')?.click();
    expect(document.body.textContent).toContain('Молоко');
    expect(api.updateShoppingItem).not.toHaveBeenCalled();

    document.querySelector<HTMLButtonElement>('[data-action="shopping-edit"]')?.click();
    const editForm = document.querySelector<HTMLFormElement>('form[data-action="shopping-edit-save"]')!;
    editForm.querySelector<HTMLInputElement>('input[name="text"]')!.value = 'Хлеб';
    editForm.requestSubmit();
    await flush(8);
    expect(api.updateShoppingItem).toHaveBeenCalledWith(1, 10, { text: 'Хлеб' });
  });

  it('preserves the concrete-workspace explanation for all-workspaces shopping', async () => {
    localStorage.setItem('finuchet-miniapp-state-v1', JSON.stringify({ workspaceId: 'all' }));
    const api = installAppMocks();
    api.shoppingItems.mockResolvedValue({ items: [], read_only: true, active_count: 0, completed_count: 0, note: 'Выберите одно пространство для списка покупок.' });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="shopping-open"]')?.click();
    await flush();

    expect(document.body.textContent).toContain('Выберите одно пространство для списка покупок.');
    expect(document.body.textContent).not.toContain('Список доступен только для чтения.');
  });

  it('calculates, applies and saves a recommendation through the existing limit API', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await flush();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="limit-create"][data-scope="category"]')?.click();
    await flush(8);
    document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')!.checked = false;
    document.querySelector<HTMLButtonElement>('[data-action="planning-calculate"]')?.click();
    await flush(8);

    expect(api.planningEstimate).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'category_limit',
      workspace_id: 10,
      currency: 'RUB',
      category: 'Food',
      period: 'month',
    }));
    expect(document.querySelectorAll('[data-testid="planning-history-row"]')).toHaveLength(4);
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);

    document.querySelector<HTMLButtonElement>('[data-action="planning-apply"]')?.click();
    const amount = document.querySelector<HTMLInputElement>('form[data-action="create-limit"] input[name="amount"]');
    expect(amount?.value).toBe('16750.00');
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);

    document.querySelector<HTMLFormElement>('form[data-action="create-limit"]')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush(8);
    expect(api.createLimit).toHaveBeenCalledWith(expect.objectContaining({ amount: '16750.00', category: 'Food', scope: 'category', alerts_enabled: false }));
  });

  it('keeps an existing limit alert choice through planning rerender', async () => {
    installAppMocks();
    await import('../src/main');
    await flush();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="limit-edit"][data-id="category:month:Food"]')?.click();
    await flush(8);
    document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')!.checked = false;
    document.querySelector<HTMLButtonElement>('[data-action="planning-calculate"]')?.click();
    await flush(8);

    expect(document.querySelector<HTMLInputElement>('form[data-action="save-limit"] input[name="alerts_enabled"]')?.checked).toBe(false);
  });

  it('persists category limit title, amount, category, period and alerts through reload without a duplicate', async () => {
    const api = installAppMocks();
    let stored = { ...plansData.limits[0] };
    api.categories.mockResolvedValue({
      items: [
        { name: 'Food', normalized_name: 'food', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false },
        { name: 'Cafe', normalized_name: 'cafe', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false },
      ],
      read_only: false,
    });
    api.plans.mockImplementation(async () => ({ ...plansData, limits: [stored] }));
    (api.updateLimit as any).mockImplementation(async (_id: string, payload: any) => {
      stored = {
        ...stored,
        id: `category:${payload.period}:${payload.category}`,
        title: payload.title,
        category: payload.category,
        amount: payload.amount,
        period: payload.period,
        alerts_enabled: payload.alerts_enabled,
      };
      return { limit: stored };
    });

    await import('../src/main');
    await flush();
    await openPlansLimits();
    document.querySelector<HTMLButtonElement>('[data-action="limit-edit"][data-id="category:month:Food"]')?.click();
    await flush(8);
    const form = document.querySelector<HTMLFormElement>('form[data-action="save-limit"]')!;
    form.querySelector<HTMLInputElement>('[name="title"]')!.value = 'Coffee out';
    form.querySelector<HTMLInputElement>('[name="amount"]')!.value = '750';
    form.querySelector<HTMLSelectElement>('[name="category"]')!.value = 'Cafe';
    form.querySelector<HTMLSelectElement>('[name="period"]')!.value = 'week';
    form.querySelector<HTMLInputElement>('[name="alerts_enabled"]')!.checked = false;
    form.requestSubmit();
    await flush(12);

    expect(api.updateLimit).toHaveBeenCalledWith('category:month:Food', expect.objectContaining({
      title: 'Coffee out', amount: '750.00', category: 'Cafe', period: 'week', alerts_enabled: false,
    }));
    expect((await api.plans()).limits).toHaveLength(1);
    document.querySelector<HTMLButtonElement>('[data-action="limit-edit"][data-id="category:week:Cafe"]')?.click();
    await flush(8);
    const reloaded = document.querySelector<HTMLFormElement>('form[data-action="save-limit"]')!;
    expect(reloaded.querySelector<HTMLInputElement>('[name="title"]')?.value).toBe('Coffee out');
    expect(reloaded.querySelector<HTMLInputElement>('[name="amount"]')?.value).toBe('750.00');
    expect(reloaded.querySelector<HTMLSelectElement>('[name="category"]')?.value).toBe('Cafe');
    expect(reloaded.querySelector<HTMLSelectElement>('[name="period"]')?.value).toBe('week');
    expect(reloaded.querySelector<HTMLInputElement>('[name="alerts_enabled"]')?.checked).toBe(false);
  });

  it('implements the complete grouped-budget pointer lifecycle and preserves tap fallback', async () => {
    installAppMocks();
    await import('../src/main');
    await flush();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="category-budget-create"]')?.click();
    await flush(8);
    document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')!.checked = false;
    document.querySelector<HTMLButtonElement>('[data-action="planning-category-toggle"][data-category="Food"]')?.click();
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).not.toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);

    document.querySelector<HTMLButtonElement>('.planning-selected-chip[data-category="Food"]')?.click();
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).toBeNull();

    const successfulZone = planningDropZone();
    const successfulHandle = document.querySelector<HTMLElement>('[data-planning-drag="Food"]')!;
    dispatchPointer(successfulHandle, 'pointerdown', 20, 20);
    dispatchPointer(window, 'pointermove', 150, 150);
    expect(successfulZone.classList.contains('drag-over')).toBe(true);
    dispatchPointer(window, 'pointerup', 150, 150);
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).not.toBeNull();
    expect(successfulZone.classList.contains('drag-over')).toBe(false);
    expect(successfulHandle.closest('.planning-category-chip')?.classList.contains('dragging')).toBe(false);
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);

    document.querySelector<HTMLButtonElement>('[data-action="planning-category-toggle"][data-category="Food"]')?.click();
    expect(document.querySelectorAll('input[name="categories"][value="Food"]')).toHaveLength(1);

    const duplicateZone = planningDropZone();
    dispatchPointer(document.querySelector<HTMLElement>('[data-planning-drag="Food"]')!, 'pointerdown', 20, 20);
    dispatchPointer(window, 'pointerup', 150, 150);
    expect(duplicateZone.classList.contains('drag-over')).toBe(false);
    expect(document.querySelectorAll('input[name="categories"][value="Food"]')).toHaveLength(1);

    await new Promise((resolve) => window.setTimeout(resolve, 0));
    document.querySelector<HTMLButtonElement>('.planning-selected-chip[data-category="Food"]')?.click();
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).toBeNull();

    const outsideZone = planningDropZone();
    dispatchPointer(document.querySelector<HTMLElement>('[data-planning-drag="Food"]')!, 'pointerdown', 20, 20);
    dispatchPointer(window, 'pointermove', 50, 50);
    dispatchPointer(window, 'pointerup', 50, 50);
    expect(outsideZone.classList.contains('drag-over')).toBe(false);
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).toBeNull();

    const cancelledZone = planningDropZone();
    const cancelledHandle = document.querySelector<HTMLElement>('[data-planning-drag="Food"]')!;
    dispatchPointer(cancelledHandle, 'pointerdown', 20, 20);
    dispatchPointer(window, 'pointermove', 150, 150);
    dispatchPointer(window, 'pointercancel', 150, 150);
    expect(cancelledZone.classList.contains('drag-over')).toBe(false);
    expect(cancelledHandle.closest('.planning-category-chip')?.classList.contains('dragging')).toBe(false);
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).toBeNull();

    document.querySelector<HTMLButtonElement>('[data-action="planning-category-toggle"][data-category="Food"]')?.click();
    expect(document.querySelector<HTMLInputElement>('input[name="categories"][value="Food"]')).not.toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);
    document.querySelector<HTMLButtonElement>('[data-action="planning-calculate"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="planning-apply"]')?.click();
    expect(document.querySelector<HTMLInputElement>('input[name="alerts_enabled"]')?.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>('input[name="amount"]')?.value).toBe('16750.00');
  });

  it('applies goal comfort pace only to the draft and requires a fresh preview hash', async () => {
    const api = installAppMocks();
    api.planningEstimate.mockResolvedValue({
      estimate: {
        kind: 'goal',
        scope: { workspace_id: 10, currency: 'RUB', period: 'month', categories: [] },
        history: [],
        periods_requested: 4,
        valid_periods: 4,
        history_confidence: 'good',
        baseline_average: '25000.00',
        recommendation: '15000.00',
        required_pace: { amount: '20000.00', monthly_amount: '20000.00', occurrence_count: 6 },
        comfortable_pace: { amount: '15000.00', monthly_amount: '15000.00', average_monthly_net: '25000.00', other_goal_commitments: '10000.00', commitment_count: 1 },
        feasibility: 'stretched',
        gap: '5000.00',
        comfortable_completion_date: '2027-03-05',
        conflicts: [],
        read_only: false,
        can_apply: true,
      },
    });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="goal-create"]')?.click();

    const form = document.querySelector<HTMLFormElement>('form[data-action="create-goal"]')!;
    form.querySelector<HTMLInputElement>('input[name="title"]')!.value = 'Trip';
    form.querySelector<HTMLInputElement>('input[name="target_amount"]')!.value = '120000';
    form.querySelector<HTMLInputElement>('input[name="deadline"]')!.value = '2027-02-28';
    form.querySelector<HTMLSelectElement>('select[name="frequency"]')!.value = 'monthly';
    form.querySelector<HTMLInputElement>('input[name="day"]')!.value = '5';
    expect(form.querySelector<HTMLSelectElement>('select[name="strategy"]')!.value).toBe('deadline');
    form.querySelector<HTMLButtonElement>('[data-action="planning-calculate"]')!.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="planning-apply"]')?.click();

    expect(document.querySelector<HTMLSelectElement>('select[name="strategy"]')?.value).toBe('contribution');
    expect(document.querySelector<HTMLInputElement>('input[name="comfortable_amount"]')?.value).toBe('15000.00');
    expect(document.querySelector('[data-submit-mode="confirm"]')).toBeNull();
    document.querySelector<HTMLButtonElement>('[data-submit-mode="preview"]')?.click();
    await flush(8);
    expect(api.goalPlanPreview).toHaveBeenCalledWith(expect.objectContaining({ strategy: 'contribution', comfortable_amount: '15000.00' }));
    expect(document.querySelector('[data-submit-mode="confirm"]')).not.toBeNull();
  });

  it('opens general limit edit and delete handlers from general_limits', async () => {
    installAppMocks();
    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="limit-edit"][data-id="general:1"]')?.click();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Изменить лимит');
    expect(document.querySelector<HTMLSelectElement>('form[data-action="save-limit"] select[name="scope"]')?.value).toBe('all_expenses');
    expect(document.querySelector<HTMLInputElement>('form[data-action="save-limit"] input[name="currency"]')?.value).toBe('EUR');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="limit-delete"][data-id="general:1"]')?.click();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Удалить лимит?');
    expect(document.querySelector<HTMLButtonElement>('[data-action="confirm-limit-delete"]')?.dataset.id).toBe('general:1');
  });

  it('opens category detail and safely deletes with immediate list and filter refresh', async () => {
    const api = installAppMocks();
    let deleted = false;
    api.deleteCategory.mockImplementation(async () => {
      deleted = true;
      return { deleted: true, references: {} };
    });
    api.categories.mockImplementation(async (_workspaceId, type) => ({
      items: type === 'income' ? [] : deleted ? [{ name: 'Other', normalized_name: 'other', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false }] : [
        { name: 'Food', normalized_name: 'food', type: 'Расходы', source: 'custom', operation_count: 2, has_budget: false },
        { name: 'Other', normalized_name: 'other', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false },
      ],
      read_only: false,
    }));
    api.managedCategories.mockImplementation(async () => ({
      items: deleted ? [{ name: 'Other', normalized_name: 'other', token: 'other', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false, protected: false, references: { operations: 0, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 0 } }] : [
        { name: 'Food', normalized_name: 'food', token: 'food', type: 'Расходы', source: 'custom', operation_count: 2, has_budget: false, protected: false, references: { operations: 2, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 2 } },
        { name: 'Other', normalized_name: 'other', token: 'other', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false, protected: false, references: { operations: 0, drafts: 0, category_limits: 0, category_budget_groups: 0, reminders: 0, aliases: 0, ml_observations: 0, total: 0 } },
      ], read_only: false,
    }));
    await import('../src/main');
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="global-filters-open"]')?.click();
    const categoryFilter = document.querySelector<HTMLSelectElement>('[data-action="category-filter"]')!;
    categoryFilter.value = 'Food';
    categoryFilter.dispatchEvent(new Event('change', { bubbles: true }));
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="plans-mode"][data-mode="categories"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="category-open"][data-token="food"]')?.click();
    expect(document.body.textContent).toContain('Автокатегоризация');
    document.querySelector<HTMLButtonElement>('[data-action="category-delete"]')?.click();
    const form = document.querySelector<HTMLFormElement>('form[data-action="delete-category"]')!;
    form.querySelector<HTMLSelectElement>('select[name="transfer_to"]')!.value = 'Other';
    form.querySelector<HTMLInputElement>('input[name="confirmed"]')!.checked = true;
    form.requestSubmit();
    await flush(12);

    expect(api.deleteCategory).toHaveBeenCalledWith('food', { workspace_id: 10, type: 'expense', transfer_to: 'Other' });
    expect(document.querySelector('[data-action="category-open"][data-token="food"]')).toBeNull();
    document.querySelector<HTMLButtonElement>('[data-action="global-filters-open"]')?.click();
    expect(document.querySelector<HTMLSelectElement>('[data-action="category-filter"]')?.value).toBe('all');
  });

  it('navigates the goal archive, restores a goal, and supports Telegram BackButton', async () => {
    const api = installAppMocks();
    api.plans.mockResolvedValue({ ...plansData, goals: [{ ...goalData(), id: 8 }], archived_goals: [goalData('archived')] });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.click();
    expect(document.querySelector('[data-action="goal-archive-back"]')).not.toBeNull();

    const back = (window.Telegram!.WebApp!.BackButton!.onClick as any).mock.calls[0][0];
    back();
    expect(document.querySelector('[data-action="goal-archive-open"]')).not.toBeNull();

    document.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-open"][data-id="7"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-status"][data-status="active"]')?.click();
    await flush(8);

    expect(api.setGoalStatus).toHaveBeenCalledWith(7, 10, 'active');
    expect(document.querySelector('[data-action="goal-archive-open"]')).not.toBeNull();
  });

  it('updates active/archive goal collections across archive and restore without restarting', async () => {
    const api = installAppMocks();
    let active = [goalData('active')];
    let archived: any[] = [];
    api.plans.mockImplementation(async () => ({ ...plansData, goals: active, archived_goals: archived }));
    api.setGoalStatus.mockImplementation(async (id, _workspace, status) => {
      const source = [...active, ...archived].find((goal) => goal.id === id)!;
      const goal = { ...source, status };
      active = status === 'active' ? [goal] : active.filter((item) => item.id !== id);
      archived = status === 'archived' ? [goal] : archived.filter((item) => item.id !== id);
      return { goal };
    });

    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="goal-open"][data-id="7"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-status"][data-status="archived"]')?.click();
    await flush(12);

    expect(document.querySelector('[data-action="goal-open"][data-id="7"]')).toBeNull();
    expect(document.querySelector('[data-action="goal-archive-open"]')?.textContent).toContain('1 целей');
    document.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.click();
    expect(document.querySelector('[data-action="goal-open"][data-id="7"]')).not.toBeNull();
    document.querySelector<HTMLButtonElement>('[data-action="goal-open"][data-id="7"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-status"][data-status="active"]')?.click();
    await flush(12);

    expect(api.setGoalStatus).toHaveBeenLastCalledWith(7, 10, 'active');
    expect(document.querySelector('[data-action="goal-open"][data-id="7"]')).not.toBeNull();
    expect(document.querySelector('[data-action="goal-archive-open"]')?.textContent).toContain('0 целей');
  });

  it('requires explicit confirmation before permanently deleting an archived goal', async () => {
    const api = installAppMocks();
    api.plans.mockResolvedValue({ ...plansData, goals: [], archived_goals: [goalData('archived')] });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-open"][data-id="7"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-delete"]')?.click();

    expect(document.body.textContent).toContain('Удалить цель навсегда?');
    expect(api.deleteGoal).not.toHaveBeenCalled();
    document.querySelector<HTMLButtonElement>('[data-action="confirm-goal-delete"]')?.click();
    await flush(8);
    expect(api.deleteGoal).toHaveBeenCalledWith(7, 10);
  });

  it('keeps a permanent goal deletion failure visible in the confirmation', async () => {
    const api = installAppMocks();
    api.plans.mockResolvedValue({ ...plansData, goals: [], archived_goals: [goalData('archived')] });
    api.deleteGoal.mockRejectedValueOnce({ code: 'goal_not_archived' });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-open"][data-id="7"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="goal-delete"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="confirm-goal-delete"]')?.click();
    await flush(8);

    expect(document.body.textContent).toContain('Сначала переместите цель в архив.');
    expect(document.querySelector('[data-action="confirm-goal-delete"]')).not.toBeNull();
  });

  it('recalculates an edited goal after every plan change before saving', async () => {
    const api = installAppMocks();
    api.plans.mockResolvedValue({ ...plansData, goals: [goalData()], archived_goals: [] });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="goal-edit"][data-id="7"]')?.click();

    const firstForm = document.querySelector<HTMLFormElement>('form[data-action="save-goal"]')!;
    expect(firstForm.querySelector('[name="current_amount"]')).toBeNull();
    firstForm.querySelector<HTMLInputElement>('[name="target_amount"]')!.value = '1200';
    firstForm.querySelector<HTMLButtonElement>('[data-submit-mode="preview"]')!.click();
    await flush(8);
    expect(api.goalPlanPreview).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-submit-mode="confirm"]')).not.toBeNull();

    const changedTarget = document.querySelector<HTMLInputElement>('form[data-action="save-goal"] [name="target_amount"]')!;
    changedTarget.value = '1500';
    changedTarget.dispatchEvent(new Event('input', { bubbles: true }));
    expect(document.querySelector<HTMLButtonElement>('[data-submit-mode="confirm"]')?.hidden).toBe(true);
    document.querySelector<HTMLButtonElement>('form[data-action="save-goal"] [data-submit-mode="preview"]')!.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('form[data-action="save-goal"] [data-submit-mode="confirm"]')!.click();
    await flush(8);

    expect(api.goalPlanPreview).toHaveBeenCalledTimes(2);
    expect(api.updateGoal).toHaveBeenCalledWith(7, expect.objectContaining({ target_amount: '1500.00', preview_payload_hash: 'preview-hash' }));
  });

  it('opens insight detail, records an impression and feedback, and supports BackButton', async () => {
    const api = installAppMocks([homeInsight()]);
    await import('../src/main');
    await flush();

    expect(api.insightImpression).toHaveBeenCalledWith('a'.repeat(64), 10);
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();

    expect(document.body.textContent).toContain('18 400 ₽');
    expect(document.body.textContent).toContain('Яндекс Лавка');
    expect(document.querySelector('[data-action="insight-feedback"]')).not.toBeNull();

    document.querySelector<HTMLButtonElement>('[data-action="insight-feedback"][data-feedback="useful"]')?.click();
    await flush();
    expect(api.insightFeedback).toHaveBeenCalledWith('a'.repeat(64), 10, 'useful');
    expect(document.body.textContent).toContain('Спасибо, учтём этот выбор.');

    const backHandler = (window.Telegram?.WebApp?.BackButton?.onClick as ReturnType<typeof vi.fn>).mock.calls[0]?.[0];
    backHandler?.();
    expect(document.querySelector('[data-sheet]')).toBeNull();
  });

  it('removes a not-useful insight after persistence even when overview is stale', async () => {
    const api = installAppMocks([homeInsight()]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();

    document.querySelector<HTMLButtonElement>('[data-action="insight-feedback"][data-feedback="not_useful"]')?.click();
    await flush(12);

    expect(api.insightFeedback).toHaveBeenCalledWith('a'.repeat(64), 10, 'not_useful');
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(document.querySelector('[data-sheet]')).toBeNull();
    expect(document.querySelector('[data-action="home-insight"]')).toBeNull();
  });

  it('opens merchant Analytics detail with the stable merchant key', async () => {
    const api = installAppMocks([homeInsight()]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(api.analytics).toHaveBeenCalledWith(10, expect.objectContaining({
      detail_kind: 'merchant',
      detail_value: 'яндекс лавка',
      detail_currency: 'RUB',
      detail_category_key: 'продукты',
      category: 'all',
    }));
  });

  it('preserves a selected Home category through merchant Analytics drilldown', async () => {
    const action = { type: 'OPEN_MERCHANT', label: 'Посмотреть Bistro', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'Рестораны', scope_category: 'Рестораны', target_category: 'Рестораны', category_key: 'рестораны', merchant_key: 'bistro', currency: 'RUB' } };
    const api = installAppMocks([homeInsight([action])]);
    api.analytics.mockImplementation(async () => {
      const response = analyticsData(['RUB'], 'RUB');
      response.selected_detail = {
        kind: 'merchant',
        title: 'Bistro',
        currency: 'RUB',
        operation_type: 'expense',
        merchant_key: 'bistro',
        category_key: 'рестораны',
        operation_count: 8,
        operations: [],
        operation_scope: {
          period: 'current_month',
          start_date: '2026-08-01',
          end_date: '2026-08-10',
          operation_type: 'expense',
          category: 'all',
          scope_category: 'Рестораны',
          category_key: 'рестораны',
          merchant_key: 'bistro',
          currency: 'RUB',
        },
      };
      return response;
    });
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(api.analytics).toHaveBeenCalledWith(10, expect.objectContaining({
      category: 'Рестораны',
      detail_kind: 'merchant',
      detail_value: 'bistro',
      detail_category_key: 'рестораны',
    }));

    document.querySelector<HTMLButtonElement>('[data-action="analytics-open-operations"]')?.click();
    await flush(8);
    expect(api.operations).toHaveBeenCalledWith(10, expect.objectContaining({
      category: 'Рестораны',
      scope_category: 'Рестораны',
      category_key: 'рестораны',
      merchant_key: 'bistro',
    }), 0, '');
  });

  it('uses canonical category key instead of an exact display variant for merchant detail', async () => {
    const action = { type: 'OPEN_MERCHANT', label: 'Посмотреть магазин', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', scope_category: null, target_category: 'Прочее', category_key: 'прочее', merchant_key: 'shop', currency: 'RUB' } };
    const api = installAppMocks([homeInsight([action])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(api.analytics).toHaveBeenCalledWith(10, expect.objectContaining({
      category: 'all',
      detail_category_key: 'прочее',
    }));
  });

  it('opens the existing category Analytics detail with preserved scope', async () => {
    const categoryAction = { type: 'OPEN_CATEGORY', label: 'Посмотреть категорию', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', scope_category: null, target_category: 'Продукты', category_key: 'продукты', currency: 'RUB' } };
    const api = installAppMocks([homeInsight([categoryAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(api.analytics).toHaveBeenCalledWith(10, expect.objectContaining({
      detail_kind: 'category',
      detail_value: 'Продукты',
      detail_currency: 'RUB',
    }));
  });

  it('opens the existing limit edit flow for an active limit insight', async () => {
    const limitAction = { type: 'OPEN_LIMIT', label: 'Открыть лимит', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'Food', category_key: 'food', currency: 'RUB', limit_id: 'category:month:Food' } };
    installAppMocks([homeInsight([limitAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(document.querySelector('form[data-action="save-limit"]')).not.toBeNull();
    expect(document.querySelector<HTMLSelectElement>('select[name="category"]')?.value).toBe('Food');
  });

  it('opens scoped Operations and prefills the existing create-limit flow', async () => {
    const operationsAction = { type: 'OPEN_OPERATIONS', label: 'Посмотреть операции', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category_key: 'продукты', merchant_key: 'яндекс лавка', currency: 'RUB' } };
    const api = installAppMocks([homeInsight([operationsAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(api.operations).toHaveBeenCalledWith(10, expect.objectContaining({ merchant_key: 'яндекс лавка', category_key: 'продукты', currency: 'RUB' }), 0, '');

    vi.resetModules();
    document.body.innerHTML = '<div id="app">Загрузка КопиPaste…</div>';
    const createLimitAction = { type: 'CREATE_LIMIT', label: 'Установить лимит', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', target_category: 'Food', category_key: 'food', currency: 'RUB' } };
    installAppMocks([homeInsight([createLimitAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    expect(document.querySelector('form[data-action="create-limit"]')).not.toBeNull();
    expect(document.querySelector<HTMLSelectElement>('select[name="category"]')?.value).toBe('Food');
  });

  it.each(['USD', 'EUR', 'RUB'])('submits %s from an insight-created limit', async (currency) => {
    const createLimitAction = { type: 'CREATE_LIMIT', label: 'Установить лимит', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', target_category: 'Food', category_key: 'food', currency } };
    const api = installAppMocks([homeInsight([createLimitAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);

    const currencyInput = document.querySelector<HTMLInputElement>('form[data-action="create-limit"] input[name="currency"]');
    expect(currencyInput?.value).toBe(currency);
    expect(currencyInput?.readOnly).toBe(true);
    const amount = document.querySelector<HTMLInputElement>('form[data-action="create-limit"] input[name="amount"]');
    if (amount) amount.value = '100';
    document.querySelector<HTMLFormElement>('form[data-action="create-limit"]')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush(12);

    expect(api.createLimit).toHaveBeenCalledWith(expect.objectContaining({ currency, amount: '100.00' }));
  });

  it('clears insight currency before ordinary limit creation', async () => {
    const createLimitAction = { type: 'CREATE_LIMIT', label: 'Установить лимит', params: { workspace_id: 10, period: 'current_month', operation_type: 'expense', category: 'all', target_category: 'Food', category_key: 'food', currency: 'USD' } };
    installAppMocks([homeInsight([createLimitAction])]);
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="insight-action"]')?.click();
    await flush(8);
    expect(document.querySelector<HTMLInputElement>('input[name="currency"]')?.value).toBe('USD');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="limit-create"][data-scope="category"]')?.click();
    await flush();

    expect(document.querySelector('form[data-action="create-limit"] input[name="currency"]')).toBeNull();
  });

  it('records identical insight fingerprints once per workspace', async () => {
    const api = installAppMocks(
      [homeInsight()],
      [
        { workspace_id: 10, name: 'Family', kind: 'group', role: 'member', active: true, read_only: false },
        { workspace_id: 11, name: 'Work', kind: 'group', role: 'member', active: false, read_only: false },
      ],
    );
    await import('../src/main');
    await flush();
    expect(api.insightImpression).toHaveBeenCalledWith('a'.repeat(64), 10);

    const workspace = document.querySelector<HTMLSelectElement>('[data-action="workspace"]');
    if (!workspace) throw new Error('workspace filter missing');
    workspace.value = '11';
    workspace.dispatchEvent(new Event('change'));
    await flush(8);

    expect(api.insightImpression).toHaveBeenCalledWith('a'.repeat(64), 11);
    expect(api.insightImpression).toHaveBeenCalledTimes(2);
  });

  it('opens create limit forms with the scope from the clicked plus button', async () => {
    installAppMocks();
    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="limit-create"][data-scope="all_expenses"]')?.click();
    await Promise.resolve();
    expect(document.querySelector<HTMLSelectElement>('form[data-action="create-limit"] select[name="scope"]')?.value).toBe('all_expenses');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="limit-create"][data-scope="category"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelector<HTMLSelectElement>('form[data-action="create-limit"] select[name="scope"]')?.value).toBe('category');
  });

  it('toggles a general limit explicitly without using edit', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();
    await openPlansLimits();

    document.querySelector<HTMLButtonElement>('[data-action="limit-toggle"][data-id="general:1"]')?.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(api.updateLimit).toHaveBeenCalledWith('general:1', { workspace_id: 10, toggle: true, enabled: false });
  });

  it('asks for a writable workspace before recording Home reminder from all workspaces', async () => {
    const api = installAppMocks();
    api.overview.mockResolvedValue({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
      workspace_scope: 'all',
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      reminder: {
        state: 'overdue',
        id: 7,
        title: 'Internet',
        event_date: '2026-08-05',
        amount_text: '100 €',
        category: 'Food',
        next_event_date: '2026-09-05',
        status_text: 'Нужно было оплатить',
        overdue_days: 2,
        repeat_rule: 'monthly',
      }
    });
    localStorage.setItem('finuchet-miniapp-state-v1', JSON.stringify({ workspaceId: 'all' }));
    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="home-reminder"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="reminder-record"][data-id="7"]')?.click();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Выберите пространство, куда записать операцию.');
    expect(document.body.textContent).toContain('Family');
    expect(api.recordReminder).not.toHaveBeenCalled();
  });

  it('opens the first fixed Home reminder summary by id', async () => {
    const api = installAppMocks();
    api.overview.mockResolvedValue({
      period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-07' },
      workspace_scope: 10,
      aggregation_available: true,
      totals_by_currency: {},
      recent_operations: [],
      reminders: [
        { state: 'upcoming', id: 10, title: 'A', event_date: '2026-08-10', status_text: 'A status', overdue_days: 0 },
        { state: 'overdue', id: 20, title: 'B', event_date: '2026-08-11', status_text: 'B status', overdue_days: 1 },
        { state: 'upcoming', id: 30, title: 'C', event_date: '2026-08-12', status_text: 'C status', overdue_days: 0 },
      ],
    });

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    expect(document.querySelectorAll('[data-action="carousel-dot"][data-carousel="reminder"]')).toHaveLength(3);
    expect(document.querySelector<HTMLButtonElement>('[data-action="home-reminder"]')?.dataset.id).toBe('10');
    document.querySelector<HTMLButtonElement>('[data-action="home-reminder"]')?.click();
    await Promise.resolve();
    expect(api.reminderDetail).toHaveBeenLastCalledWith(10);
  });

  it('opens the global menu and requests Telegram native add-to-home', async () => {
    const api = installAppMocks();
    const addToHomeScreen = vi.fn();
    const eventHandlers: Record<string, Array<() => void>> = {};
    const checkHomeScreenStatus = vi.fn((callback?: (status: 'missed') => void) => callback?.('missed'));
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn((event: string, callback: () => void) => {
          eventHandlers[event] = [...(eventHandlers[event] || []), callback];
        }),
        addToHomeScreen,
        checkHomeScreenStatus,
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Добавить на главный экран');
    expect(eventHandlers.homeScreenAdded).toHaveLength(1);

    document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')?.click();
    await Promise.resolve();

    expect(addToHomeScreen).toHaveBeenCalled();
    expect(api.track).toHaveBeenCalledWith('mini_app_add_to_home_requested', { source: 'mini_app' });
    expect(document.body.textContent).toContain('Подтвердите добавление в Telegram');

    eventHandlers.homeScreenAdded[0]();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Добавлено');
    expect(document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')).toBeNull();
  });

  it('recovers pending add-to-home state when Telegram later reports added', async () => {
    installAppMocks();
    const addToHomeScreen = vi.fn();
    const statuses: Array<'missed' | 'added'> = ['missed', 'added'];
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        addToHomeScreen,
        checkHomeScreenStatus: vi.fn((callback?: (status: 'missed' | 'added') => void) => callback?.(statuses.shift() || 'added')),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(document.body.textContent).toContain('Добавить на главный экран');

    document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')?.click();
    await Promise.resolve();
    expect(document.body.textContent).toContain('Подтвердите добавление в Telegram');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Добавлено');
    expect(document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')).toBeNull();
  });

  it('does not downgrade added when a stale add-to-home check resolves later', async () => {
    installAppMocks();
    const eventHandlers: Record<string, Array<() => void>> = {};
    let resolveCheck: ((status: 'missed') => void) | undefined;
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn((event: string, callback: () => void) => {
          eventHandlers[event] = [...(eventHandlers[event] || []), callback];
        }),
        addToHomeScreen: vi.fn(),
        checkHomeScreenStatus: vi.fn((callback?: (status: 'missed') => void) => {
          resolveCheck = callback;
        }),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    expect(eventHandlers.homeScreenAdded).toHaveLength(1);

    eventHandlers.homeScreenAdded[0]();
    await Promise.resolve();
    expect(document.body.textContent).toContain('Добавлено');

    resolveCheck?.('missed');
    await Promise.resolve();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Добавлено');
    expect(document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')).toBeNull();
  });

  it('turns pending add-to-home state into a retryable unknown state', async () => {
    installAppMocks();
    const addToHomeScreen = vi.fn();
    const statuses: Array<'missed' | 'unknown'> = ['missed', 'unknown'];
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        addToHomeScreen,
        checkHomeScreenStatus: vi.fn((callback?: (status: 'missed' | 'unknown') => void) => callback?.(statuses.shift() || 'unknown')),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')?.click();
    await Promise.resolve();
    expect(document.body.textContent).toContain('Подтвердите добавление в Telegram');

    document.querySelector<HTMLButtonElement>('[data-action="close-sheet"]')?.click();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();

    const menuDetails = Array.from(document.querySelectorAll('.bottom-sheet .detail-row strong')).map((node) => node.textContent);
    expect(menuDetails).not.toContain('Подтвердите добавление в Telegram');
    expect(document.body.textContent).toContain('Добавить на главный экран');
    expect(document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')).not.toBeNull();
  });

  it('does not call native add-to-home when Telegram already reports added', async () => {
    installAppMocks();
    const addToHomeScreen = vi.fn();
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        addToHomeScreen,
        checkHomeScreenStatus: vi.fn((callback?: (status: 'added') => void) => callback?.('added')),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(document.body.textContent).toContain('Добавлено');
    expect(document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')).toBeNull();
    expect(addToHomeScreen).not.toHaveBeenCalled();
  });

  it('requests native add-to-home for unknown status and degrades safely without native support', async () => {
    installAppMocks();
    const addToHomeScreen = vi.fn();
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        addToHomeScreen,
        checkHomeScreenStatus: vi.fn((callback?: (status: 'unknown') => void) => callback?.('unknown')),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')?.click();
    await Promise.resolve();

    expect(addToHomeScreen).toHaveBeenCalledTimes(1);

    vi.resetModules();
    vi.doUnmock('../src/api');
    const api = installAppMocks();
    document.body.innerHTML = '<div id="app">Загрузка КопиPaste…</div>';
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        platform: 'tdesktop',
        ready: vi.fn(),
        expand: vi.fn(),
        onEvent: vi.fn(),
        BackButton: { onClick: vi.fn(), show: vi.fn(), hide: vi.fn() },
      }
    } as typeof window.Telegram;

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(document.body.textContent).toContain('поддерживаемых мобильных версиях Telegram');
    expect(api.track).not.toHaveBeenCalledWith('mini_app_add_to_home_requested', { source: 'mini_app' });
  });

  it('opens merchant analytics operations with canonical merchant key', async () => {
    const api = installAppMocks();
    const data = analyticsData(['RUB'], 'RUB');
    data.selected_detail = {
      kind: 'merchant',
      title: 'Яндекс Лавка',
      merchant_key: 'яндекс лавка',
      currency: 'RUB',
      operation_type: 'expense',
      total: '800.00',
      previous_total: '300.00',
      delta: '500.00',
      pct: '166.67',
      state: 'ok',
      operation_count: 2,
      previous_operation_count: 1,
      average_check: '400.00',
      previous_average_check: '300.00',
      operations: [],
      operation_scope: {
        workspace_id: 10,
        period: 'custom',
        start_date: '2026-08-01',
        end_date: '2026-08-07',
        operation_type: 'expense',
        category: 'all',
        currency: 'RUB',
        merchant_key: 'яндекс лавка',
      },
    };
    api.analytics.mockResolvedValue(data);
    api.operations.mockResolvedValue({ items: [], has_more: false, limit: 30, offset: 0, period: data.period });

    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="analytics-open-operations"]')?.click();
    await flush(8);

    expect(api.operations).toHaveBeenCalledWith(10, expect.objectContaining({
      currency: 'RUB',
      merchant_key: 'яндекс лавка',
      category_key: undefined,
    }), 0, '');
    expect(api.operations.mock.calls[0][1]).not.toHaveProperty('merchant');
  });

  it('falls back from stale Analytics EUR to RUB after scope changes without a reload loop', async () => {
    const api = installAppMocks();
    let scope: 'mixed' | 'rub' = 'mixed';
    api.analytics.mockImplementation(async (_workspaceId, filters): Promise<any> => {
      if (scope === 'mixed') return analyticsData(['RUB', 'EUR'], String(filters.currency || '') || null);
      if (filters.currency === 'EUR') return analyticsData(['RUB'], null);
      return analyticsData(['RUB'], 'RUB');
    });

    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);

    const currencySelect = document.querySelector<HTMLSelectElement>('[data-action="chart-currency"][data-chart="analytics"]');
    expect(currencySelect).not.toBeNull();
    currencySelect!.value = 'EUR';
    currencySelect!.dispatchEvent(new Event('change', { bubbles: true }));
    await flush(8);
    expect(api.analytics.mock.calls.at(-1)?.[1].currency).toBe('EUR');

    scope = 'rub';
    const beforeSwitch = api.analytics.mock.calls.length;
    const period = document.querySelector<HTMLSelectElement>('[data-action="period"]');
    period!.value = 'previous_month';
    period!.dispatchEvent(new Event('change', { bubbles: true }));
    await flush(10);

    const switchCurrencies = api.analytics.mock.calls.slice(beforeSwitch).map((call) => call[1].currency);
    expect(switchCurrencies).toEqual(['EUR', 'RUB']);
    expect(document.querySelector<HTMLSelectElement>('[data-action="chart-currency"][data-chart="analytics"]')).toBeNull();
    expect(document.body.textContent).toContain('Расходы · RUB');
    expect(document.body.textContent).not.toContain('Не получилось выполнить действие');
  });

  it('clears stale Analytics currency for an empty scope without retrying forever', async () => {
    const api = installAppMocks();
    let emptyScope = false;
    api.analytics.mockImplementation(async (_workspaceId, filters): Promise<any> => {
      if (emptyScope) return analyticsData([], null);
      return analyticsData(['RUB'], String(filters.currency || 'RUB'));
    });

    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="analytics"]')?.click();
    await flush(8);
    expect(api.analytics.mock.calls.at(-1)?.[1].currency).toBeUndefined();

    emptyScope = true;
    const beforeSwitch = api.analytics.mock.calls.length;
    const period = document.querySelector<HTMLSelectElement>('[data-action="period"]');
    period!.value = 'previous_month';
    period!.dispatchEvent(new Event('change', { bubbles: true }));
    await flush(10);

    expect(api.analytics.mock.calls.slice(beforeSwitch)).toHaveLength(1);
    expect(document.body.textContent).toContain('Нет структуры');
    expect(document.body.textContent).not.toContain('Не получилось выполнить действие');
  });

  it('saves Vacation Mode without changing notification switches', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="profile"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="profile-section"][data-section="behaviour"]')?.click();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-action="vacation-open"]')?.click();
    const form = document.querySelector<HTMLFormElement>('form[data-action="vacation-save"]')!;
    form.querySelector<HTMLInputElement>('[name="enabled"]')!.checked = true;
    form.querySelector<HTMLInputElement>('[name="start_date"]')!.value = '2026-08-13';
    form.querySelector<HTMLInputElement>('[name="end_date"]')!.value = '2026-08-20';
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush(8);

    expect(api.setVacation).toHaveBeenCalledWith({ enabled: true, start_date: '2026-08-13', end_date: '2026-08-20' });
    expect(document.querySelector('form[data-action="vacation-save"]')).toBeNull();
    expect('updateNotificationPreferences' in api).toBe(false);
  });

  it('walks history deletion back from confirmation to preview and period selection', async () => {
    let backHandler: (() => void) | undefined;
    window.Telegram!.WebApp!.BackButton!.onClick = vi.fn((callback: () => void) => { backHandler = callback; });
    const api = installAppMocks();
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="profile"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="profile-section"][data-section="privacy"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="privacy-history-open"]')?.click();
    const form = document.querySelector<HTMLFormElement>('form[data-action="privacy-history-preview"]')!;
    form.querySelector<HTMLSelectElement>('[name="period"]')!.value = 'all';
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush(8);
    expect(api.previewHistoryDeletion).toHaveBeenCalledWith('all');
    document.querySelector<HTMLButtonElement>('[data-action="privacy-history-confirm"]')?.click();
    expect(document.body.textContent).toContain('Удалить выбранные данные');
    backHandler?.();
    expect(document.body.textContent).toContain('Будет удалено');
    backHandler?.();
    expect(document.querySelector('form[data-action="privacy-history-preview"]')).not.toBeNull();
  });

  it('updates category relevance and stays in the same category mode', async () => {
    const api = installAppMocks();
    await import('../src/main');
    await flush();
    document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="plans-mode"][data-mode="categories"]')?.click();
    await flush(8);
    document.querySelector<HTMLButtonElement>('[data-action="category-open"]')?.click();
    document.querySelector<HTMLButtonElement>('[data-action="category-preference-relevance"]')?.click();
    await flush(8);

    expect(api.updateCategoryPreference).toHaveBeenCalledWith('food', { workspace_id: 10, type: 'expense', priority: 'normal', relevant: false });
    window.Telegram!.WebApp!.BackButton!.onClick;
    expect(document.body.textContent).toContain('Категории');
  });
});
