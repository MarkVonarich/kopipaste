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
  const challenge = overview?.challenge;
  const focus = overview?.focus;
  const insight = overview?.insight;
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
      <div class="home-columns" data-testid="income-expense-columns">
        <div class="home-column income" data-testid="income-column">
          <div class="metric-line income">
            <span>Доходы</span>
            <strong>${esc(totalLines(overview, 'income', fallbackCurrency))}</strong>
          </div>
          <button class="button secondary" data-action="open-add" data-kind="income" ${canWrite ? '' : 'disabled'}>${icon('income')}Добавить доход</button>
        </div>
        <div class="home-column expense" data-testid="expense-column">
          <div class="metric-line expense">
            <span>Расходы</span>
            <strong>${esc(totalLines(overview, 'expense', fallbackCurrency))}</strong>
          </div>
          <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite ? '' : 'disabled'}>${icon('expense')}Добавить расход</button>
        </div>
      </div>
      <div class="smart-home-grid" data-testid="smart-home-grid">
        <button class="smart-card" data-action="home-challenge" type="button" ${challenge ? '' : 'disabled'}>
          <span>Челлендж дня</span>
          <strong>${esc(challenge?.completed ? 'Готово' : challenge?.title || 'Нет задания')}</strong>
          <small>${esc(challenge ? `${challenge.progress}/${challenge.target} · ${challenge.description}` : 'Появится после загрузки')}</small>
        </button>
        <button class="smart-card" data-action="home-focus" data-mode="${esc(focus?.target_mode || 'goals')}" type="button">
          <span>Фокус</span>
          <strong>${esc(focus?.title || 'Фокус свободен')}</strong>
          <small>${esc(focus?.description || 'Цели и лимиты появятся здесь')}</small>
        </button>
        <button class="smart-card ${esc(insight?.tone || 'neutral')}" data-action="home-insight" type="button">
          <span>Инсайт периода</span>
          <strong>${esc(insight?.title || overview?.info?.text || 'Период')}</strong>
          <small>${esc(insight?.text || overview?.info?.text || 'Данные обновятся после операций')}</small>
        </button>
      </div>
      ${SectionHeader(
        'Последние операции',
        'Самое свежее за выбранный период',
        `<button class="icon-button" data-action="open-actions" aria-label="Добавить операцию" ${canWrite ? '' : 'disabled'}>${icon('plus')}</button>`
      )}
      ${recent.length ? TransactionList(recent.slice(0, 3), 'За период операций нет.') : EmptyPanel('Операций пока нет', 'Добавьте первый расход или доход, чтобы увидеть историю здесь.', emptyAction)}
      <button class="button text" data-action="go-operations">Все операции</button>
    </section>
  `;
}
