import { describe, expect, it } from 'vitest';
import { AppShell } from '../src/components/AppShell';
import { BottomNavigation } from '../src/components/BottomNavigation';
import { BottomSheet } from '../src/components/BottomSheet';
import { ConfirmDialog } from '../src/components/ConfirmDialog';
import { HomeScreen } from '../src/components/HomeScreen';
import { OperationsScreen } from '../src/components/OperationsScreen';
import { PlansScreen } from '../src/components/PlansScreen';
import { ErrorState, LoadingState } from '../src/components/States';
import { TransactionList } from '../src/components/TransactionList';
import type { AppState, Operation } from '../src/types';

const overview = {
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-31' },
  workspace_scope: 1,
  aggregation_available: false,
  totals_by_currency: {
    RUB: { income: '2000.00', expense: '750.00', count: 4 },
    EUR: { income: '0.00', expense: '20.00', count: 1 },
  },
  recent_operations: [],
};

const operation: Operation = {
  id: 7,
  op_date: '2026-08-05',
  type: 'Расходы',
  category: 'Кофе и еда рядом с работой',
  amount: '420.50',
  amount_text: '',
  currency: 'RUB',
  description: 'капучино и сэндвич',
  workspace_id: 1,
  workspace_name: 'Семья',
};

function doc(html: string): Document {
  return new DOMParser().parseFromString(html, 'text/html');
}

async function readStyles(): Promise<string> {
  // @ts-ignore Node built-in is available in Vitest but not bundled into frontend code.
  const fs = await import('node:fs/promises');
  const cwd = (globalThis as unknown as { process: { cwd: () => string } }).process.cwd();
  return fs.readFile(`${cwd}/src/styles.css`, 'utf8');
}

