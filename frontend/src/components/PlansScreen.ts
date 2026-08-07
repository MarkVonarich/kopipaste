import { formatMoneyString } from '../money';
import type { BudgetLimit, CategoryBudgetGroup, GeneralSpendingLimit, Goal, GoalPlanPreview, Reminder } from '../types';
import { EmptyPanel, ProgressBar, SectionHeader, esc, icon } from './ui';

type PlansData = {
  all_scope_note?: string | null;
  read_only?: boolean;
  goals: Goal[];
  limits: BudgetLimit[];
  general_limits?: GeneralSpendingLimit[];
  category_budgets?: CategoryBudgetGroup[];
  reminders?: Reminder[];
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

function goalCard(goal: Goal): string {
  return `
    <article class="plan-card goal-card" data-goal-id="${goal.id}">
      <div class="entity-head">
        <div>
          <h3>${esc(goal.title)}</h3>
          <p>${goal.deadline ? `Срок: ${esc(goal.deadline)}` : 'Без срока'}</p>
        </div>
        <span class="pill">${esc(statusLabel(goal.status))}</span>
      </div>
      ${ProgressBar(goal.percent, 'Прогресс цели')}
      <div class="detail-row light"><span>Накоплено</span><strong>${formatMoneyString(goal.current, goal.currency)} / ${formatMoneyString(goal.target, goal.currency)}</strong></div>
      <div class="detail-row light"><span>Следующий шаг</span><strong>${esc(goal.next_action)}</strong></div>
      <div class="detail-row light"><span>План</span><strong>${esc(goal.frequency)}</strong></div>
      <div class="actions">
        <button class="button primary" data-action="goal-contribution" data-id="${goal.id}">Пополнить</button>
        <button class="button secondary" data-action="goal-edit" data-id="${goal.id}">Изменить</button>
      </div>
      <div class="actions">
        <button class="button text" data-action="goal-status" data-id="${goal.id}" data-status="${goal.status === 'paused' ? 'active' : 'paused'}">${goal.status === 'paused' ? 'Возобновить' : 'Пауза'}</button>
        <button class="button danger" data-action="goal-status" data-id="${goal.id}" data-status="archived">Архив</button>
      </div>
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

export function PlansScreen(plans: PlansData | null, mode: 'goals' | 'limits' | 'reminders' = 'goals', canWrite = false): string {
  if (plans?.all_scope_note && mode !== 'reminders') {
    return `<section class="screen">${EmptyPanel('Выберите пространство', plans.all_scope_note)}</section>`;
  }
  return `
    <section class="screen plans-screen">
      <div class="segmented" role="tablist" aria-label="Планы">
        <button data-action="plans-mode" data-mode="goals" class="${mode === 'goals' ? 'active' : ''}" role="tab" aria-selected="${mode === 'goals'}">Цели</button>
        <button data-action="plans-mode" data-mode="limits" class="${mode === 'limits' ? 'active' : ''}" role="tab" aria-selected="${mode === 'limits'}">Лимиты и бюджеты</button>
        <button data-action="plans-mode" data-mode="reminders" class="${mode === 'reminders' ? 'active' : ''}" role="tab" aria-selected="${mode === 'reminders'}">Напоминания</button>
      </div>
      ${mode === 'goals' ? `
        ${SectionHeader('Цели', 'Крупные намерения и план пополнения', canWrite ? `<button class="icon-button" data-action="goal-create" aria-label="Создать цель">${icon('plus')}</button>` : '')}
        ${(plans?.goals || []).map(goalCard).join('') || EmptyPanel('Целей пока нет', 'Создайте цель, чтобы видеть прогресс и следующий шаг.')}
      ` : mode === 'limits' ? `
        ${SectionHeader('Общие лимиты', 'Один предел для расходов периода', canWrite ? `<button class="icon-button" data-action="limit-create" data-scope="all_expenses" aria-label="Создать общий лимит">${icon('plus')}</button>` : '')}
        ${(plans?.general_limits || []).map(limitCard).join('') || EmptyPanel('Общих лимитов пока нет', 'Добавьте общий лимит на неделю или месяц.')}
        ${SectionHeader('Бюджеты категорий', 'Несколько категорий под одним бюджетом', canWrite ? `<button class="icon-button" data-action="category-budget-create" aria-label="Создать бюджет категорий">${icon('plus')}</button>` : '')}
        ${(plans?.category_budgets || []).map(categoryBudgetCard).join('') || EmptyPanel('Бюджетов категорий пока нет', 'Соберите несколько категорий в один бюджет.')}
        ${SectionHeader('Лимиты категорий', 'Ограничение одной категории', canWrite ? `<button class="icon-button" data-action="limit-create" data-scope="category" aria-label="Создать лимит категории">${icon('plus')}</button>` : '')}
        ${(plans?.limits || []).map(limitCard).join('') || EmptyPanel('Лимитов категорий пока нет', 'Добавьте лимит на отдельную категорию.')}
      ` : `
        ${SectionHeader('Личные напоминания', 'Те же напоминания, что и в Telegram-боте', canWrite ? `<button class="button secondary" data-action="reminder-create">+ Новое напоминание</button>` : '')}
        ${(plans?.reminders || []).map(reminderCard).join('') || EmptyPanel('Напоминаний пока нет', 'Создайте оплату, подписку или будущий доход.')}
      `}
    </section>
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

export function GoalForm(goal: Goal | null, saving = false, error = '', preview?: GoalPlanPreview, draft?: Record<string, unknown>): string {
  const value = (key: string, fallback: unknown = '') => String(draft?.[key] ?? fallback ?? '');
  const monthlyDay = scheduleValue(goal, draft, 'day');
  const twiceDays = scheduleValue(goal, draft, 'days').split(',').filter(Boolean);
  const weeklyDay = scheduleValue(goal, draft, 'weekday');
  return `
    <form class="form-grid" data-action="${goal ? 'save-goal' : 'create-goal'}" ${goal ? `data-id="${goal.id}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="80" placeholder="Например, отпуск" value="${esc(value('title', goal?.title || ''))}" required /></label>
      <label class="field">Целевая сумма<input class="input amount-input" name="target_amount" inputmode="decimal" placeholder="0,00" value="${esc(value('target_amount', goal?.target || ''))}" required /></label>
      <label class="field">Уже накоплено<input class="input amount-input" name="current_amount" inputmode="decimal" placeholder="0,00" value="${esc(value('current_amount', goal?.current || ''))}" /></label>
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

export function LimitForm(limit: BudgetLimit | null, categories: Array<{ name: string }>, saving = false, error = '', initialScope: 'all_expenses' | 'category' = 'category'): string {
  const scope = limit?.scope || initialScope;
  return `
    <form class="form-grid" data-action="${limit ? 'save-limit' : 'create-limit'}" ${limit ? `data-id="${esc(limit.id)}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="80" placeholder="Например, Кафе" value="${esc(limit?.title || '')}" /></label>
      <label class="field">Что ограничиваем<select class="select" name="scope">
        <option value="category" ${scope !== 'all_expenses' ? 'selected' : ''}>Категория</option>
        <option value="all_expenses" ${scope === 'all_expenses' ? 'selected' : ''}>Все расходы</option>
      </select></label>
      <label class="field" data-field="limit-category" ${scope === 'all_expenses' ? 'hidden' : ''}>Категория<select class="select" name="category" ${scope === 'all_expenses' ? 'disabled' : ''}>
        ${categories.map((cat) => `<option value="${esc(cat.name)}" ${limit?.category === cat.name ? 'selected' : ''}>${esc(cat.name)}</option>`).join('')}
      </select></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" placeholder="0,00" value="${esc(limit?.amount || '')}" required /></label>
      ${limit?.currency ? `<input type="hidden" name="currency" value="${esc(limit.currency)}" />` : ''}
      <label class="field">Период<select class="select" name="period">
        <option value="month" ${limit?.period !== 'week' ? 'selected' : ''}>Месяц</option>
        <option value="week" ${limit?.period === 'week' ? 'selected' : ''}>Неделя</option>
      </select></label>
      <label class="toggle-row"><input type="checkbox" name="alerts_enabled" ${limit?.alerts_enabled !== false ? 'checked' : ''} /> Оповещения</label>
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

export function CategoryBudgetForm(budget: CategoryBudgetGroup | null, categories: Array<{ name: string }>, saving = false, error = '', availableCurrencies: string[] = ['RUB', 'USD', 'EUR'], defaultCurrency = 'RUB'): string {
  const selected = new Set(budget?.categories || []);
  const selectedCurrency = budget?.currency || defaultCurrency;
  return `
    <form class="form-grid" data-action="${budget ? 'save-category-budget' : 'create-category-budget'}" ${budget ? `data-id="${budget.id}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="120" value="${esc(budget?.title || '')}" required /></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" value="${esc(budget?.amount || '')}" required /></label>
      <label class="field">Валюта<select class="select" name="currency">${currencyOptions(selectedCurrency, availableCurrencies)}</select></label>
      <label class="field">Период<select class="select" name="period">
        <option value="month" ${budget?.period !== 'week' ? 'selected' : ''}>Месяц</option>
        <option value="week" ${budget?.period === 'week' ? 'selected' : ''}>Неделя</option>
      </select></label>
      <fieldset class="chip-select">
        <legend>Категории</legend>
        ${categories.map((cat) => `<label class="chip"><input type="checkbox" name="categories" value="${esc(cat.name)}" ${selected.has(cat.name) ? 'checked' : ''} /> ${esc(cat.name)}</label>`).join('')}
      </fieldset>
      <label class="toggle-row"><input type="checkbox" name="alerts_enabled" ${budget?.alerts_enabled !== false ? 'checked' : ''} /> Оповещения</label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}
