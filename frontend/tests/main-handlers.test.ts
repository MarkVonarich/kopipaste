import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const plansData = {
  read_only: false,
  goals: [],
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

function installAppMocks() {
  const api = {
    bootstrap: vi.fn(async () => ({
      user: { currency: 'RUB', timezone: 'Europe/Moscow' },
      workspaces: [{ workspace_id: 10, name: 'Family', kind: 'group', role: 'member', active: true, read_only: false }],
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
    })),
    operations: vi.fn(),
    analytics: vi.fn(),
    plans: vi.fn(async () => plansData),
    profile: vi.fn(),
    categories: vi.fn(async (_workspaceId, type) => ({
      items: type === 'income'
        ? [{ name: 'Salary', normalized_name: 'salary', type: 'Доходы', source: 'custom', operation_count: 0, has_budget: false }]
        : [{ name: 'Food', normalized_name: 'food', type: 'Расходы', source: 'custom', operation_count: 0, has_budget: false }],
      read_only: false,
    })),
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
    updateLimit: vi.fn(async () => ({ limit: plansData.general_limits[0] })),
    deleteLimit: vi.fn(),
    track: vi.fn(async () => undefined),
  };
  vi.doMock('../src/api', () => ({ api, requestId: () => 'request-id' }));
  return api;
}

async function openPlansLimits() {
  document.querySelector<HTMLButtonElement>('[data-tab="plans"]')?.click();
  await Promise.resolve();
  await Promise.resolve();
  document.querySelector<HTMLButtonElement>('[data-action="plans-mode"][data-mode="limits"]')?.click();
  await Promise.resolve();
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
});
