import './styles.css';
import { api, type OperationPayload, type OperationsResponse, type Overview } from './api';
import { formatMoneyString, normalizeMoneyText } from './money';
import { getTelegramWebApp, initTelegramShell } from './telegram';
import { initialState, periodLabel, persistState, pickInitialWorkspace, TAB_ORDER, tabLabel } from './state';
import type { AppState, Operation, OperationType, PeriodKey, ThemeMode, Workspace } from './types';

const appRoot = document.querySelector<HTMLDivElement>('#app');
if (!appRoot) throw new Error('Missing app root');
const app: HTMLDivElement = appRoot;

let state: AppState = initialState();
let overview: Overview | null = null;
let operations: OperationsResponse | null = null;
let analytics: { top_expense_categories: Array<{ category: string; total: string; currency: string; count: number }> } | null = null;
let plans: { goals: unknown[]; limits: unknown[] } | null = null;
let profile: { theme: ThemeMode; currency: string; timezone: string; version: string } | null = null;
let selectedOperation: Operation | null = null;
let toastTimer = 0;

function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function applyTheme(mode: ThemeMode): void {
  const tg = getTelegramWebApp();
  const effective = mode === 'telegram' ? tg?.colorScheme || 'light' : mode;
  document.documentElement.dataset.theme = effective;
}

function workspaceLabel(workspace: Workspace): string {
  if (workspace.workspace_id === 'all') return 'Все пространства';
  return workspace.name || 'Личное';
}

function currentWorkspace(): Workspace | undefined {
  return state.boot?.workspaces.find((workspace) => workspace.workspace_id === state.workspaceId);
}

function canWrite(): boolean {
  const workspace = currentWorkspace();
  return Boolean(workspace && workspace.workspace_id !== 'all' && !workspace.read_only);
}

function operationAmount(op: Operation): string {
  return op.amount_text || formatMoneyString(op.amount, op.currency);
}

function operationKind(op: Operation): 'income' | 'expense' {
  return op.type === 'Доходы' ? 'income' : 'expense';
}

function moneyFromTotals(type: 'income' | 'expense'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (currencies.length === 0) return formatMoneyString('0.00', state.boot?.user.currency || 'RUB');
  if (currencies.length === 1) return formatMoneyString(totals[currencies[0]][type], currencies[0]);
  return currencies.map((currency) => formatMoneyString(totals[currency][type], currency)).join(' · ');
}

