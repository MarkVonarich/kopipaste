import './styles.css';
import Chart from 'chart.js/auto';
import { api, requestId, type GoalMovementPayload, type GoalPayload, type LimitPayload, type OperationPayload, type OperationsResponse, type Overview, type PlansResponse, type AnalyticsResponse } from './api';
import { decimalStringToVisualPoint } from './chartDecimal';
import { formatMoneyString, normalizeMoneyText } from './money';
import { getTelegramWebApp, initTelegramShell } from './telegram';
import { initialState, persistState, pickInitialWorkspace } from './state';
import type { AppState, BudgetLimit, CategoryOption, Goal, Operation, OperationType, PeriodKey, ThemeMode, Workspace } from './types';
import { AppShell } from './components/AppShell';
import { BottomNavigation } from './components/BottomNavigation';
import { BottomSheet } from './components/BottomSheet';
import { ConfirmDialog } from './components/ConfirmDialog';
import { HomeScreen } from './components/HomeScreen';
import { OperationsScreen } from './components/OperationsScreen';
import { AnalyticsScreen } from './components/AnalyticsScreen';
import { GoalContributionForm, GoalForm, LimitForm, PlansScreen } from './components/PlansScreen';
import { AdditionalMenu, InfoPanel, ProfileScreen } from './components/ProfileScreen';
import { LoadingState, ErrorState } from './components/States';
import { TransactionForm } from './components/TransactionForm';

const appRoot = document.querySelector<HTMLDivElement>('#app');
if (!appRoot) throw new Error('Missing app root');
const app: HTMLDivElement = appRoot;

let state: AppState = initialState();
let overview: Overview | null = null;
let operations: OperationsResponse | null = null;
let analytics: AnalyticsResponse | null = null;
let plans: PlansResponse | null = null;
let profile: Awaited<ReturnType<typeof api.profile>> | null = null;
let selectedOperation: Operation | null = null;
let selectedGoal: Goal | null = null;
let selectedLimit: BudgetLimit | null = null;
let categoryOptions: CategoryOption[] = [];
let toastTimer = 0;
let chartInstances: Chart[] = [];

function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function applyTheme(mode: ThemeMode): void {
  const tg = getTelegramWebApp();
  const effective = mode === 'telegram' ? tg?.colorScheme || 'light' : mode;
  document.documentElement.dataset.theme = effective;
}

function workspaceLabel(workspace: Workspace): string {
  if (workspace.workspace_id === 'all') return 'Все пространства';
  return workspace.name || 'Личное';
}

function currentWorkspace(): Workspace | undefined {
  return state.boot?.workspaces.find((workspace) => workspace.workspace_id === state.workspaceId);
}

function canWrite(): boolean {
  const workspace = currentWorkspace();
  return Boolean(workspace && workspace.workspace_id !== 'all' && !workspace.read_only);
}

function operationAmount(op: Operation): string {
  return op.amount_text || formatMoneyString(op.amount, op.currency);
}

