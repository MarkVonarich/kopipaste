import { formatMoneyString } from '../money';
import type { BudgetLimit, CategoryBudgetGroup, CategoryOption, GeneralSpendingLimit, Goal, GoalPlanPreview, PlanningEstimate, Reminder } from '../types';
import { canonicalCategoryKey, dedupePlanningCategories } from '../planningSelection';
import { EmptyPanel, ProgressBar, SectionHeader, esc, icon } from './ui';

type PlansData = {
  all_scope_note?: string | null;
  read_only?: boolean;
  goals: Goal[];
  archived_goals?: Goal[];
  limits: BudgetLimit[];
  general_limits?: GeneralSpendingLimit[];
  category_budgets?: CategoryBudgetGroup[];
  reminders?: Reminder[];
  categories?: CategoryOption[];
  categories_read_only?: boolean;
  category_type?: 'expense' | 'income';
};

function statusLabel(status: string): string {
  return {
    active: 'активна',
    paused: 'приостановлена',
    achieved: 'выполнена',
    archived: 'архивирована',
    normal: 'в норме',
    half_used: 'половина',
    approaching: 'близко',
    critical: 'критично',
    reached: 'исчерпан',
    exceeded: 'превышен',
  }[status] || status;
}

function goalCard(goal: Goal, archived: boolean, canWrite: boolean): string {
  return `
    <article class="plan-card goal-card" data-goal-id="${goal.id}">
      <button class="entity-open" data-action="goal-open" data-id="${goal.id}">
      <div class="entity-head">
        <div>
          <h3>${esc(goal.title)}</h3>
          <p>${goal.deadline ? `Срок: ${esc(goal.deadline)}` : 'Без срока'}</p>
        </div>
        <span class="entity-affordance"><span class="pill">${esc(statusLabel(goal.status))}</span>${icon('chevron')}</span>
      </div>
      </button>
      ${ProgressBar(goal.percent, 'Прогресс цели')}
      <div class="detail-row light"><span>Накоплено</span><strong>${formatMoneyString(goal.current, goal.currency)} / ${formatMoneyString(goal.target, goal.currency)}</strong></div>
      <div class="detail-row light"><span>Следующий шаг</span><strong>${esc(goal.next_action)}</strong></div>
      ${canWrite && !archived ? `<div class="actions">
        <button class="button secondary" data-action="goal-edit" data-id="${goal.id}">Изменить</button>
        <button class="button danger" data-action="goal-status" data-id="${goal.id}" data-status="archived">Архив</button>
      </div>` : ''}
    </article>
  `;
}

function periodLabel(period: string): string {
  return period === 'week' ? 'Неделя' : 'Месяц';
}

function repeatLabel(reminder: Reminder): string {
  if (reminder.repeat_rule === 'weekly') return 'еженедельно';
  if (reminder.repeat_rule === 'monthly') return 'ежемесячно';
  if (reminder.repeat_rule === 'yearly') return 'ежегодно';
  if (reminder.repeat_rule === 'custom_days') return `каждые ${reminder.repeat_interval_days || 1} дн.`;
  return 'не повторять';
}

function limitCard(limit: BudgetLimit | GeneralSpendingLimit): string {
  const tone = limit.percent >= 100 ? 'danger' : limit.percent >= 90 ? 'danger' : limit.percent >= 80 ? 'warning' : limit.percent >= 50 ? 'accent' : '';
  return `
    <article class="plan-card limit-card ${esc(limit.status)}" data-limit-id="${esc(limit.id)}">
      <div class="entity-head">
        <div>
          <h3>${esc(limit.title)}</h3>
          <p>${periodLabel(limit.period)} · ${limit.scope === 'all_expenses' ? 'Все расходы' : esc(limit.category)}</p>
        </div>
        <span class="pill">${esc(statusLabel(limit.status))}</span>
      </div>
      ${ProgressBar(limit.percent, 'Использовано', tone)}
      <div class="detail-row light"><span>Потрачено</span><strong>${formatMoneyString(limit.spent, limit.currency)} / ${formatMoneyString(limit.amount, limit.currency)}</strong></div>
      <div class="detail-row light"><span>Осталось</span><strong>${formatMoneyString(limit.remaining, limit.currency)}</strong></div>
      <div class="actions">
        <button class="button secondary" data-action="limit-edit" data-id="${esc(limit.id)}">Изменить</button>
        ${limit.kind === 'general' ? `<button class="button text" data-action="limit-toggle" data-id="${esc(limit.id)}">${limit.enabled === false ? 'Включить' : 'Выключить'}</button>` : ''}
        <button class="button danger" data-action="limit-delete" data-id="${esc(limit.id)}">Удалить</button>
      </div>
    </article>
  `;
}

