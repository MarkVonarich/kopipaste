import { formatMoneyString, subtractMoneyStrings } from '../money';
import type { Operation } from '../types';
import type { Overview } from '../api';
import { TransactionList } from './TransactionList';
import { EmptyPanel, SectionHeader, esc, icon } from './ui';

function resultLines(overview: Overview | null, fallbackCurrency = 'RUB'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) return formatMoneyString('0.00', fallbackCurrency);
  return currencies.map((currency) => {
    const result = subtractMoneyStrings(totals[currency].income, totals[currency].expense);
    const sign = result.startsWith('-') ? '' : '+';
    return `${sign}${formatMoneyString(result, currency)}`;
  }).join(' · ');
}

function totalLines(overview: Overview | null, type: 'income' | 'expense', fallbackCurrency = 'RUB'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) return formatMoneyString('0.00', fallbackCurrency);
  return currencies.map((currency) => formatMoneyString(totals[currency][type], currency)).join(' · ');
}

export function HomeScreen(overview: Overview | null, recent: Operation[], fallbackCurrency: string, canWrite: boolean): string {
  const period = overview?.period ? `${overview.period.start_date} — ${overview.period.end_date}` : '';
  const emptyAction = canWrite ? `<button class="button primary" data-action="open-add" data-kind="expense">${icon('expense')}Добавить первую операцию</button>` : '';
  return `
    <section class="screen home-screen">
      <div class="hero-metric" data-testid="hero-financial-result" aria-label="Доходы − Расходы">
        <span class="eyebrow">Финансовый результат</span>
        <strong>${esc(resultLines(overview, fallbackCurrency))}</strong>
        ${period ? `<p>${esc(period)}</p>` : ''}
        <div class="currency-lines" aria-label="Финансовый результат по валютам">
          ${Object.keys(overview?.totals_by_currency || {}).map((currency) => {
            const totals = overview?.totals_by_currency[currency];
            const result = totals ? subtractMoneyStrings(totals.income, totals.expense) : '0.00';
            return `<span>${esc(currency)} · ${esc(formatMoneyString(result, currency))}</span>`;
          }).join('') || `<span>${esc(formatMoneyString('0.00', fallbackCurrency))}</span>`}
        </div>
        ${overview && !overview.aggregation_available ? '<p class="caption">Валюты показаны отдельно. Разные валюты не складываются.</p>' : ''}
      </div>
      <div class="metric-pair" data-testid="income-expense-pair">
        <div class="metric-line income">
          <span>Доходы</span>
          <strong>${esc(totalLines(overview, 'income', fallbackCurrency))}</strong>
        </div>
        <div class="metric-line expense">
          <span>Расходы</span>
          <strong>${esc(totalLines(overview, 'expense', fallbackCurrency))}</strong>
        </div>
      </div>
      <div class="quick-actions" data-testid="quick-actions">
        <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite ? '' : 'disabled'}>${icon('expense')}Добавить расход</button>
        <button class="button secondary" data-action="open-add" data-kind="income" ${canWrite ? '' : 'disabled'}>${icon('income')}Добавить доход</button>
      </div>
      ${SectionHeader(
        'Последние операции',
        'Самое свежее за выбранный период',
        `<button class="icon-button" data-action="open-actions" aria-label="Добавить операцию" ${canWrite ? '' : 'disabled'}>${icon('plus')}</button>`
      )}
      ${recent.length ? TransactionList(recent, 'За период операций нет.') : EmptyPanel('Операций пока нет', 'Добавьте первый расход или доход, чтобы увидеть историю здесь.', emptyAction)}
      <button class="button text" data-action="go-operations">Все операции</button>
    </section>
  `;
}