describe('mini app design system semantics', () => {
  it('renders five navigation tabs with Home in the center and a stable active state', () => {
    const parsed = doc(BottomNavigation('home'));
    const buttons = [...parsed.querySelectorAll('button[data-tab]')];

    expect(buttons).toHaveLength(5);
    expect(buttons.map((button) => button.getAttribute('data-tab'))).toEqual(['operations', 'analytics', 'home', 'plans', 'profile']);
    expect(buttons[2].getAttribute('data-tab')).toBe('home');
    expect(buttons[2].className).toContain('active');
    expect(buttons[2].getAttribute('aria-current')).toBe('page');
    expect(parsed.querySelectorAll('svg.ui-icon')).toHaveLength(5);
  });

  it('renders the redesigned AppShell with one screen h1 and compact selectors area', () => {
    const state = {
      tab: 'analytics',
      theme: 'telegram',
      workspaceId: null,
      period: { period: 'current_week' },
      globalFilters: { period: 'current_week', operation_type: 'all', category: 'all' },
      loading: false,
      search: '',
      saving: false,
      dirty: false,
      sheet: null,
      boot: { user: { id: '1', locale: 'ru', currency: 'RUB', timezone: 'Europe/Moscow' }, workspaces: [{ workspace_id: null, name: 'Личное', kind: 'personal', role: 'owner' }], periods: ['current_week'], theme: 'telegram', version: 'test' },
    } satisfies AppState;
    const parsed = doc(AppShell(state, '<div class="toolbar"><select></select></div>', '<section class="screen"><h2>Body</h2></section>'));

    expect(parsed.querySelector('.brand')?.textContent).toContain('КопиPaste');
    expect(parsed.querySelectorAll('h1')).toHaveLength(1);
    expect(parsed.querySelector('h1')?.textContent).toBe('Аналитика');
    expect(parsed.querySelector('.toolbar')).toBeTruthy();
    expect(parsed.querySelector('[data-action="open-menu"]')).toBeTruthy();
    expect(parsed.querySelector('.screen-title p')).toBeFalsy();
  });

  it('renders compact result and Spendable summaries, separate income and expense, and quick actions', () => {
    const parsed = doc(HomeScreen(overview, [operation], 'RUB', true));

    expect(parsed.querySelector('[data-testid="home-summary-row"]')?.textContent).toContain('Итог');
    expect(parsed.querySelector('[data-testid="home-summary-row"]')?.textContent).toContain('Свободно');
    expect(parsed.querySelector('[data-testid="income-column"]')?.textContent).toContain('Доходы');
    expect(parsed.querySelector('[data-testid="expense-column"]')?.textContent).toContain('Расходы');
    expect(parsed.querySelector('button[data-kind="expense"]')?.textContent).toContain('Добавить расход');
    expect(parsed.querySelector('button[data-kind="income"]')?.textContent).toContain('Добавить доход');
  });

  it('renders operation rows as lightweight list rows with accessible detail buttons', () => {
    const parsed = doc(TransactionList([operation]));
    const row = parsed.querySelector('button.operation-row');

    expect(row?.getAttribute('data-action')).toBe('operation-detail');
    expect(row?.textContent).toContain('Кофе');
    expect(row?.textContent).toContain('капучино');
    expect(row?.textContent).toContain('-420,50 ₽');
    expect(parsed.querySelector('.operation-mark')).toBeTruthy();
  });

  it('renders empty, loading, error, bottom sheet and confirm dialog states', () => {
    expect(doc(OperationsScreen({ items: [], has_more: false, limit: 30, offset: 0, period: overview.period }, true, '')).querySelector('[data-state="empty"]')).toBeTruthy();
    expect(doc(LoadingState()).querySelector('.skeleton-stack')).toBeTruthy();
    expect(doc(ErrorState('Ошибка')).querySelector('[role="alert"]')).toBeTruthy();
    expect(doc(BottomSheet('Добавить операцию', '<button>OK</button>')).querySelector('[role="dialog"]')).toBeTruthy();
    expect(doc(ConfirmDialog(1, 'Кофе')).querySelector('[role="alertdialog"]')).toBeTruthy();
  });

  it('renders segmented controls and read-only disabled actions', () => {
    const plans = doc(PlansScreen({ goals: [], limits: [], general_limits: [], category_budgets: [], reminders: [] }, 'goals', true));
    const readOnlyHome = doc(HomeScreen(overview, [], 'RUB', false));

    expect(plans.querySelector('.segmented')?.getAttribute('role')).toBe('tablist');
    expect(plans.querySelectorAll('[role="tab"]')).toHaveLength(4);
    expect(readOnlyHome.querySelector('button[data-action="open-actions"]')?.hasAttribute('disabled')).toBe(true);
    expect(readOnlyHome.querySelector('button[data-kind="expense"]')?.hasAttribute('disabled')).toBe(true);
  });

  it('keeps icon-only buttons labelled and heading hierarchy predictable', () => {
    const parsed = doc(HomeScreen(overview, [], 'RUB', true));
    const iconButtons = [...parsed.querySelectorAll('button.icon-button')];

    expect(iconButtons.length).toBeGreaterThan(0);
    expect(iconButtons.every((button) => button.hasAttribute('aria-label'))).toBe(true);
    expect(parsed.querySelector('h2')?.textContent).toBeTruthy();
    expect(parsed.querySelectorAll('h1')).toHaveLength(0);
  });

  it('defines light and dark token themes without horizontal-scroll-prone structural classes', async () => {
    const styles = await readStyles();

    expect(styles).toContain(':root {');
    expect(styles).toContain(':root[data-theme="dark"]');
    expect(styles).toContain('--accent:');
    expect(styles).not.toContain('overflow-x: scroll');
    expect(styles).not.toContain('width: 100vw');
    expect(styles).toContain('prefers-reduced-motion: reduce');
  });

  it('keeps smart card typography from breaking words arbitrarily', async () => {
    const styles = await readStyles();
    const smartCardRules = styles
      .split('}')
      .filter((rule) => rule.includes('.smart-card'))
      .join('}');

    expect(smartCardRules).not.toContain('word-break: break-all');
    expect(smartCardRules).toContain('word-break: normal');
    expect(smartCardRules).not.toContain('overflow-wrap: anywhere');
  });
});
