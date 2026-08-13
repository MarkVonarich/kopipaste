import './styles.css';
import Chart from 'chart.js/auto';
import { api, requestId, type GoalMovementPayload, type GoalPayload, type LimitPayload, type OperationPayload, type OperationsResponse, type Overview, type PlansResponse, type AnalyticsResponse, type PlanningPayload } from './api';
import { decimalStringToVisualPoint } from './chartDecimal';
import { formatMoneyString, normalizeMoneyText } from './money';
import { checkHomeScreenStatus, getTelegramWebApp, hapticDestructive, hapticError, hapticSelection, hapticSuccess, initTelegramShell, prepareTelegramLaunch, requestAddToHomeScreen } from './telegram';
import { initialState, persistState, pickInitialWorkspace } from './state';
import type { Announcement, AppState, BudgetLimit, CategoryBudgetGroup, CategoryOption, FinancialReport, GlobalFinancialFilters, Goal, HomeWidgetKey, Insight, InsightActionType, Operation, OperationType, PeriodKey, Reminder, ReportKind, ReportOperationScope, ShoppingItem, ThemeMode, Workspace } from './types';
import { AppShell } from './components/AppShell';
import { BottomNavigation } from './components/BottomNavigation';
import { BottomSheet } from './components/BottomSheet';
import { ConfirmDialog } from './components/ConfirmDialog';
import { HomeScreen, InsightDetail } from './components/HomeScreen';
import { OperationsScreen } from './components/OperationsScreen';
import { AnalyticsScreen } from './components/AnalyticsScreen';
import { ReportsScreen } from './components/ReportsScreen';
import { CategoryBudgetForm, CategoryDeleteForm, CategoryDetail, CategoryForm, GoalContributionForm, GoalDetail, GoalForm, LimitForm, PlansScreen, ReminderForm } from './components/PlansScreen';
import { AccountDeletionForm, AdditionalMenu, CurrencyForm, ExportForm, HistoryDeletionForm, InfoPanel, PreferredNameForm, ProfileScreen, QuietHoursForm, TimezoneForm, VacationForm, WorkspaceForm } from './components/ProfileScreen';
import { HomeSettingsForm } from './components/HomeSettings';
import { ShoppingList } from './components/ShoppingList';
import { LoadingState, ErrorState } from './components/States';
import { TransactionForm } from './components/TransactionForm';
import { canonicalCategoryKey, togglePlanningCategory } from './planningSelection';

const appRoot = document.querySelector<HTMLDivElement>('#app');
if (!appRoot) throw new Error('Missing app root');
const app: HTMLDivElement = appRoot;

let state: AppState = initialState();
let overview: Overview | null = null;
let operations: OperationsResponse | null = null;
let analytics: AnalyticsResponse | null = null;
let report: FinancialReport | null = null;
let plans: PlansResponse | null = null;
let profile: Awaited<ReturnType<typeof api.profile>> | null = null;
let shoppingItems: ShoppingItem[] = [];
let shoppingReadOnly = true;
let shoppingNote = '';
let shoppingActiveCount = 0;
let shoppingCompletedCount = 0;
let selectedOperation: Operation | null = null;
let selectedGoal: Goal | null = null;
let selectedLimit: BudgetLimit | null = null;
let selectedReminder: Reminder | null = null;
let selectedCategoryBudget: CategoryBudgetGroup | null = null;
let selectedCategory: CategoryOption | null = null;
let selectedInsight: Insight | null = null;
let selectedAnnouncement: Announcement | null = null;
let categoryOptions: CategoryOption[] = [];
let globalCategoryOptions: CategoryOption[] = [];
let toastTimer = 0;
let chartInstances: Chart[] = [];
let homeScreenEventsRegistered = false;
let homeScreenCheckSeq = 0;
const impressedInsightIds = new Set<string>();
const impressedAnnouncementIds = new Set<string>();
const rejectedInsightIds = new Set<string>();
let suppressPlanningCategoryClick = '';
let cancelPlanningDrag: (() => void) | null = null;

function pointerIsOverElement(event: PointerEvent, element: HTMLElement): boolean {
  const rect = element.getBoundingClientRect();
  if (rect.width > 0 || rect.height > 0) {
    return event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom;
  }
  const hit = document.elementFromPoint?.(event.clientX, event.clientY);
  return hit === element || (hit instanceof Node && element.contains(hit));
}

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

const categoryKey = canonicalCategoryKey;

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

function planningDraftFromForm(form: HTMLFormElement): Record<string, unknown> {
  const data = new FormData(form);
  const draft: Record<string, unknown> = {};
  data.forEach((value, key) => {
    if (key === 'categories') return;
    draft[key] = String(value);
  });
  draft.categories = data.getAll('categories').map(String);
  for (const name of ['alerts_enabled', 'reminders_enabled']) {
    const checkbox = form.querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (checkbox) draft[name] = checkbox.checked;
  }
  if (form.dataset.action === 'create-goal' || form.dataset.action === 'save-goal') {
    return { ...state.goalDraft, ...draft };
  }
  return draft;
}

function planningPayload(form: HTMLFormElement, kind: PlanningPayload['kind']): PlanningPayload {
  if (kind === 'goal') {
    const goal = goalPayload(form);
    return {
      workspace_id: state.workspaceId,
      kind,
      currency: selectedGoal?.currency || state.boot?.user.currency || 'RUB',
      editing_entity_id: selectedGoal?.id,
      target_amount: goal.target_amount,
      current_amount: goal.current_amount,
      deadline: goal.deadline,
      frequency: goal.frequency,
      day: goal.day,
      days: goal.days,
      weekday: goal.weekday,
    };
  }
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    kind,
    currency: String(data.get('currency') || selectedLimit?.currency || state.boot?.user.currency || 'RUB'),
    period: String(data.get('period') || 'month') as 'week' | 'month',
    category: String(data.get('category') || ''),
    categories: data.getAll('categories').map(String),
    editing_entity_id: kind === 'category_budget' ? selectedCategoryBudget?.id : selectedLimit?.id,
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
    goal: state.homeGoalIndex || 0,
    limit: state.homeLimitIndex || 0,
    reminder: state.homeReminderIndex || 0,
    announcement: state.announcementIndex || 0,
  });
}

function renderOperations(): string {
  return OperationsScreen(operations, canWrite(), esc(state.search));
}

function renderAnalytics(): string {
  const filters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
  return filters.mode === 'reports'
    ? ReportsScreen(report, filters.reportKind || 'selected')
    : AnalyticsScreen(analytics, filters, activeFilters());
}

function renderPlans(): string {
  return PlansScreen(plans, state.plansMode || 'goals', canWrite(), state.plansGoalView || 'active');
}

