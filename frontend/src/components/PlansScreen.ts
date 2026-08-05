import { formatMoneyString } from '../money';
import type { BudgetLimit, Goal, GoalPlanPreview } from '../types';

type PlansData = {
  all_scope_note?: string | null;
  read_only?: boolean;
  goals: Goal[];
  limits: BudgetLimit[];
};

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

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
    <article class="plan-card" data-goal-id="${goal.id}">
      <div class="section-header">
        <strong>${esc(goal.title)}</strong>
        <span class="pill">${esc(statusLabel(goal.status))}</span>
      </div>
      <div class="progress" aria-label="${goal.percent}%"><span style="width:${Math.min(100, Math.max(0, goal.percent))}%"></span></div>
      <div class="detail-row"><span>Накоплено</span><strong>${formatMoneyString(goal.current, goal.currency)} / ${formatMoneyString(goal.target, goal.currency)}</strong></div>
      <div class="detail-row"><span>Следующий шаг</span><strong>${esc(goal.next_action)}</strong></div>
      <div class="detail-row"><span>План</span><strong>${esc(goal.frequency)}${goal.deadline ? ` · ${esc(goal.deadline)}` : ''}</strong></div>
      <div class="actions">
        <button class="button" data-action="goal-contribution" data-id="${goal.id}">Пополнить</button>
        <button class="button" data-action="goal-edit" data-id="${goal.id}">Изменить</button>
      </div>
      <div class="actions">
        <button class="button" data-action="goal-status" data-id="${goal.id}" data-status="${goal.status === 'paused' ? 'active' : 'paused'}">${goal.status === 'paused' ? 'Возобновить' : 'Пауза'}</button>
        <button class="button danger" data-action="goal-status" data-id="${goal.id}" data-status="archived">Архив</button>
      </div>
    </article>
  `;
}

function limitCard(limit: BudgetLimit): string {
  return `
    <article class="plan-card ${esc(limit.status)}" data-limit-id="${esc(limit.id)}">
      <div class="section-header">
        <strong>${esc(limit.title)}</strong>
        <span class="pill">${esc(statusLabel(limit.status))}</span>
      </div>
      <div class="progress" aria-label="${limit.percent}%"><span style="width:${Math.min(100, Math.max(0, limit.percent))}%"></span></div>
      <div class="detail-row"><span>${limit.scope === 'all_expenses' ? 'Все расходы' : esc(limit.category)}</span><strong>${formatMoneyString(limit.spent, limit.currency)} / ${formatMoneyString(limit.amount, limit.currency)}</strong></div>
      <div class="detail-row"><span>Осталось</span><strong>${formatMoneyString(limit.remaining, limit.currency)}</strong></div>
      <div class="detail-row"><span>Период</span><strong>${limit.period === 'week' ? 'Неделя' : 'Месяц'}</strong></div>
      <div class="actions">
        <button class="button" data-action="limit-edit" data-id="${esc(limit.id)}">Изменить</button>
        <button class="button danger" data-action="limit-delete" data-id="${esc(limit.id)}">Удалить</button>
      </div>
    </article>
  `;
}

export function PlansScreen(plans: PlansData | null, mode: 'goals' | 'limits' = 'goals', canWrite = false): string {
  if (plans?.all_scope_note) {
    return `<section class="screen"><div class="panel">${esc(plans.all_scope_note)}</div></section>`;
  }
  return `
    <section class="screen">
      <div class="segmented">
        <button data-action="plans-mode" data-mode="goals" class="${mode === 'goals' ? 'active' : ''}">Цели</button>
        <button data-action="plans-mode" data-mode="limits" class="${mode === 'limits' ? 'active' : ''}">Бюджеты и лимиты</button>
      </div>
      ${mode === 'goals' ? `
        <div class="section-header">
          <strong>Цели</strong>
          ${canWrite ? '<button class="icon-button" data-action="goal-create" aria-label="Создать цель">+</button>' : ''}
        </div>
        ${(plans?.goals || []).map(goalCard).join('') || '<div class="empty">Целей пока нет.</div>'}
      ` : `
        <div class="section-header">
          <strong>Бюджеты и лимиты</strong>
          ${canWrite ? '<button class="icon-button" data-action="limit-create" aria-label="Создать лимит">+</button>' : ''}
        </div>
        ${(plans?.limits || []).map(limitCard).join('') || '<div class="empty">Лимитов пока нет.</div>'}
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
      <div class="detail-row"><span>Осталось</span><strong>${formatMoneyString(preview.remaining_amount)}</strong></div>
      ${preview.recommended_amount ? `<div class="detail-row"><span>Рекомендуемый взнос</span><strong>${formatMoneyString(preview.recommended_amount)}</strong></div>` : ''}
      ${preview.comfortable_amount ? `<div class="detail-row"><span>Комфортный взнос</span><strong>${formatMoneyString(preview.comfortable_amount)}</strong></div>` : ''}
      <div class="detail-row"><span>Частота</span><strong>${esc(preview.frequency)}</strong></div>
      <div class="detail-row"><span>Расписание</span><strong>${esc(scheduleText)}</strong></div>
      <div class="detail-row"><span>Взносов</span><strong>${preview.required_contributions ?? preview.occurrence_count}</strong></div>
      <div class="detail-row"><span>Следующая дата</span><strong>${esc(preview.next_occurrence || '-')}</strong></div>
      <div class="detail-row"><span>Оценка завершения</span><strong>${esc(preview.projected_completion_date || '-')}</strong></div>
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
      <input class="input" name="title" maxlength="80" placeholder="Название" value="${esc(value('title', goal?.title || ''))}" required />
      <input class="input" name="target_amount" inputmode="decimal" placeholder="Цель" value="${esc(value('target_amount', goal?.target || ''))}" required />
      <input class="input" name="current_amount" inputmode="decimal" placeholder="Уже накоплено" value="${esc(value('current_amount', goal?.current || ''))}" />
      <input class="input" name="deadline" type="date" value="${esc(value('deadline', goal?.deadline || ''))}" />
      <select class="select" name="strategy">
        <option value="deadline" ${value('strategy', goal?.strategy || 'deadline') === 'deadline' ? 'selected' : ''}>К сроку</option>
        <option value="contribution" ${value('strategy', goal?.strategy || '') === 'contribution' ? 'selected' : ''}>Комфортная сумма</option>
        <option value="none" ${value('strategy', goal?.strategy || '') === 'none' ? 'selected' : ''}>Без плана</option>
      </select>
      <select class="select" name="frequency">
        <option value="monthly" ${value('frequency', goal?.frequency || 'none') === 'monthly' ? 'selected' : ''}>Раз в месяц</option>
        <option value="twice_monthly" ${value('frequency', goal?.frequency || 'none') === 'twice_monthly' ? 'selected' : ''}>Два раза в месяц</option>
        <option value="weekly" ${value('frequency', goal?.frequency || 'none') === 'weekly' ? 'selected' : ''}>Раз в неделю</option>
        <option value="none" ${value('frequency', goal?.frequency || 'none') === 'none' ? 'selected' : ''}>Без расписания</option>
      </select>
      <input class="input" name="comfortable_amount" inputmode="decimal" placeholder="Комфортное пополнение" value="${esc(value('comfortable_amount', goal?.comfortable_amount || ''))}" />
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
        <select class="select" name="weekday">
          <option value="" ${weeklyDay === '' ? 'selected' : ''}>Выберите день недели</option>
          ${['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((label, index) => `<option value="${index}" ${weeklyDay === String(index) ? 'selected' : ''}>${label}</option>`).join('')}
        </select>
      </div>
      <label class="toggle-row"><input type="checkbox" name="reminders_enabled" ${draft?.reminders_enabled === true || (!draft && goal?.reminders_enabled) ? 'checked' : ''} /> Напоминания</label>
      ${GoalPreview(preview)}
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button" type="submit" data-submit-mode="preview" ${saving ? 'disabled' : ''}>Предпросмотр</button>
      ${preview ? `<button class="button primary" type="submit" data-submit-mode="confirm" ${saving || !preview.feasible ? 'disabled' : ''}>Подтвердить сохранение</button>` : ''}
    </form>
  `;
}