function categoryBudgetCard(budget: CategoryBudgetGroup): string {
  const tone = budget.percent >= 100 ? 'danger' : budget.percent >= 80 ? 'warning' : budget.percent >= 50 ? 'accent' : '';
  return `
    <article class="plan-card budget-card ${budget.enabled ? '' : 'inactive'}" data-budget-id="${budget.id}">
      <div class="entity-head">
        <div>
          <h3>${esc(budget.title)}</h3>
          <p>${periodLabel(budget.period)} · ${budget.categories.length} категорий</p>
        </div>
        <span class="pill">${budget.enabled ? esc(statusLabel(budget.status)) : 'выключен'}</span>
      </div>
      ${ProgressBar(budget.percent, 'Использовано', tone)}
      <div class="detail-row light"><span>Потрачено</span><strong>${formatMoneyString(budget.spent, budget.currency)} / ${formatMoneyString(budget.amount, budget.currency)}</strong></div>
      <div class="detail-row light"><span>Осталось</span><strong>${formatMoneyString(budget.remaining, budget.currency)}</strong></div>
      <p class="chip-line">${budget.categories.map((category) => `<span class="chip">${esc(category)}</span>`).join('')}</p>
      <div class="actions">
        <button class="button secondary" data-action="category-budget-edit" data-id="${budget.id}">Изменить</button>
        <button class="button text" data-action="category-budget-toggle" data-id="${budget.id}">${budget.enabled ? 'Выключить' : 'Включить'}</button>
      </div>
      <button class="button danger" data-action="category-budget-delete" data-id="${budget.id}">Удалить</button>
    </article>
  `;
}

function reminderCard(reminder: Reminder): string {
  const status = reminder.status === 'overdue' ? 'ПРОСРОЧЕНО' : reminder.status === 'today' ? 'Сегодня' : reminder.status === 'inactive' ? 'Выключено' : 'Будущее';
  const primary = reminder.status === 'overdue' ? 'Оплачено — записать' : 'Записать операцию';
  return `
    <article class="plan-card reminder-item ${esc(reminder.status)}" data-reminder-id="${reminder.id}">
      <div class="entity-head">
        <div>
          <h3>${esc(reminder.title)}</h3>
          <p>${esc(reminder.category)} · ${esc(reminder.rem_type)}</p>
        </div>
        <span class="pill">${esc(status)}</span>
      </div>
      <div class="detail-row light"><span>Сумма</span><strong>${esc(reminder.amount_text)}</strong></div>
      <div class="detail-row light"><span>Дата</span><strong>${esc(reminder.event_date)}</strong></div>
      <div class="detail-row light"><span>Повтор</span><strong>${esc(repeatLabel(reminder))}</strong></div>
      <div class="detail-row light"><span>Напомнить</span><strong>за ${reminder.notify_days_before} дн.</strong></div>
      ${reminder.next_event_date ? `<div class="detail-row light"><span>Следующая</span><strong>${esc(reminder.next_event_date)}</strong></div>` : ''}
      <div class="actions">
        <button class="button primary" data-action="reminder-record" data-id="${reminder.id}" ${reminder.is_active ? '' : 'disabled'}>${esc(primary)}</button>
        <button class="button secondary" data-action="reminder-open" data-id="${reminder.id}">Открыть</button>
      </div>
    </article>
  `;
}

function categoryRow(category: CategoryOption): string {
  return `
    <button class="settings-row category-row" data-action="category-open" data-token="${esc(category.token || category.normalized_name)}">
      <span><strong>${esc(category.name)}</strong></span>
      <em>${category.protected ? '<small>системная</small>' : ''}${icon('chevron')}</em>
    </button>
  `;
}

