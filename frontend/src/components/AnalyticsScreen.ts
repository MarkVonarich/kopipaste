import { formatMoneyString } from '../money';
import type { ChartCategoryItem, GlobalFinancialFilters, MerchantStructureItem, Operation, TimeDynamicsItem } from '../types';
import type { AnalyticsResponse } from '../api';
import { TransactionList } from './TransactionList';
import { ActivityCalendarView } from './ActivityCalendar';
import { EmptyPanel, SectionHeader, esc, icon } from './ui';

export function deltaText(metric: { delta: string; pct?: string | null; state: string } | undefined, currency: string): string {
  if (!metric) return 'Нет сравнения';
  const delta = Number(metric.delta || 0);
  if (metric.state === 'zero_baseline') return `было 0 · ${delta > 0 ? '+' : ''}${formatMoneyString(metric.delta, currency)}`;
  if (metric.state === 'empty_previous') return 'прошлый период пуст';
  if (metric.state === 'sign_change') return `смена знака · ${delta > 0 ? '+' : ''}${formatMoneyString(metric.delta, currency)}`;
  const sign = delta > 0 ? '+' : '';
  const pctValue = Number(metric.pct || 0);
  const pct = metric.pct ? ` · ${pctValue > 0 ? '+' : ''}${String(metric.pct).replace('.', ',')}%` : '';
  return `${sign}${formatMoneyString(metric.delta, currency)}${pct}`;
}

function metricRows(analytics: AnalyticsResponse | null, selectedCurrency?: string): string {
  const totals = analytics?.summary.totals_by_currency || {};
  const currencies = selectedCurrency ? [selectedCurrency] : Object.keys(totals);
  if (!currencies.length) {
    return `
      <div class="metric-line income"><span>Доходы</span><strong>0 ₽</strong></div>
      <div class="metric-line expense"><span>Расходы</span><strong>0 ₽</strong></div>
      <div class="metric-line"><span>Результат</span><strong>0 ₽</strong></div>
    `;
  }
  return currencies.map((currency) => {
    const item = totals[currency] || { income: '0.00', expense: '0.00', count: 0 };
    const result = analytics?.summary.result_by_currency[currency] || '0.00';
    const metrics = analytics?.overview_metrics?.[currency];
    return `
      <div class="metric-line income"><span>Доходы · ${esc(currency)}<small>${esc(deltaText(metrics?.income, currency))}</small></span><strong>${formatMoneyString(item.income, currency)}</strong></div>
      <div class="metric-line expense"><span>Расходы · ${esc(currency)}<small>${esc(deltaText(metrics?.expense, currency))}</small></span><strong>${formatMoneyString(item.expense, currency)}</strong></div>
      <div class="metric-line"><span>Финрезультат · ${esc(currency)}<small>${esc(deltaText(metrics?.result, currency))}</small></span><strong>${formatMoneyString(result, currency)}</strong></div>
    `;
  }).join('');
}

function categoryBars(items: ChartCategoryItem[]): string {
  if (!items.length) return EmptyPanel('Нет структуры', 'За этот период не хватает операций для категорий.');
  return items.map((item) => {
    const content = `
      <div class="bar-label"><span>${esc(item.category)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}${item.delta !== undefined ? ` · ${esc(deltaText({ delta: item.delta, pct: null, state: Number(item.previous_total || 0) === 0 ? 'zero_baseline' : 'ok' }, item.currency))}` : ''}</small>
    `;
    if (item.drillable === false || item.synthetic || item.fallback) {
      return `<div class="bar-row">${content}</div>`;
    }
    return `<button class="bar-row action-row" data-action="analytics-drill" data-kind="category" data-value="${esc(item.category)}" data-currency="${esc(item.currency)}">${content}</button>`;
  }).join('');
}

