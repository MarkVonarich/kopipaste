import './styles.css';
import { api, requestId, type OperationPayload, type OperationsResponse, type Overview, type PlansResponse } from './api';
import { formatMoneyString, normalizeMoneyText } from './money';
import { getTelegramWebApp, initTelegramShell } from './telegram';
import { initialState, persistState, pickInitialWorkspace } from './state';
import type { AppState, CategoryOption, Operation, OperationType, PeriodKey, ThemeMode, Workspace } from './types';
import { AppShell } from './components/AppShell';
import { BottomNavigation } from './components/BottomNavigation';
import { BottomSheet } from './components/BottomSheet';
import { ConfirmDialog } from './components/ConfirmDialog';
import { HomeScreen } from './components/HomeScreen';
import { OperationsScreen } from './components/OperationsScreen';
import { PlansScreen } from './components/PlansScreen';
import { ProfileScreen } from './components/ProfileScreen';
import { LoadingState, ErrorState } from './components/States';
import { TransactionForm } from './components/TransactionForm';

const appRoot = document.querySelector<HTMLDivElement>('#app');
if (!appRoot) throw new Error('Missing app root');
const app: HTMLDivElement = appRoot;

let state: AppState = initialState();
let overview: Overview | null = null;
let operations: OperationsResponse | null = null;
let analytics: { top_expense_categories: Array<{ category: string; total: string; currency: string; count: number }> } | null = null;
let plans: PlansResponse | null = null;
let profile: { theme: ThemeMode; currency: string; timezone: string; version: string; links?: { privacy?: string | null; terms?: string | null } } | null = null;
let selectedOperation: Operation | null = null;
let categoryOptions: CategoryOption[] = [];
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

function moneyFromTotals(type: 'income' | 'expense'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) return formatMoneyString('0.00', state.boot?.user.currency || 'RUB');
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
  return BottomNavigation(state.tab);
}

function renderHome(): string {
  return HomeScreen(overview, overview?.recent_operations || [], state.boot?.user.currency || 'RUB', canWrite());
}

function renderOperations(): string {
  return OperationsScreen(operations, canWrite(), esc(state.search));
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
  return PlansScreen(plans);
}

function renderProfile(): string {
  return ProfileScreen(profile, state.boot?.workspaces || [], state.theme);
}

function renderSheet(): string {
  if (state.confirmDeleteId && selectedOperation) {
    return ConfirmDialog(state.confirmDeleteId, `${selectedOperation.category} · ${operationAmount(selectedOperation)}`);
  }
  if (!state.sheet && !selectedOperation) return '';
  if (state.sheet === 'actions') {
    return BottomSheet('Добавить операцию', `
      <div class="form-grid">
        <button class="button primary" data-action="open-add" data-kind="expense">Расход</button>
        <button class="button" data-action="open-add" data-kind="income">Доход</button>
      </div>
    `);
  }
  if (selectedOperation) {
    const op = selectedOperation;
    return BottomSheet(esc(op.category), `
          <div class="detail-grid">
            <div class="detail-row"><span>Сумма</span><strong>${esc(operationAmount(op))}</strong></div>
            <div class="detail-row"><span>Тип</span><strong>${esc(op.type)}</strong></div>
            <div class="detail-row"><span>Дата</span><strong>${esc(op.op_date)}</strong></div>
            <div class="detail-row"><span>Автор</span><strong>${esc(op.actor_user_id || '-')}</strong></div>
            <div class="detail-row"><span>Пространство</span><strong>${esc(op.workspace_name || 'Личное')}</strong></div>
            <div class="detail-row"><span>Описание</span><strong>${esc(op.description || '-')}</strong></div>
          </div>
          ${TransactionForm(categoryOptions, { action: 'edit-operation', id: op.id, type: op.type, operation: op, saving: state.saving, error: state.saveError })}
          <button class="button danger" type="button" data-action="delete-operation" data-id="${op.id}" ${state.saving ? 'disabled' : ''}>Удалить</button>
    `);
  }
  const type: OperationType = state.sheet === 'add-income' ? 'Доходы' : 'Расходы';
  return BottomSheet(type === 'Доходы' ? 'Новый доход' : 'Новый расход', TransactionForm(categoryOptions, {
    action: 'create-operation',
    type,
    operation: state.formDraft as Operation | undefined,
    saving: state.saving,
    error: state.saveError,
  }));
}

function render(): void {
  applyTheme(state.theme);
  const screen = state.loading
    ? LoadingState()
    : state.error
      ? ErrorState(esc(state.error))
      : state.tab === 'operations'
        ? renderOperations()
        : state.tab === 'analytics'
          ? renderAnalytics()
          : state.tab === 'plans'
            ? renderPlans()
            : state.tab === 'profile'
              ? renderProfile()
              : renderHome();
  app.innerHTML = `${AppShell(state, renderTopbar(), screen)}${renderNav()}${renderSheet()}`;
  wireEvents();
  const tg = getTelegramWebApp();
  if (tg?.BackButton) {
    if (state.confirmDeleteId || state.sheet || selectedOperation) tg.BackButton.show();
    else tg.BackButton.hide();
  }
}

