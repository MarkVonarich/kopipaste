import { initData } from './telegram';
import type { Bootstrap, CategoryOption, Operation, PeriodState, ThemeMode, Workspace } from './types';

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

export type OperationsResponse = {
  items: Operation[];
  has_more: boolean;
  limit: number;
  offset: number;
  period: { key: string; start_date: string; end_date: string };
};

export type PlansResponse = {
  read_only: boolean;
  goals: Array<{ title: string; target: string; current: string; percent: number; currency: string; status: string; deadline?: string | null }>;
  limits: Array<{ category: string; amount: string; spent: string; remaining: string; percent: number; period: string; status: string; currency: string }>;
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
    apiFetch<{ overview: Overview; top_expense_categories: Array<{ category: string; total: string; currency: string; count: number }> }>(
      `/miniapp/api/analytics${query({ workspace_id: workspaceId, ...period })}`
    ),
  plans: (workspaceId: number | 'all' | null) => apiFetch<PlansResponse>(`/miniapp/api/plans${query({ workspace_id: workspaceId })}`),
  profile: () => apiFetch<{ theme: ThemeMode; currency: string; timezone: string; workspaces: Workspace[]; version: string }>('/miniapp/api/profile'),
  setTheme: (theme: ThemeMode) => apiFetch<{ theme: ThemeMode }>('/miniapp/api/profile/theme', { method: 'POST', body: JSON.stringify({ theme }) }),
  track: (event: string, properties: Record<string, string>) =>
    apiFetch<{ tracked: boolean }>('/miniapp/api/analytics/event', { method: 'POST', body: JSON.stringify({ event, properties }) }).catch(() => undefined)
};