function merchantBars(items: MerchantStructureItem[]): string {
  if (!items.length) return EmptyPanel('Нет мерчантов', 'В выбранном периоде нет описаний операций для группировки.');
  return items.map((item) => {
    const content = `
      <div class="bar-label"><span>${esc(item.merchant)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}${item.delta !== undefined ? ` · ${esc(deltaText({ delta: item.delta, pct: null, state: Number(item.previous_total || 0) === 0 ? 'zero_baseline' : 'ok' }, item.currency))}` : ''}</small>
    `;
    if (item.drillable === false || item.synthetic || item.fallback) {
      return `<div class="bar-row">${content}</div>`;
    }
    return `<button class="bar-row action-row" data-action="analytics-drill" data-kind="merchant" data-value="${esc(item.merchant)}" data-currency="${esc(item.currency)}">${content}</button>`;
  }).join('');
}

export function contributionRows(items: ChartCategoryItem[], currency: string): string {
  if (!items.length) return EmptyPanel('Нет изменений', 'Для вклада нужен текущий или предыдущий сопоставимый период.');
  const maxAbsDelta = Math.max(...items.map((item) => Math.abs(Number(item.delta || 0))), 0);
  return items.map((item) => {
    const delta = Number(item.delta || 0);
    const width = maxAbsDelta > 0 && delta !== 0 ? Math.max(8, Math.min(100, Math.abs(delta) / maxAbsDelta * 100)) : 0;
    const content = `
        <span>${esc(item.category)}</span>
        <strong>${delta > 0 ? '+' : ''}${formatMoneyString(item.delta || '0.00', item.currency || currency)}</strong>
        <i class="${delta >= 0 ? 'positive' : 'negative'}" style="width:${width}%"></i>
    `;
    if (item.drillable === false || item.synthetic || item.fallback) {
      return `<div class="contribution-row">${content}</div>`;
    }
    return `<button class="contribution-row" data-action="analytics-drill" data-kind="category" data-value="${esc(item.category)}" data-currency="${esc(item.currency || currency)}">${content}</button>`;
  }).join('');
}

function searchResults(analytics: AnalyticsResponse | null): string {
  const search = analytics?.search;
  if (!search?.query) return '';
  if (!search.items.length) return EmptyPanel('Ничего не найдено', 'Поиск учитывает текущие фильтры, период, пространство и валюту.');
  return `
    <div class="search-results">
      ${search.items.map((item) => item.kind === 'operation'
        ? `<button class="detail-row light" data-action="operation-detail" data-id="${item.operation_id}"><span>${esc(item.title)}<br><small>${esc(item.subtitle)}</small></span><strong>${formatMoneyString(item.amount, item.currency)}</strong></button>`
        : `<button class="detail-row light" data-action="analytics-drill" data-kind="${item.kind}" data-value="${esc(item.title)}" data-currency="${esc(item.currency)}"><span>${esc(item.title)}<br><small>${esc(item.subtitle)}</small></span><strong>${formatMoneyString(item.amount, item.currency)}</strong></button>`
      ).join('')}
    </div>
  `;
}

function detailPanel(analytics: AnalyticsResponse | null): string {
  const detail = analytics?.selected_detail;
  if (!detail) return '';
  const amount = detail.total || detail.visible_total;
  const merchants = detail.kind === 'category' ? merchantBars(detail.merchant_breakdown?.items || []) : '';
  const comparison = detail.delta !== undefined
    ? `<div class="metric-line"><span>К прошлому периоду</span><strong>${esc(deltaText({ delta: detail.delta, pct: detail.pct ?? null, state: detail.state || 'ok' }, detail.currency))}</strong></div>`
    : '';
  return `
    <section class="chart-section analytics-detail">
      ${SectionHeader(detail.title, `${detail.operation_count} операций · ${esc(detail.currency)}`, '<button class="button text" data-action="analytics-back" type="button">Назад</button>')}
      <div class="metrics-grid compact">
        <div class="metric-line"><span>Итого</span><strong>${formatMoneyString(amount || '0.00', detail.currency)}</strong></div>
        ${comparison}
        ${detail.average_check ? `<div class="metric-line"><span>Средний чек</span><strong>${formatMoneyString(detail.average_check, detail.currency)}</strong></div>` : ''}
      </div>
      ${detail.kind === 'category' ? `<div class="detail-stack">${merchants}</div>` : ''}
      ${TransactionList((detail.operations || []) as Operation[], 'Операций в этом срезе нет.')}
      <button class="button secondary" data-action="analytics-open-operations" type="button">Все операции с этим фильтром</button>
    </section>
  `;
}

