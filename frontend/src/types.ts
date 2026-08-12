export type TabKey = 'operations' | 'analytics' | 'home' | 'plans' | 'profile';
export type ThemeMode = 'telegram' | 'light' | 'dark';
export type PeriodKey = 'current_week' | 'current_month' | 'previous_month' | 'custom';
export type GlobalOperationType = 'all' | 'expense' | 'income';
export type OperationType = 'Расходы' | 'Доходы';
export type ProfileSection = 'user' | 'appearance' | 'home' | 'workspaces' | 'notifications' | 'premium' | 'help' | 'legal';

export type HomeWidgetKey = 'financial_result' | 'activity' | 'income_expense' | 'challenges' | 'goals' | 'limits' | 'reminders' | 'insights' | 'shopping_list' | 'recent_operations' | 'whats_new';
export type HomeWidget = { key: HomeWidgetKey; title: string; description: string; layout: 'compact' | 'wide'; default_enabled: boolean; default_order: number };
export type HomePreferences = { widgets: HomeWidget[]; order: HomeWidgetKey[]; enabled: HomeWidgetKey[] };
export type ShoppingItem = { id: number; workspace_id: number; text: string; completed: boolean; completed_at?: string | null; created_at: string; updated_at: string };
export type AnnouncementActionType = 'OPEN_HOME_SETTINGS' | 'OPEN_SHOPPING_LIST' | 'OPEN_PLANS' | 'OPEN_PROFILE' | 'OPEN_ANALYTICS' | 'OPEN_REPORTS' | 'OPEN_REPORT_WEEKLY' | 'OPEN_REPORT_MONTHLY' | 'OPEN_DETAIL';
export type Announcement = { id: string; family: string; kind: 'feature' | 'improvement' | 'fix' | 'report'; released_on: string; title: string; description: string; detail?: string | null; action: { type: AnnouncementActionType; label: string } };

export type ReportKind = 'selected' | 'completed_week' | 'completed_month';
export type ReportDataState = 'no_data' | 'income_only' | 'expense_only' | 'complete';
export type ReportOperationScope = {
  workspace_id: number | 'all' | null;
  period: string;
  start_date: string;
  end_date: string;
  operation_type: 'expense' | 'income';
  category: string;
  scope_category?: string | null;
  currency: string;
  category_key?: string | null;
  merchant_key?: string | null;
};
export type ReportMetric = { current: string; previous: string; delta: string; pct?: string | null; state: string };
export type ReportDimensionItem = {
  key: string;
  category?: string;
  merchant?: string;
  currency: string;
  total: string;
  previous_total?: string;
  delta?: string;
  count: number;
  previous_count?: number;
  share: number;
  synthetic?: boolean;
  fallback?: boolean;
  drillable: boolean;
  average_check?: string | null;
  operation_scope?: ReportOperationScope | null;
};
export type FinancialReport = {
  kind: ReportKind;
  period: { key: string; start_date: string; end_date: string };
  comparison_period: { key: string; start_date: string; end_date: string };
  workspace: { scope: number | 'all' | null; name: string; type: string; read_only: boolean };
  filters: { operation_type: 'all' | 'expense' | 'income'; category: string };
  available_currencies: string[];
  selected_currency: string;
  data_state: ReportDataState;
  summary: { currency: string; income: string; expense: string; result: string; operation_count: number } | null;
  comparison: ({ income: ReportMetric; expense: ReportMetric; result: ReportMetric; count: number; previous_count: number }) | null;
  structure_type: 'expense' | 'income';
  categories: ReportDimensionItem[];
  merchants: ReportDimensionItem[];
  observations: Array<{
    kind: string;
    title: string;
    description: string;
    delta?: string | null;
    currency: string;
    comparison_state?: string;
    drilldown?: ReportOperationScope | null;
  }>;
  export_available: boolean;
  export_reason?: string | null;
};

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

export type InsightActionType = 'OPEN_ANALYTICS' | 'OPEN_CATEGORY' | 'OPEN_MERCHANT' | 'OPEN_OPERATIONS' | 'OPEN_LIMIT' | 'CREATE_LIMIT';

export type InsightEvidence = {
  kind: 'amount_comparison' | 'contribution_share' | 'merchant_contribution' | 'count_comparison' | 'average_check' | 'limit_pace';
  label: string;
  currency?: string;
  current_amount?: string;
  previous_amount?: string;
  delta_amount?: string;
  spent_amount?: string;
  limit_amount?: string;
  current_count?: number;
  previous_count?: number;
  share_pct?: number;
  used_percent?: number;
  period_progress?: number;
  merchant_key?: string;
};

