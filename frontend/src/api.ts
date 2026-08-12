import { initData } from './telegram';
import type {
  Bootstrap,
  BudgetLimit,
  CategoryOption,
  ChartCategoryItem,
  CategoryCurrencyGroup,
  MerchantCurrencyGroup,
  MerchantStructureItem,
  Goal,
  GoalMovement,
  GoalPlanPreview,
  PlanningEstimate,
  GlobalFinancialFilters,
  NotificationPreferences,
  Operation,
  PremiumInfo,
  RadarMoneyAxis,
  ThemeMode,
  TimeDynamicsCurrencyGroup,
  TimeDynamicsItem,
  ActivityCalendar,
  HomeReminderSummary,
  Insight,
  CategoryBudgetGroup,
  CategoryBudgetPayload,
  GeneralSpendingLimit,
  Reminder,
  ReminderPayload,
  ReminderRecordPayload,
  ReminderSnoozePayload,
  Workspace,
  HomePreferences,
  ShoppingItem,
  Announcement,
  FinancialReport,
  ReportKind
} from './types';

type Envelope<T> = {
  ok: boolean;
  request_id: string;
  data?: T;
  error?: { code: string; message: string };
};

export type Overview = {
  period: { key: string; start_date: string; end_date: string };
  filters?: { operation_type: string; category: string };
  workspace_scope: number | 'all' | null;
  aggregation_available: boolean;
  totals_by_currency: Record<string, { income: string; expense: string; count: number }>;
  recent_operations: Operation[];
  info?: { kind: string; text: string } | null;
  challenge?: { key: string; title: string; description: string; progress: number; target: number; completed: boolean; cta_label: string; period_type?: string; period_key: string; period_end?: string | null } | null;
  challenges?: Array<{ key: string; title: string; description: string; progress: number; target: number; completed: boolean; cta_label: string; period_type?: string; period_key: string; period_end?: string | null }>;
  focus?: { kind: string; id?: string | number | null; title: string; description: string; percent?: number; projected_percent?: number | null; status?: string; severity?: string; cta_label?: string; target_mode?: 'goals' | 'limits'; read_only?: boolean } | null;
  focus_items?: Array<{ kind: string; id?: string | number | null; title: string; description: string; percent?: number; projected_percent?: number | null; status?: string; severity?: string; cta_label?: string; target_mode?: 'goals' | 'limits'; read_only?: boolean }>;
  goal_items?: Array<{ kind: string; id?: string | number | null; title: string; description: string; percent?: number; status?: string; severity?: string; target_mode?: 'goals' }>;
  limit_items?: Array<{ kind: string; id?: string | number | null; title: string; description: string; percent?: number; projected_percent?: number | null; status?: string; severity?: string; target_mode?: 'limits' }>;
  insights?: Insight[];
  insight?: Insight | null;
  reminder?: HomeReminderSummary | null;
  reminders?: HomeReminderSummary[];
  activity?: ActivityCalendar;
  home_widgets?: HomePreferences['widgets'];
  home_preferences?: Omit<HomePreferences, 'widgets'>;
  shopping?: { items: ShoppingItem[]; active_count: number; completed_count: number; read_only: boolean; available: boolean };
  announcements?: Announcement[];
};

