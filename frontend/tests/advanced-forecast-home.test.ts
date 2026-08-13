import { describe, expect, it } from 'vitest';
import type { Overview } from '../src/api';
import { CanSpendView, SpendableDetail } from '../src/components/Forecasting';
import { HomeScreen } from '../src/components/HomeScreen';
import { HomeSettingsForm } from '../src/components/HomeSettings';
import type { CanSpendResult, HomePreferences, Insight, SpendableForecast } from '../src/types';

const spendable: SpendableForecast = {
  available: true,
  amount: '22000.00',
  currency: 'RUB',
  approximate: true,
  period_label: 'до конца месяца',
  quality_label: 'По вашей истории',
  quality_tier: 'personal',
  risk_state: 'normal',
  fingerprint: 'f'.repeat(64),
  feedback: null,
  experiment: { enabled: true, variant: 'compact' },
  current_result: '50000.00',
  known_commitments: '8000.00',
  known_commitment_count: 2,
  expected_income: '30000.00',
  goal_reserve: '5000.00',
  variable_q50: '12000.00',
  variable_q80: '15000.00',
  variable_q90: '18000.00',
  variable_reserve: '15000.00',
  general_budget_remaining: null,
  expected_end_result: '30000.00',
  lower_spendable: '19000.00',
  upper_spendable: '25000.00',
  model_family: 'personal_ensemble',
  model_version: 'personal-ensemble-v1',
  risk_policy_version: 'downside-q80-v1',
  calibration_state: 'insufficient',
  history_periods: 5,
  reasons: [{ code: 'known_commitments', label: 'Будущие обязательные платежи', amount: '8000.00', count: 2 }],
  trajectory: [
    { date: '2026-08-13', expected_expense: '10000.00', upper_expense: '12000.00' },
    { date: '2026-08-31', expected_expense: '22000.00', upper_expense: '28000.00' },
  ],
};

const insight: Insight = {
  id: 'a'.repeat(64),
  type: 'spendable_risk',
  detector: 'spendable_risk',
  tone: 'warning',
  severity: 'high',
  title: 'Свободная сумма требует внимания',
  summary: 'Проверьте будущие платежи',
  currency: 'RUB',
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-31' },
  comparison_period: { key: 'previous_month', start_date: '2026-07-01', end_date: '2026-07-31' },
  evidence: [],
  actions: [],
  feedback: null,
};

const overview: Overview = {
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-31' },
  workspace_scope: 10,
  aggregation_available: true,
  totals_by_currency: { RUB: { income: '50000.00', expense: '10000.00', count: 8 } },
  result_comparison: { current: '40000.00', previous: '35000.00', delta: '5000.00', pct: '14.3', state: 'ok' },
  spendable,
  recent_operations: [],
  activity: { start_date: '2026-08-01', end_date: '2026-08-31', max_count: 3, current_streak: 4, active_days: 7, days_in_period: 31, operations_count: 8, label: 'Активность', days: [] },
  announcements: [{ id: 'forecast-v1', family: 'forecast', kind: 'feature', released_on: '2026-08-13', title: 'Прогноз на главной', description: 'Свободная сумма учитывает планы и будущие платежи.', action: { type: 'OPEN_HOME', label: 'Открыть' } }],
  limit_items: [{ kind: 'limit', title: 'Общий лимит', description: 'В норме', percent: 35, target_mode: 'limits' }],
  goal_items: [{ kind: 'goal', title: 'Отпуск', description: 'В плане', percent: 40, target_mode: 'goals' }],
  reminders: [{ id: 7, state: 'upcoming', title: 'Интернет', event_date: '20 августа', status_text: 'Через 7 дней', overdue_days: 0 }],
  insights: [insight],
  shopping: { items: [], active_count: 0, completed_count: 0, read_only: false, available: true },
  home_preferences: { order: ['limits', 'goals', 'reminders', 'insights', 'shopping_list'], enabled: ['limits', 'goals', 'reminders', 'insights', 'shopping_list'] },
};

const preferences: HomePreferences = {
  widgets: [
    { key: 'limits', title: 'Лимиты', description: 'Лимиты', layout: 'compact', default_enabled: true, default_order: 0 },
    { key: 'goals', title: 'Цели', description: 'Цели', layout: 'compact', default_enabled: true, default_order: 1 },
    { key: 'reminders', title: 'Напоминания', description: 'События', layout: 'compact', default_enabled: true, default_order: 2 },
    { key: 'insights', title: 'Инсайты', description: 'Изменения', layout: 'compact', default_enabled: true, default_order: 3 },
    { key: 'shopping_list', title: 'Список покупок', description: 'Покупки', layout: 'compact', default_enabled: true, default_order: 4 },
  ],
  order: ['limits', 'goals', 'reminders', 'insights', 'shopping_list'],
  enabled: ['limits', 'goals', 'reminders', 'insights', 'shopping_list'],
};