export function PlansScreen(plans: PlansData | null, mode: 'goals' | 'limits' | 'reminders' | 'categories' = 'goals', canWrite = false, goalView: 'active' | 'archive' = 'active'): string {
  if (plans?.all_scope_note && mode !== 'reminders' && mode !== 'categories') {
    return `<section class="screen">${EmptyPanel('Выберите пространство', plans.all_scope_note)}</section>`;
  }
  return `
    <section class="screen plans-screen">
      <div class="segmented" role="tablist" aria-label="Планы">
        <button data-action="plans-mode" data-mode="goals" class="${mode === 'goals' ? 'active' : ''}" role="tab" aria-selected="${mode === 'goals'}">Цели</button>
        <button data-action="plans-mode" data-mode="limits" class="${mode === 'limits' ? 'active' : ''}" role="tab" aria-selected="${mode === 'limits'}">Лимиты и бюджеты</button>
        <button data-action="plans-mode" data-mode="reminders" class="${mode === 'reminders' ? 'active' : ''}" role="tab" aria-selected="${mode === 'reminders'}">Напоминания</button>
        <button data-action="plans-mode" data-mode="categories" class="${mode === 'categories' ? 'active' : ''}" role="tab" aria-selected="${mode === 'categories'}">Категории</button>
      </div>
      ${mode === 'goals' ? `
        ${goalView === 'archive'
          ? `${SectionHeader('Архив целей', 'Завершённые планы можно восстановить или удалить навсегда', '<button class="button text" data-action="goal-archive-back">Назад</button>')}
             ${(plans?.archived_goals || []).map((goal) => goalCard(goal, true, canWrite)).join('') || EmptyPanel('Архив пуст', 'Архивированные цели появятся здесь.')}`
          : `${SectionHeader('Цели', 'Копите на крупную покупку или планируйте закрытие кредита или долга. Укажите сумму и срок и следите за прогрессом.', canWrite ? `<button class="icon-button" data-action="goal-create" aria-label="Создать цель">${icon('plus')}</button>` : '')}
             ${(plans?.goals || []).map((goal) => goalCard(goal, false, canWrite)).join('') || EmptyPanel('Целей пока нет', 'Создайте цель, чтобы видеть прогресс и следующий шаг.')}
             <button class="settings-row archive-entry" data-action="goal-archive-open"><span><strong>Архив целей</strong><small>${plans?.archived_goals?.length || 0} целей</small></span><em>${icon('chevron')}</em></button>`}
      ` : mode === 'limits' ? `
        ${SectionHeader('Лимиты', 'Задайте границу расходов для категории или всех трат и следите, насколько быстро она расходуется.')}
        ${SectionHeader('Общие лимиты', 'Один предел для всех расходов периода', canWrite ? `<button class="icon-button" data-action="limit-create" data-scope="all_expenses" aria-label="Создать общий лимит">${icon('plus')}</button>` : '')}
        ${(plans?.general_limits || []).map(limitCard).join('') || EmptyPanel('Общих лимитов пока нет', 'Добавьте общий лимит на неделю или месяц.')}
        ${SectionHeader('Общие бюджеты', 'Объединяйте несколько категорий в один бюджет и контролируйте их общую сумму.', canWrite ? `<button class="icon-button" data-action="category-budget-create" aria-label="Создать бюджет категорий">${icon('plus')}</button>` : '')}
        ${(plans?.category_budgets || []).map(categoryBudgetCard).join('') || EmptyPanel('Бюджетов категорий пока нет', 'Соберите несколько категорий в один бюджет.')}
        ${SectionHeader('Лимиты категорий', 'Ограничение одной категории', canWrite ? `<button class="icon-button" data-action="limit-create" data-scope="category" aria-label="Создать лимит категории">${icon('plus')}</button>` : '')}
        ${(plans?.limits || []).map(limitCard).join('') || EmptyPanel('Лимитов категорий пока нет', 'Добавьте лимит на отдельную категорию.')}
      ` : mode === 'reminders' ? `
        ${SectionHeader('Напоминания', 'Запланируйте будущий расход или доход, чтобы не забыть о платеже и быстро записать его в операции.', canWrite ? `<button class="button secondary" data-action="reminder-create">+ Новое напоминание</button>` : '')}
        ${(plans?.reminders || []).map(reminderCard).join('') || EmptyPanel('Напоминаний пока нет', 'Создайте оплату, подписку или будущий доход.')}
      ` : `
        <div class="segmented compact" role="tablist" aria-label="Тип категорий">
          <button data-action="category-type" data-type="expense" class="${(plans?.category_type || 'expense') === 'expense' ? 'active' : ''}">Расходы</button>
          <button data-action="category-type" data-type="income" class="${plans?.category_type === 'income' ? 'active' : ''}">Доходы</button>
        </div>
        ${SectionHeader('Категории', 'Настройте структуру доходов и расходов: создавайте, переименовывайте и управляйте своими категориями.', canWrite && !plans?.categories_read_only ? `<button class="icon-button" data-action="category-create" aria-label="Создать категорию">${icon('plus')}</button>` : '')}
        <div class="settings-list category-list">${(plans?.categories || []).map(categoryRow).join('')}</div>
        ${(plans?.categories || []).length ? '' : EmptyPanel('Категорий пока нет', 'Добавьте категорию или запишите операцию.')}
      `}
    </section>
  `;
}

export function CategoryDetail(category: CategoryOption, type: 'expense' | 'income', canWrite: boolean): string {
  const refs = category.references;
  const auto = (refs?.aliases || 0) + (refs?.ml_observations || 0);
  return `
    <div class="detail-grid" data-category-token="${esc(category.token || category.normalized_name)}">
      <div class="detail-row"><span>Тип</span><strong>${type === 'income' ? 'Доходы' : 'Расходы'}</strong></div>
      <div class="detail-row"><span>Операции</span><strong>${refs?.operations ?? category.operation_count}</strong></div>
      <div class="detail-row"><span>Лимиты</span><strong>${refs?.category_limits || 0}</strong></div>
      <div class="detail-row"><span>Общие бюджеты</span><strong>${refs?.category_budget_groups || 0}</strong></div>
      <div class="detail-row"><span>Напоминания</span><strong>${refs?.reminders || 0}</strong></div>
      <div class="detail-row"><span>Автокатегоризация</span><strong>${auto}</strong></div>
    </div>
    ${category.protected ? '<p class="caption">Системную категорию нельзя переименовать или удалить.</p>' : ''}
    ${canWrite && !category.protected ? `<div class="actions">
      <button class="button secondary" data-action="category-rename" data-token="${esc(category.token || category.normalized_name)}">Переименовать</button>
      <button class="button danger" data-action="category-delete" data-token="${esc(category.token || category.normalized_name)}">Удалить</button>
    </div>` : ''}
  `;
}

