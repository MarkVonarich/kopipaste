import { formatMoneyString } from '../money';
import { esc } from './ui';

export type CashflowDraft = { balance: string; reserve: string; horizon: 'month_end' | 'next_income' | 'date'; end_date: string };
export type CashflowResult = {
  currency: string; as_of: string; end_date: string | null; current_balance: string | null;
  protected_reserve: string; goal_reserve: string; planned_expense: string; expected_income: string;
  ordinary_expense: string | null; can_spend: string | null; daily_allowance: string | null; daily_pace: string | null;
  exhaustion_without_income: string | null; missing: string[]; overdue_income_count: number;
  trajectory: Array<{ date: string; without_income: string; with_expected_income: string }>;
  events: Array<{ date: string; amount: string; kind: string; label: string; overdue: boolean }>;
};

export function CashflowForm(currency: string, draft: CashflowDraft, result: CashflowResult | null, saving = false, error = ''): string {
  return `<form class="form-grid" data-action="cashflow-preview">
    <label class="field">Горизонт<select class="select" name="horizon">
      <option value="month_end" ${draft.horizon === 'month_end' ? 'selected' : ''}>До конца месяца</option>
      <option value="next_income" ${draft.horizon === 'next_income' ? 'selected' : ''}>До следующего дохода</option>
      <option value="date" ${draft.horizon === 'date' ? 'selected' : ''}>До указанной даты</option>
    </select></label>
    <label class="field" data-cashflow-date ${draft.horizon === 'date' ? '' : 'hidden'}>До даты<input class="input" type="date" name="end_date" value="${esc(draft.end_date)}" ${draft.horizon === 'date' ? 'required' : ''} /></label>
    <label class="field">Фактический остаток сейчас, ${esc(currency)}<input class="input" name="balance" inputmode="decimal" value="${esc(draft.balance)}" placeholder="Не указан" /></label>
    <p class="caption">Сумма на ваших счетах и наличных в этой валюте, включая резерв. Деньги, которые ещё не поступили, сюда не входят.</p>
    <label class="field">Неприкосновенный резерв, ${esc(currency)}<input class="input" name="reserve" inputmode="decimal" value="${esc(draft.reserve)}" /></label>
    <p class="caption">Уже отложенные сбережения включите в резерв. Будущие взносы в цели будут защищены отдельно.</p>
    ${error ? `<p class="error-text" role="alert">${esc(error)}</p>` : ''}
    <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>${saving ? 'Рассчитываем…' : 'Рассчитать'}</button>
  </form>
  ${result ? CashflowView(result) : ''}`;
}

export function CashflowView(result: CashflowResult): string {
  const money = (amount: string | null) => amount === null ? 'Не определено' : esc(formatMoneyString(amount, result.currency));
  const missing: Record<string, string> = {
    balance: 'Укажите фактический остаток сейчас.',
    next_income_date: 'Нет будущего дохода с датой. Добавьте его в напоминаниях или выберите другой горизонт.',
    spending_history: 'Пока недостаточно истории расходов. Нужны минимум 14 отмеченных дней за последние 60 дней; пропуски не считаются нулевыми расходами.',
  };
  return `<section class="cashflow-result" aria-live="polite">
    <h3>Можно потратить до ${esc(result.end_date || 'следующего дохода')}</h3>
    <strong class="cashflow-amount">${result.can_spend === null ? 'Не хватает данных' : `~${money(result.can_spend)}`}</strong>
    <p class="caption">Оценка по состоянию на ${esc(result.as_of)}, а не гарантия. После новых операций подтвердите актуальный остаток и пересчитайте.</p>
    ${result.missing.map(code => `<p class="caption">${esc(missing[code] || code)}</p>`).join('')}
    <div class="detail-row"><span>Деньги сейчас, подтверждено вами</span><strong>${money(result.current_balance)}</strong></div>
    <div class="detail-row"><span>Ожидаемые доходы, ещё не доступны</span><strong>${money(result.expected_income)}</strong></div>
    <div class="detail-row"><span>Запланированные платежи</span><strong>${money(result.planned_expense)}</strong></div>
    <div class="detail-row"><span>Защищённый резерв</span><strong>${money(result.protected_reserve)}</strong></div>
    <div class="detail-row"><span>Будущие взносы в цели</span><strong>${money(result.goal_reserve)}</strong></div>
    ${result.daily_allowance !== null ? `<div class="detail-row"><span>Ориентир обычных расходов в день</span><strong>${money(result.daily_allowance)}</strong></div>` : ''}
    ${result.daily_pace !== null ? `<div class="detail-row"><span>Текущий средний темп в день</span><strong>${money(result.daily_pace)}</strong></div>` : ''}
    ${result.exhaustion_without_income ? `<p class="error-text">Без новых доходов при этом темпе деньги могут закончиться ${esc(result.exhaustion_without_income)}.</p>` : ''}
    ${result.overdue_income_count ? '<p class="caption">Есть просроченный доход. Он исключён из ожидаемых поступлений до уточнения даты.</p>' : ''}
    <details><summary>Как рассчитано</summary><p>Из подтверждённого остатка вычтены неоплаченные платежи, неприкосновенный резерв, будущие взносы в цели и оценка обычных расходов. Ожидаемые доходы не увеличивают сумму, доступную сейчас.</p><p>Цели защищают деньги, но не создают расход повторно. Сценарий использует ваши напоминания. Переводы между своими счетами не меняют общий остаток. Лимиты категорий продолжают действовать отдельно.</p><div class="detail-row"><span>Обычные расходы до выбранной даты</span><strong>${money(result.ordinary_expense)}</strong></div></details>
    ${result.events.length ? `<details><summary>Платежи, доходы и цели (${result.events.length})</summary>${result.events.map(event => `<div class="detail-row"><span>${esc(event.label)}<small>${esc(event.date)}${event.overdue ? ' · просрочен, резервируется сегодня' : ''} · ${event.kind === 'income' ? 'ожидаемый доход' : event.kind === 'goal' ? 'резерв на цель' : 'платёж'}</small></span><strong>${money(event.amount)}</strong></div>`).join('')}</details>` : ''}
    ${result.trajectory.length ? `<details><summary>Остаток по дням</summary><p class="caption">${result.daily_pace === null ? 'Только известные планы, без обычных расходов.' : 'С учётом среднего темпа обычных расходов.'} Остаток включает защищённые сбережения. Доходы в правом столбце пока не получены.</p><table class="cashflow-table"><thead><tr><th>Дата</th><th>Без новых доходов</th><th>С ожидаемыми доходами</th></tr></thead><tbody>${result.trajectory.map(row => `<tr><td>${esc(row.date)}</td><td>${money(row.without_income)}</td><td>${money(row.with_expected_income)}</td></tr>`).join('')}</tbody></table></details>` : ''}
  </section>`;
}
