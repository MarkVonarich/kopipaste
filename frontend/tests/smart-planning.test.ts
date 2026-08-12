import { describe, expect, it } from 'vitest';
import { CategoryBudgetForm, GoalForm, LimitForm, PlanningPanel } from '../src/components/PlansScreen';
import { canonicalCategoryKey, dedupePlanningCategories, togglePlanningCategory } from '../src/planningSelection';
import type { PlanningEstimate } from '../src/types';

function estimate(overrides: Partial<PlanningEstimate> = {}): PlanningEstimate {
  return {
    kind: 'category_limit',
    scope: { workspace_id: 10, currency: 'RUB', period: 'month', categories: ['заведения'] },
    history: [
      { start_date: '2026-05-01', end_date: '2026-05-31', label: 'Май', amount: '15400.00', income: '0.00', expense: '15400.00', net: '-15400.00', operation_count: 3 },
      { start_date: '2026-06-01', end_date: '2026-06-30', label: 'Июнь', amount: '17900.00', income: '0.00', expense: '17900.00', net: '-17900.00', operation_count: 4 },
      { start_date: '2026-07-01', end_date: '2026-07-31', label: 'Июль', amount: '12300.00', income: '0.00', expense: '12300.00', net: '-12300.00', operation_count: 2 },
      { start_date: '2026-08-01', end_date: '2026-08-31', label: 'Август', amount: '21400.00', income: '0.00', expense: '21400.00', net: '-21400.00', operation_count: 5 },
    ],
    periods_requested: 4,
    valid_periods: 4,
    history_confidence: 'good',
    baseline_average: '16750.00',
    recommendation: '16750.00',
    conflicts: [{ kind: 'above_general_limit', severity: 'warning', title: 'Выше общего лимита', description: 'Предлагаемая сумма выше общего лимита.' }],
    read_only: false,
    can_apply: true,
    ...overrides,
  };
}

describe('Smart Planning Studio', () => {
  it('renders the history entry point, four periods, recommendation and conflict in the existing limit form', () => {
    const html = LimitForm(null, [{ name: 'Заведения' }], false, '', 'category', 'Заведения', 'RUB', estimate(), { amount: '', period: 'month', category: 'Заведения', scope: 'category' });

    expect(html).toContain('Рассчитать по истории');
    expect(html.match(/data-testid="planning-history-row"/g)).toHaveLength(4);
    expect(html).toContain('16 750 ₽');
    expect(html).toContain('Выше общего лимита');
    expect(html).toContain('data-action="planning-apply"');
    expect(html).toContain('data-action="create-limit"');
    const applied = LimitForm(null, [{ name: 'Заведения' }], false, '', 'category', 'Заведения', 'RUB', estimate(), { amount: '16750.00', period: 'month', category: 'Заведения', scope: 'category' });
    expect(applied).toContain('name="amount" inputmode="decimal" placeholder="0,00" value="16750.00"');
  });

  it('keeps manual limit and general-limit flows available without calculation', () => {
    const category = LimitForm(null, [{ name: 'Продукты' }]);
    const general = LimitForm(null, [], false, '', 'all_expenses');

    expect(category).toContain('name="amount"');
    expect(category).toContain('type="submit"');
    expect(general).toContain('data-kind="general_limit"');
    expect(general).not.toContain('data-testid="smart-planning-result"');
  });

  it('deduplicates canonical category variants and supports tap add/remove', () => {
    expect(canonicalCategoryKey(' ПРОЧЕЕ ')).toBe('прочее');
    expect(dedupePlanningCategories(['Прочее', ' ПРОЧЕЕ ', 'прочее'])).toEqual(['Прочее']);
    expect(togglePlanningCategory(['Продукты'], 'Такси')).toEqual(['Продукты', 'Такси']);
    expect(togglePlanningCategory(['Продукты', 'Такси'], ' ТАКСИ ')).toEqual(['Продукты']);
  });

  it('renders touch-safe drag handles, tap fallback and selected hidden save fields', () => {
    const html = CategoryBudgetForm(
      null,
      [{ name: 'Продукты', normalized_name: 'продукты' }, { name: 'Такси', normalized_name: 'такси' }],
      false,
      '',
      ['RUB'],
      'RUB',
      undefined,
      { categories: ['Продукты'], amount: '', period: 'month', currency: 'RUB' },
    );

    expect(html).toContain('data-planning-drag="Продукты"');
    expect(html).toContain('data-action="planning-category-toggle"');
    expect(html).toContain('data-planning-drop-zone');
    expect(html).toContain('name="categories" value="Продукты"');
    expect(html).toContain('data-action="create-category-budget"');
  });

  it('shows required and comfortable goal pace while retaining preview and save controls', () => {
    const planning = estimate({
      kind: 'goal',
      scope: { workspace_id: 10, currency: 'RUB', period: 'month', categories: [] },
      recommendation: '15000.00',
      required_pace: { amount: '20000.00', monthly_amount: '20000.00', occurrence_count: 6 },
      comfortable_pace: { amount: '15000.00', monthly_amount: '15000.00', average_monthly_net: '25000.00', other_goal_commitments: '10000.00', commitment_count: 1 },
      feasibility: 'stretched',
      gap: '5000.00',
      comfortable_completion_date: '2027-03-05',
      conflicts: [],
    });
    const html = GoalForm(null, false, '', undefined, { target_amount: '120000', frequency: 'monthly', deadline: '2027-02-28' }, planning);

    expect(html).toContain('Необходимый темп');
    expect(html).toContain('20 000 ₽ / месяц');
    expect(html).toContain('Комфортный темп');
    expect(html).toContain('15 000 ₽ / месяц');
    expect(html).toContain('data-submit-mode="preview"');
    expect(html).not.toContain('data-submit-mode="confirm"');
  });

  it('renders insufficient history without an apply action', () => {
    const html = PlanningPanel(estimate({ history: [], valid_periods: 0, history_confidence: 'insufficient', baseline_average: null, recommendation: null, can_apply: false, conflicts: [] }));

    expect(html).toContain('Недостаточно истории');
    expect(html).not.toContain('data-action="planning-apply"');
  });

  it('wires Pointer Events, old save APIs, preview invalidation and BackButton architecture', async () => {
    const main = await import('../src/main.ts?raw').then((module) => module.default as string);

    expect(main).toContain("addEventListener('pointerdown'");
    expect(main).toContain("addEventListener('pointerup'");
    expect(main).toContain('togglePlanningCategory');
    expect(main).toContain('await api.createLimit(payload)');
    expect(main).toContain('await api.createCategoryBudget(categoryBudgetPayload(form))');
    expect(main).toContain('state.goalPreviewPayloadHash = undefined');
    expect(main).toContain('tg?.BackButton?.onClick');
  });

  it('keeps draggable motion scoped and avoids horizontal fixed-width architecture', async () => {
    // @ts-ignore Node built-in is available in Vitest but not bundled into frontend code.
    const fs = await import('node:fs/promises');
    const cwd = (globalThis as unknown as { process: { cwd: () => string } }).process.cwd();
    const styles = await fs.readFile(`${cwd}/src/styles.css`, 'utf8');

    expect(styles).toContain('.planning-drag-handle');
    expect(styles).toContain('touch-action: none');
    expect(styles).toContain('flex-wrap: wrap');
    expect(styles).toContain('@media (prefers-reduced-motion: reduce)');
    expect(styles).not.toMatch(/\.planning-panel\s*\{[^}]*width:\s*\d{3,}px/s);
  });
});
