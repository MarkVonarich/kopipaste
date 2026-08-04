export type TabKey = 'operations' | 'analytics' | 'home' | 'plans' | 'profile';
export type ThemeMode = 'telegram' | 'light' | 'dark';
export type PeriodKey = 'current_month' | 'previous_month' | 'last_30' | 'custom';
export type OperationType = 'Расходы' | 'Доходы';

export type Workspace = {
  workspace_id: number | 'all' | null;
  name: string;
  kind: string;
  role: string;
  active?: boolean;
  read_only?: boolean;
};

export type PeriodState = {
  period: PeriodKey;
  start_date?: string;
  end_date?: string;
};

export type Operation = {
  id: number;
  op_date: string;
  type: OperationType;
  category: string;
  amount: string;
  amount_text: string;
  currency: string;
  description: string;
  workspace_id: number | null;
  workspace_name?: string | null;
  actor_user_id?: string | null;
  created_at?: string | null;
};

export type CategoryOption = {
  name: string;
  normalized_name: string;
  type: OperationType;
  source: string;
  operation_count: number;
  has_budget: boolean;
};

export type Bootstrap = {
  user: { id: string; locale: string; currency: string; timezone: string };
  workspaces: Workspace[];
  periods: PeriodKey[];
  theme: ThemeMode;
  version: string;
};

export type AppState = {
  tab: TabKey;
  theme: ThemeMode;
  workspaceId: number | 'all' | null;
  period: PeriodState;
  boot?: Bootstrap;
  loading: boolean;
  error?: string;
  detailOperationId?: number;
  search: string;
  saving: boolean;
  saveError?: string;
  addIdempotencyKey?: string;
  formDraft?: Partial<Operation>;
  confirmDeleteId?: number;
  dirty: boolean;
  sheet: null | 'add-expense' | 'add-income' | 'actions';
};
