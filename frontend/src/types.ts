export type TabKey = 'operations' | 'analytics' | 'home' | 'plans' | 'profile';
export type ThemeMode = 'telegram' | 'light' | 'dark';
export type PeriodKey = 'current_week' | 'current_month' | 'previous_month' | 'custom';
export type GlobalOperationType = 'all' | 'expense' | 'income';
export type OperationType = 'Расходы' | 'Доходы';
export type ProfileSection = 'user' | 'appearance' | 'workspaces' | 'notifications' | 'premium' | 'help' | 'legal';

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

export type GlobalFinancialFilters = PeriodState & {
  operation_type: GlobalOperationType;
  category: string;
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
  key?: string;
  category: string;
  currency: string;
  total: string;
  previous_total?: string;
  delta?: string;
  previous_count?: number;
  count: number;
  share: number;
  synthetic?: boolean;
  drillable?: boolean;
  fallback?: boolean;
};

export type MerchantStructureItem = {
  key?: string;
  merchant: string;
  currency: string;
  total: string;
  previous_total?: string;
  delta?: string;
  previous_count?: number;
  count: number;
  share: number;
  synthetic?: boolean;
  drillable?: boolean;
  fallback?: boolean;
  source?: string;
  raw_aliases?: string[];
};

export type CategoryCurrencyGroup = {
  currency: string;
  total: string;
  items: ChartCategoryItem[];
};

export type MerchantCurrencyGroup = {
  currency: string;
  total: string;
  items: MerchantStructureItem[];
};

export type TimeDynamicsItem = {
  date: string;
  currency: string;
  income: string;
  expense: string;
  result: string;
  count: number;
};

export type TimeDynamicsDataset = {
  kind: 'expense' | 'income' | 'result';
  items: Array<{ date: string; amount: string; count: number }>;
};

export type TimeDynamicsCurrencyGroup = {
  currency: string;
  datasets: TimeDynamicsDataset[];
};

export type RadarMoneyAxis = {
  category: string;
  current_amount: string;
  previous_amount: string;
};

export type RadarScale = {
  max: string;
  step: string;
  ticks: string[];
};

export type ActivityDay = {
  date: string;
  count: number;
};

export type ActivityCalendar = {
  start_date: string;
  end_date: string;
  max_count: number;
  current_streak?: number;
  active_days?: number;
  days_in_period?: number;
  operations_count?: number;
  label?: string;
  days: ActivityDay[];
};

export type HomeReminderSummary = {
  state: 'upcoming' | 'overdue' | 'empty';
  id?: number | null;
  title: string;
  event_date?: string | null;
  amount_text?: string | null;
  category?: string | null;
  next_event_date?: string | null;
  status_text: string;
  overdue_days: number;
  repeat_rule?: string | null;
};

export type ReminderStatus = 'overdue' | 'today' | 'upcoming' | 'inactive';

export type Reminder = {
  id: number;
  title: string;
  amount: string;
  amount_text: string;
  currency: string;
  category: string;
  rem_type: OperationType;
  event_date: string;
  status: ReminderStatus;
  repeat_rule: 'none' | 'weekly' | 'monthly' | 'yearly' | 'custom_days';
  repeat_interval_days?: number | null;
  notify_days_before: number;
  next_event_date?: string | null;
  is_active: boolean;
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
  enabled?: boolean;
};

export type GeneralSpendingLimit = BudgetLimit & {
  enabled?: boolean;
};

export type CategoryBudgetGroup = {
  id: number;
  kind: 'category_budget';
  title: string;
  amount: string;
  currency: string;
  spent: string;
  remaining: string;
  percent: number;
  period: string;
  status: string;
  categories: string[];
  enabled: boolean;
  alerts_enabled: boolean;
  workspace_id: number | null;
};