export type AnalyticsResponse = {
  period: { key: string; start_date: string; end_date: string };
  previous_period?: { key: string; start_date: string; end_date: string };
  filters?: { operation_type: string; category: string };
  overview: Overview;
  aggregation_available: boolean;
  available_currencies: string[];
  radar_available_currencies: string[];
  selected_currency?: string | null;
  currency_groups: Record<string, {
    summary: { income: string; expense: string; result: string; count: number };
    category_structure: CategoryCurrencyGroup;
    merchant_structure?: MerchantCurrencyGroup;
    time_dynamics: TimeDynamicsCurrencyGroup;
  }>;
  summary: {
    aggregation_available: boolean;
    available_currencies: string[];
    currency_groups: Record<string, { income: string; expense: string; result: string; count: number }>;
    totals_by_currency: Record<string, { income: string; expense: string; count: number }>;
    result_by_currency: Record<string, string>;
  };
  overview_metrics?: Record<string, Record<'income' | 'expense' | 'result', { current: string; previous: string; delta: string; pct?: string | null; state: string }> & { count: number; previous_count: number }>;
  category_structure: { type: 'expense' | 'income'; top_n: number; currency_groups: Record<string, CategoryCurrencyGroup>; items: ChartCategoryItem[] };
  merchant_structure?: { type: 'expense' | 'income'; dimension: 'merchant'; top_n: number; currency_groups: Record<string, MerchantCurrencyGroup>; items: MerchantStructureItem[] };
  change_contribution?: { type: 'expense' | 'income'; currency_groups: Record<string, { currency: string; type: 'expense' | 'income'; current_total: string; previous_total: string; total_delta: string; reconciles: boolean; items: ChartCategoryItem[] }>; items: ChartCategoryItem[] };
  time_dynamics: { grouping: string; currency_groups: Record<string, TimeDynamicsCurrencyGroup>; items: TimeDynamicsItem[] };
  radar: {
    type: 'expense' | 'income';
    currency?: string | null;
    aggregation_available?: boolean;
    current_period: { key: string; start_date: string; end_date: string };
    previous_period: { key: string; start_date: string; end_date: string };
    metric: string;
    max_axes: number;
    scale: { max: string; step: string; ticks: string[] };
    insufficient_data: boolean;
    reason?: string | null;
    explanation: string;
    axes: RadarMoneyAxis[];
  };
  activity_calendar: ActivityCalendar;
  search?: { query: string; items: Array<{ kind: 'category' | 'merchant' | 'operation'; title: string; subtitle: string; currency: string; amount: string; operation_id?: number; params?: Record<string, string> }> };
  selected_detail?: null | {
    kind: 'category' | 'merchant';
    title: string;
    currency: string;
    operation_type: 'expense' | 'income';
    category_key?: string;
    merchant_key?: string;
    total?: string;
    visible_total?: string;
    previous_total?: string;
    delta?: string;
    pct?: string | null;
    state?: string;
    operation_count: number;
    previous_operation_count?: number;
    average_check?: string;
    previous_average_check?: string;
    frequency_delta?: number;
    frequency_pct?: string | null;
    average_check_delta?: string;
    average_check_pct?: string | null;
    merchant_share_of_category?: string | null;
    merchant_share_of_total?: string | null;
    primary_category?: { category_key: string; category: string; category_total: string; merchant_total: string; merchant_count: number; merchant_share_of_category?: string | null } | null;
    baseline?: { method: string; periods_used: number; amount: string; count: string | number; average_check: string; sufficient_data: boolean; periods?: Array<{ start_date: string; end_date: string; total: string; count: number }> };
    raw_aliases?: string[];
    merchant_breakdown?: MerchantCurrencyGroup;
    operations: Operation[];
    operation_scope: Record<string, string | number | null>;
  };
  top_expense_categories: ChartCategoryItem[];
};

export type OperationsResponse = {
  items: Operation[];
  has_more: boolean;
  limit: number;
  offset: number;
  period: { key: string; start_date: string; end_date: string };
};

export type PlansResponse = {
  read_only: boolean;
  goals: Goal[];
  archived_goals: Goal[];
  limits: BudgetLimit[];
  general_limits: GeneralSpendingLimit[];
  category_budgets: CategoryBudgetGroup[];
  reminders: Reminder[];
  categories?: CategoryOption[];
  categories_read_only?: boolean;
  category_type?: 'expense' | 'income';
  all_scope_note?: string | null;
};

export type OperationPayload = {
  workspace_id: number | 'all' | null;
  type: 'expense' | 'income' | 'Расходы' | 'Доходы';
  amount: string;
  category: string;
  description: string;
  op_date: string;
  idempotency_key: string;
};

export type GoalPayload = {
  workspace_id: number | 'all' | null;
  idempotency_key?: string;
  title?: string;
  display_name?: string;
  target_amount?: string;
  current_amount?: string;
  initial_amount?: string;
  deadline?: string;
  strategy?: 'none' | 'deadline' | 'contribution';
  frequency?: 'none' | 'monthly' | 'twice_monthly' | 'weekly';
  comfortable_amount?: string;
  reminders_enabled?: boolean;
  day?: number;
  days?: number[];
  weekday?: number;
  preview_payload_hash?: string;
};