export function GoalDetail(goal: Goal, canWrite: boolean): string {
  const archived = goal.status === 'archived';
  return `
    <div class="detail-grid" data-goal-id="${goal.id}">
      <div class="detail-row"><span>Статус</span><strong>${esc(statusLabel(goal.status))}</strong></div>
      <div class="detail-row"><span>Накоплено</span><strong>${formatMoneyString(goal.current, goal.currency)}</strong></div>
      <div class="detail-row"><span>Цель</span><strong>${formatMoneyString(goal.target, goal.currency)}</strong></div>
      <div class="detail-row"><span>Осталось</span><strong>${formatMoneyString(goal.remaining, goal.currency)}</strong></div>
      <div class="detail-row"><span>Срок</span><strong>${esc(goal.deadline || 'Без срока')}</strong></div>
      <div class="detail-row"><span>Следующий шаг</span><strong>${esc(goal.next_action)}</strong></div>
    </div>
    ${canWrite && archived ? `<div class="actions action-stack">
      <button class="button primary" data-action="goal-status" data-id="${goal.id}" data-status="active">Восстановить</button>
      <button class="button danger" data-action="goal-delete" data-id="${goal.id}">Удалить навсегда</button>
    </div>` : ''}
    ${canWrite && !archived ? `<div class="actions">
      <button class="button primary" data-action="goal-contribution" data-id="${goal.id}">Пополнить</button>
      <button class="button secondary" data-action="goal-edit" data-id="${goal.id}">Изменить</button>
    </div>
    <button class="button danger" data-action="goal-status" data-id="${goal.id}" data-status="archived">В архив</button>` : ''}
  `;
}

