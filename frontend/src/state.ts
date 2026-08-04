import type { AppState, PeriodState, TabKey, ThemeMode, Workspace } from './types';

const STORAGE_KEY = 'finuchet-miniapp-state-v1';

type PersistedState = {
  theme?: ThemeMode;
  workspaceId?: number | 'all' | null;
  period?: PeriodState;
};

export const TAB_ORDER: TabKey[] = ['operations', 'analytics', 'home', 'plans', 'profile'];

export function readPersistedState(): PersistedState {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as PersistedState;
  } catch {
    return {};
  }
}

export function persistState(state: AppState): void {
  const persisted: PersistedState = {
    theme: state.theme,
    workspaceId: state.workspaceId,
    period: state.period
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
}

export function initialState(): AppState {
  const persisted = readPersistedState();
  return {
    tab: 'home',
    theme: persisted.theme || 'telegram',
    workspaceId: persisted.workspaceId ?? null,
    period: persisted.period || { period: 'current_month' },
    loading: true,
    search: '',
    sheet: null
  };
}

export function pickInitialWorkspace(workspaces: Workspace[], preferred: number | 'all' | null | undefined): number | 'all' | null {
  if (preferred === 'all' || workspaces.some((workspace) => workspace.workspace_id === preferred)) return preferred ?? null;
  const active = workspaces.find((workspace) => workspace.active && workspace.workspace_id !== 'all');
  return active?.workspace_id ?? workspaces[0]?.workspace_id ?? null;
}

export function tabLabel(tab: TabKey): string {
  return {
    operations: 'Операции',
    analytics: 'Аналитика',
    home: 'Главная',
    plans: 'Планы',
    profile: 'Профиль'
  }[tab];
}

export function periodLabel(period: PeriodState): string {
  return {
    current_month: 'Месяц',
    previous_month: 'Прошлый',
    last_30: '30 дней',
    custom: `${period.start_date || ''} - ${period.end_date || ''}`
  }[period.period];
}