export type GoalMovementPayload = {
  workspace_id: number | 'all' | null;
  movement_type: 'contribution' | 'withdrawal' | 'adjustment';
  amount?: string;
  new_balance?: string;
  idempotency_key: string;
};

export type LimitPayload = {
  workspace_id: number | 'all' | null;
  idempotency_key?: string;
  title?: string;
  scope: 'category' | 'all_expenses';
  category?: string;
  amount: string;
  period: 'week' | 'month';
  currency?: string;
  alerts_enabled?: boolean;
};

export type PlanningPayload = {
  workspace_id: number | 'all' | null;
  kind: PlanningEstimate['kind'];
  currency: string;
  period?: 'week' | 'month';
  category?: string;
  categories?: string[];
  editing_entity_id?: string | number;
  target_amount?: string;
  current_amount?: string;
  deadline?: string;
  frequency?: GoalPayload['frequency'];
  day?: number;
  days?: number[];
  weekday?: number;
};

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const BASE = import.meta.env.VITE_MINIAPP_API_BASE || '';

export function requestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `miniapp-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function query(params: Record<string, unknown>): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    out.set(key, String(value));
  }
  const rendered = out.toString();
  return rendered ? `?${rendered}` : '';
}

function categoryPathToken(token: string): string {
  return encodeURIComponent(encodeURIComponent(token));
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Content-Type', 'application/json');
  headers.set('Authorization', `tma ${initData()}`);
  headers.set('X-Request-ID', requestId());
  const response = await fetch(`${BASE}${path}`, { ...init, headers, credentials: 'same-origin' });
  const envelope = (await response.json()) as Envelope<T>;
  if (!response.ok || !envelope.ok) {
    throw new ApiError(envelope.error?.code || 'request_failed', envelope.error?.message || 'Request failed');
  }
  return envelope.data as T;
}

export const api = {
  bootstrap: () => apiFetch<Bootstrap>('/miniapp/api/bootstrap'),
  workspaces: () => apiFetch<{ items: Workspace[] }>('/miniapp/api/workspaces'),
  overview: (workspaceId: number | 'all' | null, filters: GlobalFinancialFilters) =>
    apiFetch<Overview>(`/miniapp/api/overview${query({ workspace_id: workspaceId, ...filters })}`),
  homePreferences: () => apiFetch<HomePreferences>('/miniapp/api/profile/home'),
  saveHomePreferences: (order: HomePreferences['order'], enabled: HomePreferences['enabled']) =>
    apiFetch<HomePreferences>('/miniapp/api/profile/home', { method: 'POST', body: JSON.stringify({ order, enabled }) }),
  shoppingItems: (workspaceId: number | 'all' | null) => apiFetch<{ items: ShoppingItem[]; read_only: boolean; active_count?: number; completed_count?: number; note?: string }>(`/miniapp/api/shopping${query({ workspace_id: workspaceId })}`),
  createShoppingItem: (workspace_id: number | 'all' | null, text: string) => apiFetch<{ item: ShoppingItem }>('/miniapp/api/shopping', { method: 'POST', body: JSON.stringify({ workspace_id, text }) }),
  updateShoppingItem: (id: number, workspace_id: number | 'all' | null, payload: { text?: string; completed?: boolean }) => apiFetch<{ item: ShoppingItem }>(`/miniapp/api/shopping/${id}`, { method: 'PATCH', body: JSON.stringify({ workspace_id, ...payload }) }),
  deleteShoppingItem: (id: number, workspace_id: number | 'all' | null) => apiFetch<{ deleted: boolean }>(`/miniapp/api/shopping/${id}`, { method: 'DELETE', body: JSON.stringify({ workspace_id }) }),
  clearCompletedShoppingItems: (workspace_id: number | 'all' | null) => apiFetch<{ deleted: number }>('/miniapp/api/shopping/completed', { method: 'DELETE', body: JSON.stringify({ workspace_id }) }),
  dismissAnnouncement: (id: string, workspaceId: number | 'all' | null) => apiFetch<{ dismissed: boolean }>(`/miniapp/api/announcements/${encodeURIComponent(id)}/dismiss`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId }) }),
  insightImpression: (id: string, workspaceId: number | 'all' | null) =>
    apiFetch<{ recorded: boolean }>(`/miniapp/api/insights/${encodeURIComponent(id)}/impression`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId }) }),
  insightFeedback: (id: string, workspaceId: number | 'all' | null, feedbackType: 'useful' | 'not_useful') =>
    apiFetch<{ recorded: boolean; feedback_type: 'useful' | 'not_useful'; suppressed_until?: string | null }>(`/miniapp/api/insights/${encodeURIComponent(id)}/feedback`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, feedback_type: feedbackType }) }),
  operations: (workspaceId: number | 'all' | null, filters: GlobalFinancialFilters & { currency?: string; merchant?: string; merchant_key?: string; category_key?: string; scope_category?: string }, offset = 0, search = '') =>
    apiFetch<OperationsResponse>(`/miniapp/api/operations${query({ workspace_id: workspaceId, ...filters, offset, search })}`),
  categories: (workspaceId: number | 'all' | null, type: 'expense' | 'income' | 'Расходы' | 'Доходы') =>
    apiFetch<{ items: CategoryOption[]; read_only: boolean; note?: string }>(`/miniapp/api/categories${query({ workspace_id: workspaceId, type })}`),
  managedCategories: (workspaceId: number | 'all' | null, type: 'expense' | 'income') =>
    apiFetch<{ items: CategoryOption[]; read_only: boolean; note?: string }>(`/miniapp/api/categories/manage${query({ workspace_id: workspaceId, type })}`),
  createCategory: (payload: { workspace_id: number | 'all' | null; type: 'expense' | 'income'; name: string }) =>
    apiFetch<{ category: CategoryOption; created: boolean }>('/miniapp/api/categories', { method: 'POST', body: JSON.stringify(payload) }),
  renameCategory: (token: string, payload: { workspace_id: number | 'all' | null; type: 'expense' | 'income'; name: string }) =>
    apiFetch<{ category: CategoryOption; result: string }>(`/miniapp/api/categories/${categoryPathToken(token)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteCategory: (token: string, payload: { workspace_id: number | 'all' | null; type: 'expense' | 'income'; transfer_to?: string }) =>
    apiFetch<{ deleted: boolean; references: Record<string, number> }>(`/miniapp/api/categories/${categoryPathToken(token)}`, { method: 'DELETE', body: JSON.stringify(payload) }),
  operationDetail: (id: number) => apiFetch<Operation>(`/miniapp/api/operations/${id}`),
  createOperation: (payload: OperationPayload) =>
    apiFetch<{ operation: Operation }>('/miniapp/api/operations', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  updateOperation: (id: number, payload: Partial<OperationPayload>) =>
    apiFetch<{ operation: Operation }>(`/miniapp/api/operations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteOperation: (id: number) => apiFetch<{ deleted: boolean; operation_id: number }>(`/miniapp/api/operations/${id}`, { method: 'DELETE', body: '{}' }),
  analytics: (workspaceId: number | 'all' | null, filters: GlobalFinancialFilters & { currency?: string; category_type?: string; radar_type?: string; grouping?: string; analytics_search?: string; detail_kind?: string; detail_value?: string; detail_currency?: string; detail_operation_type?: string; detail_category_key?: string }) =>
    apiFetch<AnalyticsResponse>(
      `/miniapp/api/analytics${query({ workspace_id: workspaceId, ...filters })}`
    ),
  report: (workspaceId: number | 'all' | null, filters: GlobalFinancialFilters & { report_kind: ReportKind; currency?: string }) =>
    apiFetch<{ report: FinancialReport }>(
      `/miniapp/api/reports${query({ workspace_id: workspaceId, ...filters })}`
    ),
  plans: (workspaceId: number | 'all' | null) => apiFetch<PlansResponse>(`/miniapp/api/plans${query({ workspace_id: workspaceId })}`),
  planningEstimate: (payload: PlanningPayload) =>
    apiFetch<{ estimate: PlanningEstimate }>('/miniapp/api/planning/estimate', { method: 'POST', body: JSON.stringify(payload) }),
  goals: (workspaceId: number | 'all' | null, statusGroup: 'active' | 'archive' = 'active') => apiFetch<{ items: Goal[]; read_only: boolean; note?: string }>(`/miniapp/api/goals${query({ workspace_id: workspaceId, status_group: statusGroup })}`),
  goalDetail: (id: number, workspaceId: number | 'all' | null) =>
    apiFetch<{ goal: Goal; movements: GoalMovement[] }>(`/miniapp/api/goals/${id}${query({ workspace_id: workspaceId })}`),
  createGoal: (payload: GoalPayload) =>
    apiFetch<{ goal: Goal; plan_preview: GoalPlanPreview }>('/miniapp/api/goals', { method: 'POST', body: JSON.stringify(payload) }),
  updateGoal: (id: number, payload: GoalPayload) =>
    apiFetch<{ goal: Goal; plan_preview: GoalPlanPreview }>(`/miniapp/api/goals/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  goalPlanPreview: (payload: GoalPayload, id?: number) =>
    apiFetch<{ plan_preview: GoalPlanPreview }>(`/miniapp/api/goals/${id ? `${id}/` : ''}plan-preview`, { method: 'POST', body: JSON.stringify(payload) }),
  addGoalMovement: (id: number, payload: GoalMovementPayload) =>
    apiFetch<{ goal: Goal; movement?: GoalMovement | null; created: boolean }>(`/miniapp/api/goals/${id}/contributions`, { method: 'POST', body: JSON.stringify(payload) }),
  setGoalStatus: (id: number, workspaceId: number | 'all' | null, status: string) =>
    apiFetch<{ goal: Goal }>(`/miniapp/api/goals/${id}/status`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, status }) }),
  deleteGoal: (id: number, workspaceId: number | 'all' | null) =>
    apiFetch<{ deleted: boolean; goal_id: number; deleted_movement_count: number }>(`/miniapp/api/goals/${id}`, { method: 'DELETE', body: JSON.stringify({ workspace_id: workspaceId }) }),
  setGoalReminders: (id: number, workspaceId: number | 'all' | null, enabled: boolean) =>
    apiFetch<{ goal: Goal }>(`/miniapp/api/goals/${id}/reminders`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, enabled }) }),
  reminders: () => apiFetch<{ items: Reminder[] }>('/miniapp/api/reminders'),
  reminderDetail: (id: number) => apiFetch<{ reminder: Reminder }>(`/miniapp/api/reminders/${id}`),
  createReminder: (payload: ReminderPayload) =>
    apiFetch<{ reminder: Reminder }>('/miniapp/api/reminders', { method: 'POST', body: JSON.stringify(payload) }),
  updateReminder: (id: number, payload: Partial<ReminderPayload>) =>
    apiFetch<{ reminder: Reminder }>(`/miniapp/api/reminders/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteReminder: (id: number) => apiFetch<{ deleted: boolean; reminder_id: number }>(`/miniapp/api/reminders/${id}`, { method: 'DELETE', body: '{}' }),
  recordReminder: (id: number, payload: ReminderRecordPayload) =>
    apiFetch<{ result: string; reminder?: Reminder | null; operation?: Operation | null }>(`/miniapp/api/reminders/${id}/record`, { method: 'POST', body: JSON.stringify(payload) }),
  snoozeReminder: (id: number, payload: ReminderSnoozePayload = {}) =>
    apiFetch<{ reminder: Reminder }>(`/miniapp/api/reminders/${id}/snooze`, { method: 'POST', body: JSON.stringify(payload) }),
  toggleReminder: (id: number, enabled?: boolean) =>
    apiFetch<{ reminder: Reminder }>(`/miniapp/api/reminders/${id}/toggle`, { method: 'POST', body: JSON.stringify(enabled === undefined ? {} : { enabled }) }),
  limits: (workspaceId: number | 'all' | null) => apiFetch<{ items: BudgetLimit[]; read_only: boolean; note?: string }>(`/miniapp/api/limits${query({ workspace_id: workspaceId })}`),
  createLimit: (payload: LimitPayload) =>
    apiFetch<{ limit: BudgetLimit }>('/miniapp/api/limits', { method: 'POST', body: JSON.stringify(payload) }),
  updateLimit: (id: string, payload: LimitPayload | { workspace_id: number | 'all' | null; toggle: true; enabled?: boolean; alerts_enabled?: boolean }) =>
    apiFetch<{ limit: BudgetLimit }>(`/miniapp/api/limits/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteLimit: (id: string, workspaceId: number | 'all' | null) =>
    apiFetch<{ deleted: boolean; limit_id: string }>(`/miniapp/api/limits/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify({ workspace_id: workspaceId }) }),
  createCategoryBudget: (payload: CategoryBudgetPayload) =>
    apiFetch<{ budget: CategoryBudgetGroup }>('/miniapp/api/category-budgets', { method: 'POST', body: JSON.stringify(payload) }),
  updateCategoryBudget: (id: number, payload: CategoryBudgetPayload | ({ workspace_id: number | 'all' | null; toggle: true; enabled?: boolean; alerts_enabled?: boolean })) =>
    apiFetch<{ budget: CategoryBudgetGroup }>(`/miniapp/api/category-budgets/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteCategoryBudget: (id: number, workspaceId: number | 'all' | null) =>
    apiFetch<{ deleted: boolean; budget_id: number }>(`/miniapp/api/category-budgets/${id}`, { method: 'DELETE', body: JSON.stringify({ workspace_id: workspaceId }) }),
  profile: () => apiFetch<{ theme: ThemeMode; preferred_name?: string | null; display_name?: string; currency: string; available_currencies?: string[]; timezone: string; timezone_options?: Array<{ label: string; value: string }>; workspaces: Workspace[]; version: string; links?: { privacy?: string | null; terms?: string | null }; help_url: string; notifications: NotificationPreferences; premium: PremiumInfo; export: { available: boolean; status: string; presets: string[]; privacy_note: string }; categories: { expense: CategoryOption[]; income: CategoryOption[] }; home_preferences: HomePreferences }>('/miniapp/api/profile'),
  setTheme: (theme: ThemeMode) => apiFetch<{ theme: ThemeMode }>('/miniapp/api/profile/theme', { method: 'POST', body: JSON.stringify({ theme }) }),
  setPreferredName: (preferred_name: string) => apiFetch<{ preferred_name?: string | null; display_name: string }>('/miniapp/api/profile/preferred-name', { method: 'POST', body: JSON.stringify({ preferred_name }) }),
  setCurrency: (currency: string) => apiFetch<{ currency: string }>('/miniapp/api/profile/currency', { method: 'POST', body: JSON.stringify({ currency }) }),
  setTimezone: (timezone: string) => apiFetch<{ timezone: string; notifications: NotificationPreferences }>('/miniapp/api/profile/timezone', { method: 'POST', body: JSON.stringify({ timezone }) }),
  setActiveWorkspace: (workspace_id: number) => apiFetch<{ workspaces: Workspace[]; active_workspace_id: number }>('/miniapp/api/profile/active-workspace', { method: 'POST', body: JSON.stringify({ workspace_id }) }),
  renameWorkspace: (workspace_id: number, name: string) => apiFetch<{ workspace: Workspace; workspaces: Workspace[] }>(`/miniapp/api/workspaces/${workspace_id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  notificationPreferences: () => apiFetch<NotificationPreferences>('/miniapp/api/profile/notifications'),
  updateNotificationPreferences: (payload: Record<string, unknown>) =>
    apiFetch<NotificationPreferences>('/miniapp/api/profile/notifications', { method: 'POST', body: JSON.stringify(payload) }),
  premium: () => apiFetch<PremiumInfo>('/miniapp/api/profile/premium'),
  exportInfo: () => apiFetch<{ available: boolean; status: string; presets: string[]; privacy_note: string }>('/miniapp/api/profile/export'),
  exportPreview: (payload: Record<string, unknown>) =>
    apiFetch<{ preset: string; period: { start_date: string; end_date: string }; count: number; totals_by_currency: Record<string, { income: string; expense: string; count: number }> }>('/miniapp/api/profile/export', { method: 'POST', body: JSON.stringify({ ...payload, action: 'preview' }) }),
  sendExport: (payload: Record<string, unknown>) =>
    apiFetch<{ result: string; filename: string; preset: string; period: { start_date: string; end_date: string }; count: number; totals_by_currency: Record<string, { income: string; expense: string; count: number }> }>('/miniapp/api/profile/export', { method: 'POST', body: JSON.stringify({ ...payload, action: 'send' }) }),
  track: (event: string, properties: Record<string, string>) =>
    apiFetch<{ tracked: boolean }>('/miniapp/api/analytics/event', { method: 'POST', body: JSON.stringify({ event, properties }) }).catch(() => undefined)
};