function renderTopbar(): string {
  const workspaceOptions = (state.boot?.workspaces || [])
    .map((workspace) => `<option value="${esc(workspace.workspace_id)}" ${workspace.workspace_id === state.workspaceId ? 'selected' : ''}>${esc(workspaceLabel(workspace))}</option>`)
    .join('');
  const periodOptions: Array<[PeriodKey, string]> = [
    ['current_month', 'Этот месяц'],
    ['previous_month', 'Прошлый месяц'],
    ['last_30', '30 дней'],
    ['custom', 'Период']
  ];
  return `
    <div class="toolbar">
      <select class="select" data-action="workspace">${workspaceOptions}</select>
      <select class="select" data-action="period">
        ${periodOptions.map(([key, label]) => `<option value="${key}" ${state.period.period === key ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
    </div>
    ${state.period.period === 'custom' ? `
      <div class="toolbar">
        <input class="input" type="date" data-action="start-date" value="${esc(state.period.start_date || '')}" />
        <input class="input" type="date" data-action="end-date" value="${esc(state.period.end_date || '')}" />
      </div>
    ` : ''}
  `;
}

function renderNav(): string {
  return BottomNavigation(state.tab);
}

function renderHome(): string {
  return HomeScreen(overview, overview?.recent_operations || [], state.boot?.user.currency || 'RUB', canWrite());
}

function renderOperations(): string {
  return OperationsScreen(operations, canWrite(), esc(state.search));
}

function renderAnalytics(): string {
  return AnalyticsScreen(analytics, state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' });
}

function renderPlans(): string {
  return PlansScreen(plans, state.plansMode || 'goals', canWrite());
}

function renderProfile(): string {
  return ProfileScreen(profile, state.boot?.workspaces || [], state.theme);
}

function renderSheet(): string {
  if (state.confirmDeleteId && selectedOperation) {
    return ConfirmDialog(state.confirmDeleteId, `${selectedOperation.category} · ${operationAmount(selectedOperation)}`);
  }
  if (state.confirmLimitDeleteId && selectedLimit) {
    return ConfirmDialog(state.confirmLimitDeleteId, `${selectedLimit.title} · ${formatMoneyString(selectedLimit.amount, selectedLimit.currency)}`, 'Удалить лимит?', 'confirm-limit-delete');
  }
  if (!state.sheet && !selectedOperation) return '';
  if (state.sheet === 'goal-create') {
    return BottomSheet('Новая цель', GoalForm(null, state.saving, state.saveError, state.goalPlanPreview, state.goalDraft));
  }
  if (state.sheet === 'goal-edit' && selectedGoal) {
    return BottomSheet('Изменить цель', GoalForm(selectedGoal, state.saving, state.saveError, state.goalPlanPreview, state.goalDraft));
  }
  if (state.sheet === 'goal-contribution' && selectedGoal) {
    return BottomSheet('Пополнить цель', GoalContributionForm(selectedGoal, state.goalIdempotencyKey || requestId(), state.saving, state.saveError));
  }
  if (state.sheet === 'limit-create') {
    return BottomSheet('Новый лимит', LimitForm(null, categoryOptions, state.saving, state.saveError));
  }
  if (state.sheet === 'limit-edit' && selectedLimit) {
    return BottomSheet('Изменить лимит', LimitForm(selectedLimit, categoryOptions, state.saving, state.saveError));
  }
  if (state.sheet === 'premium') {
    return BottomSheet('Premium', InfoPanel('Premium', profile?.premium?.description || 'Premium-раздел пока информационный.'));
  }
  if (state.sheet === 'export') {
    return BottomSheet('Экспорт и данные', InfoPanel('Экспорт', profile?.export?.privacy_note || 'Экспорт использует существующий Telegram flow.'));
  }
  if (state.sheet === 'menu') {
    const nav = navigator as Navigator & { standalone?: boolean };
    const canAdd = 'standalone' in nav || 'BeforeInstallPromptEvent' in window;
    return BottomSheet('Меню', AdditionalMenu(profile, canAdd));
  }
  if (state.sheet === 'actions') {
    return BottomSheet('Добавить операцию', `
      <div class="form-grid">
        <button class="button primary" data-action="open-add" data-kind="expense">Расход</button>
        <button class="button" data-action="open-add" data-kind="income">Доход</button>
      </div>
    `);
  }
  if (selectedOperation) {
    const op = selectedOperation;
    return BottomSheet(esc(op.category), `
          <div class="detail-grid">
            <div class="detail-row"><span>Сумма</span><strong>${esc(operationAmount(op))}</strong></div>
            <div class="detail-row"><span>Тип</span><strong>${esc(op.type)}</strong></div>
            <div class="detail-row"><span>Дата</span><strong>${esc(op.op_date)}</strong></div>
            <div class="detail-row"><span>Автор</span><strong>${esc(op.actor_user_id || '-')}</strong></div>
            <div class="detail-row"><span>Пространство</span><strong>${esc(op.workspace_name || 'Личное')}</strong></div>
            <div class="detail-row"><span>Описание</span><strong>${esc(op.description || '-')}</strong></div>
          </div>
          ${TransactionForm(categoryOptions, { action: 'edit-operation', id: op.id, type: op.type, operation: op, saving: state.saving, error: state.saveError })}
          <button class="button danger" type="button" data-action="delete-operation" data-id="${op.id}" ${state.saving ? 'disabled' : ''}>Удалить</button>
    `);
  }
  const type: OperationType = state.sheet === 'add-income' ? 'Доходы' : 'Расходы';
  return BottomSheet(type === 'Доходы' ? 'Новый доход' : 'Новый расход', TransactionForm(categoryOptions, {
    action: 'create-operation',
    type,
    operation: state.formDraft as Operation | undefined,
    saving: state.saving,
    error: state.saveError,
  }));
}

function render(): void {
  applyTheme(state.theme);
  chartInstances.forEach((chart) => chart.destroy());
  chartInstances = [];
  const screen = state.loading
    ? LoadingState()
    : state.error
      ? ErrorState(esc(state.error))
      : state.tab === 'operations'
        ? renderOperations()
        : state.tab === 'analytics'
          ? renderAnalytics()
          : state.tab === 'plans'
            ? renderPlans()
            : state.tab === 'profile'
              ? renderProfile()
              : renderHome();
  app.innerHTML = `${AppShell(state, renderTopbar(), screen)}${renderNav()}${renderSheet()}`;
  wireEvents();
  syncGoalScheduleFields();
  renderCharts();
  const tg = getTelegramWebApp();
  if (tg?.BackButton) {
    if (state.confirmDeleteId || state.sheet || selectedOperation) tg.BackButton.show();
    else tg.BackButton.hide();
  }
}

function safeError(error: unknown): string {
  const code = typeof error === 'object' && error && 'code' in error ? String((error as { code?: string }).code) : '';
  return {
    unauthorized: 'Не удалось подтвердить Telegram-вход.',
    workspace_access_denied: 'Нет доступа к этому пространству.',
    workspace_read_only: 'Это пространство доступно только для чтения.',
    concrete_workspace_required: 'Выберите одно пространство.',
    category_not_available: 'Выберите категорию из списка.',
    idempotency_conflict: 'Эта попытка сохранения уже использовалась для другой операции.',
    idempotency_pending: 'Операция уже сохраняется. Подождите немного.',
    goal_preview_stale: 'План изменился. Обновите предпросмотр.',
    rate_limited: 'Слишком много действий подряд. Попробуйте позже.',
    miniapp_not_configured: 'Mini App ещё не настроен на сервере.',
  }[code] || 'Не получилось выполнить действие. Попробуйте ещё раз.';
}

function showToast(text: string): void {
  window.clearTimeout(toastTimer);
  const existing = document.querySelector('.toast');
  existing?.remove();
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = text;
  document.body.appendChild(node);
  toastTimer = window.setTimeout(() => node.remove(), 2600);
}

async function loadScreen(): Promise<void> {
  if (!state.boot) return;
  state.loading = true;
  state.error = undefined;
  render();
  try {
    if (state.tab === 'home') overview = await api.overview(state.workspaceId, state.period);
    if (state.tab === 'operations') operations = await api.operations(state.workspaceId, state.period, 0, state.search);
    if (state.tab === 'analytics') {
      let response = await api.analytics(state.workspaceId, {
        ...state.period,
        category_type: state.analyticsFilters?.categoryType,
        radar_type: state.analyticsFilters?.radarType,
        currency: state.analyticsFilters?.radarCurrency
      } as typeof state.period);
      if ((response.radar_available_currencies.length > 1 || response.available_currencies.length > 1) && !state.analyticsFilters?.radarCurrency) {
        const firstCurrency = response.radar_available_currencies[0] || response.available_currencies[0];
        state.analyticsFilters = {
          categoryType: state.analyticsFilters?.categoryType || 'expense',
          dynamicsType: state.analyticsFilters?.dynamicsType || 'both',
          radarType: state.analyticsFilters?.radarType || 'expense',
          categoryCurrency: state.analyticsFilters?.categoryCurrency || response.available_currencies[0],
          dynamicsCurrency: state.analyticsFilters?.dynamicsCurrency || response.available_currencies[0],
          radarCurrency: firstCurrency
        };
        response = await api.analytics(state.workspaceId, {
          ...state.period,
          category_type: state.analyticsFilters.categoryType,
          radar_type: state.analyticsFilters.radarType,
          currency: firstCurrency
        } as typeof state.period);
      }
      overview = response.overview;
      analytics = response;
    }
    if (state.tab === 'plans') plans = await api.plans(state.workspaceId);
    if (state.tab === 'profile') profile = await api.profile();
  } catch (error) {
    state.error = safeError(error);
  } finally {
    state.loading = false;
    persistState(state);
    render();
  }
}

async function bootstrap(): Promise<void> {
  initTelegramShell();
  const tg = getTelegramWebApp();
  tg?.onEvent('themeChanged', () => applyTheme(state.theme));
  tg?.BackButton?.onClick(() => closeSheet());
  try {
    const boot = await api.bootstrap();
    state.boot = boot;
    state.theme = state.theme || boot.theme;
    state.workspaceId = pickInitialWorkspace(boot.workspaces, state.workspaceId);
    await loadScreen();
  } catch (error) {
    state.loading = false;
    state.error = safeError(error);
    render();
  }
}

function closeSheet(): void {
  if (state.confirmDeleteId) {
    state.confirmDeleteId = undefined;
    render();
    return;
  }
  if (state.confirmLimitDeleteId) {
    state.confirmLimitDeleteId = undefined;
    render();
    return;
  }
  if (state.dirty && !window.confirm('Закрыть без сохранения?')) return;
  state.sheet = null;
  selectedOperation = null;
  selectedGoal = null;
  selectedLimit = null;
  state.saveError = undefined;
  state.saving = false;
  state.dirty = false;
  state.addIdempotencyKey = undefined;
  state.goalIdempotencyKey = undefined;
  state.goalCreateIdempotencyKey = undefined;
  state.limitCreateIdempotencyKey = undefined;
  state.goalPlanPreview = undefined;
  state.goalPreviewPayloadHash = undefined;
  state.goalDraft = undefined;
  state.confirmLimitDeleteId = undefined;
  state.formDraft = undefined;
  render();
}

function syncGoalScheduleFields(): void {
  const form = app.querySelector<HTMLFormElement>('form[data-action="create-goal"], form[data-action="save-goal"]');
  if (!form) return;
  const frequency = form.querySelector<HTMLSelectElement>('select[name="frequency"]')?.value || 'none';
  form.querySelectorAll<HTMLElement>('[data-schedule]').forEach((node) => {
    node.hidden = node.dataset.schedule !== frequency;
  });
}

function renderCharts(): void {
  if (state.tab !== 'analytics' || !analytics) return;
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue('--tg-theme-button-color').trim() || styles.getPropertyValue('--accent').trim() || '#0a7a75';
  const destructive = styles.getPropertyValue('--danger').trim() || '#b83242';
  const positive = styles.getPropertyValue('--positive').trim() || '#147a43';
  const categoryCurrency = state.analyticsFilters?.categoryCurrency || analytics.available_currencies[0];
  const dynamicsCurrency = state.analyticsFilters?.dynamicsCurrency || analytics.available_currencies[0];
  const categoryCanvas = document.querySelector<HTMLCanvasElement>('#categoryChart');
  const categoryItems = categoryCurrency ? analytics.category_structure.currency_groups[categoryCurrency]?.items || [] : analytics.category_structure.items;
  if (categoryCanvas && categoryItems.length) {
    chartInstances.push(new Chart(categoryCanvas, {
      type: 'bar',
      data: {
        labels: categoryItems.map((item) => `${item.category} · ${item.currency}`),
        datasets: [{ label: `Доля · ${categoryCurrency}`, data: categoryItems.map((item) => item.share), backgroundColor: accent }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${categoryItems[ctx.dataIndex]?.share ?? 0}% · ${formatMoneyString(categoryItems[ctx.dataIndex]?.total || '0.00', categoryItems[ctx.dataIndex]?.currency || categoryCurrency)}` } }
        },
        scales: { x: { beginAtZero: true, max: 100 } }
      }
    }));
  }
  const dynamicsCanvas = document.querySelector<HTMLCanvasElement>('#dynamicsChart');
  const dynamicsItems = dynamicsCurrency ? analytics.time_dynamics.items.filter((item) => item.currency === dynamicsCurrency) : analytics.time_dynamics.items;
  if (dynamicsCanvas && dynamicsItems.length) {
    const labels = dynamicsItems.map((item) => item.date);
    const mode = state.analyticsFilters?.dynamicsType || 'both';
    const datasets = [];
    const expensePoints = dynamicsItems.map((item) => decimalStringToVisualPoint(item.expense));
    const incomePoints = dynamicsItems.map((item) => decimalStringToVisualPoint(item.income));
    if (mode !== 'income') datasets.push({ label: `Расходы · ${dynamicsCurrency}`, data: expensePoints.map((point) => point.value), borderColor: destructive, backgroundColor: 'rgba(184,50,66,.18)' });
    if (mode !== 'expense') datasets.push({ label: `Доходы · ${dynamicsCurrency}`, data: incomePoints.map((point) => point.value), borderColor: positive, backgroundColor: 'rgba(20,122,67,.18)' });
    chartInstances.push(new Chart(dynamicsCanvas, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const source = ctx.datasetIndex === 0 && mode !== 'income' ? expensePoints : incomePoints;
                return `${ctx.dataset.label}: ${formatMoneyString(source[ctx.dataIndex]?.original || '0.00', dynamicsCurrency)}`;
              }
            }
          }
        },
        scales: { y: { beginAtZero: true } }
      }
    }));
  }
}