function renderTopbar(): string {
  const workspaceOptions = (state.boot?.workspaces || [])
    .map((workspace) => `<option value="${esc(workspace.workspace_id)}" ${workspace.workspace_id === state.workspaceId ? 'selected' : ''}>${esc(workspaceLabel(workspace))}</option>`)
    .join('');
  const periodOptions: Array<[PeriodKey, string]> = [
    ['current_month', 'Этот месяц'],
    ['previous_month', 'Прошлый месяц'],
    ['last_30', '30 дней'],
    ['custom', 'Период']
  ];
  return `
    <header class="topbar">
      <div class="brand">
        <h1>КопиPaste</h1>
        <p>${esc(tabLabel(state.tab))} · ${esc(periodLabel(state.period))}</p>
      </div>
      <button class="button" data-action="refresh" title="Обновить">↻</button>
    </header>
    <div class="toolbar">
      <select class="select" data-action="workspace">${workspaceOptions}</select>
      <select class="select" data-action="period">
        ${periodOptions.map(([key, label]) => `<option value="${key}" ${state.period.period === key ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
    </div>
    ${state.period.period === 'custom' ? `
      <div class="toolbar">
        <input class="input" type="date" data-action="start-date" value="${esc(state.period.start_date || '')}" />
        <input class="input" type="date" data-action="end-date" value="${esc(state.period.end_date || '')}" />
      </div>
    ` : ''}
  `;
}

function renderNav(): string {
  const icons: Record<string, string> = { operations: '≡', analytics: '⌁', home: '●', plans: '◇', profile: '☉' };
  return `
    <nav class="nav" aria-label="Основная навигация">
      ${TAB_ORDER.map((tab) => `
        <button data-tab="${tab}" class="${state.tab === tab ? 'active ' : ''}${tab === 'home' ? 'home-tab' : ''}" aria-label="${esc(tabLabel(tab))}">
          <span class="nav-icon">${icons[tab]}</span>
          ${esc(tabLabel(tab))}
        </button>
      `).join('')}
    </nav>
  `;
}

function renderOperationsList(items: Operation[], emptyText = 'Операций пока нет.'): string {
  if (!items.length) return `<div class="empty">${esc(emptyText)}</div>`;
  return `
    <div class="operation-list">
      ${items.map((op) => `
        <button class="operation-row" data-action="operation-detail" data-id="${op.id}">
          <span>
            <span class="operation-title">${esc(op.category || op.description || 'Операция')}</span>
            <span class="operation-meta">${esc(op.op_date)}${op.workspace_name ? ` · ${esc(op.workspace_name)}` : ''}${op.description ? ` · ${esc(op.description)}` : ''}</span>
          </span>
          <span class="operation-amount ${operationKind(op)}">${operationKind(op) === 'income' ? '+' : '-'}${esc(operationAmount(op))}</span>
        </button>
      `).join('')}
    </div>
  `;
}

function renderHome(): string {
  return `
    <section class="screen">
      <div class="metrics">
        <div class="metric"><span>Расходы</span><strong>${esc(moneyFromTotals('expense'))}</strong></div>
        <div class="metric"><span>Доходы</span><strong>${esc(moneyFromTotals('income'))}</strong></div>
      </div>
      ${overview?.info ? `<div class="panel">${esc(overview.info.text)}</div>` : ''}
      <div class="actions">
        <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite() ? '' : 'disabled'}>− Расход</button>
        <button class="button" data-action="open-add" data-kind="income" ${canWrite() ? '' : 'disabled'}>+ Доход</button>
      </div>
      ${renderOperationsList(overview?.recent_operations || [], 'За период операций нет.')}
    </section>
  `;
}

function renderOperations(): string {
  return `
    <section class="screen">
      <input class="input" type="search" data-action="search" placeholder="Поиск" value="${esc(state.search)}" />
      <div class="actions">
        <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite() ? '' : 'disabled'}>− Расход</button>
        <button class="button" data-action="open-add" data-kind="income" ${canWrite() ? '' : 'disabled'}>+ Доход</button>
      </div>
      ${renderOperationsList(operations?.items || [], 'Список пуст.')}
      ${operations?.has_more ? '<button class="button" data-action="load-more">Ещё</button>' : ''}
    </section>
  `;
}

function renderAnalytics(): string {
  const rows = analytics?.top_expense_categories || [];
  return `
    <section class="screen">
      <div class="metrics">
        <div class="metric"><span>Расходы</span><strong>${esc(moneyFromTotals('expense'))}</strong></div>
        <div class="metric"><span>Доходы</span><strong>${esc(moneyFromTotals('income'))}</strong></div>
      </div>
      <div class="panel">
        <strong>Категории</strong>
        ${rows.length ? rows.map((row) => `<div class="detail-row"><span>${esc(row.category)}</span><strong>${esc(formatMoneyString(row.total, row.currency))}</strong></div>`).join('') : '<p class="caption">Нет данных за период.</p>'}
      </div>
    </section>
  `;
}

function renderPlans(): string {
  return `
    <section class="screen">
      <div class="panel">
        <strong>Цели</strong>
        <p class="caption">${(plans?.goals || []).length} активных записей</p>
      </div>
      <div class="panel">
        <strong>Лимиты категорий</strong>
        <p class="caption">${(plans?.limits || []).length} настроек</p>
      </div>
    </section>
  `;
}

function renderProfile(): string {
  return `
    <section class="screen">
      <div class="panel detail-grid">
        <div class="detail-row"><span>Валюта</span><strong>${esc(profile?.currency || state.boot?.user.currency || 'RUB')}</strong></div>
        <div class="detail-row"><span>Часовой пояс</span><strong>${esc(profile?.timezone || state.boot?.user.timezone || '')}</strong></div>
        <div class="detail-row"><span>Версия</span><strong>${esc(profile?.version || state.boot?.version || '')}</strong></div>
      </div>
      <div class="panel">
        <strong>Тема</strong>
        <div class="segmented" data-action="theme">
          ${(['telegram', 'light', 'dark'] as ThemeMode[]).map((theme) => `<button data-theme="${theme}" class="${state.theme === theme ? 'active' : ''}">${theme === 'telegram' ? 'Telegram' : theme === 'light' ? 'Светлая' : 'Тёмная'}</button>`).join('')}
        </div>
      </div>
    </section>
  `;
}

function renderSheet(): string {
  if (!state.sheet && !selectedOperation) return '';
  if (selectedOperation) {
    const op = selectedOperation;
    return `
      <div class="sheet-backdrop" data-action="close-sheet">
        <div class="sheet" data-sheet>
          <h2>${esc(op.category)}</h2>
          <div class="detail-grid">
            <div class="detail-row"><span>Сумма</span><strong>${esc(operationAmount(op))}</strong></div>
            <div class="detail-row"><span>Тип</span><strong>${esc(op.type)}</strong></div>
            <div class="detail-row"><span>Дата</span><strong>${esc(op.op_date)}</strong></div>
            <div class="detail-row"><span>Описание</span><strong>${esc(op.description || '-')}</strong></div>
          </div>
          <form class="form-grid" data-action="edit-operation" data-id="${op.id}">
            <label>Сумма<input class="input" name="amount" inputmode="decimal" value="${esc(op.amount)}" required /></label>
            <label>Категория<input class="input" name="category" value="${esc(op.category)}" maxlength="64" required /></label>
            <label>Описание<textarea class="textarea" name="description" maxlength="200">${esc(op.description || '')}</textarea></label>
            <label>Дата<input class="input" name="op_date" type="date" value="${esc(op.op_date)}" required /></label>
            <div class="actions">
              <button class="button primary" type="submit">Сохранить</button>
              <button class="button danger" type="button" data-action="delete-operation" data-id="${op.id}">Удалить</button>
            </div>
          </form>
        </div>
      </div>
    `;
  }
  const type: OperationType = state.sheet === 'add-income' ? 'Доходы' : 'Расходы';
  const today = new Date().toISOString().slice(0, 10);
  return `
    <div class="sheet-backdrop" data-action="close-sheet">
      <div class="sheet" data-sheet>
        <h2>${type === 'Доходы' ? 'Новый доход' : 'Новый расход'}</h2>
        <form class="form-grid" data-action="create-operation">
          <input type="hidden" name="type" value="${type}" />
          <label>Сумма<input class="input" name="amount" inputmode="decimal" placeholder="0,00" required /></label>
          <label>Категория<input class="input" name="category" maxlength="64" required /></label>
          <label>Описание<textarea class="textarea" name="description" maxlength="200" required></textarea></label>
          <label>Дата<input class="input" name="op_date" type="date" value="${today}" required /></label>
          <button class="button primary" type="submit">Сохранить</button>
        </form>
      </div>
    </div>
  `;
}

function render(): void {
  applyTheme(state.theme);
  const screen = state.loading
    ? '<div class="loading">Загрузка...</div>'
    : state.error
      ? `<div class="error">${esc(state.error)}</div>`
      : state.tab === 'operations'
        ? renderOperations()
        : state.tab === 'analytics'
          ? renderAnalytics()
          : state.tab === 'plans'
            ? renderPlans()
            : state.tab === 'profile'
              ? renderProfile()
              : renderHome();
  app.innerHTML = `<main class="app">${renderTopbar()}${screen}</main>${renderNav()}${renderSheet()}`;
  wireEvents();
  const tg = getTelegramWebApp();
  if (tg?.BackButton) {
    if (state.sheet || selectedOperation) tg.BackButton.show();
    else tg.BackButton.hide();
  }
}

function showToast(text: string): void {
  window.clearTimeout(toastTimer);
  const existing = document.querySelector('.toast');
  existing?.remove();
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = text;
  document.body.appendChild(node);
  toastTimer = window.setTimeout(() => node.remove(), 2600);
}

async function loadScreen(): Promise<void> {
  if (!state.boot) return;
  state.loading = true;
  state.error = undefined;
  render();
  try {
    if (state.tab === 'home') overview = await api.overview(state.workspaceId, state.period);
    if (state.tab === 'operations') operations = await api.operations(state.workspaceId, state.period, 0, state.search);
    if (state.tab === 'analytics') {
      const response = await api.analytics(state.workspaceId, state.period);
      overview = response.overview;
      analytics = { top_expense_categories: response.top_expense_categories };
    }
    if (state.tab === 'plans') plans = await api.plans(state.workspaceId);
    if (state.tab === 'profile') profile = await api.profile();
  } catch (error) {
    state.error = error instanceof Error ? error.message : 'Не удалось загрузить данные.';
  } finally {
    state.loading = false;
    persistState(state);
    render();
  }
}

async function bootstrap(): Promise<void> {
  initTelegramShell();
  const tg = getTelegramWebApp();
  tg?.onEvent('themeChanged', () => applyTheme(state.theme));
  tg?.BackButton?.onClick(() => closeSheet());
  try {
    const boot = await api.bootstrap();
    state.boot = boot;
    state.theme = state.theme || boot.theme;
    state.workspaceId = pickInitialWorkspace(boot.workspaces, state.workspaceId);
    await loadScreen();
  } catch (error) {
    state.loading = false;
    state.error = error instanceof Error ? error.message : 'Mini App недоступен.';
    render();
  }
}

function closeSheet(): void {
  state.sheet = null;
  selectedOperation = null;
  render();
}

async function reloadActive(): Promise<void> {
  await loadScreen();
}

function formPayload(form: HTMLFormElement): OperationPayload {
  const data = new FormData(form);
  return {
    workspace_id: state.workspaceId,
    type: String(data.get('type') || selectedOperation?.type || '') as OperationPayload['type'],
    amount: normalizeMoneyText(String(data.get('amount') || '0')),
    category: String(data.get('category') || '').trim(),
    description: String(data.get('description') || '').trim(),
    op_date: String(data.get('op_date') || '')
  };
}

function wireEvents(): void {
  app.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((button) => {
    button.addEventListener('click', async () => {
      const tab = button.dataset.tab as AppState['tab'];
      state.tab = tab;
      state.sheet = null;
      selectedOperation = null;
      await api.track('mini_app_tab_opened', { tab });
      await loadScreen();
    });
  });

  app.querySelector<HTMLButtonElement>('[data-action="refresh"]')?.addEventListener('click', () => void reloadActive());
  app.querySelector<HTMLSelectElement>('[data-action="workspace"]')?.addEventListener('change', async (event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    state.workspaceId = value === 'all' ? 'all' : value === 'null' || value === '' ? null : Number(value);
    await api.track('mini_app_workspace_changed', { scope: String(state.workspaceId) });
    await loadScreen();
  });
  app.querySelector<HTMLSelectElement>('[data-action="period"]')?.addEventListener('change', async (event) => {
    const period = (event.currentTarget as HTMLSelectElement).value as PeriodKey;
    if (period === 'custom') {
      const today = new Date().toISOString().slice(0, 10);
      state.period = { period, start_date: today, end_date: today };
    } else {
      state.period = { period };
    }
    await api.track('mini_app_period_changed', { period: state.period.period });
    await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="search"]')?.addEventListener('change', async (event) => {
    state.search = (event.currentTarget as HTMLInputElement).value.trim();
    await loadScreen();
  });
  app.querySelector<HTMLButtonElement>('[data-action="load-more"]')?.addEventListener('click', async () => {
    if (!operations) return;
    const next = await api.operations(state.workspaceId, state.period, operations.offset + operations.limit, state.search);
    operations = { ...next, items: [...operations.items, ...next.items] };
    render();
  });
  app.querySelector<HTMLInputElement>('[data-action="start-date"]')?.addEventListener('change', async (event) => {
    state.period = { ...state.period, start_date: (event.currentTarget as HTMLInputElement).value };
    if (state.period.end_date) await loadScreen();
  });
  app.querySelector<HTMLInputElement>('[data-action="end-date"]')?.addEventListener('change', async (event) => {
    state.period = { ...state.period, end_date: (event.currentTarget as HTMLInputElement).value };
    if (state.period.start_date) await loadScreen();
  });

  app.querySelectorAll<HTMLButtonElement>('[data-action="open-add"]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.sheet = button.dataset.kind === 'income' ? 'add-income' : 'add-expense';
      selectedOperation = null;
      await api.track('mini_app_transaction_add_opened', { action: button.dataset.kind || 'expense' });
      render();
    });
  });

  app.querySelectorAll<HTMLButtonElement>('[data-action="operation-detail"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = Number(button.dataset.id);
      selectedOperation = await api.operationDetail(id);
      state.sheet = null;
      render();
    });
  });

  app.querySelector('[data-action="close-sheet"]')?.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).dataset.action === 'close-sheet') closeSheet();
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    await api.createOperation(formPayload(form));
    closeSheet();
    showToast('Операция сохранена');
    await reloadActive();
  });

  app.querySelector<HTMLFormElement>('form[data-action="edit-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    const id = Number(form.dataset.id);
    await api.updateOperation(id, formPayload(form));
    closeSheet();
    showToast('Операция обновлена');
    await reloadActive();
  });

  app.querySelector<HTMLButtonElement>('[data-action="delete-operation"]')?.addEventListener('click', async (event) => {
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id);
    await api.deleteOperation(id);
    closeSheet();
    showToast('Операция удалена');
    await reloadActive();
  });

  app.querySelector('[data-action="theme"]')?.addEventListener('click', async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-theme]');
    if (!button) return;
    const theme = button.dataset.theme as ThemeMode;
    state.theme = theme;
    applyTheme(theme);
    await api.setTheme(theme);
    render();
  });
}

void bootstrap();