export function CategoryForm(category: CategoryOption | null, type: 'expense' | 'income', saving = false, error = ''): string {
  return `
    <form class="form-grid" data-action="${category ? 'save-category' : 'create-category'}" ${category ? `data-token="${esc(category.token || category.normalized_name)}"` : ''}>
      <input type="hidden" name="type" value="${esc(type)}" />
      <label class="field">Название<input class="input" name="name" maxlength="64" value="${esc(category?.name || '')}" required /></label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}

export function CategoryDeleteForm(category: CategoryOption, type: 'expense' | 'income', categories: CategoryOption[], saving = false, error = ''): string {
  const refs = category.references;
  const used = (refs?.total || 0) > 0;
  const replacements = categories.filter((item) => (item.token || item.normalized_name) !== (category.token || category.normalized_name));
  const referenceText = [
    refs?.operations ? `${refs.operations} операций` : '',
    refs?.category_limits ? `${refs.category_limits} лимитов` : '',
    refs?.category_budget_groups ? `${refs.category_budget_groups} общих бюджетов` : '',
    refs?.reminders ? `${refs.reminders} напоминаний` : '',
    refs?.drafts ? `${refs.drafts} черновиков` : '',
  ].filter(Boolean).join(', ');
  return `
    <form class="form-grid" data-action="delete-category" data-token="${esc(category.token || category.normalized_name)}">
      <input type="hidden" name="type" value="${esc(type)}" />
      <p class="caption">${used ? `Категория используется: ${esc(referenceText || 'есть связанные настройки')}. Выберите замену: операции и настройки будут перенесены, а финансовые данные сохранятся.` : 'Категория не используется и может быть удалена.'}</p>
      ${used ? `<label class="field">Перенести в<select class="select" name="transfer_to" required>
        <option value="">Выберите категорию</option>
        ${replacements.map((item) => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('')}
      </select></label>` : ''}
      ${used && !replacements.length ? '<p class="error-text">Сначала создайте другую категорию, чтобы безопасно перенести связанные данные.</p>' : ''}
      <label class="toggle-row"><input type="checkbox" name="confirmed" required /> Подтверждаю удаление категории</label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button danger" type="submit" ${saving || (used && !replacements.length) ? 'disabled' : ''}>Удалить категорию</button>
    </form>
  `;
}

function scheduleValue(goal: Goal | null, draft: Record<string, unknown> | undefined, key: string): string {
  if (draft && draft[key] !== undefined) {
    const value = draft[key];
    return Array.isArray(value) ? value.join(',') : String(value ?? '');
  }
  const value = goal?.schedule_config?.[key];
  return Array.isArray(value) ? value.join(',') : String(value ?? '');
}

function previewReason(reason?: string | null): string {
  return {
    missing_deadline: 'Укажите срок цели.',
    no_occurrences: 'В выбранном расписании нет взносов до срока.',
    invalid_contribution: 'Комфортная сумма должна быть больше нуля.',
    horizon_exceeded: 'Расписание слишком длинное для расчёта.',
    no_schedule: 'Срок будет зависеть от ручных пополнений.',
    no_plan: 'План не настроен.',
  }[String(reason || '')] || String(reason || '');
}

function GoalPreview(preview?: GoalPlanPreview): string {
  if (!preview) return '';
  const schedule = preview.schedule_config || {};
  const scheduleText = preview.frequency === 'monthly'
    ? `День месяца: ${schedule.day ?? '-'}`
    : preview.frequency === 'twice_monthly'
      ? `Дни месяца: ${Array.isArray(schedule.days) ? schedule.days.join(' и ') : '-'}`
      : preview.frequency === 'weekly'
        ? `День недели: ${schedule.weekday ?? '-'}`
        : 'Без расписания';
  return `
    <div class="preview-panel" data-testid="goal-plan-preview">
      <strong>${preview.feasible ? 'Предпросмотр плана' : 'План требует внимания'}</strong>
      <div class="detail-row light"><span>Осталось</span><strong>${formatMoneyString(preview.remaining_amount)}</strong></div>
      ${preview.recommended_amount ? `<div class="detail-row light"><span>Рекомендуемый взнос</span><strong>${formatMoneyString(preview.recommended_amount)}</strong></div>` : ''}
      ${preview.comfortable_amount ? `<div class="detail-row light"><span>Комфортный взнос</span><strong>${formatMoneyString(preview.comfortable_amount)}</strong></div>` : ''}
      <div class="detail-row light"><span>Частота</span><strong>${esc(preview.frequency)}</strong></div>
      <div class="detail-row light"><span>Расписание</span><strong>${esc(scheduleText)}</strong></div>
      <div class="detail-row light"><span>Взносов</span><strong>${preview.required_contributions ?? preview.occurrence_count}</strong></div>
      <div class="detail-row light"><span>Следующая дата</span><strong>${esc(preview.next_occurrence || '-')}</strong></div>
      <div class="detail-row light"><span>Оценка завершения</span><strong>${esc(preview.projected_completion_date || '-')}</strong></div>
      ${!preview.feasible || preview.reason ? `<p class="caption">${esc(previewReason(preview.reason))}</p>` : ''}
    </div>
  `;
}

function confidenceLabel(value: PlanningEstimate['history_confidence']): string {
  if (value === 'good') return 'Хорошая: 4 полных периода';
  if (value === 'limited') return 'Ограниченная: 2–3 полных периода';
  return 'Недостаточно истории';
}

function feasibilityLabel(value: PlanningEstimate['feasibility']): string {
  if (value === 'compatible') return 'Срок совместим с историческим денежным потоком.';
  if (value === 'stretched') return 'Для срока нужен темп выше исторически комфортного.';
  if (value === 'required_pace_unavailable') return 'Выберите срок и расписание для необходимого темпа.';
  return 'Необходимый темп рассчитан, но истории для оценки комфортности недостаточно.';
}

export function PlanningPanel(estimate?: PlanningEstimate, currentAmount = ''): string {
  if (!estimate) return '';
  const currency = estimate.scope.currency;
  const isGoal = estimate.kind === 'goal';
  const difference = !isGoal && estimate.baseline_average && currentAmount
    ? Number(currentAmount.replace(',', '.')) - Number(estimate.baseline_average)
    : 0;
  return `
    <section class="planning-panel" data-testid="smart-planning-result">
      <div class="planning-panel-head"><strong>Расчёт по истории</strong><span class="pill">${esc(confidenceLabel(estimate.history_confidence))}</span></div>
      <details class="planning-history" open>
        <summary>История за ${estimate.valid_periods} из ${estimate.periods_requested} периодов</summary>
        <div class="planning-history-rows">
          ${estimate.history.map((item) => `<div class="planning-history-period" data-testid="planning-history-row"><div class="detail-row light"><span>${esc(item.label)}</span><strong>${formatMoneyString(isGoal ? item.net : item.amount, currency)}</strong></div>${isGoal ? `<p class="caption">Доходы ${formatMoneyString(item.income, currency)} · расходы ${formatMoneyString(item.expense, currency)}</p>` : ''}</div>`).join('') || '<p class="caption">Нет надёжной истории в выбранной валюте.</p>'}
        </div>
      </details>
      ${isGoal ? `
        <div class="planning-result-grid">
          <div><span>Необходимый темп</span><strong>${estimate.required_pace?.monthly_amount ? `${formatMoneyString(estimate.required_pace.monthly_amount, currency)} / месяц` : 'Недоступен'}</strong></div>
          <div><span>Комфортный темп</span><strong>${estimate.comfortable_pace?.monthly_amount != null ? `${formatMoneyString(estimate.comfortable_pace.monthly_amount, currency)} / месяц` : 'Недоступен'}</strong></div>
        </div>
        ${estimate.required_pace?.amount ? `<div class="detail-row light"><span>Необходимо за пополнение</span><strong>${formatMoneyString(estimate.required_pace.amount, currency)}</strong></div>` : ''}
        ${estimate.comfortable_pace?.amount != null ? `<div class="detail-row light"><span>Комфортно за пополнение</span><strong>${formatMoneyString(estimate.comfortable_pace.amount, currency)}</strong></div>` : ''}
        ${estimate.comfortable_pace ? `<div class="detail-row light"><span>Средний денежный поток</span><strong>${estimate.comfortable_pace.average_monthly_net != null ? formatMoneyString(estimate.comfortable_pace.average_monthly_net, currency) : '—'}</strong></div>
        <div class="detail-row light"><span>Другие активные цели</span><strong>${formatMoneyString(estimate.comfortable_pace.other_goal_commitments, currency)} / месяц</strong></div>` : ''}
        <p class="planning-explanation ${estimate.feasibility === 'stretched' ? 'warning' : ''}">${esc(feasibilityLabel(estimate.feasibility))}</p>
        ${estimate.gap ? `<div class="detail-row light"><span>Разница темпа</span><strong>${formatMoneyString(estimate.gap, currency)}</strong></div>` : ''}
        ${estimate.comfortable_completion_date ? `<p class="caption">При комфортном темпе ориентировочная дата завершения: ${esc(estimate.comfortable_completion_date)}.</p>` : ''}
      ` : `
        <div class="detail-row"><span>Среднее</span><strong>${estimate.baseline_average ? formatMoneyString(estimate.baseline_average, currency) : 'Недоступно'}</strong></div>
        <div class="detail-row planning-recommendation"><span>Предложение КопиPaste</span><strong>${estimate.recommendation ? formatMoneyString(estimate.recommendation, currency) : 'Недостаточно истории'}</strong></div>
        ${difference ? `<p class="caption">Введённая сумма на ${formatMoneyString(Math.abs(difference).toFixed(2), currency)} ${difference > 0 ? 'выше' : 'ниже'} среднего.</p>` : ''}
      `}
      ${estimate.conflicts.length ? `<div class="planning-conflicts"><strong>Проверка текущих планов</strong>${estimate.conflicts.map((conflict) => `<div class="planning-conflict ${esc(conflict.severity)}" data-warning-kind="${esc(conflict.kind)}"><span>${esc(conflict.title)}</span><p>${esc(conflict.description)}</p>${conflict.amount && conflict.currency ? `<p>Действующая сумма: <strong>${formatMoneyString(conflict.amount, conflict.currency)}</strong></p>` : ''}${conflict.entity_id ? `<button class="button text" type="button" data-action="planning-open-conflict" data-entity-id="${esc(conflict.entity_id)}">Открыть план</button>` : ''}</div>`).join('')}<p class="caption">Информационные предупреждения не меняют расчёт; сумму можно сохранить после проверки.</p></div>` : '<p class="caption">Пересечений с текущими планами не найдено.</p>'}
      ${estimate.recommendation ? `<button class="button secondary" type="button" data-action="planning-apply" ${estimate.can_apply ? '' : 'disabled'}>${isGoal ? 'Использовать комфортный темп' : `Использовать ${esc(formatMoneyString(estimate.recommendation, currency))}`}</button>` : ''}
      ${estimate.read_only ? '<p class="caption">Пространство доступно только для чтения: расчёт можно посмотреть, но применить нельзя.</p>' : ''}
    </section>
  `;
}

export function GoalForm(goal: Goal | null, saving = false, error = '', preview?: GoalPlanPreview, draft?: Record<string, unknown>, planning?: PlanningEstimate): string {
  const value = (key: string, fallback: unknown = '') => String(draft?.[key] ?? fallback ?? '');
  const monthlyDay = scheduleValue(goal, draft, 'day');
  const twiceDays = scheduleValue(goal, draft, 'days').split(',').filter(Boolean);
  const weeklyDay = scheduleValue(goal, draft, 'weekday');
  return `
    <form class="form-grid" data-action="${goal ? 'save-goal' : 'create-goal'}" ${goal ? `data-id="${goal.id}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="80" placeholder="Например, отпуск" value="${esc(value('title', goal?.title || ''))}" required /></label>
      <label class="field">Целевая сумма<input class="input amount-input" name="target_amount" inputmode="decimal" placeholder="0,00" value="${esc(value('target_amount', goal?.target || ''))}" required /></label>
      ${goal
        ? `<div class="detail-row"><span>Уже накоплено</span><strong>${formatMoneyString(goal.current, goal.currency)}</strong></div><p class="caption">Прогресс меняется через пополнение цели.</p>`
        : `<label class="field">Уже накоплено<input class="input amount-input" name="current_amount" inputmode="decimal" placeholder="0,00" value="${esc(value('current_amount', ''))}" /></label>`}
      <label class="field">Срок<input class="input" name="deadline" type="date" value="${esc(value('deadline', goal?.deadline || ''))}" /></label>
      <label class="field">Стратегия<select class="select" name="strategy">
        <option value="deadline" ${value('strategy', goal?.strategy || 'deadline') === 'deadline' ? 'selected' : ''}>К сроку</option>
        <option value="contribution" ${value('strategy', goal?.strategy || '') === 'contribution' ? 'selected' : ''}>Комфортная сумма</option>
        <option value="none" ${value('strategy', goal?.strategy || '') === 'none' ? 'selected' : ''}>Без плана</option>
      </select></label>
      <label class="field">Частота<select class="select" name="frequency">
        <option value="monthly" ${value('frequency', goal?.frequency || 'none') === 'monthly' ? 'selected' : ''}>Раз в месяц</option>
        <option value="twice_monthly" ${value('frequency', goal?.frequency || 'none') === 'twice_monthly' ? 'selected' : ''}>Два раза в месяц</option>
        <option value="weekly" ${value('frequency', goal?.frequency || 'none') === 'weekly' ? 'selected' : ''}>Раз в неделю</option>
        <option value="none" ${value('frequency', goal?.frequency || 'none') === 'none' ? 'selected' : ''}>Без расписания</option>
      </select></label>
      <label class="field">Комфортное пополнение<input class="input amount-input" name="comfortable_amount" inputmode="decimal" placeholder="Необязательно" value="${esc(value('comfortable_amount', goal?.comfortable_amount || ''))}" /></label>
      <div class="schedule-fields" data-schedule="monthly">
        <label class="field-label">День месяца
          <input class="input" name="day" type="number" min="1" max="28" placeholder="Например, 5" value="${esc(monthlyDay)}" />
        </label>
      </div>
      <div class="schedule-fields" data-schedule="twice_monthly">
        <label class="field-label">Первый день
          <input class="input" name="day_first" type="number" min="1" max="28" placeholder="Например, 5" value="${esc(twiceDays[0] || '')}" />
        </label>
        <label class="field-label">Второй день
          <input class="input" name="day_second" type="number" min="1" max="28" placeholder="Например, 20" value="${esc(twiceDays[1] || '')}" />
        </label>
      </div>
      <div class="schedule-fields" data-schedule="weekly">
        <label class="field">День недели<select class="select" name="weekday">
          <option value="" ${weeklyDay === '' ? 'selected' : ''}>Выберите день недели</option>
          ${['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((label, index) => `<option value="${index}" ${weeklyDay === String(index) ? 'selected' : ''}>${label}</option>`).join('')}
        </select></label>
      </div>
      <label class="toggle-row"><input type="checkbox" name="reminders_enabled" ${draft?.reminders_enabled === true || (!draft && goal?.reminders_enabled) ? 'checked' : ''} /> Напоминания</label>
      <button class="button secondary" type="button" data-action="planning-calculate" data-kind="goal" ${saving ? 'disabled' : ''}>Рассчитать по истории</button>
      ${PlanningPanel(planning, value('comfortable_amount', goal?.comfortable_amount || ''))}
      ${GoalPreview(preview)}
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button secondary" type="submit" data-submit-mode="preview" ${saving ? 'disabled' : ''}>Предпросмотр</button>
      ${preview ? `<button class="button primary" type="submit" data-submit-mode="confirm" ${saving || !preview.feasible ? 'disabled' : ''}>Подтвердить сохранение</button>` : ''}
    </form>
  `;
}

export function GoalContributionForm(goal: Goal, idempotencyKey: string, saving = false, error = ''): string {
  return `
    <form class="form-grid" data-action="goal-movement" data-id="${goal.id}">
      <input type="hidden" name="idempotency_key" value="${esc(idempotencyKey)}" />
      <label class="field">Действие<select class="select" name="movement_type">
        <option value="contribution">Пополнить</option>
        <option value="withdrawal">Уменьшить</option>
        <option value="adjustment">Изменить прогресс</option>
      </select></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" placeholder="0,00" /></label>
      <label class="field">Новый прогресс<input class="input amount-input" name="new_balance" inputmode="decimal" placeholder="Только для режима изменения" /></label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Применить</button>
    </form>
  `;
}

function currencyOptions(selected?: string | null, available: string[] = ['RUB', 'USD', 'EUR']): string {
  const codes = available.length ? available : ['RUB', 'USD', 'EUR'];
  return codes.map((code) => `<option value="${esc(code)}" ${code === selected ? 'selected' : ''}>${esc(code)}</option>`).join('');
}

export function LimitForm(limit: BudgetLimit | null, categories: Array<{ name: string }>, saving = false, error = '', initialScope: 'all_expenses' | 'category' = 'category', initialCategory = '', initialCurrency = '', planning?: PlanningEstimate, draft?: Record<string, unknown>): string {
  const value = (key: string, fallback: unknown = '') => String(draft?.[key] ?? fallback ?? '');
  const scope = value('scope', limit?.scope || initialScope) as 'all_expenses' | 'category';
  return `
    <form class="form-grid" data-action="${limit ? 'save-limit' : 'create-limit'}" ${limit ? `data-id="${esc(limit.id)}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="80" placeholder="Например, Кафе" value="${esc(value('title', limit?.title || ''))}" /></label>
      <label class="field">Что ограничиваем<select class="select" name="scope">
        <option value="category" ${scope !== 'all_expenses' ? 'selected' : ''}>Категория</option>
        <option value="all_expenses" ${scope === 'all_expenses' ? 'selected' : ''}>Все расходы</option>
      </select></label>
      <label class="field" data-field="limit-category" ${scope === 'all_expenses' ? 'hidden' : ''}>Категория<select class="select" name="category" ${scope === 'all_expenses' ? 'disabled' : ''}>
        ${categories.map((cat) => `<option value="${esc(cat.name)}" ${value('category', limit?.category || initialCategory) === cat.name ? 'selected' : ''}>${esc(cat.name)}</option>`).join('')}
      </select></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" placeholder="0,00" value="${esc(value('amount', limit?.amount || ''))}" required /></label>
      ${initialCurrency && !limit
        ? `<label class="field">Валюта<input class="input" name="currency" value="${esc(initialCurrency)}" readonly /></label>`
        : limit?.currency ? `<input type="hidden" name="currency" value="${esc(limit.currency)}" />` : ''}
      <label class="field">Период<select class="select" name="period">
        <option value="month" ${value('period', limit?.period || 'month') !== 'week' ? 'selected' : ''}>Месяц</option>
        <option value="week" ${value('period', limit?.period || 'month') === 'week' ? 'selected' : ''}>Неделя</option>
      </select></label>
      <label class="toggle-row"><input type="checkbox" name="alerts_enabled" ${limit?.alerts_enabled !== false ? 'checked' : ''} /> Оповещения</label>
      <button class="button secondary" type="button" data-action="planning-calculate" data-kind="${scope === 'all_expenses' ? 'general_limit' : 'category_limit'}" ${saving ? 'disabled' : ''}>Рассчитать по истории</button>
      ${PlanningPanel(planning, value('amount', limit?.amount || ''))}
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}

export function ReminderForm(reminder: Reminder | null, categories: Array<{ name: string }>, saving = false, error = '', draft?: Record<string, unknown>): string {
  const value = (key: string, fallback: unknown = '') => String(draft?.[key] ?? fallback ?? '');
  const repeat = value('repeat_rule', reminder?.repeat_rule || 'none');
  const remType = value('rem_type', reminder?.rem_type === 'Доходы' ? 'income' : 'expense');
  const selectedCategory = value('category', reminder?.category || '');
  return `
    <form class="form-grid" data-action="${reminder ? 'save-reminder' : 'create-reminder'}" ${reminder ? `data-id="${reminder.id}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="120" value="${esc(value('title', reminder?.title || ''))}" required /></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" value="${esc(value('amount', reminder?.amount || ''))}" required /></label>
      ${reminder?.currency ? `<input type="hidden" name="currency" value="${esc(reminder.currency)}" />` : ''}
      <label class="field">Категория<select class="select" name="category">
        ${categories.map((cat) => `<option value="${esc(cat.name)}" ${selectedCategory === cat.name ? 'selected' : ''}>${esc(cat.name)}</option>`).join('')}
      </select></label>
      <label class="field">Тип<select class="select" name="rem_type">
        <option value="expense" ${remType !== 'income' ? 'selected' : ''}>Расход</option>
        <option value="income" ${remType === 'income' ? 'selected' : ''}>Доход</option>
      </select></label>
      <label class="field">Дата<input class="input" name="event_date" type="date" value="${esc(value('event_date', reminder?.event_date || ''))}" required /></label>
      <label class="field">Повтор<select class="select" name="repeat_rule">
        <option value="none" ${repeat === 'none' ? 'selected' : ''}>Не повторять</option>
        <option value="weekly" ${repeat === 'weekly' ? 'selected' : ''}>Еженедельно</option>
        <option value="monthly" ${repeat === 'monthly' ? 'selected' : ''}>Ежемесячно</option>
        <option value="yearly" ${repeat === 'yearly' ? 'selected' : ''}>Ежегодно</option>
        <option value="custom_days" ${repeat === 'custom_days' ? 'selected' : ''}>Каждые N дней</option>
      </select></label>
      <label class="field">Интервал дней<input class="input" name="repeat_interval_days" type="number" min="1" max="3650" value="${esc(value('repeat_interval_days', reminder?.repeat_interval_days || ''))}" /></label>
      <label class="field">Напомнить заранее<input class="input" name="notify_days_before" type="number" min="0" max="30" value="${esc(value('notify_days_before', reminder?.notify_days_before ?? 1))}" /></label>
      <label class="toggle-row"><input type="checkbox" name="is_active" ${draft?.is_active === false ? '' : reminder?.is_active !== false ? 'checked' : ''} /> Активно</label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}

export function CategoryBudgetForm(budget: CategoryBudgetGroup | null, categories: Array<{ name: string; normalized_name?: string }>, saving = false, error = '', availableCurrencies: string[] = ['RUB', 'USD', 'EUR'], defaultCurrency = 'RUB', planning?: PlanningEstimate, draft?: Record<string, unknown>): string {
  const value = (key: string, fallback: unknown = '') => String(draft?.[key] ?? fallback ?? '');
  const selectedNames = dedupePlanningCategories(Array.isArray(draft?.categories) ? draft.categories.map(String) : budget?.categories || []);
  const selectedKeys = new Set(selectedNames.map(canonicalCategoryKey));
  const selectedCurrency = value('currency', budget?.currency || defaultCurrency);
  return `
    <form class="form-grid" data-action="${budget ? 'save-category-budget' : 'create-category-budget'}" ${budget ? `data-id="${budget.id}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="120" value="${esc(value('title', budget?.title || ''))}" required /></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" value="${esc(value('amount', budget?.amount || ''))}" required /></label>
      <label class="field">Валюта<select class="select" name="currency">${currencyOptions(selectedCurrency, availableCurrencies)}</select></label>
      <label class="field">Период<select class="select" name="period">
        <option value="month" ${value('period', budget?.period || 'month') !== 'week' ? 'selected' : ''}>Месяц</option>
        <option value="week" ${value('period', budget?.period || 'month') === 'week' ? 'selected' : ''}>Неделя</option>
      </select></label>
      <fieldset class="planning-category-picker">
        <legend>Доступные категории</legend>
        <p class="caption">Перетащите за ручку или нажмите, чтобы добавить.</p>
        <div class="planning-category-source">
          ${categories.map((cat) => {
            const selected = selectedKeys.has(canonicalCategoryKey(cat.normalized_name || cat.name));
            return `<button class="planning-category-chip ${selected ? 'selected' : ''}" type="button" data-action="planning-category-toggle" data-category="${esc(cat.name)}" aria-pressed="${selected}"><span class="planning-drag-handle" data-planning-drag="${esc(cat.name)}" title="Перетащить">${icon('grip')}</span><span>${esc(cat.name)}</span></button>`;
          }).join('')}
        </div>
        <div class="planning-drop-zone ${selectedNames.length ? 'has-items' : ''}" data-planning-drop-zone>
          <strong>В расчёте</strong>
          <div>${selectedNames.map((name) => `<button class="planning-selected-chip" type="button" data-action="planning-category-toggle" data-category="${esc(name)}"><span>${esc(name)}</span><span aria-hidden="true">×</span></button><input type="hidden" name="categories" value="${esc(name)}" />`).join('') || '<p class="caption">Добавьте хотя бы одну категорию.</p>'}</div>
        </div>
      </fieldset>
      <label class="toggle-row"><input type="checkbox" name="alerts_enabled" ${budget?.alerts_enabled !== false ? 'checked' : ''} /> Оповещения</label>
      <button class="button secondary" type="button" data-action="planning-calculate" data-kind="category_budget" ${saving || !selectedNames.length ? 'disabled' : ''}>Рассчитать</button>
      ${PlanningPanel(planning, value('amount', budget?.amount || ''))}
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}
