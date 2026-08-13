import type { AppState, GlobalFinancialFilters, GlobalOperationType, PeriodState, ProfileSection, TabKey, ThemeMode, Workspace } from './types';

const STORAGE_KEY = 'finuchet-miniapp-state-v1';

type PersistedState = {
  theme?: ThemeMode;
  workspaceId?: number | 'all' | null;
  period?: Partial<PeriodState> & { period?: PeriodState['period'] | 'last_30' };
  globalFilters?: Partial<GlobalFinancialFilters>;
  profileAccordion?: ProfileSection | null;
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
    period: state.globalFilters,
    globalFilters: state.globalFilters,
    profileAccordion: state.profileAccordion
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
}

function normalizePeriod(period?: Partial<PeriodState> & { period?: PeriodState['period'] | 'last_30' }): PeriodState {
  const raw = String(period?.period || '');
  const key = raw === 'last_30' ? 'current_week' : raw;
  if (key === 'custom') {
    return { period: 'custom', start_date: period?.start_date, end_date: period?.end_date };
  }
  if (key === 'current_week' || key === 'current_month' || key === 'previous_month') {
    return { period: key };
  }
  return { period: 'current_month' };
}

function normalizeOperationType(value?: string): GlobalOperationType {
  return value === 'expense' || value === 'income' ? value : 'all';
}

function initialGlobalFilters(persisted: PersistedState): GlobalFinancialFilters {
  const source = (persisted.globalFilters || persisted.period || {}) as Partial<GlobalFinancialFilters>;
  const period = normalizePeriod(source);
  return {
    ...period,
    operation_type: normalizeOperationType(source.operation_type),
    category: typeof source.category === 'string' && source.category.trim() ? source.category.trim() : 'all',
    currency: typeof source.currency === 'string' && source.currency.trim() ? source.currency.trim() : undefined,
  };
}

export function initialState(): AppState {
  const persisted = readPersistedState();
  const globalFilters = initialGlobalFilters(persisted);
  return {
    tab: 'home',
    theme: persisted.theme || 'telegram',
    workspaceId: persisted.workspaceId ?? null,
    period: globalFilters,
    globalFilters,
    loading: true,
    search: '',
    saving: false,
    dirty: false,
    sheet: null,
    profileAccordion: persisted.profileAccordion ?? null,
    plansMode: 'goals',
    plansGoalView: 'active',
    homeChallengeIndex: 0,
    homeFocusIndex: 0,
    homeGoalIndex: 0,
    homeLimitIndex: 0,
    homeReminderIndex: 0,
    announcementIndex: 0,
    analyticsFilters: { categoryType: 'expense', dynamicsType: 'both', radarType: 'expense', structureMode: 'category' }
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
    current_week: 'Неделя',
    custom: `${period.start_date || ''} - ${period.end_date || ''}`
  }[period.period];
}