function safeError(error: unknown): string {
  const code = typeof error === 'object' && error && 'code' in error ? String((error as { code?: string }).code) : '';
  return {
    unauthorized: 'Не удалось подтвердить Telegram-вход.',
    workspace_access_denied: 'Нет доступа к этому пространству.',
    workspace_read_only: 'Это пространство доступно только для чтения.',
    concrete_workspace_required: 'Выберите одно пространство.',
    category_not_available: 'Выберите категорию из списка.',
    idempotency_conflict: 'Эта попытка сохранения уже использовалась для другой операции.',
    idempotency_pending: 'Операция уже сохраняется. Подождите немного.',
    rate_limited: 'Слишком много действий подряд. Попробуйте позже.',
    miniapp_not_configured: 'Mini App ещё не настроен на сервере.',
  }[code] || 'Не получилось выполнить действие. Попробуйте ещё раз.';
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
    state.error = safeError(error);
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
    state.error = safeError(error);
    render();
  }
}

function closeSheet(): void {
  if (state.confirmDeleteId) {
    state.confirmDeleteId = undefined;
    render();
    return;
  }
  if (state.dirty && !window.confirm('Закрыть без сохранения?')) return;
  state.sheet = null;
  selectedOperation = null;
  state.saveError = undefined;
  state.saving = false;
  state.dirty = false;
  state.addIdempotencyKey = undefined;
  state.formDraft = undefined;
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
    op_date: String(data.get('op_date') || ''),
    idempotency_key: state.addIdempotencyKey || requestId()
  };
}

async function loadCategoriesFor(type: 'expense' | 'income' | 'Расходы' | 'Доходы'): Promise<void> {
  try {
    const response = await api.categories(state.workspaceId, type);
    categoryOptions = response.items;
  } catch {
    categoryOptions = [];
  }
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

  app.querySelector<HTMLButtonElement>('[data-action="retry"]')?.addEventListener('click', () => void reloadActive());
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
      state.addIdempotencyKey = requestId();
      state.saveError = undefined;
      state.formDraft = undefined;
      state.dirty = false;
      await loadCategoriesFor(button.dataset.kind === 'income' ? 'income' : 'expense');
      await api.track('mini_app_transaction_add_opened', { action: button.dataset.kind || 'expense' });
      render();
    });
  });
  app.querySelector<HTMLButtonElement>('[data-action="open-actions"]')?.addEventListener('click', () => {
    state.sheet = 'actions';
    selectedOperation = null;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="go-operations"]')?.addEventListener('click', async () => {
    state.tab = 'operations';
    await loadScreen();
  });

  app.querySelectorAll<HTMLButtonElement>('[data-action="operation-detail"]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = Number(button.dataset.id);
      selectedOperation = await api.operationDetail(id);
      await loadCategoriesFor(selectedOperation.type);
      state.sheet = null;
      state.saveError = undefined;
      state.dirty = false;
      render();
    });
  });

  app.querySelector('[data-action="close-sheet"]')?.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).dataset.action === 'close-sheet') closeSheet();
  });
  app.querySelector('[data-action="close-confirm"]')?.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).dataset.action === 'close-confirm') closeSheet();
  });
  app.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('form input, form textarea, form select').forEach((input) => {
    input.addEventListener('input', () => {
      state.dirty = true;
    });
    input.addEventListener('change', () => {
      state.dirty = true;
    });
  });

  app.querySelector<HTMLFormElement>('form[data-action="create-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const payload = formPayload(form);
    state.formDraft = {
      amount: payload.amount,
      category: payload.category,
      description: payload.description,
      op_date: payload.op_date,
      type: payload.type === 'income' ? 'Доходы' : payload.type === 'expense' ? 'Расходы' : payload.type,
    };
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.createOperation(payload);
      state.addIdempotencyKey = undefined;
      state.formDraft = undefined;
      state.dirty = false;
      closeSheet();
      showToast('Операция сохранена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLFormElement>('form[data-action="edit-operation"]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving) return;
    const form = event.currentTarget as HTMLFormElement;
    const id = Number(form.dataset.id);
    const payload = formPayload(form);
    if (selectedOperation) {
      selectedOperation = {
        ...selectedOperation,
        amount: payload.amount,
        category: payload.category,
        description: payload.description,
        op_date: payload.op_date,
        type: payload.type === 'income' ? 'Доходы' : payload.type === 'expense' ? 'Расходы' : payload.type,
      };
    }
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.updateOperation(id, payload);
      state.dirty = false;
      closeSheet();
      showToast('Операция обновлена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
  });

  app.querySelector<HTMLButtonElement>('[data-action="delete-operation"]')?.addEventListener('click', async (event) => {
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id);
    state.confirmDeleteId = id;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="cancel-delete"]')?.addEventListener('click', () => {
    state.confirmDeleteId = undefined;
    render();
  });
  app.querySelector<HTMLButtonElement>('[data-action="confirm-delete"]')?.addEventListener('click', async (event) => {
    if (state.saving) return;
    const id = Number((event.currentTarget as HTMLButtonElement).dataset.id);
    state.saving = true;
    state.saveError = undefined;
    render();
    try {
      await api.deleteOperation(id);
      state.dirty = false;
      state.confirmDeleteId = undefined;
      closeSheet();
      showToast('Операция удалена');
      await reloadActive();
    } catch (error) {
      state.saving = false;
      state.saveError = safeError(error);
      render();
    }
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