function allGoals(): Goal[] {
  return [...(plans?.goals || []), ...(plans?.archived_goals || [])];
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

function carouselTotal(kind: 'challenge' | 'goal' | 'limit' | 'reminder' | 'announcement'): number {
  if (kind === 'challenge') return Math.max(1, overview?.challenges?.length || (overview?.challenge ? 1 : 0));
  if (kind === 'goal') return Math.max(1, overview?.goal_items?.length || 0);
  if (kind === 'limit') return Math.max(1, overview?.limit_items?.length || 0);
  if (kind === 'announcement') return Math.max(1, overview?.announcements?.length || 0);
  return Math.max(1, overview?.reminders?.length || (overview?.reminder ? 1 : 0));
}

function setHomeCarouselIndex(kind: 'challenge' | 'goal' | 'limit' | 'reminder' | 'announcement', index: number, direction: string): void {
  const total = carouselTotal(kind);
  const clamped = Math.max(0, Math.min(index, total - 1));
  if (kind === 'challenge') state.homeChallengeIndex = clamped;
  if (kind === 'goal') state.homeGoalIndex = clamped;
  if (kind === 'limit') state.homeLimitIndex = clamped;
  if (kind === 'reminder') state.homeReminderIndex = clamped;
  if (kind === 'announcement') state.announcementIndex = clamped;
  const eventKind = kind === 'goal' || kind === 'limit' ? 'focus' : kind;
  void api.track(`mini_app_${eventKind}_carousel_changed`, { direction, position: String(clamped + 1), total: String(total), source: 'mini_app' });
}

function currentAnnouncement(): Announcement | null {
  const items = overview?.announcements || [];
  if (!items.length) return null;
  const index = Math.max(0, Math.min(state.announcementIndex || 0, items.length - 1));
  return items[index] || null;
}

function trackVisibleAnnouncement(): void {
  if (state.tab !== 'home') return;
  if (!app.querySelector('[data-carousel="announcement"]')) return;
  const announcement = currentAnnouncement();
  if (!announcement || impressedAnnouncementIds.has(announcement.id)) return;
  impressedAnnouncementIds.add(announcement.id);
  void api.track('mini_app_announcement_impression', {
    update_key: announcement.id,
    update_kind: announcement.kind,
    source: 'mini_app',
  }).catch(() => impressedAnnouncementIds.delete(announcement.id));
}

function applyShoppingResponse(response: Awaited<ReturnType<typeof api.shoppingItems>>): void {
  shoppingItems = response.items;
  shoppingReadOnly = response.read_only;
  shoppingNote = response.note || '';
  shoppingActiveCount = Number(response.active_count || 0);
  shoppingCompletedCount = Number(response.completed_count || 0);
}

async function refreshShoppingItems(): Promise<void> {
  applyShoppingResponse(await api.shoppingItems(state.workspaceId));
  syncShoppingOverview();
}

async function openShoppingList(): Promise<void> {
  state.sheet = 'shopping-list';
  state.saveError = undefined;
  state.confirmClearShopping = false;
  state.shoppingEditId = undefined;
  state.shoppingEditText = undefined;
  try {
    const response = await api.shoppingItems(state.workspaceId);
    applyShoppingResponse(response);
    await api.track('mini_app_shopping_opened', { source: 'mini_app', result: response.read_only ? 'read_only' : 'write' });
  } catch (error) {
    shoppingItems = [];
    shoppingReadOnly = true;
    shoppingNote = '';
    shoppingActiveCount = 0;
    shoppingCompletedCount = 0;
    state.saveError = safeError(error);
  }
  render();
}

function syncShoppingOverview(): void {
  if (!overview) return;
  overview = {
    ...overview,
    shopping: {
      items: shoppingItems.slice(0, 5),
      active_count: shoppingActiveCount,
      completed_count: shoppingCompletedCount,
      read_only: shoppingReadOnly,
      available: !shoppingNote && state.workspaceId !== 'all' && state.workspaceId !== null,
    },
  };
}

function openHomeSettings(): void {
  const preferences = profile?.home_preferences || (overview?.home_widgets && overview.home_preferences ? { widgets: overview.home_widgets, ...overview.home_preferences } : null);
  if (preferences) {
    state.homeDraftOrder = [...preferences.order];
    state.homeDraftEnabled = [...preferences.enabled];
  }
  state.sheet = 'home-settings';
  state.saveError = undefined;
  void api.track('mini_app_home_customization_opened', { source: 'mini_app' });
  render();
}

async function openAnnouncementTarget(announcement: Announcement): Promise<void> {
  const target = announcement.action.type;
  if (target === 'OPEN_DETAIL') {
    if (!announcement.detail?.trim()) return;
    selectedAnnouncement = announcement;
    state.sheet = 'announcement-detail';
    state.saveError = undefined;
    render();
    return;
  }
  if (target === 'OPEN_HOME_SETTINGS') {
    openHomeSettings();
    return;
  }
  if (target === 'OPEN_SHOPPING_LIST') {
    await openShoppingList();
    return;
  }
  state.sheet = null;
  if (target === 'OPEN_PROFILE') state.tab = 'profile';
  else if (target === 'OPEN_ANALYTICS') state.tab = 'analytics';
  else if (target === 'OPEN_REPORTS' || target === 'OPEN_REPORT_WEEKLY' || target === 'OPEN_REPORT_MONTHLY') {
    const currentFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
    state.tab = 'analytics';
    state.analyticsFilters = {
      ...currentFilters,
      mode: 'reports',
      reportKind: target === 'OPEN_REPORT_WEEKLY' ? 'completed_week' : target === 'OPEN_REPORT_MONTHLY' ? 'completed_month' : 'selected',
      reportCurrency: currentFilters.reportCurrency || currentFilters.analyticsCurrency,
    };
  }
  else if (target === 'OPEN_PLANS') state.tab = 'plans';
  else return;
  await loadScreen();
}

async function openCurrentAnnouncement(): Promise<void> {
  const announcement = currentAnnouncement();
  if (!announcement) return;
  void api.track('mini_app_announcement_opened', {
    action_type: announcement.action.type,
    update_key: announcement.id,
    update_kind: announcement.kind,
    source: 'mini_app',
  });
  await openAnnouncementTarget(announcement);
}

function announcementKindLabel(kind: Announcement['kind']): string {
  return kind === 'feature' ? 'Новая возможность' : kind === 'improvement' ? 'Улучшение' : kind === 'fix' ? 'Исправление' : 'Новое в КопиPaste';
}

function renderSheet(): string {
  if (state.confirmDeleteId && selectedOperation) {
    return ConfirmDialog(state.confirmDeleteId, `${selectedOperation.category} · ${operationAmount(selectedOperation)}`);
  }
  if (state.confirmLimitDeleteId && selectedLimit) {
    return ConfirmDialog(state.confirmLimitDeleteId, `${selectedLimit.title} · ${formatMoneyString(selectedLimit.amount, selectedLimit.currency)}`, 'Удалить лимит?', 'confirm-limit-delete');
  }
  if (state.confirmGoalDeleteId && selectedGoal) {
    return ConfirmDialog(
      state.confirmGoalDeleteId,
      `Цель «${selectedGoal.title}» и её история пополнений будут удалены. Финансовые операции останутся без изменений.`,
      'Удалить цель навсегда?',
      'confirm-goal-delete',
      state.saveError,
    );
  }
  if (state.sheet === 'insight-detail' && selectedInsight) {
    return BottomSheet('Инсайт', InsightDetail(selectedInsight, state.saving, state.saveError));
  }
  if (state.sheet === 'announcement-detail' && selectedAnnouncement?.detail?.trim()) {
    return BottomSheet(selectedAnnouncement.title, `<div class="detail-grid announcement-detail"><span class="eyebrow">${esc(announcementKindLabel(selectedAnnouncement.kind))}</span><p>${esc(selectedAnnouncement.detail)}</p></div>`);
  }
  if (state.sheet === 'home-settings') {
    const preferences = profile?.home_preferences || (overview?.home_widgets && overview.home_preferences ? { widgets: overview.home_widgets, ...overview.home_preferences } : null);
    if (!preferences) return BottomSheet('Настройка главной', '<p class="caption">Настройки временно недоступны.</p>');
    return BottomSheet('Настройка главной', HomeSettingsForm(
      preferences,
      state.homeDraftOrder || preferences.order,
      state.homeDraftEnabled || preferences.enabled,
      state.saving,
      state.saveError,
    ));
  }
  if (state.sheet === 'shopping-list') {
    return BottomSheet('Список покупок', ShoppingList(shoppingItems, shoppingReadOnly, state.saving, Boolean(state.confirmClearShopping), state.saveError, shoppingNote, state.shoppingEditId, state.shoppingEditText));
  }
  if (!state.sheet && !selectedOperation) return '';
  if (state.sheet === 'goal-create') {
    return BottomSheet('Новая цель', GoalForm(null, state.saving, state.saveError, state.goalPlanPreview, state.goalDraft, state.planningEstimate));
  }
  if (state.sheet === 'goal-detail' && selectedGoal) {
    return BottomSheet(selectedGoal.title, GoalDetail(selectedGoal, canWrite()));
  }
  if (state.sheet === 'goal-edit' && selectedGoal) {
    return BottomSheet('Изменить цель', GoalForm(selectedGoal, state.saving, state.saveError, state.goalPlanPreview, state.goalDraft, state.planningEstimate));
  }
  if (state.sheet === 'goal-contribution' && selectedGoal) {
    return BottomSheet('Пополнить цель', GoalContributionForm(selectedGoal, state.goalIdempotencyKey || requestId(), state.saving, state.saveError));
  }
  if (state.sheet === 'limit-create') {
    return BottomSheet('Новый лимит', LimitForm(null, categoryOptions, state.saving, state.saveError, state.limitCreateScope || 'category', state.insightLimitCategory || '', state.insightLimitCurrency || '', state.planningEstimate, state.planningDraft));
  }
  if (state.sheet === 'limit-edit' && selectedLimit) {
    return BottomSheet('Изменить лимит', LimitForm(selectedLimit, categoryOptions, state.saving, state.saveError, 'category', '', '', state.planningEstimate, state.planningDraft));
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
    return BottomSheet('Новый бюджет категорий', CategoryBudgetForm(null, categoryOptions, state.saving, state.saveError, profile?.available_currencies, state.boot?.user.currency || 'RUB', state.planningEstimate, state.planningDraft));
  }
  if (state.sheet === 'category-budget-edit' && selectedCategoryBudget) {
    return BottomSheet('Изменить бюджет категорий', CategoryBudgetForm(selectedCategoryBudget, categoryOptions, state.saving, state.saveError, profile?.available_currencies, state.boot?.user.currency || 'RUB', state.planningEstimate, state.planningDraft));
  }
  if (state.sheet === 'category-detail' && selectedCategory) {
    return BottomSheet(selectedCategory.name, CategoryDetail(selectedCategory, state.categoryType || 'expense', canWrite() && !plans?.categories_read_only));
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
  if (state.sheet === 'vacation') {
    return BottomSheet('Режим отпуска', VacationForm(profile?.vacation_mode, state.saving, state.saveError));
  }
  if (state.sheet === 'privacy-history') {
    return BottomSheet('Удалить финансовую историю', HistoryDeletionForm(
      (state.privacyStage === 'preview' || state.privacyStage === 'confirm') ? state.privacyStage : 'select',
      state.privacyPeriod || 'this_month',
      state.privacyPreview,
      state.saving,
      state.saveError,
    ));
  }
  if (state.sheet === 'privacy-account') {
    const stage = ['account-info', 'account-preview', 'account-confirm', 'deleted'].includes(state.privacyStage || '')
      ? state.privacyStage as 'account-info' | 'account-preview' | 'account-confirm' | 'deleted'
      : 'account-info';
    return BottomSheet('Удалить аккаунт и мои данные', AccountDeletionForm(stage, state.accountPreview, state.saving, state.saveError, state.accountDeletedMessage));
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
  if (!state.loading && !state.error && state.tab === 'home') trackVisibleAnnouncement();
  const tg = getTelegramWebApp();
  if (tg?.BackButton) {
    if (state.confirmDeleteId || state.confirmGoalDeleteId || state.sheet || selectedOperation || state.reportReturnContext || (state.tab === 'analytics' && state.analyticsFilters?.mode === 'reports') || (state.tab === 'plans' && state.plansMode === 'goals' && state.plansGoalView === 'archive')) tg.BackButton.show();
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
    category_protected: 'Системную категорию нельзя удалить.',
    category_transfer_required: 'Выберите категорию, куда перенести связанные данные.',
    category_destination_not_found: 'Категория для переноса больше недоступна. Обновите список.',
    category_same_destination: 'Выберите другую категорию для переноса.',
    category_delete_failed: 'Не удалось безопасно удалить категорию. Обновите список и попробуйте снова.',
    goal_not_found: 'Цель не найдена или уже была удалена.',
    goal_not_archived: 'Сначала переместите цель в архив.',
    schedule_required: 'Выберите расписание для расчёта.',
    bad_goal: 'Проверьте поля цели.',
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
    categories_required: 'Выберите хотя бы одну категорию для расчёта.',
    bad_planning_request: 'Проверьте параметры расчёта.',
    bad_planning_period: 'Выберите неделю или месяц.',
    bad_goal_schedule: 'Проверьте расписание цели.',
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
    if (state.tab === 'home') {
      overview = await api.overview(state.workspaceId, filters);
      const insightValues = overview.insights?.length ? overview.insights : overview.insight ? [overview.insight] : [];
      const visibleInsights = insightValues.filter((insight) => !rejectedInsightIds.has(`${state.workspaceId ?? 'personal'}:${insight.id}`));
      overview = { ...overview, insights: visibleInsights, insight: visibleInsights[0] || null };
      for (const insight of visibleInsights) {
        const impressionKey = `${state.workspaceId ?? 'personal'}:${insight.id}`;
        if (impressedInsightIds.has(impressionKey)) continue;
        impressedInsightIds.add(impressionKey);
        void api.insightImpression(insight.id, state.workspaceId).catch(() => impressedInsightIds.delete(impressionKey));
      }
      const announcementTotal = overview.announcements?.length || 0;
      state.announcementIndex = announcementTotal ? Math.min(state.announcementIndex || 0, announcementTotal - 1) : 0;
    }
    if (state.tab === 'operations') operations = await api.operations(state.workspaceId, { ...filters, ...(state.operationScope || {}) }, 0, state.search);
    if (state.tab === 'analytics') {
      const analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
      if (analyticsFilters.mode === 'reports') {
        const response = await api.report(state.workspaceId, {
          ...filters,
          report_kind: analyticsFilters.reportKind || 'selected',
          currency: analyticsFilters.reportCurrency,
        });
        report = response.report;
        state.analyticsFilters = {
          ...analyticsFilters,
          reportCurrency: response.report.selected_currency,
        };
      } else {
        let requestedCurrency = analyticsFilters.analyticsCurrency;
        let response = await api.analytics(state.workspaceId, {
          ...filters,
          category_type: analyticsFilters.categoryType,
          radar_type: analyticsFilters.radarType,
          currency: requestedCurrency,
          grouping: analyticsFilters.grouping || 'auto',
          analytics_search: analyticsFilters.search,
          detail_kind: analyticsFilters.detailKind,
          detail_value: analyticsFilters.detailValue,
          detail_currency: analyticsFilters.detailCurrency,
          detail_operation_type: analyticsFilters.detailOperationType,
          detail_category_key: analyticsFilters.detailCategoryKey
        });
        const fallbackCurrency = response.available_currencies.includes(requestedCurrency || '') ? requestedCurrency : response.available_currencies[0];
        const shouldRetryCurrency = Boolean(fallbackCurrency) && (requestedCurrency !== fallbackCurrency) && (Boolean(requestedCurrency) || response.available_currencies.length > 1);
        if (requestedCurrency !== fallbackCurrency || !state.analyticsFilters) {
          state.analyticsFilters = {
            ...analyticsFilters,
            analyticsCurrency: fallbackCurrency,
            detailKind: requestedCurrency === fallbackCurrency ? analyticsFilters.detailKind : undefined,
            detailValue: requestedCurrency === fallbackCurrency ? analyticsFilters.detailValue : undefined,
            detailCurrency: requestedCurrency === fallbackCurrency ? analyticsFilters.detailCurrency : undefined,
            detailOperationType: requestedCurrency === fallbackCurrency ? analyticsFilters.detailOperationType : undefined,
            detailCategoryKey: requestedCurrency === fallbackCurrency ? analyticsFilters.detailCategoryKey : undefined,
          };
        }
        if (shouldRetryCurrency) {
          requestedCurrency = fallbackCurrency;
          response = await api.analytics(state.workspaceId, {
            ...filters,
            category_type: analyticsFilters.categoryType,
            radar_type: analyticsFilters.radarType,
            currency: requestedCurrency,
            grouping: analyticsFilters.grouping || 'auto',
            analytics_search: analyticsFilters.search,
            detail_kind: undefined,
            detail_value: undefined,
            detail_currency: undefined,
            detail_operation_type: undefined,
            detail_category_key: undefined
          });
        }
        overview = response.overview;
        analytics = response;
      }
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

function applyInsightScope(params: Record<string, string | number | null>): void {
  const workspaceId = params.workspace_id;
  if (workspaceId === null || typeof workspaceId === 'number') state.workspaceId = workspaceId;
  const rawPeriod = String(params.period || 'current_month');
  const period: PeriodKey = rawPeriod === 'current_week' || rawPeriod === 'previous_month' || rawPeriod === 'custom' ? rawPeriod : 'current_month';
  state.globalFilters = {
    period,
    start_date: period === 'custom' ? String(params.start_date || '') : undefined,
    end_date: period === 'custom' ? String(params.end_date || '') : undefined,
    operation_type: params.operation_type === 'income' ? 'income' : 'expense',
    category: params.category && params.category !== 'all' ? String(params.category) : 'all',
  };
  state.period = state.globalFilters;
}

async function openInsightAction(type: InsightActionType, params: Record<string, string | number | null>): Promise<void> {
  applyInsightScope(params);
  state.sheet = null;
  state.saveError = undefined;
  selectedInsight = null;
  if (type === 'OPEN_OPERATIONS') {
    state.operationScope = {
      currency: String(params.currency || ''),
      merchant_key: params.merchant_key ? String(params.merchant_key) : undefined,
      category_key: params.category_key ? String(params.category_key) : undefined,
      scope_category: params.scope_category ? String(params.scope_category) : undefined,
    };
    state.tab = 'operations';
    await loadScreen();
    return;
  }
  if (type === 'OPEN_ANALYTICS' || type === 'OPEN_CATEGORY' || type === 'OPEN_MERCHANT') {
    const detailKind = type === 'OPEN_CATEGORY' ? 'category' : type === 'OPEN_MERCHANT' ? 'merchant' : undefined;
    const detailValue = detailKind === 'category' ? (params.target_category || params.category_key) : detailKind === 'merchant' ? params.merchant_key : undefined;
    state.analyticsFilters = {
      ...(state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' }),
      categoryType: 'expense',
      analyticsCurrency: String(params.currency || ''),
      structureMode: detailKind === 'merchant' ? 'merchant' : 'category',
      detailKind,
      detailValue: detailValue ? String(detailValue) : undefined,
      detailCurrency: String(params.currency || ''),
      detailOperationType: 'expense',
      detailCategoryKey: detailKind === 'merchant' && params.category_key ? String(params.category_key) : undefined,
    };
    state.tab = 'analytics';
    await loadScreen();
    return;
  }
  state.tab = 'plans';
  state.plansMode = 'limits';
  await loadScreen();
  await loadCategoriesFor('expense');
  if (type === 'OPEN_LIMIT') {
    selectedLimit = findLimitById(params.limit_id ? String(params.limit_id) : undefined);
    state.sheet = selectedLimit && canWrite() ? 'limit-edit' : null;
  } else {
    selectedLimit = null;
    state.limitCreateScope = 'category';
    state.insightLimitCategory = params.target_category
      ? String(params.target_category)
      : params.category && params.category !== 'all' ? String(params.category) : undefined;
    state.insightLimitCurrency = params.currency ? String(params.currency) : undefined;
    state.limitCreateIdempotencyKey = requestId();
    state.sheet = 'limit-create';
  }
  render();
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
  tg?.BackButton?.onClick(() => navigateBack());
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
  if (state.privacyStage === 'deleted') {
    getTelegramWebApp()?.close?.();
    return;
  }
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
  if (state.confirmGoalDeleteId) {
    state.confirmGoalDeleteId = undefined;
    render();
    return;
  }
  if (state.dirty && !window.confirm('Закрыть без сохранения?')) return;
  state.sheet = null;
  selectedOperation = null;
  selectedGoal = null;
  selectedLimit = null;
  selectedReminder = null;
  selectedCategoryBudget = null;
  selectedCategory = null;
  selectedInsight = null;
  selectedAnnouncement = null;
  state.selectedWorkspaceId = undefined;
  state.saveError = undefined;
  state.saving = false;
  state.dirty = false;
  state.addIdempotencyKey = undefined;
  state.goalIdempotencyKey = undefined;
  state.goalCreateIdempotencyKey = undefined;
  state.limitCreateIdempotencyKey = undefined;
  state.insightLimitCategory = undefined;
  state.insightLimitCurrency = undefined;
  state.goalPlanPreview = undefined;
  state.goalPreviewPayloadHash = undefined;
  state.goalDraft = undefined;
  state.planningEstimate = undefined;
  state.planningDraft = undefined;
  state.reminderDraft = undefined;
  state.confirmLimitDeleteId = undefined;
  state.confirmGoalDeleteId = undefined;
  state.formDraft = undefined;
  state.shoppingEditId = undefined;
  state.shoppingEditText = undefined;
  state.privacyStage = undefined;
  state.privacyPeriod = undefined;
  state.privacyPreview = undefined;
  state.accountPreview = undefined;
  state.accountDeletedMessage = undefined;
  render();
}

function navigateBack(): void {
  if (state.sheet === 'privacy-history' && state.privacyStage === 'confirm') {
    state.privacyStage = 'preview';
    render();
    return;
  }
  if (state.sheet === 'privacy-history' && state.privacyStage === 'preview') {
    state.privacyStage = 'select';
    render();
    return;
  }
  if (state.sheet === 'privacy-account' && state.privacyStage === 'account-confirm') {
    state.privacyStage = 'account-preview';
    render();
    return;
  }
  if (state.sheet === 'privacy-account' && state.privacyStage === 'account-preview') {
    state.privacyStage = 'account-info';
    render();
    return;
  }
  if (state.confirmDeleteId || state.confirmLimitDeleteId || state.confirmGoalDeleteId || state.sheet || selectedOperation) {
    closeSheet();
    return;
  }
  if (state.tab === 'plans' && state.plansMode === 'goals' && state.plansGoalView === 'archive') {
    state.plansGoalView = 'active';
    render();
    return;
  }
  if (state.reportReturnContext) {
    const context = state.reportReturnContext;
    state.reportReturnContext = undefined;
    state.operationScope = undefined;
    state.workspaceId = context.workspaceId;
    state.search = context.search;
    setGlobalFilters({ ...context.globalFilters });
    state.analyticsFilters = {
      ...(state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' }),
      mode: context.mode,
      reportKind: context.reportKind,
      reportCurrency: context.reportCurrency,
    };
    state.tab = 'analytics';
    void loadScreen();
    return;
  }
  if (state.tab === 'analytics' && state.analyticsFilters?.mode === 'reports') {
    state.analyticsFilters.mode = 'analytics';
    void loadScreen();
  }
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
  const analyticsCurrency = state.analyticsFilters?.analyticsCurrency || state.analyticsFilters?.categoryCurrency || state.analyticsFilters?.dynamicsCurrency || state.analyticsFilters?.radarCurrency || analytics.selected_currency || analytics.available_currencies[0];
  const categoryCanvas = document.querySelector<HTMLCanvasElement>('#categoryChart');
  const structureMode = state.analyticsFilters?.structureMode || 'category';
  const categoryItems = analyticsCurrency ? analytics.category_structure.currency_groups[analyticsCurrency]?.items || [] : analytics.category_structure.items;
  const merchantItems = analyticsCurrency ? analytics.merchant_structure?.currency_groups?.[analyticsCurrency]?.items || [] : analytics.merchant_structure?.items || [];
  const structureItems = structureMode === 'merchant' ? merchantItems : categoryItems;
  if (categoryCanvas && structureItems.length) {
    chartInstances.push(new Chart(categoryCanvas, {
      type: 'bar',
      data: {
        labels: structureItems.map((item) => `${'merchant' in item ? item.merchant : item.category} · ${item.currency}`),
        datasets: [{ label: `Доля · ${analyticsCurrency}`, data: structureItems.map((item) => item.share), backgroundColor: accent }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${structureItems[ctx.dataIndex]?.share ?? 0}% · ${formatMoneyString(structureItems[ctx.dataIndex]?.total || '0.00', structureItems[ctx.dataIndex]?.currency || analyticsCurrency)}` } }
        },
        scales: { x: { beginAtZero: true, max: 100 } }
      }
    }));
  }
  const dynamicsCanvas = document.querySelector<HTMLCanvasElement>('#dynamicsChart');
  const dynamicsItems = analyticsCurrency ? analytics.time_dynamics.items.filter((item) => item.currency === analyticsCurrency) : analytics.time_dynamics.items;
  if (dynamicsCanvas && dynamicsItems.length) {
    const labels = dynamicsItems.map((item) => item.date);
    const mode = state.globalFilters.operation_type === 'expense' || state.globalFilters.operation_type === 'income'
      ? state.globalFilters.operation_type
      : state.analyticsFilters?.dynamicsType || 'both';
    const datasets = [];
    const expensePoints = dynamicsItems.map((item) => decimalStringToVisualPoint(item.expense));
    const incomePoints = dynamicsItems.map((item) => decimalStringToVisualPoint(item.income));
    const resultPoints = dynamicsItems.map((item) => decimalStringToVisualPoint(item.result));
    if (mode === 'result') {
      datasets.push({ label: `Финрезультат · ${analyticsCurrency}`, data: resultPoints.map((point) => point.value), borderColor: accent, backgroundColor: 'rgba(10,122,117,.18)' });
    } else {
      if (mode !== 'income') datasets.push({ label: `Расходы · ${analyticsCurrency}`, data: expensePoints.map((point) => point.value), borderColor: destructive, backgroundColor: 'rgba(184,50,66,.18)' });
      if (mode !== 'expense') datasets.push({ label: `Доходы · ${analyticsCurrency}`, data: incomePoints.map((point) => point.value), borderColor: positive, backgroundColor: 'rgba(20,122,67,.18)' });
    }
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
                const source = mode === 'result' ? resultPoints : ctx.datasetIndex === 0 && mode !== 'income' ? expensePoints : incomePoints;
                return `${ctx.dataset.label}: ${formatMoneyString(source[ctx.dataIndex]?.original || '0.00', analyticsCurrency)}`;
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
    deadline: String(data.get('deadline') || ''),
    strategy: String(data.get('strategy') || 'none') as GoalPayload['strategy'],
    frequency,
    comfortable_amount: String(data.get('comfortable_amount') || '').trim() ? normalizeMoneyText(String(data.get('comfortable_amount'))) : '',
    reminders_enabled: data.get('reminders_enabled') === 'on',
  };
  if (form.dataset.action === 'create-goal') {
    payload.current_amount = normalizeMoneyText(String(data.get('current_amount') || '0'));
  }
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

async function loadCategoriesFor(type: 'expense' | 'income' | 'Расходы' | 'Доходы', currentCategory?: string): Promise<void> {
  try {
    const response = await api.categories(state.workspaceId, type, currentCategory);
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
      for (const item of response.items) map.set(categoryKey(item.normalized_name || item.name), item);
    } catch {
      // Read-only/all scopes simply hide category options.
    }
  }
  globalCategoryOptions = [...map.values()].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  if (state.globalFilters.category !== 'all' && !globalCategoryOptions.some((item) => categoryKey(item.name) === categoryKey(state.globalFilters.category))) {
    setGlobalFilters({ ...state.globalFilters, category: 'all' });
  }
}

function wireEvents(): void {
  app.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((button) => {
    button.addEventListener('click', async () => {
      const tab = button.dataset.tab as AppState['tab'];
      hapticSelection();
      state.tab = tab;
      state.reportReturnContext = undefined;
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
  app.querySelector<HTMLButtonElement>('[data-action="reports-open"]')?.addEventListener('click', async () => {
    const currentFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
    state.analyticsFilters = {
      ...currentFilters,
      mode: 'reports',
      reportKind: 'selected',
      reportCurrency: currentFilters.reportCurrency || currentFilters.analyticsCurrency,
    };
    hapticSelection();
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="reports-close"]')?.addEventListener('click', async () => {
    if (!state.analyticsFilters) return;
    state.analyticsFilters.mode = 'analytics';
    hapticSelection();
    await loadScreen();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="report-kind"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!state.analyticsFilters) return;
      state.analyticsFilters.reportKind = (button.dataset.kind || 'selected') as ReportKind;
      hapticSelection();
      await loadScreen();
    });
  });
  app.querySelector<HTMLSelectElement>('[data-action="report-currency"]')?.addEventListener('change', async (event) => {
    if (!state.analyticsFilters) return;
    state.analyticsFilters.reportCurrency = (event.currentTarget as HTMLSelectElement).value;
    hapticSelection();
    await loadScreen();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="report-drill"]').forEach((button) => {
    button.addEventListener('click', async () => {
      let scope: ReportOperationScope;
      try {
        scope = JSON.parse(button.dataset.scope || '{}') as ReportOperationScope;
      } catch {
        return;
      }
      if (!scope.start_date || !scope.end_date || !scope.currency) return;
      state.reportReturnContext = {
        workspaceId: state.workspaceId,
        globalFilters: { ...state.globalFilters },
        mode: 'reports',
        reportKind: state.analyticsFilters?.reportKind || report?.kind || 'selected',
        reportCurrency: state.analyticsFilters?.reportCurrency || report?.selected_currency,
        search: state.search,
      };
      if (scope.workspace_id === 'all' || scope.workspace_id === null || typeof scope.workspace_id === 'number') state.workspaceId = scope.workspace_id;
      setGlobalFilters({
        period: 'custom',
        start_date: scope.start_date,
        end_date: scope.end_date,
        operation_type: scope.operation_type,
        category: scope.scope_category || scope.category || 'all',
      });
      state.operationScope = {
        currency: scope.currency,
        category_key: scope.category_key || undefined,
        merchant_key: scope.merchant_key || undefined,
        scope_category: scope.scope_category || undefined,
      };
      state.search = '';
      state.tab = 'operations';
      await api.track('report_drilldown_opened', {
        report_kind: report?.kind || 'selected',
        kind: button.dataset.kind || 'unknown',
        currency: scope.currency,
        source: 'mini_app',
      });
      await loadScreen();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="report-export"]')?.addEventListener('click', async () => {
    if (!report?.export_available) return;
    await api.exportInfo();
    await api.track('report_export_requested', { report_kind: report.kind, currency: report.selected_currency, source: 'mini_app' });
    state.sheet = 'export';
    state.exportDraft = {
      workspace_id: report.workspace.scope,
      operation_type: report.filters.operation_type,
      category: report.filters.category,
      preset: 'custom',
      start_date: report.period.start_date,
      end_date: report.period.end_date,
    };
    state.exportPreview = undefined;
    state.exportSent = false;
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLSelectElement>('[data-action="workspace"]')?.addEventListener('change', async (event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    hapticSelection();
    state.workspaceId = value === 'all' ? 'all' : value === 'null' || value === '' ? null : Number(value);
    state.operationScope = undefined;
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
    state.operationScope = undefined;
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type: state.globalFilters.operation_type, has_category_filter: String(state.globalFilters.category !== 'all'), source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="operation-type"]')?.addEventListener('change', async (event) => {
    const operation_type = (event.currentTarget as HTMLSelectElement).value as GlobalFinancialFilters['operation_type'];
    hapticSelection();
    setGlobalFilters({ ...state.globalFilters, operation_type, category: 'all' });
    state.operationScope = undefined;
    await loadFilterCategories();
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type, has_category_filter: 'false', source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="category-filter"]')?.addEventListener('change', async (event) => {
    const category = (event.currentTarget as HTMLSelectElement).value || 'all';
    hapticSelection();
    setGlobalFilters({ ...state.globalFilters, category });
    state.operationScope = undefined;
    await api.track('mini_app_global_filter_applied', { period_kind: state.globalFilters.period, operation_type: state.globalFilters.operation_type, has_category_filter: String(category !== 'all'), source: 'mini_app' });
    await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="search"]')?.addEventListener('change', async (event) => {
    state.search = (event.currentTarget as HTMLInputElement).value.trim();
    state.operationScope = undefined;
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="load-more"]')?.addEventListener('click', async () => {
    if (!operations) return;
    const next = await api.operations(state.workspaceId, { ...activeFilters(), ...(state.operationScope || {}) }, operations.offset + operations.limit, state.search);
    operations = { ...next, items: [...operations.items, ...next.items] };
    render();
  });
  app.querySelector<HTMLInputElement>('[data-action="start-date"]')?.addEventListener('change', async (event) => {
    setGlobalFilters({ ...state.globalFilters, start_date: (event.currentTarget as HTMLInputElement).value });
    state.operationScope = undefined;
    if (state.globalFilters.end_date) await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="end-date"]')?.addEventListener('change', async (event) => {
    setGlobalFilters({ ...state.globalFilters, end_date: (event.currentTarget as HTMLInputElement).value });
    state.operationScope = undefined;
    if (state.globalFilters.start_date) await loadScreen();
  });
  app.querySelectorAll<HTMLSelectElement>('[data-action="chart-filter"]').forEach((select) => {
    select.addEventListener('change', async () => {
      const chart = select.dataset.chart || '';
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense' };
      if (chart === 'category') state.analyticsFilters.categoryType = select.value as 'expense' | 'income';
      if (chart === 'dynamics') state.analyticsFilters.dynamicsType = select.value as 'expense' | 'income' | 'result' | 'both';
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
      state.analyticsFilters.analyticsCurrency = select.value;
      state.analyticsFilters.detailKind = undefined;
      state.analyticsFilters.detailValue = undefined;
      state.analyticsFilters.detailCurrency = undefined;
      hapticSelection();
      await api.track('mini_app_analytics_chart_filter_changed', { chart_type: chart, filter_kind: 'currency', source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelector<HTMLInputElement>('[data-action="analytics-search"]')?.addEventListener('change', async (event) => {
    state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
    state.analyticsFilters.search = (event.currentTarget as HTMLInputElement).value.trim();
    state.analyticsFilters.detailKind = undefined;
    state.analyticsFilters.detailValue = undefined;
    state.analyticsFilters.detailCategoryKey = undefined;
    hapticSelection();
    await api.track('mini_app_analytics_search_used', { has_query: String(Boolean(state.analyticsFilters.search)), source: 'mini_app' });
    await loadScreen();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="analytics-structure"]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
      state.analyticsFilters.structureMode = button.dataset.mode === 'merchant' ? 'merchant' : 'category';
      hapticSelection();
      await api.track('mini_app_analytics_structure_changed', { mode: state.analyticsFilters.structureMode, source: 'mini_app' });
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="analytics-drill"]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.analyticsFilters = state.analyticsFilters || { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' };
      state.analyticsFilters.detailKind = button.dataset.kind === 'merchant' ? 'merchant' : 'category';
      state.analyticsFilters.detailValue = button.dataset.value || '';
      state.analyticsFilters.detailCurrency = button.dataset.currency || state.analyticsFilters.analyticsCurrency;
      state.analyticsFilters.detailOperationType = state.globalFilters.operation_type === 'income' ? 'income' : state.globalFilters.operation_type === 'expense' ? 'expense' : state.analyticsFilters.categoryType;
      state.analyticsFilters.detailCategoryKey = undefined;
      hapticSelection();
      await api.track('mini_app_analytics_drilldown_opened', { kind: state.analyticsFilters.detailKind, source: 'mini_app' });
      await loadScreen();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="analytics-back"]')?.addEventListener('click', async () => {
    if (!state.analyticsFilters) return;
    state.analyticsFilters.detailKind = undefined;
    state.analyticsFilters.detailValue = undefined;
    state.analyticsFilters.detailCurrency = undefined;
    state.analyticsFilters.detailOperationType = undefined;
    state.analyticsFilters.detailCategoryKey = undefined;
    hapticSelection();
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="analytics-open-operations"]')?.addEventListener('click', async () => {
    const detail = analytics?.selected_detail;
    if (!detail) return;
    const scope = detail.operation_scope || {};
    setGlobalFilters({
      period: 'custom',
      start_date: String(scope.start_date || analytics?.period.start_date || ''),
      end_date: String(scope.end_date || analytics?.period.end_date || ''),
      operation_type: detail.operation_type,
      category: scope.scope_category ? String(scope.scope_category) : 'all',
    });
    state.search = '';
    state.operationScope = {
      currency: String(scope.currency || detail.currency || ''),
      merchant_key: detail.kind === 'merchant' ? String(scope.merchant_key || detail.merchant_key || '') : undefined,
      category_key: scope.category_key ? String(scope.category_key) : detail.kind === 'category' ? String(detail.category_key || '') : undefined,
      scope_category: scope.scope_category ? String(scope.scope_category) : undefined,
    };
    state.tab = 'operations';
    state.sheet = null;
    await api.track('mini_app_analytics_operations_opened', { kind: detail.kind, source: 'mini_app' });
    await loadScreen();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="plans-mode"]').forEach((button) => {
    button.addEventListener('click', async () => {
      hapticSelection();
      state.plansMode = button.dataset.mode === 'limits' ? 'limits' : button.dataset.mode === 'reminders' ? 'reminders' : button.dataset.mode === 'categories' ? 'categories' : 'goals';
      if (state.plansMode === 'goals') state.plansGoalView = 'active';
      await loadScreen();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="carousel-dot"]').forEach((button) => {
    button.addEventListener('click', () => {
      const kind = button.dataset.carousel as 'challenge' | 'goal' | 'limit' | 'reminder' | 'announcement';
      setHomeCarouselIndex(kind, Number(button.dataset.index || 0), 'dot');
      hapticSelection();
      render();
    });
  });
  app.querySelectorAll<HTMLElement>('[data-carousel]').forEach((node) => {
    let startX = 0;
    const kind = node.dataset.carousel as 'challenge' | 'goal' | 'limit' | 'reminder' | 'announcement';
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
      if (event.key === 'Enter' && kind === 'announcement') {
        event.preventDefault();
        void openCurrentAnnouncement();
      }
    });
    node.addEventListener('pointerdown', (event) => {
      startX = event.clientX;
    });
    node.addEventListener('pointerup', (event) => {
      const delta = event.clientX - startX;
      if (Math.abs(delta) < 32) return;
      node.dataset.suppressClick = 'true';
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
  app.querySelectorAll<HTMLButtonElement>('[data-action="shopping-open"]').forEach((button) => {
    button.addEventListener('click', () => void openShoppingList());
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="home-settings-open"]').forEach((button) => {
    button.addEventListener('click', openHomeSettings);
  });
  app.querySelector<HTMLButtonElement>('[data-action="announcement-open"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    await openCurrentAnnouncement();
  });
  app.querySelector<HTMLElement>('[data-announcement-target]')?.addEventListener('click', async (event) => {
    if ((event.currentTarget as HTMLElement).dataset.suppressClick === 'true' || (event.target as HTMLElement).closest('button')) return;
    await openCurrentAnnouncement();
  });
  app.querySelector<HTMLButtonElement>('[data-action="announcement-dismiss"]')?.addEventListener('click', async (event) => {
    const id = (event.currentTarget as HTMLButtonElement).dataset.id;
    if (!id) return;
    try {
      await api.dismissAnnouncement(id, state.workspaceId);
      if (overview) overview = { ...overview, announcements: (overview.announcements || []).filter((item) => item.id !== id) };
      state.announcementIndex = Math.min(state.announcementIndex || 0, Math.max(0, (overview?.announcements?.length || 0) - 1));
      render();
    } catch (error) {
      state.error = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLFormElement>('form[data-action="shopping-add"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = String(new FormData(event.currentTarget as HTMLFormElement).get('text') || '').trim();
    if (!text || state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createShoppingItem(state.workspaceId, text);
      await refreshShoppingItems();
      state.saving = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="shopping-toggle"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      state.saving = true;
      render();
      try {
        await api.updateShoppingItem(Number(button.dataset.id), state.workspaceId, { completed: button.dataset.completed !== 'true' });
        await refreshShoppingItems();
        state.saving = false;
        render();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="shopping-delete"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      state.saving = true;
      render();
      try {
        await api.deleteShoppingItem(Number(button.dataset.id), state.workspaceId);
        await refreshShoppingItems();
        state.saving = false;
        render();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="shopping-edit"]').forEach((button) => {
    button.addEventListener('click', () => {
      const item = shoppingItems.find((entry) => entry.id === Number(button.dataset.id));
      if (!item || state.saving) return;
      state.shoppingEditId = item.id;
      state.shoppingEditText = item.text;
      state.saveError = undefined;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="shopping-edit-cancel"]')?.addEventListener('click', () => {
    state.shoppingEditId = undefined;
    state.shoppingEditText = undefined;
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLFormElement>('form[data-action="shopping-edit-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    const item = shoppingItems.find((entry) => entry.id === Number(form.dataset.id));
    const text = String(new FormData(form).get('text') || '').trim();
    if (!item || !text || state.saving) return;
    if (text === item.text) {
      state.shoppingEditId = undefined;
      state.shoppingEditText = undefined;
      render();
      return;
    }
    state.shoppingEditText = text;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.updateShoppingItem(item.id, state.workspaceId, { text });
      await refreshShoppingItems();
      state.shoppingEditId = undefined;
      state.shoppingEditText = undefined;
      state.saving = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="shopping-clear"]')?.addEventListener('click', () => {
    state.confirmClearShopping = true;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="shopping-clear-cancel"]')?.addEventListener('click', () => {
    state.confirmClearShopping = false;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="shopping-clear-confirm"]')?.addEventListener('click', async () => {
    if (state.saving) return;
    state.saving = true;
    render();
    try {
      await api.clearCompletedShoppingItems(state.workspaceId);
      await refreshShoppingItems();
      state.saving = false;
      state.confirmClearShopping = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
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
  app.querySelectorAll<HTMLButtonElement>('[data-action="home-insight"]').forEach((button) => {
    button.addEventListener('click', async () => {
      selectedInsight = (overview?.insights || []).find((item) => item.id === button.dataset.insightId) || overview?.insight || null;
      if (!selectedInsight) return;
      state.sheet = 'insight-detail';
      state.saveError = undefined;
      await api.track('insight_opened', { detector_type: selectedInsight.detector, surface: 'home' });
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="insight-action"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!selectedInsight) return;
      const action = selectedInsight.actions[Number(button.dataset.index || 0)];
      if (!action) return;
      await api.track('insight_action_clicked', { detector_type: selectedInsight.detector, action_type: action.type, surface: 'detail' });
      await openInsightAction(action.type, action.params);
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="insight-feedback"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!selectedInsight) return;
      const feedback = button.dataset.feedback === 'not_useful' ? 'not_useful' : 'useful';
      state.saving = true;
      state.saveError = undefined;
      render();
      try {
        const insightId = selectedInsight.id;
        await api.insightFeedback(insightId, state.workspaceId, feedback);
        if (feedback === 'not_useful') {
          rejectedInsightIds.add(`${state.workspaceId ?? 'personal'}:${insightId}`);
          state.sheet = null;
          selectedInsight = null;
          state.saving = false;
          showToast('Спасибо, учтём этот выбор.');
          await loadScreen();
          return;
        }
        selectedInsight.feedback = feedback;
        showToast('Спасибо, учтём этот выбор.');
      } catch (error) {
        state.saveError = safeError(error);
      } finally {
        state.saving = false;
        render();
      }
    });
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
    state.planningEstimate = undefined;
    state.planningDraft = undefined;
    state.saveError = undefined;
    state.dirty = false;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="goal-archive-open"]')?.addEventListener('click', () => {
    state.plansGoalView = 'archive';
    hapticSelection();
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="goal-archive-back"]')?.addEventListener('click', () => {
    state.plansGoalView = 'active';
    hapticSelection();
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-open"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = allGoals().find((goal) => goal.id === Number(button.dataset.id)) || null;
      state.sheet = selectedGoal ? 'goal-detail' : null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-edit"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = allGoals().find((goal) => goal.id === Number(button.dataset.id)) || null;
      state.sheet = selectedGoal ? 'goal-edit' : null;
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
      state.goalDraft = undefined;
      state.planningEstimate = undefined;
      state.planningDraft = undefined;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-contribution"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = allGoals().find((goal) => goal.id === Number(button.dataset.id)) || null;
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
        const status = button.dataset.status || 'active';
        await api.setGoalStatus(Number(button.dataset.id), state.workspaceId, status);
        state.sheet = null;
        selectedGoal = null;
        if (status === 'active') state.plansGoalView = 'active';
        showToast(status === 'archived' ? 'Цель перемещена в архив' : status === 'active' ? 'Цель восстановлена' : 'Цель обновлена');
        await reloadActive();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="goal-delete"]').forEach((button) => {
    button.addEventListener('click', () => {
      selectedGoal = allGoals().find((goal) => goal.id === Number(button.dataset.id)) || null;
      if (!selectedGoal || selectedGoal.status !== 'archived') return;
      state.saveError = undefined;
      state.confirmGoalDeleteId = selectedGoal.id;
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="confirm-goal-delete"]')?.addEventListener('click', async (event) => {
    if (state.saving) return;
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id || 0);
    state.saving = true;
    render();
    try {
      await api.deleteGoal(id, state.workspaceId);
      state.confirmGoalDeleteId = undefined;
      state.sheet = null;
      selectedGoal = null;
      showToast('Цель удалена навсегда');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="limit-create"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const scope = button.dataset.scope === 'all_expenses' ? 'all_expenses' : 'category';
      state.limitCreateScope = scope;
      state.insightLimitCategory = undefined;
      state.insightLimitCurrency = undefined;
      selectedLimit = null;
      if (scope === 'category') await loadCategoriesFor('expense');
      else categoryOptions = [];
      state.sheet = 'limit-create';
      state.limitCreateIdempotencyKey = requestId();
      state.saveError = undefined;
      state.dirty = false;
      state.planningEstimate = undefined;
      state.planningDraft = undefined;
      render();
    });
  });
  app.querySelector<HTMLSelectElement>('form[data-action="create-limit"] select[name="scope"]')?.addEventListener('change', async (event) => {
    const form = (event.currentTarget as HTMLSelectElement).closest<HTMLFormElement>('form');
    const scope = (event.currentTarget as HTMLSelectElement).value === 'all_expenses' ? 'all_expenses' : 'category';
    state.limitCreateScope = scope;
    if (form) state.planningDraft = planningDraftFromForm(form);
    state.planningEstimate = undefined;
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
      state.planningEstimate = undefined;
      state.planningDraft = undefined;
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
    state.planningEstimate = undefined;
    state.planningDraft = undefined;
    state.dirty = false;
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-budget-edit"]').forEach((button) => {
    button.addEventListener('click', async () => {
      await loadCategoriesFor('expense');
      selectedCategoryBudget = (plans?.category_budgets || []).find((budget) => budget.id === Number(button.dataset.id)) || null;
      state.sheet = selectedCategoryBudget ? 'category-budget-edit' : null;
      state.saveError = undefined;
      state.planningEstimate = undefined;
      state.planningDraft = undefined;
      state.dirty = false;
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
  app.querySelectorAll<HTMLButtonElement>('[data-action="category-open"]').forEach((button) => {
    button.addEventListener('click', () => {
      const token = button.dataset.token || '';
      selectedCategory = (plans?.categories || []).find((item) => (item.token || item.normalized_name) === token) || null;
      state.sheet = selectedCategory ? 'category-detail' : null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
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
  app.querySelector<HTMLButtonElement>('[data-action="category-preference-priority"]')?.addEventListener('click', async () => {
    if (!selectedCategory) return;
    state.saving = true;
    render();
    try {
      const response = await api.updateCategoryPreference(selectedCategory.token || selectedCategory.normalized_name, {
        workspace_id: state.workspaceId,
        type: state.categoryType || 'expense',
        priority: selectedCategory.priority === 'high' ? 'normal' : 'high',
        relevant: selectedCategory.relevant !== false,
      });
      selectedCategory = response.category;
      if (plans?.categories) plans = { ...plans, categories: plans.categories.map((item) => item.normalized_name === response.category.normalized_name ? response.category : item) };
      hapticSelection();
    } catch (error) {
      state.saveError = safeError(error);
    } finally {
      state.saving = false;
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="category-preference-relevance"]')?.addEventListener('click', async () => {
    if (!selectedCategory) return;
    state.saving = true;
    render();
    try {
      const response = await api.updateCategoryPreference(selectedCategory.token || selectedCategory.normalized_name, {
        workspace_id: state.workspaceId,
        type: state.categoryType || 'expense',
        priority: selectedCategory.priority || 'normal',
        relevant: selectedCategory.relevant === false,
      });
      selectedCategory = response.category;
      if (plans?.categories) plans = { ...plans, categories: plans.categories.map((item) => item.normalized_name === response.category.normalized_name ? response.category : item) };
      hapticSelection();
    } catch (error) {
      state.saveError = safeError(error);
    } finally {
      state.saving = false;
      render();
    }
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
  app.querySelector<HTMLButtonElement>('[data-action="vacation-open"]')?.addEventListener('click', () => {
    state.sheet = 'vacation';
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-history-open"]')?.addEventListener('click', () => {
    state.sheet = 'privacy-history';
    state.privacyStage = 'select';
    state.privacyPeriod = 'this_month';
    state.privacyPreview = undefined;
    state.saveError = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-account-open"]')?.addEventListener('click', () => {
    state.sheet = 'privacy-account';
    state.privacyStage = 'account-info';
    state.accountPreview = undefined;
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
  app.querySelector<HTMLButtonElement>('[data-action="home-enable-all"]')?.addEventListener('click', () => {
    const widgets = profile?.home_preferences.widgets || overview?.home_widgets || [];
    state.homeDraftEnabled = widgets.map((widget) => widget.key);
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="home-reset"]')?.addEventListener('click', () => {
    const widgets = profile?.home_preferences.widgets || overview?.home_widgets || [];
    const defaults = [...widgets].sort((left, right) => left.default_order - right.default_order);
    state.homeDraftOrder = defaults.map((widget) => widget.key);
    state.homeDraftEnabled = defaults.filter((widget) => widget.default_enabled).map((widget) => widget.key);
    render();
  });
  app.querySelectorAll<HTMLInputElement>('[data-action="home-widget-toggle"]').forEach((input) => {
    input.addEventListener('change', () => {
      const key = input.dataset.key as HomeWidgetKey;
      const enabled = new Set(state.homeDraftEnabled || []);
      if (input.checked) enabled.add(key); else enabled.delete(key);
      state.homeDraftEnabled = (state.homeDraftOrder || []).filter((item) => enabled.has(item));
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="home-widget-move"]').forEach((button) => {
    button.addEventListener('click', () => {
      const order = [...(state.homeDraftOrder || [])];
      const index = order.indexOf(button.dataset.key as HomeWidgetKey);
      const next = button.dataset.direction === 'up' ? index - 1 : index + 1;
      if (index < 0 || next < 0 || next >= order.length) return;
      [order[index], order[next]] = [order[next], order[index]];
      state.homeDraftOrder = order;
      hapticSelection();
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="home-drag"]').forEach((handle) => {
    handle.addEventListener('pointerdown', (event) => {
      const key = handle.closest<HTMLElement>('[data-home-key]')?.dataset.homeKey as HomeWidgetKey;
      if (!key) return;
      handle.setPointerCapture(event.pointerId);
      let changed = false;
      const move = (moveEvent: PointerEvent) => {
        const target = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY)?.closest<HTMLElement>('[data-home-key]');
        const targetKey = target?.dataset.homeKey as HomeWidgetKey;
        if (!targetKey || targetKey === key) return;
        const order = [...(state.homeDraftOrder || [])];
        const from = order.indexOf(key);
        const to = order.indexOf(targetKey);
        if (from < 0 || to < 0) return;
        order.splice(to, 0, order.splice(from, 1)[0]);
        state.homeDraftOrder = order;
        changed = true;
        const dragged = handle.closest<HTMLElement>('[data-home-key]');
        if (dragged && target?.parentElement) target.parentElement.insertBefore(dragged, from < to ? target.nextSibling : target);
      };
      const finish = () => {
        handle.removeEventListener('pointermove', move);
        if (changed) hapticSelection();
        render();
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish, { once: true });
      handle.addEventListener('pointercancel', finish, { once: true });
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="home-settings-save"]')?.addEventListener('click', async () => {
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const saved = await api.saveHomePreferences(state.homeDraftOrder || [], state.homeDraftEnabled || []);
      if (profile) profile = { ...profile, home_preferences: saved };
      if (overview) overview = { ...overview, home_widgets: saved.widgets, home_preferences: { order: saved.order, enabled: saved.enabled } };
      state.saving = false;
      state.sheet = null;
      showToast('Главная сохранена');
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
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
      await loadCategoriesFor(selectedOperation.type, selectedOperation.category);
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
  app.querySelectorAll<HTMLButtonElement>('[data-action="planning-calculate"]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (state.saving) return;
      const form = button.closest<HTMLFormElement>('form');
      if (!form) return;
      const kind = button.dataset.kind as PlanningPayload['kind'];
      const payload = planningPayload(form, kind);
      if (kind === 'goal') state.goalDraft = goalPayload(form) as unknown as Record<string, unknown>;
      else state.planningDraft = planningDraftFromForm(form);
      state.saving = true;
      state.saveError = undefined;
      void api.track('smart_planning_opened', {
        planning_kind: kind,
        period_kind: payload.period || 'month',
        source: 'mini_app',
        workspace_type: currentWorkspace()?.kind || 'unknown',
      });
      render();
      try {
        const response = await api.planningEstimate(payload);
        state.planningEstimate = response.estimate;
        state.saving = false;
        for (const conflict of response.estimate.conflicts) {
          void api.track('smart_planning_warning_seen', {
            planning_kind: kind,
            period_kind: response.estimate.scope.period,
            history_confidence: response.estimate.history_confidence,
            warning_kind: conflict.kind,
            source: 'mini_app',
          });
        }
        render();
      } catch (error) {
        state.saving = false;
        state.saveError = safeError(error);
        render();
      }
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="planning-apply"]')?.addEventListener('click', () => {
    const estimate = state.planningEstimate;
    if (!estimate?.can_apply || !estimate.recommendation) return;
    if (estimate.kind === 'goal') {
      state.goalDraft = {
        ...(state.goalDraft || {}),
        strategy: 'contribution',
        comfortable_amount: estimate.recommendation,
      };
      state.goalPlanPreview = undefined;
      state.goalPreviewPayloadHash = undefined;
    } else {
      state.planningDraft = { ...(state.planningDraft || {}), amount: estimate.recommendation };
    }
    state.dirty = true;
    void api.track('smart_planning_applied', {
      planning_kind: estimate.kind,
      period_kind: estimate.scope.period,
      history_confidence: estimate.history_confidence,
      source: 'mini_app',
    });
    render();
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="planning-open-conflict"]').forEach((button) => {
    button.addEventListener('click', () => {
      const entityId = button.dataset.entityId || '';
      state.planningEstimate = undefined;
      state.planningDraft = undefined;
      state.saveError = undefined;
      state.dirty = false;
      if (entityId.startsWith('budget:')) {
        selectedCategoryBudget = (plans?.category_budgets || []).find((budget) => budget.id === Number(entityId.split(':')[1])) || null;
        state.sheet = selectedCategoryBudget ? 'category-budget-edit' : state.sheet;
      } else {
        selectedLimit = findLimitById(entityId);
        state.sheet = selectedLimit ? 'limit-edit' : state.sheet;
      }
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>('[data-action="planning-category-toggle"]').forEach((button) => {
    button.addEventListener('click', () => {
      const category = button.dataset.category || '';
      if (!category || suppressPlanningCategoryClick === category) {
        suppressPlanningCategoryClick = '';
        return;
      }
      const form = button.closest<HTMLFormElement>('form');
      if (!form) return;
      const draft = planningDraftFromForm(form);
      draft.categories = togglePlanningCategory((draft.categories as string[]) || [], category);
      state.planningDraft = draft;
      state.planningEstimate = undefined;
      state.dirty = true;
      hapticSelection();
      render();
    });
  });
  app.querySelectorAll<HTMLElement>('[data-planning-drag]').forEach((handle) => {
    handle.addEventListener('pointerdown', (event) => {
      const category = handle.dataset.planningDrag || '';
      const form = handle.closest<HTMLFormElement>('form');
      const dropZone = form?.querySelector<HTMLElement>('[data-planning-drop-zone]');
      const chip = handle.closest<HTMLElement>('.planning-category-chip');
      if (!category || !form || !dropZone || !chip) return;
      cancelPlanningDrag?.();
      const pointerId = event.pointerId;
      let cleaned = false;
      const setDragOver = (over: boolean) => dropZone.classList.toggle('drag-over', over);
      const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', cancel);
        chip.classList.remove('dragging');
        setDragOver(false);
        try {
          if (handle.hasPointerCapture?.(pointerId)) handle.releasePointerCapture(pointerId);
        } catch {
          // The WebView may release capture before pointerup reaches this listener.
        }
        if (cancelPlanningDrag === cleanup) cancelPlanningDrag = null;
      };
      const move = (moveEvent: PointerEvent) => {
        if (moveEvent.pointerId !== pointerId) return;
        setDragOver(pointerIsOverElement(moveEvent, dropZone));
      };
      const finish = (upEvent: PointerEvent) => {
        if (upEvent.pointerId !== pointerId) return;
        const accepted = pointerIsOverElement(upEvent, dropZone);
        cleanup();
        if (!accepted) return;
        suppressPlanningCategoryClick = category;
        window.setTimeout(() => {
          if (suppressPlanningCategoryClick === category) suppressPlanningCategoryClick = '';
        }, 0);
        const draft = planningDraftFromForm(form);
        const selected = (draft.categories as string[]) || [];
        if (selected.some((item) => canonicalCategoryKey(item) === canonicalCategoryKey(category))) return;
        draft.categories = [...selected, category];
        state.planningDraft = draft;
        state.planningEstimate = undefined;
        state.dirty = true;
        hapticSelection();
        render();
      };
      const cancel = (cancelEvent: PointerEvent) => {
        if (cancelEvent.pointerId === pointerId) cleanup();
      };
      cancelPlanningDrag = cleanup;
      chip.classList.add('dragging');
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', cancel);
      try {
        handle.setPointerCapture?.(pointerId);
      } catch {
        // Window listeners still complete the gesture when capture is unavailable.
      }
      event.preventDefault();
      event.stopPropagation();
    });
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
      if (input.name !== 'amount' && input.name !== 'comfortable_amount') {
        state.planningEstimate = undefined;
        input.closest('form')?.querySelector('[data-testid="smart-planning-result"]')?.remove();
      }
    });
    input.addEventListener('change', () => {
      state.dirty = true;
      invalidateGoalPreview();
      if (input.name !== 'amount' && input.name !== 'comfortable_amount') {
        state.planningEstimate = undefined;
        input.closest('form')?.querySelector('[data-testid="smart-planning-result"]')?.remove();
      }
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
    state.confirmGoalDeleteId = undefined;
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
      const deletedCategory = selectedCategory;
      await api.deleteCategory(form.dataset.token || '', { workspace_id: state.workspaceId, type, transfer_to: String(data.get('transfer_to') || '').trim() || undefined });
      state.sheet = null;
      selectedCategory = null;
      state.saving = false;
      state.dirty = false;
      state.categoryType = type;
      state.plansMode = 'categories';
      if (deletedCategory && categoryKey(state.globalFilters.category) === categoryKey(deletedCategory.name)) {
        setGlobalFilters({ ...state.globalFilters, category: 'all' });
      }
      await loadFilterCategories();
      await loadScreen();
      showToast('Категория удалена');
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

  app.querySelector<HTMLFormElement>('form[data-action="vacation-save"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const data = new FormData(event.currentTarget as HTMLFormElement);
    const enabled = data.get('enabled') === 'on';
    const start_date = String(data.get('start_date') || '') || null;
    const end_date = String(data.get('end_date') || '') || null;
    if (enabled && (!start_date || !end_date || end_date < start_date)) {
      state.saveError = !start_date || !end_date ? 'Укажите дату начала и окончания.' : 'Дата окончания не может быть раньше даты начала.';
      render();
      return;
    }
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const response = await api.setVacation({ enabled, start_date, end_date });
      if (profile) profile = { ...profile, vacation_mode: response.vacation_mode };
      closeSheet();
      showToast('Режим отпуска сохранён');
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="privacy-history-preview"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const period = String(new FormData(event.currentTarget as HTMLFormElement).get('period') || 'this_month') as AppState['privacyPeriod'];
    state.privacyPeriod = period;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      state.privacyPreview = await api.previewHistoryDeletion(period || 'this_month');
      state.privacyStage = 'preview';
      state.saving = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-history-confirm"]')?.addEventListener('click', () => {
    state.privacyStage = 'confirm';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-history-back"]')?.addEventListener('click', () => {
    state.privacyStage = state.privacyStage === 'confirm' ? 'preview' : 'select';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-history-delete"]')?.addEventListener('click', async () => {
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.deleteHistory(state.privacyPeriod || 'this_month');
      overview = null;
      operations = null;
      analytics = null;
      report = null;
      plans = null;
      closeSheet();
      showToast('Финансовая история удалена');
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLButtonElement>('[data-action="privacy-account-preview"]')?.addEventListener('click', async () => {
    if (state.saving) return;
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      state.accountPreview = await api.previewAccountDeletion();
      state.privacyStage = 'account-preview';
      state.saving = false;
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-account-confirm"]')?.addEventListener('click', () => {
    state.privacyStage = 'account-confirm';
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="privacy-account-back"]')?.addEventListener('click', () => {
    state.privacyStage = state.privacyStage === 'account-confirm' ? 'account-preview' : 'account-info';
    render();
  });
  app.querySelector<HTMLFormElement>('form[data-action="privacy-account-delete"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const confirmation = String(new FormData(event.currentTarget as HTMLFormElement).get('confirmation_text') || '');
    if (confirmation !== 'УДАЛИТЬ') {
      state.saveError = 'Введите УДАЛИТЬ без изменений.';
      render();
      return;
    }
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      const response = await api.deleteAccount(confirmation);
      overview = null;
      operations = null;
      analytics = null;
      report = null;
      plans = null;
      profile = null;
      shoppingItems = [];
      categoryOptions = [];
      globalCategoryOptions = [];
      state.boot = undefined;
      state.accountDeletedMessage = response.message;
      state.privacyStage = 'deleted';
      state.saving = false;
      window.localStorage.clear();
      render();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });
  app.querySelector<HTMLButtonElement>('[data-action="close-miniapp"]')?.addEventListener('click', () => getTelegramWebApp()?.close?.());
}

void bootstrap();
