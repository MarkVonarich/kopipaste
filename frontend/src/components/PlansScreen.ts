import { formatMoneyString } from '../money';
import type { BudgetLimit, Goal, GoalPlanPreview } from '../types';
import { EmptyPanel, ProgressBar, SectionHeader, esc, icon } from './ui';

type PlansData = {
  all_scope_note?: string | null;
  read_only?: boolean;
  goals: Goal[];
  limits: BudgetLimit[];
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

function limitCard(limit: BudgetLimit): string {
  const tone = limit.percent >= 100 ? 'danger' : limit.percent >= 90 ? 'danger' : limit.percent >= 80 ? 'warning' : limit.percent >= 50 ? 'accent' : '';
  return `
    <article class="plan-card limit-card ${esc(limit.status)}" data-limit-id="${esc(limit.id)}">
      <div class="entity-head">
        <div>
          <h3>${esc(limit.title)}</h3>
          <p>${limit.period === 'week' ? 'Неделя' : 'Месяц'} · ${limit.scope === 'all_expenses' ? 'Все расходы' : esc(limit.category)}</p>
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

export function PlansScreen(plans: PlansData | null, mode: 'goals' | 'limits' = 'goals', canWrite = false): string {
  if (plans?.all_scope_note) {
    return `<section class="screen">${EmptyPanel('Выберите пространство', plans.all_scope_note)}</section>`;
  }
  return `
    <section class="screen plans-screen">
      <div class="segmented" role="tablist" aria-label="Планы">
        <button data-action="plans-mode" data-mode="goals" class="${mode === 'goals' ? 'active' : ''}" role="tab" aria-selected="${mode === 'goals'}">Цели</button>
        <button data-action="plans-mode" data-mode="limits" class="${mode === 'limits' ? 'active' : ''}" role="tab" aria-selected="${mode === 'limits'}">Лимиты и бюджеты</button>
      </div>
      ${mode === 'goals' ? `
        ${SectionHeader('Цели', 'Крупные намерения и план пополнения', canWrite ? `<button class="icon-button" data-action="goal-create" aria-label="Создать цель">${icon('plus')}</button>` : '')}
        ${(plans?.goals || []).map(goalCard).join('') || EmptyPanel('Целей пока нет', 'Создайте цель, чтобы видеть прогресс и следующий шаг.')}
      ` : `
        ${SectionHeader('Лимиты и бюджеты', 'Контроль расходов без лишней тревожности', canWrite ? `<button class="icon-button" data-action="limit-create" aria-label="Создать лимит">${icon('plus')}</button>` : '')}
        ${(plans?.limits || []).map(limitCard).join('') || EmptyPanel('Лимитов пока нет', 'Добавьте лимит на категорию или все расходы.')}
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

export function LimitForm(limit: BudgetLimit | null, categories: Array<{ name: string }>, saving = false, error = ''): string {
  return `
    <form class="form-grid" data-action="${limit ? 'save-limit' : 'create-limit'}" ${limit ? `data-id="${esc(limit.id)}"` : ''}>
      <label class="field">Название<input class="input" name="title" maxlength="80" placeholder="Например, Кафе" value="${esc(limit?.title || '')}" /></label>
      <label class="field">Что ограничиваем<select class="select" name="scope">
        <option value="category" ${limit?.scope !== 'all_expenses' ? 'selected' : ''}>Категория</option>
        <option value="all_expenses" ${limit?.scope === 'all_expenses' ? 'selected' : ''}>Все расходы</option>
      </select></label>
      <label class="field">Категория<select class="select" name="category">
        ${categories.map((cat) => `<option value="${esc(cat.name)}" ${limit?.category === cat.name ? 'selected' : ''}>${esc(cat.name)}</option>`).join('')}
      </select></label>
      <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" placeholder="0,00" value="${esc(limit?.amount || '')}" required /></label>
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
