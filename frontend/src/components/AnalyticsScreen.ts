import { formatMoneyString } from '../money';
import type { ActivityCalendar, ChartCategoryItem, GlobalFinancialFilters, RadarMoneyAxis, RadarScale, TimeDynamicsItem } from '../types';
import type { AnalyticsResponse } from '../api';
import { EmptyPanel, SectionHeader, esc } from './ui';

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
    return `
      <div class="metric-line income"><span>Доходы · ${esc(currency)}</span><strong>${formatMoneyString(item.income, currency)}</strong></div>
      <div class="metric-line expense"><span>Расходы · ${esc(currency)}</span><strong>${formatMoneyString(item.expense, currency)}</strong></div>
      <div class="metric-line"><span>Результат · ${esc(currency)}</span><strong>${formatMoneyString(result, currency)}</strong></div>
    `;
  }).join('');
}

function categoryBars(items: ChartCategoryItem[]): string {
  if (!items.length) return EmptyPanel('Нет структуры', 'За этот период не хватает операций для категорий.');
  return items.map((item) => `
    <div class="bar-row">
      <div class="bar-label"><span>${esc(item.category)}</span><strong>${formatMoneyString(item.total, item.currency)}</strong></div>
      <div class="bar-track"><span style="width:${Math.max(4, Math.min(100, item.share))}%"></span></div>
      <small>${item.share}% · ${item.count}</small>
    </div>
  `).join('');
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

function activityIntensity(count: number, max: number): number {
  if (!count || !max) return 0;
  return Math.max(1, Math.min(4, Math.ceil((count / max) * 4)));
}

function activityCalendar(calendar: ActivityCalendar | undefined): string {
  if (!calendar?.days?.length) return EmptyPanel('Нет активности', 'За выбранный период операций не было.');
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'short' });
  const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' });
  const first = new Date(`${calendar.days[0].date}T00:00:00`);
  const firstOffset = Number.isNaN(first.getTime()) ? 0 : (first.getDay() + 6) % 7;
  const padded: Array<{ kind: 'empty' } | { kind: 'day'; date: string; count: number }> = [
    ...Array.from({ length: firstOffset }, () => ({ kind: 'empty' as const })),
    ...calendar.days.map((day) => ({ kind: 'day' as const, date: day.date, count: day.count }))
  ];
  const weekCount = Math.max(1, Math.ceil(padded.length / 7));
  while (padded.length < weekCount * 7) padded.push({ kind: 'empty' });
  const monthLabels = Array.from({ length: weekCount }, (_value, weekIndex) => {
    const weekDays = padded.slice(weekIndex * 7, weekIndex * 7 + 7).filter((item): item is { kind: 'day'; date: string; count: number } => item.kind === 'day');
    const monthStart = weekDays.find((item) => new Date(`${item.date}T00:00:00`).getDate() === 1);
    const labelDay = monthStart || (weekIndex === 0 ? weekDays[0] : undefined);
    if (!labelDay) return '<span></span>';
    const current = new Date(`${labelDay.date}T00:00:00`);
    const previousDay = padded.slice(0, weekIndex * 7).reverse().find((item) => item.kind === 'day');
    const previous = previousDay && previousDay.kind === 'day' ? new Date(`${previousDay.date}T00:00:00`) : null;
    const changedMonth = !previous || previous.getMonth() !== current.getMonth() || previous.getFullYear() !== current.getFullYear() || Boolean(monthStart);
    return `<span>${changedMonth ? esc(monthFmt.format(current).replace('.', '')) : ''}</span>`;
  }).join('');
  const gridCells = padded.map((item) => {
    if (item.kind === 'empty') return '<span class="activity-cell empty" aria-hidden="true"></span>';
    const date = new Date(`${item.date}T00:00:00`);
    const label = `${dateFmt.format(date)} — ${item.count} операций`;
    const row = Number.isNaN(date.getTime()) ? 1 : ((date.getDay() + 6) % 7) + 1;
    return `<span class="activity-cell level-${activityIntensity(item.count, calendar.max_count)}" data-weekday-row="${row}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;
  }).join('');
  return `
    <div class="activity-scroll">
      <div class="activity-layout" style="--activity-weeks:${weekCount}">
        <div class="activity-months" aria-hidden="true">${monthLabels}</div>
        <div class="activity-weekdays" aria-hidden="true">${weekdays.map((day) => `<span>${esc(day)}</span>`).join('')}</div>
        <div class="activity-calendar">${gridCells}</div>
      </div>
    </div>
    <div class="activity-legend"><span>меньше</span><i class="level-0"></i><i class="level-1"></i><i class="level-2"></i><i class="level-3"></i><i class="level-4"></i><span>больше</span></div>
  `;
}

export function AnalyticsScreen(
  analytics: AnalyticsResponse | null,
  filters: { categoryType: 'expense' | 'income'; dynamicsType: 'expense' | 'income' | 'both'; radarType: 'expense' | 'income'; grouping?: 'day' | 'week' | 'month'; categoryCurrency?: string; dynamicsCurrency?: string; radarCurrency?: string },
  globalFilters: GlobalFinancialFilters
): string {
  const currencies = analytics?.available_currencies || Object.keys(analytics?.summary.totals_by_currency || {});
  const mixedCurrency = currencies.length > 1;
  const categoryCurrency = filters.categoryCurrency || currencies[0] || '';
  const dynamicsCurrency = filters.dynamicsCurrency || currencies[0] || '';
  const radarCurrencies = analytics?.radar_available_currencies || currencies;
  const radarCurrency = filters.radarCurrency || radarCurrencies[0] || '';
  const categoryItems = categoryCurrency ? analytics?.category_structure.currency_groups?.[categoryCurrency]?.items || [] : analytics?.category_structure.items || [];
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
      <section class="chart-section">
        ${SectionHeader('Структура категорий', 'Доля категорий внутри выбранной валюты')}
        <div class="chart-controls">
          ${globalType === 'all' ? `<select class="select compact" data-action="chart-filter" data-chart="category" aria-label="Тип категорий">
            <option value="expense" ${filters.categoryType === 'expense' ? 'selected' : ''}>Расходы</option>
            <option value="income" ${filters.categoryType === 'income' ? 'selected' : ''}>Доходы</option>
          </select>` : `<span class="pill">${esc(globalType === 'expense' ? 'Расходы' : 'Доходы')}</span>`}
          ${mixedCurrency ? `<select class="select compact" data-action="chart-currency" data-chart="category" aria-label="Валюта структуры">${currencyOptions(categoryCurrency)}</select>` : ''}
        </div>
        <canvas id="categoryChart" height="180"></canvas>
        <details class="chart-details"><summary aria-expanded="false">Показать категории</summary>${categoryBars(categoryItems)}</details>
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
        ${SectionHeader('Активность', 'Количество операций по дням')}
        ${activityCalendar(analytics?.activity_calendar)}
      </section>
    </section>
  `;
}