async function reloadActive(): Promise<void> {
  await loadScreen();
}

function formPayload(form: HTMLFormElement): OperationPayload {
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    type: String(data.get('type') || selectedOperation?.type || '') as OperationPayload['type'],
    amount: normalizeMoneyText(String(data.get('amount') || '0')),
    category: String(data.get('category') || '').trim(),
    description: String(data.get('description') || '').trim(),
    op_date: String(data.get('op_date') || ''),
    idempotency_key: state.addIdempotencyKey || requestId()
  };
}

function goalPayload(form: HTMLFormElement): GoalPayload {
  const data = new FormData(form);
  const frequency = String(data.get('frequency') || 'none') as GoalPayload['frequency'];
  const payload: GoalPayload = {
    workspace_id: state.workspaceId,
    idempotency_key: state.goalCreateIdempotencyKey,
    preview_payload_hash: state.goalPreviewPayloadHash,
    title: String(data.get('title') || '').trim(),
    target_amount: normalizeMoneyText(String(data.get('target_amount') || '0')),
    current_amount: normalizeMoneyText(String(data.get('current_amount') || '0')),
    deadline: String(data.get('deadline') || ''),
    strategy: String(data.get('strategy') || 'none') as GoalPayload['strategy'],
    frequency,
    comfortable_amount: String(data.get('comfortable_amount') || '').trim() ? normalizeMoneyText(String(data.get('comfortable_amount'))) : '',
    reminders_enabled: data.get('reminders_enabled') === 'on',
  };
  if (frequency === 'monthly') payload.day = globalThis.parseInt(String(data.get('day') || ''), 10);
  if (frequency === 'twice_monthly') payload.days = [
    globalThis.parseInt(String(data.get('day_first') || ''), 10),
    globalThis.parseInt(String(data.get('day_second') || ''), 10)
  ].filter((value) => !globalThis.Number.isNaN(value));
  if (frequency === 'weekly') payload.weekday = globalThis.parseInt(String(data.get('weekday') || ''), 10);
  return payload;
}