function verdict(value: CanSpendResult['verdict']): CanSpendResult {
  return {
    verdict: value,
    amount_before: '22000.00',
    projected_spendable_after: '12000.00',
    general_budget_remaining: null,
    category_limit_remaining: null,
    grouped_budget_remaining: null,
    goal_reserve: '5000.00',
    risk_state_before: 'normal',
    risk_state_after: 'watch',
    reasons: [],
  };
}

describe('Advanced Forecasting & Home Intelligence', () => {
  it('renders the fixed high-level Home order', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    const labels = ['Активность', '<span>Итог</span>', 'home-income-expense', 'Новое в КопиPaste', 'Лимиты', 'Цели', 'Напоминания', 'Инсайты', 'Список покупок', 'Последние операции'];
    const positions = labels.map((label) => html.indexOf(label));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('renders Activity as a compact strip without an inline calendar', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html).toContain('data-action="activity-open"');
    expect(html).toContain('4 дня подряд');
    expect(html).not.toContain('activity-calendar-grid');
  });

  it('renders Итог and an approximate half-row Spendable value', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html).toContain('<span>Итог</span>');
    expect(html).toContain('~22 000 ₽');
    expect(html).toContain('data-action="spendable-open"');
  });

  it('renders explicit unavailable Spendable without a fake zero', () => {
    const html = HomeScreen({ ...overview, workspace_scope: 'all', spendable: { available: false, code: 'workspace_all', title: 'Выберите пространство', description: 'Выберите пространство для прогноза.' } }, [], 'RUB', false);
    expect(html).toContain('Выберите пространство для прогноза.');
    expect(html).not.toContain('~0 ₽');
  });

  it('keeps forecast and insight feedback inline without technical experiment copy', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html.match(/Полезно\?/g)).toHaveLength(2);
    expect(html).toContain('data-action="forecast-feedback"');
    expect(html).toContain('data-action="home-insight-feedback"');
    expect(html).not.toContain('spendable-explanation-v1');
    expect(html).not.toContain('variant');
  });

  it('renders compact clickable What’s New without a large CTA', () => {
    const html = HomeScreen(overview, [], 'RUB', true);
    expect(html).toContain('announcement-card compact');
    expect(html).toContain('data-action="announcement-open"');
    expect(html).toContain('data-target="OPEN_HOME"');
    expect(html).not.toContain('>Открыть</button>');
  });

  it('does not render Challenges anywhere on Home', () => {
    const html = HomeScreen({ ...overview, challenge: { key: 'daily', title: 'Челлендж', description: 'Старый виджет', progress: 1, target: 2, completed: false, cta_label: 'Старт', period_key: '2026-08-13' } }, [], 'RUB', true);
    expect(html).not.toContain('Челлендж');
    expect(html).not.toContain('Старый виджет');
  });

  it('renders the Spendable breakdown, band, reasons, quality and calculator', () => {
    const html = SpendableDetail(spendable, [{ name: 'Продукты' }]);
    expect(html).toContain('Почему столько?');
    expect(html).toContain('Будущие обязательные платежи');
    expect(html).toContain('Защищено на цели');
    expect(html).toContain('forecast-band');
    expect(html).toContain('Сколько я могу потратить?');
    expect(html).toContain('По вашей истории');
    expect(html).toContain('Этот доход не включён в свободную сумму');
  });

  it.each([
    ['fits', 'Вписывается в текущий прогноз'],
    ['borderline', 'На границе'],
    ['does_not_fit', 'Не вписывается в текущий прогноз'],
    ['insufficient_data', 'истории пока мало'],
  ] as const)('renders can-spend state %s', (state, copy) => {
    expect(CanSpendView(verdict(state), 'RUB')).toContain(copy);
  });

  it('renders only five optional Home settings and no ordering controls', () => {
    const html = HomeSettingsForm(preferences, preferences.order, preferences.enabled);
    expect(html.match(/data-action="home-widget-toggle"/g)).toHaveLength(5);
    expect(html).not.toContain('drag');
    expect(html).not.toContain('data-direction');
    expect(html).not.toContain('Челлендж');
    expect(html).not.toContain('Свободно');
    expect(html).not.toContain('Последние операции');
  });

  it('preserves stable card positions when one optional widget is hidden', () => {
    const html = HomeScreen({ ...overview, home_preferences: { ...overview.home_preferences!, enabled: ['limits', 'reminders', 'insights', 'shopping_list'] } }, [], 'RUB', true);
    expect(html).toContain('Общий лимит');
    expect(html).not.toContain('Отпуск');
    expect(html.indexOf('Общий лимит')).toBeLessThan(html.indexOf('Интернет'));
  });
});
