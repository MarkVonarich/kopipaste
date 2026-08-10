import { formatMoneyString } from '../money';
import type { ChartCategoryItem, GlobalFinancialFilters, MerchantStructureItem, Operation, RadarMoneyAxis, RadarScale, TimeDynamicsItem } from '../types';
import type { AnalyticsResponse } from '../api';
import { TransactionList } from './TransactionList';
import { ActivityCalendarView } from './ActivityCalendar';
import { EmptyPanel, SectionHeader, esc, icon } from './ui';

function deltaText(metric: { delta: string; pct?: string | null; state: string } | undefined, currency: string): string {
  if (!metric) return 'Нет сравнения';
  const delta = Number(metric.delta || 0);
  if (metric.state === 'zero_baseline') return `было 0 · ${delta > 0 ? '+' : ''}${formatMoneyString(metric.delta, currency)}`;
  if (metric.state === 'empty_previous') return 'прошлый период пуст';
  const sign = delta > 0 ? '+' : '';
  const pct = metric.pct ? ` · ${sign}${String(metric.pct).replace('.', ',')}%` : '';
  return `${sign}${formatMoneyString(metric.delta, currency)}${pct}`;
}

function metricRows(analytics: AnalyticsResponse | null): string {
  const totals = analytics?.summary.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) {
    return `
      <div class="metric-line income"><span>Доходы</span><strong>0 ₽</strong></div>
      <div class="metric-line expense"><span>Расходы</span><strong>0 ₽</strong></div>
      <div class="metric-line"><span>Результат</span><strong>0 ₽</strong></div>
    `;
  }
  return currencies.map((currency) => {
    const item = totals[currency];
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
  return items.map((item) => `
    <button class="bar-row action-row" data-action="analytics-drill" data-kind="category" data-value="${esc(item.category)}" data-currency="${esc(item.currency)}">
      <div class="bar-label"><span>${esc(item.category)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}${item.delta !== undefined ? ` · ${esc(deltaText({ delta: item.delta, pct: null, state: Number(item.previous_total || 0) === 0 ? 'zero_baseline' : 'ok' }, item.currency))}` : ''}</small>
    </button>
  `).join('');
}

function merchantBars(items: MerchantStructureItem[]): string {
  if (!items.length) return EmptyPanel('Нет мерчантов', 'В выбранном периоде нет описаний операций для группировки.');
  return items.map((item) => `
    <button class="bar-row action-row" data-action="analytics-drill" data-kind="merchant" data-value="${esc(item.merchant)}" data-currency="${esc(item.currency)}">
      <div class="bar-label"><span>${esc(item.merchant)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}${item.delta !== undefined ? ` · ${esc(deltaText({ delta: item.delta, pct: null, state: Number(item.previous_total || 0) === 0 ? 'zero_baseline' : 'ok' }, item.currency))}` : ''}</small>
    </button>
  `).join('');
}

function contributionRows(items: ChartCategoryItem[], currency: string): string {
  if (!items.length) return EmptyPanel('Нет изменений', 'Для вклада нужен текущий или предыдущий сопоставимый период.');
  return items.map((item) => {
    const delta = Number(item.delta || 0);
    const width = Math.max(8, Math.min(100, Math.abs(delta)));
    return `
      <button class="contribution-row" data-action="analytics-drill" data-kind="category" data-value="${esc(item.category)}" data-currency="${esc(item.currency || currency)}">
        <span>${esc(item.category)}</span>
        <strong>${delta > 0 ? '+' : ''}${formatMoneyString(item.delta || '0.00', item.currency || currency)}</strong>
        <i class="${delta >= 0 ? 'positive' : 'negative'}" style="width:${width}%"></i>
      </button>
    `;
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
  const amount = detail.kind === 'merchant' ? detail.total : detail.visible_total;
  const merchants = detail.kind === 'category' ? merchantBars(detail.merchant_breakdown?.items || []) : '';
  return `
    <section class="chart-section analytics-detail">
      ${SectionHeader(detail.title, `${detail.operation_count} операций · ${esc(detail.currency)}`, '<button class="button text" data-action="analytics-back" type="button">Назад</button>')}
      <div class="metrics-grid compact">
        <div class="metric-line"><span>Итого</span><strong>${formatMoneyString(amount || '0.00', detail.currency)}</strong></div>
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
    const amount = mode === 'income' ? item.income : mode === 'expense' ? item.expense : `${formatMoneyString(item.income, item.currency)} / ${formatMoneyString(item.expense, item.currency)}`;
    return `<div class="detail-row light"><span>${esc(item.date)}<br><small>${esc(item.currency)}</small></span><strong>${typeof amount === 'string' && amount.includes('/') ? amount : formatMoneyString(String(amount), item.currency)}</strong></div>`;
  }).join('');
}

function compactAmount(value: string | number): string {
  const number = Number(value || 0);
  if (Math.abs(number) >= 1_000_000) return `${String(Math.round(number / 100_000) / 10).replace('.', ',')}м`;
  if (Math.abs(number) >= 1_000) return `${String(Math.round(number / 100) / 10).replace('.', ',')}к`;
  return String(Math.round(number));
}

function wrapLabel(label: string): string[] {
  const words = label.trim().split(/\s+/);
  const lines: string[] = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= 14) current = candidate;
    else {
      if (current) lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines;
}

function radarSvg(axes: RadarMoneyAxis[], scale: RadarScale | undefined, currency: string | null | undefined): string {
  if (axes.length < 2) return '';
  const cx = 170;
  const cy = 150;
  const radius = 88;
  const scaleMax = Math.max(1, Number(scale?.max || 0));
  const ticks = (scale?.ticks || []).map((tick) => Number(tick)).filter((tick) => tick > 0);
  const points = (key: 'current_amount' | 'previous_amount') => axes.map((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const value = Math.max(0, Math.min(scaleMax, Number(axis[key] || 0))) / scaleMax;
    return `${cx + Math.cos(angle) * radius * value},${cy + Math.sin(angle) * radius * value}`;
  }).join(' ');
  const rings = ticks.map((tick) => {
    const r = radius * tick / scaleMax;
    const polygon = axes.map((_axis, index) => {
      const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
      return `${cx + Math.cos(angle) * r},${cy + Math.sin(angle) * r}`;
    }).join(' ');
    return `<polygon class="radar-ring" points="${polygon}" /><text class="radar-tick" x="${cx + 4}" y="${cy - r + 4}">${esc(compactAmount(tick))}</text>`;
  }).join('');
  const spokes = axes.map((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    const lx = cx + Math.cos(angle) * (radius + 58);
    const ly = cy + Math.sin(angle) * (radius + 48);
    const anchor = Math.abs(Math.cos(angle)) < 0.25 ? 'middle' : Math.cos(angle) > 0 ? 'start' : 'end';
    const lines = wrapLabel(axis.category);
    const title = `${axis.category}: текущий период ${axis.current_amount} ${currency || ''}, предыдущий ${axis.previous_amount} ${currency || ''}`;
    return `<g><title>${esc(title)}</title><line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" />` +
      `<text class="radar-label" x="${lx}" y="${ly}" text-anchor="${anchor}">${lines.map((line, lineIndex) => `<tspan x="${lx}" dy="${lineIndex === 0 ? 0 : 13}">${esc(line)}</tspan>`).join('')}</text></g>`;
  }).join('');
  const dots = (key: 'current_amount' | 'previous_amount', klass: string) => axes.map((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const value = Math.max(0, Math.min(scaleMax, Number(axis[key] || 0))) / scaleMax;
    return `<circle class="${klass}" cx="${cx + Math.cos(angle) * radius * value}" cy="${cy + Math.sin(angle) * radius * value}" r="3" />`;
  }).join('');
  return `
    <svg class="radar" viewBox="0 0 340 320" role="img" aria-label="Radar ${esc(currency || '')}">
      ${rings}
      ${spokes}
      <polygon class="radar-prev" points="${points('previous_amount')}" />
      <polygon class="radar-current" points="${points('current_amount')}" />
      ${dots('previous_amount', 'radar-dot-prev')}
      ${dots('current_amount', 'radar-dot-current')}
    </svg>
  `;
}

export function AnalyticsScreen(
  analytics: AnalyticsResponse | null,
  filters: {
    categoryType: 'expense' | 'income';
    dynamicsType: 'expense' | 'income' | 'both';
    radarType: 'expense' | 'income';
    grouping?: 'day' | 'week' | 'month';
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
  const categoryCurrency = filters.categoryCurrency || currencies[0] || '';
  const dynamicsCurrency = filters.dynamicsCurrency || currencies[0] || '';
  const structureMode = filters.structureMode || 'category';
  const radarCurrencies = analytics?.radar_available_currencies || currencies;
  const radarCurrency = filters.radarCurrency || radarCurrencies[0] || '';
  const categoryItems = categoryCurrency ? analytics?.category_structure.currency_groups?.[categoryCurrency]?.items || [] : analytics?.category_structure.items || [];
  const merchantItems = categoryCurrency ? analytics?.merchant_structure?.currency_groups?.[categoryCurrency]?.items || [] : analytics?.merchant_structure?.items || [];
  const contributionGroup = categoryCurrency ? analytics?.change_contribution?.currency_groups?.[categoryCurrency] : undefined;
  const dynamicsItems = dynamicsCurrency ? (analytics?.time_dynamics.items || []).filter((item) => item.currency === dynamicsCurrency) : analytics?.time_dynamics.items || [];
  const globalType = globalFilters.operation_type;
  const dynamicsMode = globalType === 'expense' || globalType === 'income' ? globalType : filters.dynamicsType;
  const grouping = filters.grouping || (analytics?.time_dynamics.grouping as 'day' | 'week' | 'month' | undefined) || 'day';
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
      <div class="insight-block">
        <span class="eyebrow">Ключевой вывод</span>
        <h2>${mixedCurrency ? 'Смотрите каждую валюту отдельно' : 'Картина периода собрана'}</h2>
        <p>${mixedCurrency ? 'КопиPaste не смешивает валюты и не создаёт ложный общий итог.' : 'Доходы, расходы и результат ниже относятся к выбранному пространству и периоду.'}</p>
      </div>
      <div class="metrics-grid">${metricRows(analytics)}</div>
      ${note}
      <div class="search-field analytics-search">
        ${icon('search')}
        <input class="input" type="search" data-action="analytics-search" placeholder="Категория, магазин или операция" value="${esc(filters.search || '')}" aria-label="Поиск в аналитике" />
      </div>
      ${searchResults(analytics)}
      ${detailPanel(analytics)}
      <section class="chart-section">
        ${SectionHeader('Структура', structureMode === 'merchant' ? 'Мерчанты внутри выбранной валюты' : 'Категории внутри выбранной валюты')}
        <div class="chart-controls">
          <button class="segmented-button ${structureMode === 'category' ? 'active' : ''}" data-action="analytics-structure" data-mode="category" type="button">Категории</button>
          <button class="segmented-button ${structureMode === 'merchant' ? 'active' : ''}" data-action="analytics-structure" data-mode="merchant" type="button">Мерчанты</button>
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="category" aria-label="Тип категорий">
            <option value="expense" ${filters.categoryType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.categoryType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
          ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="category" aria-label="Валюта структуры">${currencyOptions(categoryCurrency)}</select>` : ''}
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
        ${contributionRows(contributionGroup?.items || [], categoryCurrency)}
      </section>
      <section class="chart-section">
        ${SectionHeader('Динамика', 'Как менялись доходы и расходы во времени')}
        <div class="chart-controls">
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="dynamics" aria-label="Тип динамики">
            <option value="both" ${filters.dynamicsType === 'both' ? 'selected' : ''}>Доходы и расходы</option>
            <option value="expense" ${filters.dynamicsType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.dynamicsType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
          <select class="select compact" data-action="chart-grouping" aria-label="Группировка">
            <option value="day" ${grouping === 'day' ? 'selected' : ''}>По дням</option>
            <option value="week" ${grouping === 'week' ? 'selected' : ''}>По неделям</option>
            <option value="month" ${grouping === 'month' ? 'selected' : ''}>По месяцам</option>
          </select>
          ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="dynamics" aria-label="Валюта динамики">${currencyOptions(dynamicsCurrency)}</select>` : ''}
        </div>
        <canvas id="dynamicsChart" height="180"></canvas>
        <details class="chart-details"><summary aria-expanded="false">Показать детали</summary>${dynamicsRows(dynamicsItems, dynamicsMode)}</details>
      </section>
      <section class="chart-section">
        ${SectionHeader('Radar', `Абсолютные суммы${analytics?.radar.currency ? ` · ${analytics.radar.currency}` : ''}`)}
        <div class="chart-controls">
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="radar" aria-label="Тип radar">
            <option value="expense" ${filters.radarType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.radarType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
          ${radarCurrencies.length > 1 ? `<select class="select compact" data-action="chart-currency" data-chart="radar" aria-label="Валюта radar">${radarCurrencyOptions(radarCurrency)}</select>` : ''}
        </div>
        ${analytics?.radar.insufficient_data ? EmptyPanel('Недостаточно данных', analytics.radar.explanation) : radarSvg(analytics?.radar.axes || [], analytics?.radar.scale, analytics?.radar.currency)}
        ${analytics?.radar.insufficient_data ? '' : '<div class="radar-legend"><span class="current">Текущий период</span><span class="previous">Прошлый период</span></div>'}
        <p class="caption">${esc(analytics?.radar.explanation || 'Значения нормализованы.')} Сравниваются выбранный и предыдущий периоды.</p>
      </section>
      <section class="chart-section">
        ${SectionHeader('Экспорт', 'XLSX за выбранный период и пространство', '<button class="button secondary" data-action="export-open" type="button">Открыть экспорт</button>')}
        <p class="caption">Файл формируется через существующий безопасный flow и отправляется в Telegram.</p>
      </section>
    </section>
  `;
}

export { ActivityCalendarView };
