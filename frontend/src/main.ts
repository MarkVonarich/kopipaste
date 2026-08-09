import './styles.css';
import Chart from 'chart.js/auto';
import { api, requestId, type GoalMovementPayload, type GoalPayload, type LimitPayload, type OperationPayload, type OperationsResponse, type Overview, type PlansResponse, type AnalyticsResponse } from './api';
import { decimalStringToVisualPoint } from './chartDecimal';
import { formatMoneyString, normalizeMoneyText } from './money';
import { checkHomeScreenStatus, getTelegramWebApp, hapticDestructive, hapticError, hapticSelection, hapticSuccess, initTelegramShell, prepareTelegramLaunch, requestAddToHomeScreen } from './telegram';
import { initialState, persistState, pickInitialWorkspace } from './state';
import type { AppState, BudgetLimit, CategoryBudgetGroup, CategoryOption, GlobalFinancialFilters, Goal, Operation, OperationType, PeriodKey, Reminder, ThemeMode, Workspace } from './types';
import { AppShell } from './components/AppShell';
import { BottomNavigation } from './components/BottomNavigation';
import { BottomSheet } from './components/BottomSheet';
import { ConfirmDialog } from './components/ConfirmDialog';
import { HomeScreen } from './components/HomeScreen';
import { OperationsScreen } from './components/OperationsScreen';
import { AnalyticsScreen } from './components/AnalyticsScreen';
import { CategoryBudgetForm, CategoryDeleteForm, CategoryForm, GoalContributionForm, GoalForm, LimitForm, PlansScreen, ReminderForm } from './components/PlansScreen';
import { AdditionalMenu, CurrencyForm, ExportForm, InfoPanel, PreferredNameForm, ProfileScreen, QuietHoursForm, TimezoneForm, WorkspaceForm } from './components/ProfileScreen';
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
let selectedReminder: Reminder | null = null;
let selectedCategoryBudget: CategoryBudgetGroup | null = null;
let selectedCategory: CategoryOption | null = null;
let categoryOptions: CategoryOption[] = [];
let globalCategoryOptions: CategoryOption[] = [];
let toastTimer = 0;
let chartInstances: Chart[] = [];
let homeScreenEventsRegistered = false;
let homeScreenCheckSeq = 0;

function showStartupBlocker(message: string): void {
  state.loading = false;
  state.error = undefined;
  app.innerHTML = `<main class="startup-screen" data-state="startup-blocked"><p>${esc(message)}</p></main>`;
}

function installStartupErrorHandlers(): void {
  const showSafeStartupError = () => showStartupBlocker('Не получилось открыть Mini App. Попробуйте ещё раз из Telegram.');
  window.addEventListener('error', showSafeStartupError);
  window.addEventListener('unhandledrejection', showSafeStartupError);
}

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
  const params = tg?.themeParams || {};
  const root = document.documentElement.style;
  const tokenMap: Array<[keyof typeof params, string]> = [
    ['bg_color', '--page'],
    ['secondary_bg_color', '--surface-secondary'],
    ['section_bg_color', '--surface'],
    ['text_color', '--text'],
    ['hint_color', '--text-secondary'],
    ['button_color', '--accent'],
    ['button_text_color', '--accent-text'],
    ['destructive_text_color', '--danger'],
  ];
  if (mode !== 'telegram') {
    for (const [, target] of tokenMap) root.removeProperty(target);
    return;
  }
  for (const [source, target] of tokenMap) {
    const value = params[source];
    if (value) root.setProperty(target, value);
  }
}

function registerHomeScreenEvents(): void {
  if (homeScreenEventsRegistered) return;
  const tg = getTelegramWebApp();
  if (typeof tg?.onEvent !== 'function') return;
  homeScreenEventsRegistered = true;
  tg.onEvent('homeScreenAdded', () => {
    state.homeScreenStatus = 'added';
    showToast('КопиPaste добавлен на главный экран');
    if (state.sheet === 'menu') render();
  });
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

function activeFilters(): GlobalFinancialFilters {
  return state.globalFilters;
}

function setGlobalFilters(next: GlobalFinancialFilters): void {
  state.globalFilters = next;
  state.period = next;
}

function operationAmount(op: Operation): string {
  return op.amount_text || formatMoneyString(op.amount, op.currency);
}

function reminderPayload(form: HTMLFormElement) {
  const data = new FormData(form);
  const repeatRule = String(data.get('repeat_rule') || 'none');
  const interval = Number(data.get('repeat_interval_days') || 0);
  return {
    workspace_id: state.workspaceId,
    title: String(data.get('title') || '').trim(),
    amount: normalizeMoneyText(String(data.get('amount') || '')),
    currency: String(data.get('currency') || '').trim() || undefined,
    category: String(data.get('category') || ''),
    rem_type: String(data.get('rem_type') || 'expense') as 'expense' | 'income',
    event_date: String(data.get('event_date') || ''),
    repeat_rule: repeatRule as 'none' | 'weekly' | 'monthly' | 'yearly' | 'custom_days',
    repeat_interval_days: repeatRule === 'custom_days' ? interval || 1 : null,
    notify_days_before: Number(data.get('notify_days_before') || 1),
    is_active: data.get('is_active') === 'on',
  };
}

function reminderDraftFromForm(form: HTMLFormElement): Record<string, unknown> {
  const data = new FormData(form);
  return {
    title: String(data.get('title') || ''),
    amount: String(data.get('amount') || ''),
    currency: String(data.get('currency') || ''),
    category: String(data.get('category') || ''),
    rem_type: String(data.get('rem_type') || 'expense'),
    event_date: String(data.get('event_date') || ''),
    repeat_rule: String(data.get('repeat_rule') || 'none'),
    repeat_interval_days: String(data.get('repeat_interval_days') || ''),
    notify_days_before: String(data.get('notify_days_before') || '1'),
    is_active: data.get('is_active') === 'on',
  };
}

function categoryBudgetPayload(form: HTMLFormElement) {
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    title: String(data.get('title') || '').trim(),
    amount: normalizeMoneyText(String(data.get('amount') || '')),
    currency: String(data.get('currency') || state.boot?.user.currency || 'RUB'),
    period: String(data.get('period') || 'month') as 'week' | 'month',
    categories: data.getAll('categories').map((item) => String(item)),
    alerts_enabled: data.get('alerts_enabled') === 'on',
  };
}