export type ReminderPayload = {
  workspace_id: number | 'all' | null;
  title: string;
  amount: string;
  currency?: string;
  category: string;
  rem_type: 'expense' | 'income' | OperationType;
  event_date: string;
  repeat_rule: Reminder['repeat_rule'];
  repeat_interval_days?: number | null;
  notify_days_before: number;
  is_active?: boolean;
};

export type ReminderRecordPayload = {
  workspace_id: number | 'all' | null;
  idempotency_key: string;
  event_date?: string;
};

export type ReminderSnoozePayload = {
  days?: number;
};

export type CategoryBudgetPayload = {
  workspace_id: number | 'all' | null;
  title: string;
  amount: string;
  currency?: string;
  period: 'week' | 'month';
  categories: string[];
  enabled?: boolean;
  alerts_enabled?: boolean;
};

export type NotificationPreferences = {
  morning_enabled: boolean;
  evening_enabled: boolean;
  limit_alerts_enabled: boolean;
  budget_alerts_enabled: boolean;
  subscription_alerts_enabled?: boolean;
  recurring_spend_alerts_enabled?: boolean;
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
  daily_notifications?: { enabled: boolean; morning_time?: string; evening_time: string };
  plans_control?: { enabled: boolean };
  reports?: { enabled: boolean };
  quiet_hours?: { enabled: boolean; start: string; end: string };
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
  token?: string;
  protected?: boolean;
  references?: {
    operations: number;
    drafts: number;
    category_limits: number;
    category_budget_groups: number;
    reminders: number;
    aliases: number;
    ml_observations: number;
    total: number;
  };
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
  globalFilters: GlobalFinancialFilters;
  boot?: Bootstrap;
  loading: boolean;
  error?: string;
  detailOperationId?: number;
  search: string;
  operationScope?: { currency?: string; merchant?: string; merchant_key?: string; category_key?: string };
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
  sheet: null | 'add-expense' | 'add-income' | 'actions' | 'goal-create' | 'goal-edit' | 'goal-contribution' | 'limit-create' | 'limit-edit' | 'reminder-create' | 'reminder-edit' | 'reminder-detail' | 'reminder-workspace-select' | 'category-budget-create' | 'category-budget-edit' | 'category-create' | 'category-rename' | 'category-delete' | 'premium' | 'export' | 'menu' | 'profile-name' | 'profile-currency' | 'profile-timezone' | 'profile-workspace' | 'quiet-hours';
  profileAccordion?: ProfileSection | null;
  selectedWorkspaceId?: number;
  plansMode?: 'goals' | 'limits' | 'reminders' | 'categories';
  categoryType?: 'expense' | 'income';
  analyticsFilters?: {
    categoryType: 'expense' | 'income';
    dynamicsType: 'expense' | 'income' | 'result' | 'both';
    radarType: 'expense' | 'income';
    grouping?: 'day' | 'week' | 'month';
    analyticsCurrency?: string;
    categoryCurrency?: string;
    dynamicsCurrency?: string;
    radarCurrency?: string;
    structureMode?: 'category' | 'merchant';
    search?: string;
    detailKind?: 'category' | 'merchant';
    detailValue?: string;
    detailCurrency?: string;
    detailOperationType?: 'expense' | 'income';
  };
  selectedGoalId?: number;
  selectedLimitId?: string;
  selectedReminderId?: number;
  selectedCategoryBudgetId?: number;
  reminderIdempotencyKey?: string;
  limitCreateScope?: 'all_expenses' | 'category';
  reminderDraft?: Partial<ReminderPayload>;
  homeChallengeIndex?: number;
  homeFocusIndex?: number;
  homeReminderIndex?: number;
  exportDraft?: Record<string, unknown>;
  exportPreview?: {
    preset: string;
    period: { start_date: string; end_date: string };
    count: number;
    totals_by_currency: Record<string, { income: string; expense: string; count: number }>;
  };
  exportSent?: boolean;
  homeScreenStatus?: 'unsupported' | 'unknown' | 'added' | 'missed' | 'pending';
};