export function GoalContributionForm(goal: Goal, idempotencyKey: string, saving = false, error = ''): string {
  return `
    <form class="form-grid" data-action="goal-movement" data-id="${goal.id}">
      <input type="hidden" name="idempotency_key" value="${esc(idempotencyKey)}" />
      <select class="select" name="movement_type">
        <option value="contribution">Пополнить</option>
        <option value="withdrawal">Уменьшить</option>
        <option value="adjustment">Изменить прогресс</option>
      </select>
      <input class="input" name="amount" inputmode="decimal" placeholder="Сумма" />
      <input class="input" name="new_balance" inputmode="decimal" placeholder="Новый прогресс для режима изменения" />
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Применить</button>
    </form>
  `;
}

export function LimitForm(limit: BudgetLimit | null, categories: Array<{ name: string }>, saving = false, error = ''): string {
  return `
    <form class="form-grid" data-action="${limit ? 'save-limit' : 'create-limit'}" ${limit ? `data-id="${esc(limit.id)}"` : ''}>
      <input class="input" name="title" maxlength="80" placeholder="Название" value="${esc(limit?.title || '')}" />
      <select class="select" name="scope">
        <option value="category" ${limit?.scope !== 'all_expenses' ? 'selected' : ''}>Категория</option>
        <option value="all_expenses" ${limit?.scope === 'all_expenses' ? 'selected' : ''}>Все расходы</option>
      </select>
      <select class="select" name="category">
        ${categories.map((cat) => `<option value="${esc(cat.name)}" ${limit?.category === cat.name ? 'selected' : ''}>${esc(cat.name)}</option>`).join('')}
      </select>
      <input class="input" name="amount" inputmode="decimal" placeholder="Сумма" value="${esc(limit?.amount || '')}" required />
      <select class="select" name="period">
        <option value="month" ${limit?.period !== 'week' ? 'selected' : ''}>Месяц</option>
        <option value="week" ${limit?.period === 'week' ? 'selected' : ''}>Неделя</option>
      </select>
      <label class="toggle-row"><input type="checkbox" name="alerts_enabled" ${limit?.alerts_enabled !== false ? 'checked' : ''} /> Оповещения</label>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button>
    </form>
  `;
}
