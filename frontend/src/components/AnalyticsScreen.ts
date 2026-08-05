import { formatMoneyString } from '../money';
import type { ChartCategoryItem, TimeDynamicsItem, RadarAxis } from '../types';
import type { AnalyticsResponse } from '../api';

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function metricRows(analytics: AnalyticsResponse | null): string {
  const totals = analytics?.summary.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) {
    return `
      <div class="metric"><span>Доходы</span><strong>0 ₽</strong></div>
      <div class="metric"><span>Расходы</span><strong>0 ₽</strong></div>
      <div class="metric"><span>Результат</span><strong>0 ₽</strong></div>
    `;
  }
  return currencies.map((currency) => {
    const item = totals[currency];
    const result = analytics?.summary.result_by_currency[currency] || '0.00';
    return `
      <div class="metric"><span>Доходы · ${esc(currency)}</span><strong>${formatMoneyString(item.income, currency)}</strong></div>
      <div class="metric"><span>Расходы · ${esc(currency)}</span><strong>${formatMoneyString(item.expense, currency)}</strong></div>
      <div class="metric"><span>Результат · ${esc(currency)}</span><strong>${formatMoneyString(result, currency)}</strong></div>
    `;
  }).join('');
}

function categoryBars(items: ChartCategoryItem[]): string {
  if (!items.length) return '<p class="caption">Нет данных за период.</p>';
  return items.map((item) => `
    <div class="bar-row">
      <div class="bar-label"><span>${esc(item.category)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}</small>
    </div>
  `).join('');
}

function dynamicsRows(items: TimeDynamicsItem[], mode: string): string {
  if (!items.length) return '<p class="caption">Нет динамики за период.</p>';
  return items.slice(-8).map((item) => {
    const amount = mode === 'income' ? item.income : mode === 'expense' ? item.expense : `${formatMoneyString(item.income, item.currency)} / ${formatMoneyString(item.expense, item.currency)}`;
    return `<div class="detail-row"><span>${esc(item.date)}<br><small>${esc(item.currency)}</small></span><strong>${typeof amount === 'string' && amount.includes('/') ? amount : formatMoneyString(String(amount), item.currency)}</strong></div>`;
  }).join('');
}

function radarSvg(axes: RadarAxis[]): string {
  if (axes.length < 2) return '';
  const cx = 90;
  const cy = 90;
  const radius = 70;
  const points = (key: 'current' | 'previous') => axes.map((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const value = Math.max(0, Math.min(100, axis[key])) / 100;
    return `${cx + Math.cos(angle) * radius * value},${cy + Math.sin(angle) * radius * value}`;
  }).join(' ');
  const spokes = axes.map((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    const lx = cx + Math.cos(angle) * (radius + 16);
    const ly = cy + Math.sin(angle) * (radius + 16);
    return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" /><text x="${lx}" y="${ly}">${esc(axis.category.slice(0, 10))}</text>`;
  }).join('');
  return `
    <svg class="radar" viewBox="0 0 180 180" role="img" aria-label="Radar">
      <circle cx="${cx}" cy="${cy}" r="35" />
      <circle cx="${cx}" cy="${cy}" r="${radius}" />
      ${spokes}
      <polygon class="radar-prev" points="${points('previous')}" />
      <polygon class="radar-current" points="${points('current')}" />
    </svg>
  `;
}

export function AnalyticsScreen(
  analytics: AnalyticsResponse | null,
  filters: { categoryType: 'expense' | 'income'; dynamicsType: 'expense' | 'income' | 'both'; radarType: 'expense' | 'income'; categoryCurrency?: string; dynamicsCurrency?: string; radarCurrency?: string }
): string {
  const currencies = analytics?.available_currencies || Object.keys(analytics?.summary.totals_by_currency || {});
  const mixedCurrency = currencies.length > 1;
  const categoryCurrency = filters.categoryCurrency || currencies[0] || '';
  const dynamicsCurrency = filters.dynamicsCurrency || currencies[0] || '';
  const radarCurrencies = analytics?.radar_available_currencies || currencies;
  const radarCurrency = filters.radarCurrency || radarCurrencies[0] || '';
  const categoryItems = categoryCurrency ? analytics?.category_structure.currency_groups?.[categoryCurrency]?.items || [] : analytics?.category_structure.items || [];
  const dynamicsItems = dynamicsCurrency ? (analytics?.time_dynamics.items || []).filter((item) => item.currency === dynamicsCurrency) : analytics?.time_dynamics.items || [];
  const note = mixedCurrency
    ? '<p class="caption">Валюты показаны отдельно. Автоматическая конвертация не выполняется.</p>'
    : '';
  const currencyOptions = (selected: string) => currencies
    .map((currency) => `<option value="${esc(currency)}" ${currency === selected ? 'selected' : ''}>${esc(currency)}</option>`)
    .join('');
  const radarCurrencyOptions = (selected: string) => radarCurrencies
    .map((currency) => `<option value="${esc(currency)}" ${currency === selected ? 'selected' : ''}>${esc(currency)}</option>`)
    .join('');
  return `
    <section class="screen analytics-screen">
      <div class="metrics triple">${metricRows(analytics)}</div>
      ${note}
      <div class="panel chart-panel">
        <div class="section-header">
          <strong>Структура категорий</strong>
          <select class="select compact" data-action="chart-filter" data-chart="category">
            <option value="expense" ${filters.categoryType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.categoryType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>
          ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="category">${currencyOptions(categoryCurrency)}</select>` : ''}
        </div>
        <canvas id="categoryChart" height="180"></canvas>
        ${categoryBars(categoryItems)}
      </div>
      <div class="panel chart-panel">
        <div class="section-header">
          <strong>Динамика</strong>
          <select class="select compact" data-action="chart-filter" data-chart="dynamics">
            <option value="both" ${filters.dynamicsType === 'both' ? 'selected' : ''}>Доходы и расходы</option>
            <option value="expense" ${filters.dynamicsType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.dynamicsType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>
          ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="dynamics">${currencyOptions(dynamicsCurrency)}</select>` : ''}
        </div>
        <canvas id="dynamicsChart" height="180"></canvas>
        <p class="caption">Группировка: ${esc(analytics?.time_dynamics.grouping || 'day')}</p>
        ${dynamicsRows(dynamicsItems, filters.dynamicsType)}
      </div>
      <div class="panel chart-panel">
        <div class="section-header">
          <strong>Radar</strong>
          <select class="select compact" data-action="chart-filter" data-chart="radar">
            <option value="expense" ${filters.radarType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.radarType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>
          ${radarCurrencies.length > 1 ? `<select class="select compact" data-action="chart-currency" data-chart="radar">${radarCurrencyOptions(radarCurrency)}</select>` : ''}
        </div>
        ${analytics?.radar.insufficient_data ? `<p class="caption">Недостаточно данных для сравнения структуры. ${esc(analytics.radar.explanation)}</p>` : radarSvg(analytics?.radar.axes || [])}
        <p class="caption">${esc(analytics?.radar.explanation || 'Значения нормализованы.')} Сравниваются выбранный и предыдущий периоды.</p>
      </div>
    </section>
  `;
}
