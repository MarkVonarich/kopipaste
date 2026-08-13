import { formatMoneyString, formatWholeMoneyString } from '../money';
import type { CanSpendResult, SpendableForecast, SpendableSummary } from '../types';
import { esc } from './ui';

export function SpendableCard(summary?: SpendableSummary, feedbackSaving = false): string {
  if (!summary?.available) {
    return `<button class="summary-card spendable-card unavailable" type="button" data-action="spendable-open" disabled>
      <span>Свободно</span>
      <strong>${esc(summary?.title || 'Прогноз пока недоступен')}</strong>
      <small>${esc(summary?.description || 'Обновите экран позже.')}</small>
    </button>`;
  }
  return `<article class="summary-card spendable-card ${esc(summary.risk_state)}" data-testid="home-spendable-card">
    <button class="summary-card-action" type="button" data-action="spendable-open">
      <span>Свободно</span>
      <strong class="home-primary-amount">~${esc(formatWholeMoneyString(summary.amount, summary.currency))}</strong>
      <small>${esc(summary.period_label)}</small>
    </button>
    ${summary.experiment?.enabled ? InlineForecastFeedback(summary.fingerprint, summary.feedback, feedbackSaving) : ''}
  </article>`;
}

export function InlineForecastFeedback(fingerprint: string, feedback?: string | null, saving = false): string {
  return `<div class="inline-feedback" aria-label="Оценка прогноза">
    <span>Полезно?</span>
    <button type="button" data-action="forecast-feedback" data-fingerprint="${esc(fingerprint)}" data-feedback="useful" aria-label="Полезно" ${saving || feedback ? 'disabled' : ''}>👍</button>
    <button type="button" data-action="forecast-feedback" data-fingerprint="${esc(fingerprint)}" data-feedback="not_useful" aria-label="Не полезно" ${saving || feedback ? 'disabled' : ''}>👎</button>
  </div>`;
}

export function ForecastBand(forecast: SpendableForecast): string {
  const points = forecast.trajectory || [];
  if (!points.length) return '<p class="caption">Траектория появится для периода с будущими днями.</p>';
  const values = points.flatMap((point) => [Number(point.expected_expense), Number(point.upper_expense)]);
  const max = Math.max(1, ...values);
  const width = 300;
  const height = 96;
  const coords = (key: 'expected_expense' | 'upper_expense') => points.map((point, index) => {
    const x = points.length === 1 ? 0 : index * width / (points.length - 1);
    const y = height - Number(point[key]) / max * (height - 10);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg class="forecast-band" viewBox="0 0 ${width} ${height}" role="img" aria-label="Прогноз расходов до конца периода">
    <polyline class="forecast-upper" points="${coords('upper_expense')}" />
    <polyline class="forecast-expected" points="${coords('expected_expense')}" />
  </svg>`;
}

export function SpendableDetail(forecast: SpendableForecast, categories: Array<{ name: string }>, saving = false, error = ''): string {
  const categoryOptions = categories.map((item) => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');
  return `<div class="spendable-detail">
    <section class="forecast-breakdown">
      <h3>Почему столько?</h3>
      <div class="detail-row"><span>Текущий итог</span><strong>${esc(formatMoneyString(forecast.current_result, forecast.currency))}</strong></div>
      <div class="detail-row"><span>Будущие обязательные платежи</span><strong>−${esc(formatMoneyString(forecast.known_commitments, forecast.currency))}</strong></div>
      <div class="detail-row"><span>Защищено на цели</span><strong>−${esc(formatMoneyString(forecast.goal_reserve, forecast.currency))}</strong></div>
      <div class="detail-row"><span>Прогноз обычных расходов</span><strong>−${esc(formatMoneyString(forecast.variable_reserve, forecast.currency))}</strong></div>
      <div class="detail-row total"><span>Расчётная свободная сумма</span><strong>~${esc(formatMoneyString(forecast.amount, forecast.currency))}</strong></div>
      ${forecast.general_budget_remaining !== null && forecast.general_budget_remaining !== undefined && forecast.amount === forecast.general_budget_remaining ? '<p class="caption">Ограничено общим бюджетом</p>' : ''}
      ${Number(forecast.expected_income) > 0 ? `<p class="caption">После ожидаемого дохода сценарий может измениться. Этот доход не включён в свободную сумму.</p>` : ''}
    </section>
    <section><h3>Прогноз до конца периода</h3>${ForecastBand(forecast)}<p class="caption">Диапазон: ${esc(formatMoneyString(forecast.lower_spendable, forecast.currency))} — ${esc(formatMoneyString(forecast.upper_spendable, forecast.currency))}</p></section>
    <section><h3>Что сильнее всего влияет</h3><div class="reason-list">${forecast.reasons.map((reason) => `<div class="detail-row"><span>${esc(reason.label)}</span>${reason.amount ? `<strong>${esc(formatMoneyString(reason.amount, forecast.currency))}</strong>` : ''}</div>`).join('')}</div></section>
    <section><h3>Сколько я могу потратить?</h3>
      <form class="form-grid" data-action="can-spend">
        <label class="field">Сумма<input class="input amount-input" name="amount" inputmode="decimal" required /></label>
        <label class="field">Категория, необязательно<select class="select" name="category"><option value="">Без категории</option>${categoryOptions}</select></label>
        ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
        <button class="button primary" type="submit" ${saving ? 'disabled' : ''}>Проверить</button>
      </form>
    </section>
    <section><h3>Как рассчитано</h3>
      <div class="detail-row"><span>Качество</span><strong>${esc(forecast.quality_label)}</strong></div>
      <div class="detail-row"><span>История</span><strong>${esc(forecast.history_periods)} периодов</strong></div>
      <div class="detail-row"><span>Известные платежи</span><strong>${esc(forecast.known_commitment_count)}</strong></div>
      <div class="detail-row"><span>Метод</span><strong>${esc(modelLabel(forecast.model_family))}</strong></div>
    </section>
  </div>`;
}

function modelLabel(family: string): string {
  if (family === 'known_only') return 'По известным платежам';
  if (family === 'robust_remainder') return 'По типичным прошлым периодам';
  if (family === 'seasonal_temporal') return 'С учётом ритма периода';
  return 'Комбинированный прогноз';
}

export function CanSpendView(result: CanSpendResult, currency: string): string {
  const copy = {
    fits: 'Вписывается в текущий прогноз',
    borderline: 'На границе',
    does_not_fit: 'Не вписывается в текущий прогноз',
    insufficient_data: 'По известным данным вписывается, но истории пока мало',
  }[result.verdict];
  return `<div class="can-spend-result ${esc(result.verdict)}">
    <strong>${esc(copy)}</strong>
    <div class="detail-row"><span>До покупки</span><strong>${esc(formatMoneyString(result.amount_before, currency))}</strong></div>
    <div class="detail-row"><span>После покупки</span><strong>${esc(formatMoneyString(result.projected_spendable_after, currency))}</strong></div>
    ${result.category_limit_remaining ? `<div class="detail-row"><span>Лимит категории</span><strong>${esc(formatMoneyString(result.category_limit_remaining, currency))}</strong></div>` : ''}
    ${result.grouped_budget_remaining ? `<div class="detail-row"><span>Общий бюджет категорий</span><strong>${esc(formatMoneyString(result.grouped_budget_remaining, currency))}</strong></div>` : ''}
    <div class="detail-row"><span>Защищено на цели</span><strong>${esc(formatMoneyString(result.goal_reserve, currency))}</strong></div>
  </div>`;
}