function goalMovementPayload(form: HTMLFormElement): GoalMovementPayload {
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    movement_type: String(data.get('movement_type') || 'contribution') as GoalMovementPayload['movement_type'],
    amount: String(data.get('amount') || '').trim() ? normalizeMoneyText(String(data.get('amount'))) : undefined,
    new_balance: String(data.get('new_balance') || '').trim() ? normalizeMoneyText(String(data.get('new_balance'))) : undefined,
    idempotency_key: String(data.get('idempotency_key') || state.goalIdempotencyKey || requestId())
  };
}

function limitPayload(form: HTMLFormElement): LimitPayload {
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    idempotency_key: state.limitCreateIdempotencyKey,
    title: String(data.get('title') || '').trim(),
    scope: String(data.get('scope') || 'category') as LimitPayload['scope'],
    category: String(data.get('category') || '').trim(),
    amount: normalizeMoneyText(String(data.get('amount') || '0')),
    period: String(data.get('period') || 'month') as LimitPayload['period'],
    alerts_enabled: data.get('alerts_enabled') === 'on',
  };
}

async function loadCategoriesFor(type: 'expense' | 'income' | 'Расходы' | 'Доходы'): Promise<void> {
  try {
    const response = await api.categories(state.workspaceId, type);
    categoryOptions = response.items;
  } catch {
    categoryOptions = [];
  }
}

