export type TabKey = 'operations' | 'analytics' | 'home' | 'plans' | 'profile';
export type ThemeMode = 'telegram' | 'light' | 'dark';
export type PeriodKey = 'current_month' | 'previous_month' | 'last_30' | 'custom';
export type OperationType = 'Расходы' | 'Доходы';
export type ProfileSection = 'user' | 'appearance' | 'workspaces' | 'categories' | 'notifications' | 'export-data' | 'premium' | 'help' | 'legal';

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

export type MoneyTotal = { income: string; expense: string; count: number };

export type ChartCategoryItem = {
  category: string;
  currency: string;
  total: string;
  count: number;
  share: number;
};

export type CategoryCurrencyGroup = {
  currency: string;
  total: string;
  items: ChartCategoryItem[];
};

export type TimeDynamicsItem = {
  date: string;
  currency: string;
  income: string;
  expense: string;
  count: number;
};

export type TimeDynamicsDataset = {
  kind: 'expense' | 'income';
  items: Array<{ date: string; amount: string; count: number }>;
};

export type TimeDynamicsCurrencyGroup = {
  currency: string;
  datasets: TimeDynamicsDataset[];
};

export type RadarAxis = {
  category: string;
  current: number;
  previous: number;
};

export type Goal = {
  id: number;
  title: string;
  target: string;
  current: string;
  remaining: string;
  percent: number;
  currency: string;
  status: string;
  deadline?: string | null;
  strategy: string;
  frequency: string;
  comfortable_amount?: string | null;
  planned_contribution_amount?: string | null;
  schedule_config?: Record<string, unknown>;
  projected_completion_date?: string | null;
  next_contribution_date?: string | null;
  reminders_enabled: boolean;
  next_action: string;
  movement_count: number;
};

export type GoalMovement = {
  id: number;
  goal_id: number;
  movement_type: string;
  amount: string;
  balance_after: string;
  occurred_at: string;
  source: string;
};

export type GoalPlanPreview = {
  strategy: string;
  frequency: string;
  remaining_amount: string;
  occurrence_count: number;
  recommended_amount?: string | null;
  comfortable_amount?: string | null;
  next_occurrence?: string | null;
  projected_completion_date?: string | null;
  required_contributions?: number | null;
  feasible: boolean;
  reason?: string | null;
  schedule_config: Record<string, unknown>;
  preview_payload_hash: string;
};

export type BudgetLimit = {
  id: string;
  kind: string;
  title: string;
  category?: string | null;
  scope: 'category' | 'all_expenses';
  amount: string;
  currency: string;
  spent: string;
  remaining: string;
  percent: number;
  period: string;
  status: string;
  alerts_enabled: boolean;
  workspace_id: number | null;
  icon: string;
};

export type NotificationPreferences = {
  morning_enabled: boolean;
  evening_enabled: boolean;
  limit_alerts_enabled: boolean;
  budget_alerts_enabled: boolean;
  weekly_reports_enabled: boolean;
  monthly_reports_enabled: boolean;
  challenge_notifications_enabled: boolean;
  goal_notifications_enabled: boolean;
  morning_time: string;
  evening_time: string;
  quiet_hours_enabled: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  timezone: string;
};

export type PremiumInfo = {
  available: boolean;
  title: string;
  status: string;
  description: string;
  features: string[];
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
  user: { id: string; locale: string; currency: string; timezone: string; preferred_name?: string | null; display_name?: string };
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
  goalIdempotencyKey?: string;
  goalCreateIdempotencyKey?: string;
  limitCreateIdempotencyKey?: string;
  goalPlanPreview?: GoalPlanPreview;
  goalPreviewPayloadHash?: string;
  goalDraft?: Record<string, unknown>;
  confirmLimitDeleteId?: string;
  formDraft?: Partial<Operation>;
  confirmDeleteId?: number;
  dirty: boolean;
  sheet: null | 'add-expense' | 'add-income' | 'actions' | 'goal-create' | 'goal-edit' | 'goal-contribution' | 'limit-create' | 'limit-edit' | 'premium' | 'export' | 'menu' | 'profile-name' | 'profile-currency' | 'profile-timezone' | 'profile-workspace' | 'quiet-hours';
  profileAccordion?: ProfileSection;
  selectedWorkspaceId?: number;
  plansMode?: 'goals' | 'limits';
  analyticsFilters?: {
    categoryType: 'expense' | 'income';
    dynamicsType: 'expense' | 'income' | 'both';
    radarType: 'expense' | 'income';
    categoryCurrency?: string;
    dynamicsCurrency?: string;
    radarCurrency?: string;
  };
  selectedGoalId?: number;
  selectedLimitId?: string;
};