function renderTopbar(): string {
  const workspaceOptions = (state.boot?.workspaces || [])
    .map((workspace) => `<option value="${esc(workspace.workspace_id)}" ${workspace.workspace_id === state.workspaceId ? 'selected' : ''}>${esc(workspaceLabel(workspace))}</option>`)
    .join('');
  const periodOptions: Array<[PeriodKey, string]> = [
    ['current_week', 'Текущая неделя'],
    ['current_month', 'Этот месяц'],
    ['previous_month', 'Прошлый месяц'],
    ['custom', 'Период']
  ];
  const filters = activeFilters();
  const categoryOptionsHtml = [
    `<option value="all" ${filters.category === 'all' ? 'selected' : ''}>Все категории</option>`,
    ...globalCategoryOptions.map((category) => `<option value="${esc(category.name)}" ${filters.category === category.name ? 'selected' : ''}>${esc(category.name)}</option>`)
  ].join('');
  return `
    <div class="toolbar global-filter-strip" aria-label="Фильтры">
      <label class="toolbar-field"><span>Пространство</span><select class="select compact" data-action="workspace" aria-label="Пространство">${workspaceOptions}</select></label>
      <label class="toolbar-field"><span>Период</span><select class="select compact" data-action="period" aria-label="Период">
        ${periodOptions.map(([key, label]) => `<option value="${key}" ${filters.period === key ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label class="toolbar-field"><span>Тип</span><select class="select compact" data-action="operation-type" aria-label="Тип операции">
        <option value="all" ${filters.operation_type === 'all' ? 'selected' : ''}>Все операции</option>
        <option value="expense" ${filters.operation_type === 'expense' ? 'selected' : ''}>Расходы</option>
        <option value="income" ${filters.operation_type === 'income' ? 'selected' : ''}>Доходы</option>
      </select></label>
      <label class="toolbar-field"><span>Категория</span><select class="select compact" data-action="category-filter" aria-label="Категория">${categoryOptionsHtml}</select></label>
    </div>
    ${filters.period === 'custom' ? `
      <div class="toolbar custom-period">
        <label class="toolbar-field"><span>Начало</span><input class="input compact" type="date" data-action="start-date" value="${esc(filters.start_date || '')}" /></label>
        <label class="toolbar-field"><span>Конец</span><input class="input compact" type="date" data-action="end-date" value="${esc(filters.end_date || '')}" /></label>
      </div>
    ` : ''}
  `;
}

function renderNav(): string {
  return BottomNavigation(state.tab);
}

function renderHome(): string {
  return HomeScreen(overview, overview?.recent_operations || [], state.boot?.user.currency || 'RUB', canWrite(), activeFilters(), {
    challenge: state.homeChallengeIndex || 0,
    focus: state.homeFocusIndex || 0,
    reminder: state.homeReminderIndex || 0,
  });
}

function renderOperations(): string {
  return OperationsScreen(operations, canWrite(), esc(state.search));
}

function renderAnalytics(): string {
  return AnalyticsScreen(analytics, state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' }, activeFilters());
}

function renderPlans(): string {
  return PlansScreen(plans, state.plansMode || 'goals', canWrite());
}

function findLimitById(id: string | undefined): BudgetLimit | null {
  if (!id) return null;
  return [...(plans?.limits || []), ...(plans?.general_limits || [])].find((limit) => limit.id === id) || null;
}

function writableWorkspaces(): Workspace[] {
  return (state.boot?.workspaces || []).filter((workspace) => workspace.workspace_id !== 'all' && workspace.workspace_id !== null && !workspace.read_only);
}

function renderProfile(): string {
  return ProfileScreen(profile, state.boot?.workspaces || [], state.theme, state.profileAccordion);
}

function carouselTotal(kind: 'challenge' | 'focus' | 'reminder'): number {
  if (kind === 'challenge') return Math.max(1, overview?.challenges?.length || (overview?.challenge ? 1 : 0));
  if (kind === 'focus') return Math.max(1, overview?.focus_items?.length || (overview?.focus ? 1 : 0));
  return Math.max(1, overview?.reminders?.length || (overview?.reminder ? 1 : 0));
}

function setHomeCarouselIndex(kind: 'challenge' | 'focus' | 'reminder', index: number, direction: string): void {
  const total = carouselTotal(kind);
  const clamped = Math.max(0, Math.min(index, total - 1));
  if (kind === 'challenge') state.homeChallengeIndex = clamped;
  if (kind === 'focus') state.homeFocusIndex = clamped;
  if (kind === 'reminder') state.homeReminderIndex = clamped;
  void api.track(`mini_app_${kind}_carousel_changed`, { direction, position: String(clamped + 1), total: String(total), source: 'mini_app' });
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
    return BottomSheet('Новый лимит', LimitForm(null, categoryOptions, state.saving, state.saveError, state.limitCreateScope || 'category'));
  }
  if (state.sheet === 'limit-edit' && selectedLimit) {
    return BottomSheet('Изменить лимит', LimitForm(selectedLimit, categoryOptions, state.saving, state.saveError));
  }
  if (state.sheet === 'reminder-create') {
    return BottomSheet('Новое напоминание', ReminderForm(null, categoryOptions, state.saving, state.saveError, state.reminderDraft as Record<string, unknown> | undefined));
  }
  if (state.sheet === 'reminder-edit' && selectedReminder) {
    return BottomSheet('Изменить напоминание', ReminderForm(selectedReminder, categoryOptions, state.saving, state.saveError, state.reminderDraft as Record<string, unknown> | undefined));
  }
  if (state.sheet === 'reminder-detail' && selectedReminder) {
    const primary = selectedReminder.status === 'overdue' ? 'Оплачено — записать' : 'Записать операцию';
    return BottomSheet(esc(selectedReminder.title), `
      <div class="detail-grid">
        <div class="detail-row"><span>Сумма</span><strong>${esc(selectedReminder.amount_text)}</strong></div>
        <div class="detail-row"><span>Тип</span><strong>${esc(selectedReminder.rem_type)}</strong></div>
        <div class="detail-row"><span>Категория</span><strong>${esc(selectedReminder.category)}</strong></div>
        <div class="detail-row"><span>Дата</span><strong>${esc(selectedReminder.event_date)}</strong></div>
        <div class="detail-row"><span>Повтор</span><strong>${esc(selectedReminder.repeat_rule)}</strong></div>
        <div class="detail-row"><span>Статус</span><strong>${selectedReminder.is_active ? 'активно' : 'выключено'}</strong></div>
      </div>
      <div class="form-grid">
        <button class="button primary" data-action="reminder-record" data-id="${selectedReminder.id}" ${selectedReminder.is_active ? '' : 'disabled'}>${esc(primary)}</button>
        <button class="button secondary" data-action="reminder-snooze" data-id="${selectedReminder.id}">Напомнить завтра</button>
        <button class="button secondary" data-action="reminder-edit" data-id="${selectedReminder.id}">Изменить</button>
        <button class="button text" data-action="reminder-toggle" data-id="${selectedReminder.id}">${selectedReminder.is_active ? 'Выключить' : 'Включить'}</button>
        <button class="button danger" data-action="reminder-delete" data-id="${selectedReminder.id}">Удалить</button>
        <button class="button text" data-action="go-reminders">Все напоминания</button>
      </div>
    `);
  }
  if (state.sheet === 'reminder-workspace-select' && selectedReminder) {
    const options = writableWorkspaces();
    return BottomSheet('Куда записать операцию', `
      <div class="detail-grid">
        <p class="caption">Выберите пространство, куда записать операцию.</p>
        ${options.map((workspace) => `<button class="button secondary" data-action="reminder-record-workspace" data-id="${selectedReminder?.id}" data-workspace-id="${esc(workspace.workspace_id)}">${esc(workspaceLabel(workspace))}</button>`).join('') || '<p class="error-text">Нет доступного пространства для записи.</p>'}
      </div>
      ${state.saveError ? `<p class="error-text">${esc(state.saveError)}</p>` : ''}
    `);
  }
  if (state.sheet === 'category-budget-create') {
    return BottomSheet('Новый бюджет категорий', CategoryBudgetForm(null, categoryOptions, state.saving, state.saveError, profile?.available_currencies, state.boot?.user.currency || 'RUB'));
  }
  if (state.sheet === 'category-budget-edit' && selectedCategoryBudget) {
    return BottomSheet('Изменить бюджет категорий', CategoryBudgetForm(selectedCategoryBudget, categoryOptions, state.saving, state.saveError, profile?.available_currencies, state.boot?.user.currency || 'RUB'));
  }
  if (state.sheet === 'category-create') {
    return BottomSheet('Новая категория', CategoryForm(null, state.categoryType || 'expense', state.saving, state.saveError));
  }
  if (state.sheet === 'category-rename' && selectedCategory) {
    return BottomSheet('Переименовать категорию', CategoryForm(selectedCategory, state.categoryType || 'expense', state.saving, state.saveError));
  }
  if (state.sheet === 'category-delete' && selectedCategory) {
    return BottomSheet('Удалить категорию', CategoryDeleteForm(selectedCategory, state.categoryType || 'expense', plans?.categories || [], state.saving, state.saveError));
  }
  if (state.sheet === 'premium') {
    return BottomSheet('Premium', InfoPanel('Premium', profile?.premium?.description || 'Premium-раздел пока информационный.'));
  }
  if (state.sheet === 'export') {
    return BottomSheet('Экспорт и данные', ExportForm(state.exportDraft, state.exportPreview, state.exportSent, state.saving, state.saveError));
  }
  if (state.sheet === 'profile-name') {
    return BottomSheet('Профиль', PreferredNameForm(profile, state.saving, state.saveError));
  }
  if (state.sheet === 'profile-currency') {
    return BottomSheet('Валюта', CurrencyForm(profile, state.saving, state.saveError));
  }
  if (state.sheet === 'profile-timezone') {
    return BottomSheet('Часовой пояс', TimezoneForm(profile, state.saving, state.saveError));
  }
  if (state.sheet === 'profile-workspace') {
    const workspace = profile?.workspaces?.find((item) => item.workspace_id === state.selectedWorkspaceId);
    return BottomSheet('Пространство', WorkspaceForm(workspace, state.saving, state.saveError));
  }
  if (state.sheet === 'quiet-hours') {
    return BottomSheet('Тихие часы', QuietHoursForm(profile?.notifications, state.saving, state.saveError));
  }
  if (state.sheet === 'menu') {
    return BottomSheet('Меню', AdditionalMenu(profile, state.homeScreenStatus || 'unknown', getTelegramWebApp()?.platform || ''));
  }
  if (state.sheet === 'actions') {
    return BottomSheet('Добавить операцию', `
      <div class="form-grid">
        <button class="button primary" data-action="open-add" data-kind="expense">Добавить расход</button>
        <button class="button secondary" data-action="open-add" data-kind="income">Добавить доход</button>
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
  hapticError();
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
    reminder_not_found: 'Напоминание не найдено.',
    reminder_inactive: 'Напоминание выключено.',
    reminder_already_recorded: 'Это напоминание уже записано.',
    reminder_invalid_date: 'Проверьте дату напоминания.',
    reminder_invalid_repeat: 'Проверьте повтор напоминания.',
    reminder_access_denied: 'Нет доступа к напоминанию.',
    reminder_stale_occurrence: 'Напоминание уже изменилось. Обновите экран.',
    budget_not_found: 'Бюджет не найден.',
    budget_invalid_categories: 'Выберите категории из списка.',
    budget_access_denied: 'Нет доступа к бюджету.',
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
  hapticSuccess();
  toastTimer = window.setTimeout(() => node.remove(), 2600);
}

async function loadScreen(): Promise<void> {
  if (!state.boot) return;
  state.loading = true;
  state.error = undefined;
  render();
  try {
    const filters = activeFilters();
    if (state.tab === 'home') overview = await api.overview(state.workspaceId, filters);
    if (state.tab === 'operations') operations = await api.operations(state.workspaceId, filters, 0, state.search);
    if (state.tab === 'analytics') {
      let response = await api.analytics(state.workspaceId, {
        ...filters,
        category_type: state.analyticsFilters?.categoryType,
        radar_type: state.analyticsFilters?.radarType,
        currency: state.analyticsFilters?.radarCurrency,
        grouping: state.analyticsFilters?.grouping || 'auto'
      });
      if ((response.radar_available_currencies.length > 1 || response.available_currencies.length > 1) && !state.analyticsFilters?.radarCurrency) {
        const firstCurrency = response.radar_available_currencies[0] || response.available_currencies[0];
        state.analyticsFilters = {
          categoryType: state.analyticsFilters?.categoryType || 'expense',
          dynamicsType: state.analyticsFilters?.dynamicsType || 'both',
          radarType: state.analyticsFilters?.radarType || 'expense',
          grouping: state.analyticsFilters?.grouping,
          categoryCurrency: state.analyticsFilters?.categoryCurrency || response.available_currencies[0],
          dynamicsCurrency: state.analyticsFilters?.dynamicsCurrency || response.available_currencies[0],
          radarCurrency: firstCurrency
        };
        response = await api.analytics(state.workspaceId, {
          ...filters,
          category_type: state.analyticsFilters.categoryType,
          radar_type: state.analyticsFilters.radarType,
          currency: firstCurrency,
          grouping: state.analyticsFilters.grouping || 'auto'
        });
      }
      overview = response.overview;
      analytics = response;
    }
    if (state.tab === 'plans') {
      plans = await api.plans(state.workspaceId);
      if ((state.plansMode || 'goals') === 'categories') {
        const categoryType = state.categoryType || 'expense';
        try {
          const managed = await api.managedCategories(state.workspaceId, categoryType);
          plans = { ...plans, categories: managed.items, categories_read_only: managed.read_only, category_type: categoryType };
        } catch {
          plans = { ...plans, categories: [], categories_read_only: true, category_type: categoryType };
        }
      }
    }
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
  installStartupErrorHandlers();
  const launchError = prepareTelegramLaunch();
  if (launchError) {
    showStartupBlocker(launchError);
    return;
  }
  initTelegramShell();
  const tg = getTelegramWebApp();
  tg?.onEvent?.('themeChanged', () => applyTheme(state.theme));
  registerHomeScreenEvents();
  tg?.BackButton?.onClick(() => closeSheet());
  try {
    const boot = await api.bootstrap();
    state.boot = boot;
    state.theme = state.theme || boot.theme;
    state.workspaceId = pickInitialWorkspace(boot.workspaces, state.workspaceId);
    await loadFilterCategories();
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
  state.selectedWorkspaceId = undefined;
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
  state.reminderDraft = undefined;
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
  const destructive = styles.getPropertyValue('--expense').trim() || '#87554f';
  const positive = styles.getPropertyValue('--income').trim() || '#147a43';
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
    const mode = state.globalFilters.operation_type === 'expense' || state.globalFilters.operation_type === 'income'
      ? state.globalFilters.operation_type
      : state.analyticsFilters?.dynamicsType || 'both';
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
    currency: String(data.get('currency') || '').trim() || undefined,
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

async function loadFilterCategories(): Promise<void> {
  if (!state.boot || state.workspaceId === 'all') {
    globalCategoryOptions = [];
    if (state.globalFilters.category !== 'all') setGlobalFilters({ ...state.globalFilters, category: 'all' });
    return;
  }
  const types = state.globalFilters.operation_type === 'all'
    ? ['expense', 'income'] as const
    : [state.globalFilters.operation_type] as const;
  const map = new Map<string, CategoryOption>();
  for (const type of types) {
    try {
      const response = await api.categories(state.workspaceId, type);
      for (const item of response.items) map.set(item.normalized_name || item.name.toLowerCase(), item);
    } catch {
      // Read-only/all scopes simply hide category options.
    }
  }
  globalCategoryOptions = [...map.values()].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  if (state.globalFilters.category !== 'all' && !globalCategoryOptions.some((item) => item.name === state.globalFilters.category)) {
    setGlobalFilters({ ...state.globalFilters, category: 'all' });
  }
}

function wireEvents(): void {
  app.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((button) => {
    button.addEventListener('click', async () => {
      const tab = button.dataset.tab as AppState['tab'];
      hapticSelection();
      state.tab = tab;
      state.sheet = null;
      selectedOperation = null;
      await api.track('mini_app_tab_opened', { tab });
      await loadScreen();
    });
  });

  app.querySelector<HTMLButtonElement>('[data-action="retry"]')?.addEventListener('click', () => void reloadActive());
  app.querySelectorAll<HTMLDetailsElement>('.chart-details').forEach((details) => {
    details.addEventListener('toggle', async () => {
      await api.track('mini_app_analytics_details_toggled', { action: details.open ? 'open' : 'close', chart_type: 'analytics', source: 'mini_app' });
    });
  });
  app.querySelector<HTMLSelectElement>('[data-action="workspace"]')?.addEventListener('change', async (event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    hapticSelection();
    state.workspaceId = value === 'all' ? 'all' : value === 'null' || value === '' ? null : Number(value);
    await loadFilterCategories();
    await api.track('mini_app_workspace_changed', { scope: String(state.workspaceId) });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="period"]')?.addEventListener('change', async (event) => {
    const period = (event.currentTarget as HTMLSelectElement).value as PeriodKey;
    hapticSelection();
    if (period === 'custom') {
      const today = new Date().toISOString().slice(0, 10);
      setGlobalFilters({ ...state.globalFilters, period, start_date: today, end_date: today });
    } else {
      setGlobalFilters({ operation_type: state.globalFilters.operation_type, category: state.globalFilters.category, period });
    }
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type: state.globalFilters.operation_type, has_category_filter: String(state.globalFilters.category !== 'all'), source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="operation-type"]')?.addEventListener('change', async (event) => {
    const operation_type = (event.currentTarget as HTMLSelectElement).value as GlobalFinancialFilters['operation_type'];
    hapticSelection();
    setGlobalFilters({ ...state.globalFilters, operation_type, category: 'all' });
    await loadFilterCategories();
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type, has_category_filter: 'false', source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="category-filter"]')?.addEventListener('change', async (event) => {
    const category = (event.currentTarget as HTMLSelectElement).value || 'all';
    hapticSelection();
    setGlobalFilters({ ...state.globalFilters, category });
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type: state.globalFilters.operation_type, has_category_filter: String(category !== 'all'), source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="search"]')?.addEventListener('change', async (event) => {
    state.search = (event.currentTarget as HTMLInputElement).value.trim();
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="load-more"]')?.addEventListener('click', async () => {
    if (!operations) return;
    const next = await api.operations(state.workspaceId, activeFilters(), operations.offset + operations.limit, state.search);
    operations = { ...next, items: [...operations.items, ...next.items] };
    render();
  });
  app.querySelector<HTMLInputElement>('[data-action="start-date"]')?.addEventListener('change', async (event) => {
    setGlobalFilters({ ...state.globalFilters, start_date: (event.currentTarget as HTMLInputElement).value });
    if (state.globalFilters.end_date) await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="end-date"]')?.addEventListener('change', async (event) => {
    setGlobalFilters({ ...state.globalFilters, end_date: (event.currentTarget as HTMLInputElement).value });
    if (state.globalFilters.start_date) await loadScreen();
  });
  app.querySelectorAll<HTMLSelectElement>('[data-action="chart-filter"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const chart = select.dataset.chart || '';
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
      if (chart === 'category') state.analyticsFilters.categoryType = select.value as 'expense' | 'income';
      if (chart === 'dynamics') state.analyticsFilters.dynamicsType = select.value as 'expense' | 'income' | 'both';
      if (chart === 'radar') state.analyticsFilters.radarType = select.value as 'expense' | 'income';
      hapticSelection();
      await api.track('mini_app_analytics_chart_filter_changed', { chart_type: chart, filter_kind: select.value, source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelector<HTMLSelectElement>('[data-action="chart-grouping"]')?.addEventListener('change', async (event) => {
    state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
    state.analyticsFilters.grouping = (event.currentTarget as HTMLSelectElement).value as 'day' | 'week' | 'month';
    hapticSelection();
    await api.track('mini_app_analytics_grouping_changed', { grouping: state.analyticsFilters.grouping, source: 'mini_app' });
    await loadScreen();
  });
  app.querySelectorAll<HTMLSelectElement>('[data-action="chart-currency"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const chart = select.dataset.chart || '';
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
      if (chart === 'category') state.analyticsFilters.categoryCurrency = select.value;
      if (chart === 'dynamics') state.analyticsFilters.dynamicsCurrency = select.value;
      if (chart === 'radar') state.analyticsFilters.radarCurrency = select.value;
      hapticSelection();
      await api.track('mini_app_analytics_chart_filter_changed', { chart_type: chart, filter_kind: 'currency', source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="plans-mode"]').forEach((button) => {
    button.addEventListener('click', async () => {
      hapticSelection();
      state.plansMode = button.dataset.mode === 'limits' ? 'limits' : button.dataset.mode === 'reminders' ? 'reminders' : button.dataset.mode === 'categories' ? 'categories' : 'goals';
      await loadScreen();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="carousel-dot"]').forEach((button) => {
    button.addEventListener('click', () => {
      const kind = button.dataset.carousel as 'challenge' | 'focus' | 'reminder';
      setHomeCarouselIndex(kind, Number(button.dataset.index || 0), 'dot');
      hapticSelection();
      render();
    });
  });
  app.querySelectorAll<HTMLElement>('[data-carousel]').forEach((node) => {
    let startX = 0;
    const kind = node.dataset.carousel as 'challenge' | 'focus' | 'reminder';
    node.addEventListener('keydown', (event) => {
      if (!(event instanceof KeyboardEvent)) return;
      const current = Number(node.dataset.index || 0);
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        setHomeCarouselIndex(kind, current - 1, 'prev');
        render();
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        setHomeCarouselIndex(kind, current + 1, 'next');
        render();
      }
    });
    node.addEventListener('pointerdown', (event) => {
      startX = event.clientX;
    });
    node.addEventListener('pointerup', (event) => {
      const delta = event.clientX - startX;
      if (Math.abs(delta) < 32) return;
      const current = Number(node.dataset.index || 0);
      setHomeCarouselIndex(kind, current + (delta < 0 ? 1 : -1), delta < 0 ? 'next' : 'prev');
      hapticSelection();
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
  app.querySelector<HTMLButtonElement>('[data-action="home-challenge"]')?.addEventListener('click', async () => {
    await api.track('mini_app_home_challenge_opened', { kind: overview?.challenge?.completed ? 'completed' : 'active', source: 'mini_app' });
    state.tab = 'plans';
    state.plansMode = 'limits';
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="home-focus"]')?.addEventListener('click', async (event) => {
    const mode = (event.currentTarget as HTMLButtonElement).dataset.mode === 'limits' ? 'limits' : 'goals';
    await api.track('mini_app_home_focus_opened', { kind: mode, source: 'mini_app' });
    state.tab = 'plans';
    state.plansMode = mode;
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="home-insight"]')?.addEventListener('click', async () => {
    await api.track('mini_app_home_insight_opened', { kind: overview?.insight?.kind || 'fallback', source: 'mini_app' });
    state.tab = 'analytics';
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="home-reminder"]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    const reminderId = Number(button.dataset.id || 0);
    await api.track('mini_app_home_reminder_opened', { result: button.dataset.state || 'empty', source: 'mini_app' });
    if (!reminderId) {
      state.tab = 'plans';
      state.plansMode = 'reminders';
      await loadScreen();
      return;
    }
    try {
      selectedReminder = (await api.reminderDetail(reminderId)).reminder;
      state.sheet = 'reminder-detail';
      state.reminderIdempotencyKey = requestId();
      render();
    } catch (error) {
      state.error = safeError(error);
      render();
    }
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
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-create"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const scope = button.dataset.scope === 'all_expenses' ? 'all_expenses' : 'category';
      state.limitCreateScope = scope;
      selectedLimit = null;
      if (scope === 'category') await loadCategoriesFor('expense');
      else categoryOptions = [];
      state.sheet = 'limit-create';
      state.limitCreateIdempotencyKey = requestId();
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelector<HTMLSelectElement>('form[data-action="create-limit"] select[name="scope"]')?.addEventListener('change', async (event) => {
    const scope = (event.currentTarget as HTMLSelectElement).value === 'all_expenses' ? 'all_expenses' : 'category';
    state.limitCreateScope = scope;
    if (scope === 'category') await loadCategoriesFor('expense');
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-edit"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedLimit = findLimitById(button.dataset.id);
      if (selectedLimit?.scope === 'category') await loadCategoriesFor('expense');
      else categoryOptions = [];
      state.sheet = selectedLimit ? 'limit-edit' : null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-delete"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedLimit = findLimitById(button.dataset.id);
      state.confirmLimitDeleteId = selectedLimit?.id;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-toggle"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const limit = findLimitById(button.dataset.id);
      if (!limit) return;
      await api.updateLimit(limit.id, { workspace_id: state.workspaceId, toggle: true, enabled: !(limit.enabled !== false) });
      showToast('Лимит обновлён');
      await reloadActive();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="reminder-create"]')?.addEventListener('click', async () => {
    await loadCategoriesFor('expense');
    selectedReminder = null;
    state.reminderDraft = undefined;
    state.sheet = 'reminder-create';
    state.saveError = undefined;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-open"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedReminder = (plans?.reminders || []).find((reminder) => reminder.id === Number(button.dataset.id)) || null;
      if (!selectedReminder) selectedReminder = (await api.reminderDetail(Number(button.dataset.id))).reminder;
      state.sheet = 'reminder-detail';
      state.reminderIdempotencyKey = requestId();
      state.saveError = undefined;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-edit"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedReminder = selectedReminder?.id === Number(button.dataset.id)
        ? selectedReminder
        : (plans?.reminders || []).find((reminder) => reminder.id === Number(button.dataset.id)) || null;
      await loadCategoriesFor(selectedReminder?.rem_type === 'Доходы' ? 'income' : 'expense');
      state.reminderDraft = undefined;
      state.sheet = selectedReminder ? 'reminder-edit' : null;
      state.saveError = undefined;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-record"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      const reminderId = Number(button.dataset.id);
      const reminder = selectedReminder?.id === reminderId ? selectedReminder : (plans?.reminders || []).find((item) => item.id === reminderId) || null;
      if (state.workspaceId === 'all') {
        selectedReminder = reminder || selectedReminder;
        state.sheet = selectedReminder ? 'reminder-workspace-select' : state.sheet;
        state.saveError = undefined;
        render();
        return;
      }
      state.saving = true;
      render();
      try {
        await api.recordReminder(reminderId, {
          workspace_id: state.workspaceId,
          idempotency_key: state.reminderIdempotencyKey || requestId(),
          event_date: reminder?.event_date
        });
        selectedReminder = null;
        state.sheet = null;
        state.reminderIdempotencyKey = requestId();
        showToast('Операция записана');
        if (state.tab === 'home') {
          await loadScreen();
        } else {
          await reloadActive();
        }
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-record-workspace"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      const reminderId = Number(button.dataset.id);
      const workspaceId = Number(button.dataset.workspaceId);
      const reminder = selectedReminder?.id === reminderId ? selectedReminder : null;
      state.saving = true;
      state.saveError = undefined;
      render();
      try {
        await api.recordReminder(reminderId, {
          workspace_id: workspaceId,
          idempotency_key: state.reminderIdempotencyKey || requestId(),
          event_date: reminder?.event_date
        });
        selectedReminder = null;
        state.sheet = null;
        state.reminderIdempotencyKey = requestId();
        showToast('Операция записана');
        await reloadActive();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-snooze"]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        selectedReminder = (await api.snoozeReminder(Number(button.dataset.id), { days: 1 })).reminder;
        state.sheet = null;
        showToast('Напомню завтра');
        await reloadActive();
      } catch (error) {
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-toggle"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const reminderId = Number(button.dataset.id);
      const reminder = selectedReminder?.id === reminderId ? selectedReminder : (plans?.reminders || []).find((item) => item.id === reminderId) || null;
      try {
        selectedReminder = (await api.toggleReminder(reminderId, reminder ? !reminder.is_active : undefined)).reminder;
        showToast('Напоминание обновлено');
        await reloadActive();
      } catch (error) {
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="reminder-delete"]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await api.deleteReminder(Number(button.dataset.id));
        selectedReminder = null;
        state.sheet = null;
        showToast('Напоминание удалено');
        await reloadActive();
      } catch (error) {
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="go-reminders"]')?.addEventListener('click', async () => {
    state.tab = 'plans';
    state.plansMode = 'reminders';
    state.sheet = null;
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="category-budget-create"]')?.addEventListener('click', async () => {
    await loadCategoriesFor('expense');
    selectedCategoryBudget = null;
    state.sheet = 'category-budget-create';
    state.saveError = undefined;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-budget-edit"]').forEach((button) => {
    button.addEventListener('click', async () => {
      await loadCategoriesFor('expense');
      selectedCategoryBudget = (plans?.category_budgets || []).find((budget) => budget.id === Number(button.dataset.id)) || null;
      state.sheet = selectedCategoryBudget ? 'category-budget-edit' : null;
      state.saveError = undefined;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-budget-toggle"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const budget = (plans?.category_budgets || []).find((item) => item.id === Number(button.dataset.id));
      if (!budget) return;
      await api.updateCategoryBudget(budget.id, { workspace_id: state.workspaceId, toggle: true, enabled: !budget.enabled });
      showToast('Бюджет обновлён');
      await reloadActive();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-budget-delete"]').forEach((button) => {
    button.addEventListener('click', async () => {
      await api.deleteCategoryBudget(Number(button.dataset.id), state.workspaceId);
      showToast('Бюджет удалён');
      await reloadActive();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-type"]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.categoryType = button.dataset.type === 'income' ? 'income' : 'expense';
      state.plansMode = 'categories';
      hapticSelection();
      await loadScreen();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="category-create"]')?.addEventListener('click', () => {
    selectedCategory = null;
    state.sheet = 'category-create';
    state.saveError = undefined;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-rename"]').forEach((button) => {
    button.addEventListener('click', () => {
      const token = button.dataset.token || '';
      selectedCategory = (plans?.categories || []).find((item) => (item.token || item.normalized_name) === token) || null;
      if (!selectedCategory) return;
      state.sheet = 'category-rename';
      state.saveError = undefined;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-delete"]').forEach((button) => {
    button.addEventListener('click', () => {
      const token = button.dataset.token || '';
      selectedCategory = (plans?.categories || []).find((item) => (item.token || item.normalized_name) === token) || null;
      if (!selectedCategory) return;
      state.sheet = 'category-delete';
      state.saveError = undefined;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="open-menu"]')?.addEventListener('click', () => {
    registerHomeScreenEvents();
    state.sheet = 'menu';
    if (state.homeScreenStatus !== 'added' && state.homeScreenStatus !== 'pending') state.homeScreenStatus = 'unknown';
    render();
    if (state.homeScreenStatus === 'added') return;
    const checkSeq = ++homeScreenCheckSeq;
    void checkHomeScreenStatus().then((status) => {
      if (checkSeq !== homeScreenCheckSeq) return;
      if (state.sheet !== 'menu') return;
      if (state.homeScreenStatus === 'added') return;
      state.homeScreenStatus = status;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="premium-open"]')?.addEventListener('click', async () => {
    await api.premium();
    state.sheet = 'premium';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="export-open"]')?.addEventListener('click', async () => {
    await api.exportInfo();
    state.sheet = 'export';
    state.exportDraft = state.exportDraft || { preset: 'month' };
    state.exportPreview = undefined;
    state.exportSent = false;
    state.saveError = undefined;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="profile-section"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const section = button.dataset.section || 'user';
      state.profileAccordion = state.profileAccordion === section ? null : section as AppState['profileAccordion'];
      persistState(state);
      await api.track('mini_app_profile_section_opened', { section, source: 'mini_app' });
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="profile-name-open"]')?.addEventListener('click', () => {
    state.sheet = 'profile-name';
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="profile-currency-open"]')?.addEventListener('click', () => {
    state.sheet = 'profile-currency';
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="profile-timezone-open"]')?.addEventListener('click', () => {
    state.sheet = 'profile-timezone';
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="profile-active-workspace-open"]')?.addEventListener('click', () => {
    state.profileAccordion = 'workspaces';
    persistState(state);
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="profile-active-workspace-set"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = Number(button.dataset.id);
      if (!id) return;
      const response = await api.setActiveWorkspace(id);
      if (profile) profile = { ...profile, workspaces: response.workspaces };
      if (state.boot) state.boot = { ...state.boot, workspaces: state.boot.workspaces.map((workspace) => workspace.workspace_id === 'all' ? workspace : { ...workspace, active: workspace.workspace_id === id }) };
      state.workspaceId = id;
      showToast('Пространство выбрано');
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="profile-workspace-open"]').forEach((button) => {
    button.addEventListener('click', () => {
      const id = Number(button.dataset.id);
      state.selectedWorkspaceId = id;
      state.sheet = 'profile-workspace';
      state.saveError = undefined;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="quiet-hours-open"]')?.addEventListener('click', () => {
    state.sheet = 'quiet-hours';
    state.saveError = undefined;
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
  app.querySelector<HTMLButtonElement>('[data-action="share-app"]')?.addEventListener('click', async () => {
    if (navigator.share) await navigator.share({ title: 'Finuchet', text: 'КопиPaste для учёта финансов' }).catch(() => undefined);
  });
  app.querySelector<HTMLButtonElement>('[data-action="add-to-home"]')?.addEventListener('click', async () => {
    if (state.homeScreenStatus === 'added') return;
    registerHomeScreenEvents();
    const requested = requestAddToHomeScreen();
    if (!requested) {
      state.homeScreenStatus = 'unsupported';
      render();
      return;
    }
    state.homeScreenStatus = 'pending';
    showToast('Подтвердите добавление в Telegram');
    await api.track('mini_app_add_to_home_requested', { source: 'mini_app' });
    render();
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
  app.querySelectorAll<HTMLSelectElement>('form[data-action="create-reminder"] select[name="rem_type"], form[data-action="save-reminder"] select[name="rem_type"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const form = select.closest<HTMLFormElement>('form');
      if (!form) return;
      const draft = reminderDraftFromForm(form);
      await loadCategoriesFor(select.value === 'income' ? 'income' : 'expense');
      if (!categoryOptions.some((category) => category.name === draft.category)) {
        draft.category = categoryOptions[0]?.name || '';
      }
      state.reminderDraft = draft;
      render();
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
    hapticDestructive();
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

  app.querySelector<HTMLFormElement>('form[data-action="create-reminder"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createReminder(reminderPayload(form));
      state.reminderDraft = undefined;
      closeSheet();
      showToast('Напоминание создано');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="save-reminder"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      selectedReminder = (await api.updateReminder(Number(form.dataset.id), reminderPayload(form))).reminder;
      state.reminderDraft = undefined;
      closeSheet();
      showToast('Напоминание обновлено');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-category-budget"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createCategoryBudget(categoryBudgetPayload(form));
      closeSheet();
      showToast('Бюджет создан');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="save-category-budget"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.updateCategoryBudget(Number(form.dataset.id), categoryBudgetPayload(form));
      closeSheet();
      showToast('Бюджет обновлён');
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

  app.querySelector<HTMLFormElement>('form[data-action="profile-name-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const response = await api.setPreferredName(String(data.get('preferred_name') || '').trim());
      if (profile) profile = { ...profile, preferred_name: response.preferred_name, display_name: response.display_name };
      if (state.boot) state.boot = { ...state.boot, user: { ...state.boot.user, preferred_name: response.preferred_name, display_name: response.display_name } };
      closeSheet();
      showToast('Имя сохранено');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="profile-currency-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const response = await api.setCurrency(String(data.get('currency') || 'RUB'));
      if (profile) profile = { ...profile, currency: response.currency };
      if (state.boot) state.boot = { ...state.boot, user: { ...state.boot.user, currency: response.currency } };
      closeSheet();
      showToast('Валюта сохранена');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="profile-timezone-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const selected = String(data.get('timezone_select') || '');
      const timezone = selected === 'custom' ? String(data.get('timezone_custom') || '').trim() : selected;
      const response = await api.setTimezone(timezone);
      if (profile) profile = { ...profile, timezone: response.timezone, notifications: response.notifications };
      if (state.boot) state.boot = { ...state.boot, user: { ...state.boot.user, timezone: response.timezone } };
      closeSheet();
      showToast('Часовой пояс сохранён');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="profile-workspace-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const id = Number(form.dataset.id);
    if (!id) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const data = new FormData(form);
      const response = await api.renameWorkspace(id, String(data.get('name') || '').trim());
      if (profile) profile = { ...profile, workspaces: response.workspaces };
      if (state.boot) {
        state.boot = {
          ...state.boot,
          workspaces: state.boot.workspaces.map((workspace) => workspace.workspace_id === id ? { ...workspace, name: response.workspace.name } : workspace)
        };
      }
      closeSheet();
      showToast('Пространство обновлено');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-category"], form[data-action="save-category"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const type = String(data.get('type') || state.categoryType || 'expense') as 'expense' | 'income';
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const name = String(data.get('name') || '').trim();
      if (form.dataset.action === 'save-category') {
        await api.renameCategory(form.dataset.token || '', { workspace_id: state.workspaceId, type, name });
        showToast('Категория переименована');
      } else {
        await api.createCategory({ workspace_id: state.workspaceId, type, name });
        showToast('Категория добавлена');
      }
      closeSheet();
      state.categoryType = type;
      state.plansMode = 'categories';
      await loadScreen();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="delete-category"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const type = String(data.get('type') || state.categoryType || 'expense') as 'expense' | 'income';
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.deleteCategory(form.dataset.token || '', { workspace_id: state.workspaceId, type, transfer_to: String(data.get('transfer_to') || '').trim() || undefined });
      closeSheet();
      showToast('Категория удалена');
      state.categoryType = type;
      state.plansMode = 'categories';
      await loadScreen();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="export-preview"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const draft = {
      workspace_id: state.workspaceId,
      operation_type: state.globalFilters.operation_type,
      category: state.globalFilters.category,
      preset: String(data.get('preset') || 'month'),
      start_date: String(data.get('start_date') || ''),
      end_date: String(data.get('end_date') || ''),
    };
    state.exportDraft = draft;
    state.saving = true;
    state.saveError = undefined;
    state.exportSent = false;
    render();
    try {
      state.exportPreview = await api.exportPreview(draft);
      state.saving = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLSelectElement>('form[data-action="export-preview"] select[name="preset"]')?.addEventListener('change', (event) => {
    const form = (event.currentTarget as HTMLSelectElement).form;
    const data = new FormData(form || undefined);
    state.exportDraft = {
      ...(state.exportDraft || {}),
      workspace_id: state.workspaceId,
      operation_type: state.globalFilters.operation_type,
      category: state.globalFilters.category,
      preset: String(data.get('preset') || 'month'),
      start_date: String(data.get('start_date') || state.exportDraft?.['start_date'] || ''),
      end_date: String(data.get('end_date') || state.exportDraft?.['end_date'] || ''),
    };
    state.exportPreview = undefined;
    state.exportSent = false;
    render();
  });

  app.querySelector<HTMLButtonElement>('[data-action="export-send"]')?.addEventListener('click', async () => {
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      state.exportPreview = await api.sendExport(state.exportDraft || { workspace_id: state.workspaceId, preset: 'month' });
      state.exportSent = true;
      state.saving = false;
      showToast('Файл отправлен в Telegram');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="quiet-hours-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const notifications = await api.updateNotificationPreferences({
        action: 'quiet_hours_update',
        enabled: data.get('enabled') === 'on',
        start: String(data.get('start') || '22:30'),
        end: String(data.get('end') || '08:00'),
      });
      if (profile) profile = { ...profile, notifications };
      closeSheet();
      showToast('Тихие часы сохранены');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
}

void bootstrap();