function wireEvents(): void {
  app.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((button) => {
    button.addEventListener('click', async () => {
      const tab = button.dataset.tab as AppState['tab'];
      state.tab = tab;
      state.sheet = null;
      selectedOperation = null;
      await api.track('mini_app_tab_opened', { tab });
      await loadScreen();
    });
  });

  app.querySelector<HTMLButtonElement>('[data-action="retry"]')?.addEventListener('click', () => void reloadActive());
  app.querySelector<HTMLSelectElement>('[data-action="workspace"]')?.addEventListener('change', async (event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    state.workspaceId = value === 'all' ? 'all' : value === 'null' || value === '' ? null : Number(value);
    await api.track('mini_app_workspace_changed', { scope: String(state.workspaceId) });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="period"]')?.addEventListener('change', async (event) => {
    const period = (event.currentTarget as HTMLSelectElement).value as PeriodKey;
    if (period === 'custom') {
      const today = new Date().toISOString().slice(0, 10);
      state.period = { period, start_date: today, end_date: today };
    } else {
      state.period = { period };
    }
    await api.track('mini_app_period_changed', { period: state.period.period });
    await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="search"]')?.addEventListener('change', async (event) => {
    state.search = (event.currentTarget as HTMLInputElement).value.trim();
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="load-more"]')?.addEventListener('click', async () => {
    if (!operations) return;
    const next = await api.operations(state.workspaceId, state.period, operations.offset + operations.limit, state.search);
    operations = { ...next, items: [...operations.items, ...next.items] };
    render();
  });
  app.querySelector<HTMLInputElement>('[data-action="start-date"]')?.addEventListener('change', async (event) => {
    state.period = { ...state.period, start_date: (event.currentTarget as HTMLInputElement).value };
    if (state.period.end_date) await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="end-date"]')?.addEventListener('change', async (event) => {
    state.period = { ...state.period, end_date: (event.currentTarget as HTMLInputElement).value };
    if (state.period.start_date) await loadScreen();
  });
  app.querySelectorAll<HTMLSelectElement>('[data-action="chart-filter"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const chart = select.dataset.chart || '';
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
      if (chart === 'category') state.analyticsFilters.categoryType = select.value as 'expense' | 'income';
      if (chart === 'dynamics') state.analyticsFilters.dynamicsType = select.value as 'expense' | 'income' | 'both';
      if (chart === 'radar') state.analyticsFilters.radarType = select.value as 'expense' | 'income';
      await api.track('mini_app_analytics_chart_filter_changed', { chart_type: chart, filter_kind: select.value, source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelectorAll<HTMLSelectElement>('[data-action="chart-currency"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const chart = select.dataset.chart || '';
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
      if (chart === 'category') state.analyticsFilters.categoryCurrency = select.value;
      if (chart === 'dynamics') state.analyticsFilters.dynamicsCurrency = select.value;
      if (chart === 'radar') state.analyticsFilters.radarCurrency = select.value;
      await api.track('mini_app_analytics_chart_filter_changed', { chart_type: chart, filter_kind: 'currency', source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="plans-mode"]').forEach((button) => {
    button.addEventListener('click', () => {
      state.plansMode = button.dataset.mode === 'limits' ? 'limits' : 'goals';
      render();
    });
  });

  app.querySelectorAll<HTMLButtonElement>('[data-action="open-add"]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.sheet = button.dataset.kind === 'income' ? 'add-income' : 'add-expense';
      selectedOperation = null;
      state.addIdempotencyKey = requestId();
      state.saveError = undefined;
      state.formDraft = undefined;
      state.dirty = false;
      await loadCategoriesFor(button.dataset.kind === 'income' ? 'income' : 'expense');
      await api.track('mini_app_transaction_add_opened', { action: button.dataset.kind || 'expense' });
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="open-actions"]')?.addEventListener('click', () => {
    state.sheet = 'actions';
    selectedOperation = null;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="go-operations"]')?.addEventListener('click', async () => {
    state.tab = 'operations';
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="goal-create"]')?.addEventListener('click', () => {
    state.sheet = 'goal-create';
    state.goalCreateIdempotencyKey = requestId();
    state.goalPlanPreview = undefined;
    state.goalPreviewPayloadHash = undefined;
    state.goalDraft = undefined;
    state.saveError = undefined;
    state.dirty = false;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-edit"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = (plans?.goals || []).find((goal) => goal.id === Number(button.dataset.id)) || null;
      state.sheet = selectedGoal ? 'goal-edit' : null;
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
      state.goalDraft = undefined;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-contribution"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = (plans?.goals || []).find((goal) => goal.id === Number(button.dataset.id)) || null;
      state.goalIdempotencyKey = requestId();
      state.sheet = selectedGoal ? 'goal-contribution' : null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-status"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      state.saving = true;
      render();
      try {
        await api.setGoalStatus(Number(button.dataset.id), state.workspaceId, button.dataset.status || 'active');
        showToast('Цель обновлена');
        await reloadActive();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="limit-create"]')?.addEventListener('click', async () => {
    await loadCategoriesFor('expense');
    state.sheet = 'limit-create';
    state.limitCreateIdempotencyKey = requestId();
    state.saveError = undefined;
    state.dirty = false;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-edit"]').forEach((button) => {
    button.addEventListener('click', async () => {
      await loadCategoriesFor('expense');
      selectedLimit = (plans?.limits || []).find((limit) => limit.id === button.dataset.id) || null;
      state.sheet = selectedLimit ? 'limit-edit' : null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-delete"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedLimit = (plans?.limits || []).find((limit) => limit.id === button.dataset.id) || null;
      state.confirmLimitDeleteId = selectedLimit?.id;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.addEventListener('click', () => {
    state.sheet = 'menu';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="premium-open"]')?.addEventListener('click', async () => {
    await api.premium();
    state.sheet = 'premium';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="export-open"]')?.addEventListener('click', async () => {
    await api.exportInfo();
    state.sheet = 'export';
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="notification-toggle"]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        const notifications = await api.updateNotificationPreferences({ action: 'toggle', key: button.dataset.key });
        if (profile) profile = { ...profile, notifications };
        showToast('Настройка сохранена');
        render();
      } catch (error) {
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="notification-quiet"]')?.addEventListener('click', async () => {
    const notifications = await api.updateNotificationPreferences({ action: 'quiet_toggle' });
    if (profile) profile = { ...profile, notifications };
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="share-app"]')?.addEventListener('click', async () => {
    if (navigator.share) await navigator.share({ title: 'Finuchet', text: 'КопиPaste для учёта финансов' }).catch(() => undefined);
  });
  app.querySelector<HTMLButtonElement>('[data-action="report-issue"]')?.addEventListener('click', () => {
    window.open(profile?.help_url || 'https://t.me/chiracredible', '_blank', 'noreferrer');
  });

  app.querySelectorAll<HTMLButtonElement>('[data-action="operation-detail"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = Number(button.dataset.id);
      selectedOperation = await api.operationDetail(id);
      await loadCategoriesFor(selectedOperation.type);
      state.sheet = null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });

  app.querySelector('[data-action="close-sheet"]')?.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).dataset.action === 'close-sheet') closeSheet();
  });
  app.querySelector('[data-action="close-confirm"]')?.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).dataset.action === 'close-confirm') closeSheet();
  });
  app.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('form input, form textarea, form select').forEach((input) => {
    const invalidateGoalPreview = () => {
      const form = input.closest<HTMLFormElement>('form[data-action="create-goal"], form[data-action="save-goal"]');
      if (!form) return;
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
      form.querySelector<HTMLElement>('[data-testid="goal-plan-preview"]')?.remove();
      const confirm = form.querySelector<HTMLButtonElement>('button[data-submit-mode="confirm"]');
      if (confirm) {
        confirm.disabled = true;
        confirm.hidden = true;
      }
    };
    input.addEventListener('input', () => {
      state.dirty = true;
      invalidateGoalPreview();
    });
    input.addEventListener('change', () => {
      state.dirty = true;
      invalidateGoalPreview();
      if (input.getAttribute('name') === 'frequency') syncGoalScheduleFields();
    });
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const payload = formPayload(form);
    state.formDraft = {
      amount: payload.amount,
      category: payload.category,
      description: payload.description,
      op_date: payload.op_date,
      type: payload.type === 'income' ? 'Доходы' : payload.type === 'expense' ? 'Расходы' : payload.type,
    };
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createOperation(payload);
      state.addIdempotencyKey = undefined;
      state.formDraft = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Операция сохранена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="edit-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const id = Number(form.dataset.id);
    const payload = formPayload(form);
    if (selectedOperation) {
      selectedOperation = {
        ...selectedOperation,
        amount: payload.amount,
        category: payload.category,
        description: payload.description,
        op_date: payload.op_date,
        type: payload.type === 'income' ? 'Доходы' : payload.type === 'expense' ? 'Расходы' : payload.type,
      };
    }
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.updateOperation(id, payload);
      state.dirty = false;
      closeSheet();
      showToast('Операция обновлена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLButtonElement>('[data-action="delete-operation"]')?.addEventListener('click', async (event) => {
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id);
    state.confirmDeleteId = id;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="cancel-delete"]')?.addEventListener('click', () => {
    state.confirmDeleteId = undefined;
    state.confirmLimitDeleteId = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="confirm-delete"]')?.addEventListener('click', async (event) => {
    if (state.saving) return;
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id);
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.deleteOperation(id);
      state.dirty = false;
      state.confirmDeleteId = undefined;
      closeSheet();
      showToast('Операция удалена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="confirm-limit-delete"]')?.addEventListener('click', async (event) => {
    if (state.saving) return;
    const id = String((event.currentTarget as HTMLButtonElement).dataset.id || '');
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.deleteLimit(id, state.workspaceId);
      state.confirmLimitDeleteId = undefined;
      selectedLimit = null;
      showToast('Лимит удалён');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-goal"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const submitter = (event as SubmitEvent).submitter as HTMLButtonElement | null;
    const payload = goalPayload(event.currentTarget as HTMLFormElement);
    state.goalDraft = payload as unknown as Record<string, unknown>;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      if (submitter?.dataset.submitMode !== 'confirm') {
        const preview = await api.goalPlanPreview(payload);
        state.goalPlanPreview = preview.plan_preview;
        state.goalPreviewPayloadHash = preview.plan_preview.preview_payload_hash;
        state.saving = false;
        render();
        return;
      }
      if (!state.goalPlanPreview) throw new Error('preview_required');
      await api.createGoal(payload);
      state.goalCreateIdempotencyKey = undefined;
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
      state.goalDraft = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Цель создана');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="save-goal"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const submitter = (event as SubmitEvent).submitter as HTMLButtonElement | null;
    const payload = goalPayload(form);
    state.goalDraft = payload as unknown as Record<string, unknown>;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      if (submitter?.dataset.submitMode !== 'confirm') {
        const preview = await api.goalPlanPreview(payload, Number(form.dataset.id));
        state.goalPlanPreview = preview.plan_preview;
        state.goalPreviewPayloadHash = preview.plan_preview.preview_payload_hash;
        state.saving = false;
        render();
        return;
      }
      if (!state.goalPlanPreview) throw new Error('preview_required');
      await api.updateGoal(Number(form.dataset.id), payload);
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
      state.goalDraft = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Цель обновлена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="goal-movement"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.addGoalMovement(Number(form.dataset.id), goalMovementPayload(form));
      state.goalIdempotencyKey = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Прогресс цели обновлён');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-limit"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const payload = limitPayload(event.currentTarget as HTMLFormElement);
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createLimit(payload);
      state.limitCreateIdempotencyKey = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Лимит создан');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="save-limit"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const payload = limitPayload(form);
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.updateLimit(form.dataset.id || '', payload);
      state.dirty = false;
      closeSheet();
      showToast('Лимит обновлён');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector('[data-action="theme"]')?.addEventListener('click', async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-theme]');
    if (!button) return;
    const theme = button.dataset.theme as ThemeMode;
    state.theme = theme;
    applyTheme(theme);
    await api.setTheme(theme);
    render();
  });
}

void bootstrap();