function dynamicsRows(items: TimeDynamicsItem[], mode: string): string {
  if (!items.length) return EmptyPanel('Нет динамики', 'Данные появятся после операций в выбранном периоде.');
  return items.slice(-8).map((item) => {
    const amount = mode === 'income' ? item.income : mode === 'expense' ? item.expense : mode === 'result' ? item.result : `${formatMoneyString(item.income, item.currency)} / ${formatMoneyString(item.expense, item.currency)}`;
    return `<div class="detail-row light"><span>${esc(item.date)}<br><small>${esc(item.currency)}</small></span><strong>${typeof amount === 'string' && amount.includes('/') ? amount : formatMoneyString(String(amount), item.currency)}</strong></div>`;
  }).join('');
}

export function AnalyticsScreen(
  analytics: AnalyticsResponse | null,
  filters: {
    categoryType: 'expense' | 'income';
    dynamicsType: 'expense' | 'income' | 'result' | 'both';
    radarType: 'expense' | 'income';
    grouping?: 'day' | 'week' | 'month';
    analyticsCurrency?: string;
    categoryCurrency?: string;
    dynamicsCurrency?: string;
    radarCurrency?: string;
    structureMode?: 'category' | 'merchant';
    search?: string;
    detailKind?: 'category' | 'merchant';
    detailValue?: string;
    detailCurrency?: string;
    detailOperationType?: 'expense' | 'income';
  },
  globalFilters: GlobalFinancialFilters
): string {
  const currencies = analytics?.available_currencies || Object.keys(analytics?.summary.totals_by_currency || {});
  const mixedCurrency = currencies.length > 1;
  const selectedCurrency = filters.analyticsCurrency || filters.categoryCurrency || filters.dynamicsCurrency || filters.radarCurrency || analytics?.selected_currency || currencies[0] || '';
  const structureMode = filters.structureMode || 'category';
  const categoryItems = selectedCurrency ? analytics?.category_structure.currency_groups?.[selectedCurrency]?.items || [] : analytics?.category_structure.items || [];
  const merchantItems = selectedCurrency ? analytics?.merchant_structure?.currency_groups?.[selectedCurrency]?.items || [] : analytics?.merchant_structure?.items || [];
  const contributionGroup = selectedCurrency ? analytics?.change_contribution?.currency_groups?.[selectedCurrency] : undefined;
  const dynamicsItems = selectedCurrency ? (analytics?.time_dynamics.items || []).filter((item) => item.currency === selectedCurrency) : analytics?.time_dynamics.items || [];
  const globalType = globalFilters.operation_type;
  const dynamicsMode = globalType === 'expense' || globalType === 'income' ? globalType : filters.dynamicsType;
  const grouping = filters.grouping || (analytics?.time_dynamics.grouping as 'day' | 'week' | 'month' | undefined) || 'day';
  const note = mixedCurrency
    ? '<p class="caption">Валюты показаны отдельно. Автоматическая конвертация не выполняется.</p>'
    : '';
  const currencyOptions = (selected: string) => currencies
    .map((currency) => `<option value="${esc(currency)}" ${currency === selected ? 'selected' : ''}>${esc(currency)}</option>`)
    .join('');
  return `
    <section class="screen analytics-screen">
      <div class="insight-block">
        <span class="eyebrow">Ключевой вывод</span>
        <h2>${mixedCurrency ? 'Смотрите каждую валюту отдельно' : 'Картина периода собрана'}</h2>
        <p>${mixedCurrency ? 'КопиPaste не смешивает валюты и не создаёт ложный общий итог.' : 'Доходы, расходы и результат ниже относятся к выбранному пространству и периоду.'}</p>
        ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="analytics" aria-label="Валюта аналитики">${currencyOptions(selectedCurrency)}</select>` : ''}
      </div>
      <div class="metrics-grid">${metricRows(analytics, selectedCurrency)}</div>
      ${note}
      <div class="search-field analytics-search">
        ${icon('search')}
        <input class="input" type="search" data-action="analytics-search" placeholder="Категория, магазин или операция" value="${esc(filters.search || '')}" aria-label="Поиск в аналитике" />
      </div>
      ${searchResults(analytics)}
      <section class="chart-section">
        ${SectionHeader('Динамика', 'Когда менялись деньги')}
        <div class="chart-controls">
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="dynamics" aria-label="Тип динамики">
            <option value="expense" ${filters.dynamicsType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.dynamicsType === 'income' ? 'selected' : ''}>Доходы</option>
            <option value="result" ${filters.dynamicsType === 'result' ? 'selected' : ''}>Финрезультат</option>
            <option value="both" ${filters.dynamicsType === 'both' ? 'selected' : ''}>Доходы и расходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
          <select class="select compact" data-action="chart-grouping" aria-label="Группировка">
            <option value="day" ${grouping === 'day' ? 'selected' : ''}>По дням</option>
            <option value="week" ${grouping === 'week' ? 'selected' : ''}>По неделям</option>
            <option value="month" ${grouping === 'month' ? 'selected' : ''}>По месяцам</option>
          </select>
        </div>
        <canvas id="dynamicsChart" height="180"></canvas>
        <details class="chart-details"><summary aria-expanded="false">Показать детали</summary>${dynamicsRows(dynamicsItems, dynamicsMode)}</details>
      </section>
      <section class="chart-section">
        ${SectionHeader('Структура', structureMode === 'merchant' ? 'Мерчанты внутри выбранной валюты' : 'Категории внутри выбранной валюты')}
        <div class="chart-controls">
          <button class="segmented-button ${structureMode === 'category' ? 'active' : ''}" data-action="analytics-structure" data-mode="category" type="button">Категории</button>
          <button class="segmented-button ${structureMode === 'merchant' ? 'active' : ''}" data-action="analytics-structure" data-mode="merchant" type="button">Мерчанты</button>
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="category" aria-label="Тип категорий">
            <option value="expense" ${filters.categoryType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.categoryType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
        </div>
        <canvas id="categoryChart" height="180"></canvas>
        <details class="chart-details" open><summary aria-expanded="true">${structureMode === 'merchant' ? 'Показать мерчантов' : 'Показать категории'}</summary>${structureMode === 'merchant' ? merchantBars(merchantItems) : categoryBars(categoryItems)}</details>
      </section>
      <section class="chart-section">
        ${SectionHeader('Что изменилось', analytics?.previous_period ? `${esc(analytics.previous_period.start_date)} - ${esc(analytics.previous_period.end_date)}` : 'Вклад категорий в изменение')}
        <div class="detail-row light">
          <span>Общее изменение</span>
          <strong>${contributionGroup ? `${Number(contributionGroup.total_delta) > 0 ? '+' : ''}${formatMoneyString(contributionGroup.total_delta, contributionGroup.currency)}` : 'нет данных'}</strong>
        </div>
        ${contributionRows(contributionGroup?.items || [], selectedCurrency)}
      </section>
      ${detailPanel(analytics)}
      <section class="chart-section">
        ${SectionHeader('Экспорт', 'XLSX за выбранный период и пространство', '<button class="button secondary" data-action="export-open" type="button">Открыть экспорт</button>')}
        <p class="caption">Файл формируется через существующий безопасный flow и отправляется в Telegram.</p>
      </section>
    </section>
  `;
}

export { ActivityCalendarView };
