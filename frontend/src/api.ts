import { initData } from './telegram';
import type {
  Bootstrap,
  BudgetLimit,
  CategoryOption,
  ChartCategoryItem,
  Goal,
  GoalMovement,
  GoalPlanPreview,
  NotificationPreferences,
  Operation,
  PeriodState,
  PremiumInfo,
  RadarAxis,
  ThemeMode,
  TimeDynamicsItem,
  Workspace
} from './types';

type Envelope<T> = {
  ok: boolean;
  request_id: string;
  data?: T;
  error?: { code: string; message: string };
};

export type Overview = {
  period: { key: string; start_date: string; end_date: string };
  workspace_scope: number | 'all' | null;
  aggregation_available: boolean;
  totals_by_currency: Record<string, { income: string; expense: string; count: number }>;
  recent_operations: Operation[];
  info?: { kind: string; text: string } | null;
};

export type AnalyticsResponse = {
  period: { key: string; start_date: string; end_date: string };
  overview: Overview;
  summary: {
    aggregation_available: boolean;
    totals_by_currency: Record<string, { income: string; expense: string; count: number }>;
    result_by_currency: Record<string, string>;
  };
  category_structure: { type: 'expense' | 'income'; top_n: number; items: ChartCategoryItem[] };
  time_dynamics: { grouping: string; items: TimeDynamicsItem[] };
  radar: {
    type: 'expense' | 'income';
    current_period: { key: string; start_date: string; end_date: string };
    previous_period: { key: string; start_date: string; end_date: string };
    metric: string;
    max_axes: number;
    insufficient_data: boolean;
    explanation: string;
    axes: RadarAxis[];
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
  limits: BudgetLimit[];
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
  title?: string;
  scope: 'category' | 'all_expenses';
  category?: string;
  amount: string;
  period: 'week' | 'month';
  currency?: string;
  alerts_enabled?: boolean;
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
  overview: (workspaceId: number | 'all' | null, period: PeriodState) =>
    apiFetch<Overview>(`/miniapp/api/overview${query({ workspace_id: workspaceId, ...period })}`),
  operations: (workspaceId: number | 'all' | null, period: PeriodState, offset = 0, search = '') =>
    apiFetch<OperationsResponse>(`/miniapp/api/operations${query({ workspace_id: workspaceId, ...period, offset, search })}`),
  categories: (workspaceId: number | 'all' | null, type: 'expense' | 'income' | 'Расходы' | 'Доходы') =>
    apiFetch<{ items: CategoryOption[]; read_only: boolean; note?: string }>(`/miniapp/api/categories${query({ workspace_id: workspaceId, type })}`),
  operationDetail: (id: number) => apiFetch<Operation>(`/miniapp/api/operations/${id}`),
  createOperation: (payload: OperationPayload) =>
    apiFetch<{ operation: Operation }>('/miniapp/api/operations', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  updateOperation: (id: number, payload: Partial<OperationPayload>) =>
    apiFetch<{ operation: Operation }>(`/miniapp/api/operations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteOperation: (id: number) => apiFetch<{ deleted: boolean; operation_id: number }>(`/miniapp/api/operations/${id}`, { method: 'DELETE', body: '{}' }),
  analytics: (workspaceId: number | 'all' | null, period: PeriodState) =>
    apiFetch<AnalyticsResponse>(
      `/miniapp/api/analytics${query({ workspace_id: workspaceId, ...period })}`
    ),
  plans: (workspaceId: number | 'all' | null) => apiFetch<PlansResponse>(`/miniapp/api/plans${query({ workspace_id: workspaceId })}`),
  goals: (workspaceId: number | 'all' | null) => apiFetch<{ items: Goal[]; read_only: boolean; note?: string }>(`/miniapp/api/goals${query({ workspace_id: workspaceId })}`),
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
  setGoalReminders: (id: number, workspaceId: number | 'all' | null, enabled: boolean) =>
    apiFetch<{ goal: Goal }>(`/miniapp/api/goals/${id}/reminders`, { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, enabled }) }),
  limits: (workspaceId: number | 'all' | null) => apiFetch<{ items: BudgetLimit[]; read_only: boolean; note?: string }>(`/miniapp/api/limits${query({ workspace_id: workspaceId })}`),
  createLimit: (payload: LimitPayload) =>
    apiFetch<{ limit: BudgetLimit }>('/miniapp/api/limits', { method: 'POST', body: JSON.stringify(payload) }),
  updateLimit: (id: string, payload: LimitPayload) =>
    apiFetch<{ limit: BudgetLimit }>(`/miniapp/api/limits/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteLimit: (id: string, workspaceId: number | 'all' | null) =>
    apiFetch<{ deleted: boolean; limit_id: string }>(`/miniapp/api/limits/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify({ workspace_id: workspaceId }) }),
  profile: () => apiFetch<{ theme: ThemeMode; currency: string; timezone: string; workspaces: Workspace[]; version: string; links?: { privacy?: string | null; terms?: string | null }; help_url: string; notifications: NotificationPreferences; premium: PremiumInfo; export: { available: boolean; status: string; presets: string[]; privacy_note: string }; categories: { expense: CategoryOption[]; income: CategoryOption[] } }>('/miniapp/api/profile'),
  setTheme: (theme: ThemeMode) => apiFetch<{ theme: ThemeMode }>('/miniapp/api/profile/theme', { method: 'POST', body: JSON.stringify({ theme }) }),
  notificationPreferences: () => apiFetch<NotificationPreferences>('/miniapp/api/profile/notifications'),
  updateNotificationPreferences: (payload: Record<string, unknown>) =>
    apiFetch<NotificationPreferences>('/miniapp/api/profile/notifications', { method: 'POST', body: JSON.stringify(payload) }),
  premium: () => apiFetch<PremiumInfo>('/miniapp/api/profile/premium'),
  exportInfo: () => apiFetch<{ available: boolean; status: string; presets: string[]; privacy_note: string }>('/miniapp/api/profile/export'),
  track: (event: string, properties: Record<string, string>) =>
    apiFetch<{ tracked: boolean }>('/miniapp/api/analytics/event', { method: 'POST', body: JSON.stringify({ event, properties }) }).catch(() => undefined)
};