export type Insight = {
  id: string;
  type: string;
  detector: string;
  tone: 'neutral' | 'positive' | 'warning';
  severity: string;
  title: string;
  summary: string;
  currency: string;
  period: { key: string; start_date: string; end_date: string };
  comparison_period: { key: string; start_date: string; end_date: string };
  evidence: InsightEvidence[];
  actions: Array<{ type: InsightActionType; label: string; params: Record<string, string | number | null> }>;
  feedback?: 'useful' | 'not_useful' | null;
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

export type PlanningHistoryPeriod = {
  start_date: string;
  end_date: string;
  label: string;
  amount: string;
  income: string;
  expense: string;
  net: string;
  operation_count: number;
  expense_count: number;
  income_count: number;
};

export type PlanningConflict = {
  kind: string;
  severity: 'info' | 'warning' | 'blocking';
  title: string;
  description: string;
  entity_id?: string | null;
  amount?: string | null;
  currency?: string | null;
};

export type PlanningEstimate = {
  kind: 'category_limit' | 'general_limit' | 'category_budget' | 'goal';
  scope: { workspace_id: number | null; currency: string; period: 'week' | 'month'; categories: string[] };
  history: PlanningHistoryPeriod[];
  periods_requested: number;
  valid_periods: number;
  history_confidence: 'good' | 'limited' | 'insufficient';
  baseline_average?: string | null;
  recommendation?: string | null;
  required_pace?: { amount?: string | null; monthly_amount?: string | null; occurrence_count?: number; next_occurrence?: string | null; reason?: string | null } | null;
  comfortable_pace?: { amount?: string | null; monthly_amount?: string | null; average_monthly_net?: string | null; other_goal_commitments: string; commitment_count: number } | null;
  feasibility?: 'compatible' | 'stretched' | 'insufficient_history' | 'required_pace_unavailable' | null;
  gap?: string | null;
  comfortable_completion_date?: string | null;
  conflicts: PlanningConflict[];
  read_only: boolean;
  can_apply: boolean;
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
    category_limit_states?: number;
    category_budget_groups: number;
    reminders: number;
    aliases: number;
    ml_observations: number;
    subscription_patterns?: number;
    recurring_spend_patterns?: number;
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
  operationScope?: { currency?: string; merchant?: string; merchant_key?: string; category_key?: string; scope_category?: string };
  reportReturnContext?: {
    workspaceId: number | 'all' | null;
    globalFilters: GlobalFinancialFilters;
    mode: 'reports';
    reportKind: ReportKind;
    reportCurrency?: string;
    search: string;
  };
  saving: boolean;
  saveError?: string;
  addIdempotencyKey?: string;
  goalIdempotencyKey?: string;
  goalCreateIdempotencyKey?: string;
  limitCreateIdempotencyKey?: string;
  goalPlanPreview?: GoalPlanPreview;
  goalPreviewPayloadHash?: string;
  goalDraft?: Record<string, unknown>;
  planningEstimate?: PlanningEstimate;
  planningDraft?: Record<string, unknown>;
  confirmLimitDeleteId?: string;
  confirmGoalDeleteId?: number;
  formDraft?: Partial<Operation>;
  confirmDeleteId?: number;
  dirty: boolean;
  sheet: null | 'add-expense' | 'add-income' | 'actions' | 'insight-detail' | 'announcement-detail' | 'goal-create' | 'goal-detail' | 'goal-edit' | 'goal-contribution' | 'limit-create' | 'limit-edit' | 'reminder-create' | 'reminder-edit' | 'reminder-detail' | 'reminder-workspace-select' | 'category-budget-create' | 'category-budget-edit' | 'category-detail' | 'category-create' | 'category-rename' | 'category-delete' | 'premium' | 'export' | 'menu' | 'profile-name' | 'profile-currency' | 'profile-timezone' | 'profile-workspace' | 'quiet-hours' | 'home-settings' | 'shopping-list';
  profileAccordion?: ProfileSection | null;
  selectedWorkspaceId?: number;
  plansMode?: 'goals' | 'limits' | 'reminders' | 'categories';
  plansGoalView?: 'active' | 'archive';
  categoryType?: 'expense' | 'income';
  analyticsFilters?: {
    mode?: 'analytics' | 'reports';
    reportKind?: ReportKind;
    reportCurrency?: string;
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
    detailCategoryKey?: string;
  };
  selectedGoalId?: number;
  selectedLimitId?: string;
  selectedReminderId?: number;
  selectedCategoryBudgetId?: number;
  reminderIdempotencyKey?: string;
  limitCreateScope?: 'all_expenses' | 'category';
  insightLimitCategory?: string;
  insightLimitCurrency?: string;
  reminderDraft?: Partial<ReminderPayload>;
  homeChallengeIndex?: number;
  homeFocusIndex?: number;
  homeGoalIndex?: number;
  homeLimitIndex?: number;
  homeReminderIndex?: number;
  announcementIndex?: number;
  homeDraftOrder?: HomeWidgetKey[];
  homeDraftEnabled?: HomeWidgetKey[];
  confirmClearShopping?: boolean;
  shoppingEditId?: number;
  shoppingEditText?: string;
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
