import { formatMoneyString, subtractMoneyStrings } from '../money';
import type { Operation } from '../types';
import type { Overview } from '../api';
import { TransactionList } from './TransactionList';

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

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
  return `
    <section class="screen">
      <div class="panel result-card">
        <span class="caption">Доходы − Расходы</span>
        <strong>${esc(resultLines(overview, fallbackCurrency))}</strong>
        ${overview && !overview.aggregation_available ? '<p class="caption">Валюты показаны отдельно без конвертации.</p>' : ''}
      </div>
      <div class="metrics">
        <div class="metric"><span>Доходы</span><strong>${esc(totalLines(overview, 'income', fallbackCurrency))}</strong></div>
        <div class="metric"><span>Расходы</span><strong>${esc(totalLines(overview, 'expense', fallbackCurrency))}</strong></div>
      </div>
      <div class="section-header"><strong>Последние операции</strong><button class="icon-button" data-action="open-actions" ${canWrite ? '' : 'disabled'}>+</button></div>
      ${TransactionList(recent, 'За период операций нет.')}
      <button class="button" data-action="go-operations">Все операции</button>
    </section>
  `;
}
