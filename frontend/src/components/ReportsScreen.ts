import { formatMoneyString } from '../money';
import type { FinancialReport, ReportDimensionItem, ReportKind, ReportOperationScope } from '../types';
import { deltaText } from './AnalyticsScreen';
import { EmptyPanel, SectionHeader, esc } from './ui';

const REPORT_LABELS: Record<ReportKind, string> = {
  selected: 'Выбранный период',
  completed_week: 'Неделя',
  completed_month: 'Месяц',
};

function periodLabel(start: string, end: string): string {
  const render = (value: string) => {
    const [year, month, day] = value.split('-');
    return year && month && day ? `${day}.${month}.${year}` : value;
  };
  return `${render(start)} - ${render(end)}`;
}

function scopeLabel(report: FinancialReport): string {
  const type = report.filters.operation_type === 'expense'
    ? 'Расходы'
    : report.filters.operation_type === 'income'
      ? 'Доходы'
      : 'Все операции';
  const category = report.filters.category === 'all' ? '' : ` · ${report.filters.category}`;
  return `${type}${category}`;
}

function scopeData(scope: ReportOperationScope | null | undefined): string {
  return esc(JSON.stringify(scope || {}));
}

function dimensionRows(items: ReportDimensionItem[], dimension: 'category' | 'merchant'): string {
  if (!items.length) {
    return dimension === 'category'
      ? EmptyPanel('Нет структуры расходов', 'В этом срезе нет расходов для группировки по категориям.')
      : EmptyPanel('Нет магазинов', 'Для группировки нужны описания расходных операций.');
  }
  return items.map((item) => {
    const label = dimension === 'category' ? item.category : item.merchant;
    const average = dimension === 'merchant' && item.average_check
      ? ` · средний чек ${formatMoneyString(item.average_check, item.currency)}`
      : '';
    const content = `
      <span>${esc(label || '')}<small>${item.share}% · ${item.count} операций${average}</small></span>
      <strong>${formatMoneyString(item.total, item.currency)}</strong>
    `;
    if (!item.drillable || !item.operation_scope) {
      return `<div class="detail-row light report-dimension-row">${content}</div>`;
    }
    return `<button class="detail-row light report-dimension-row" type="button" data-action="report-drill" data-kind="${dimension}" data-scope="${scopeData(item.operation_scope)}">${content}</button>`;
  }).join('');
}

function observations(report: FinancialReport): string {
  if (!report.observations.length) {
    return EmptyPanel('Без заметных изменений', 'Для выводов нужен сопоставимый текущий или предыдущий период.');
  }
  return report.observations.map((item) => {
    const delta = item.delta === undefined || item.delta === null
      ? ''
      : `<strong>${Number(item.delta) > 0 ? '+' : ''}${formatMoneyString(item.delta, item.currency)}</strong>`;
    const content = `<span>${esc(item.title)}<small>${esc(item.description)}</small></span>${delta}`;
    if (!item.drilldown) return `<div class="detail-row light report-observation">${content}</div>`;
    return `<button class="detail-row light report-observation" type="button" data-action="report-drill" data-kind="observation" data-scope="${scopeData(item.drilldown)}">${content}</button>`;
  }).join('');
}

export function ReportsScreen(report: FinancialReport | null, requestedKind: ReportKind = 'selected'): string {
  const currencies = report?.available_currencies || [];
  const currency = report?.selected_currency || '';
  const kind = report?.kind || requestedKind;
  const reportButtons = (Object.keys(REPORT_LABELS) as ReportKind[])
    .map((item) => `<button type="button" data-action="report-kind" data-kind="${item}" class="${item === kind ? 'active' : ''}">${REPORT_LABELS[item]}</button>`)
    .join('');
  if (!report) {
    return `<section class="screen reports-screen"><div class="segmented report-kind-selector">${reportButtons}</div>${EmptyPanel('Отчёт недоступен', 'Обновите экран и попробуйте ещё раз.')}</section>`;
  }
  const currencyControl = currencies.length > 1
    ? `<label class="report-currency"><span>Валюта</span><select class="select compact" data-action="report-currency" aria-label="Валюта отчёта">${currencies.map((item) => `<option value="${esc(item)}" ${item === currency ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></label>`
    : `<span class="pill">${esc(currency)}</span>`;
  const partialNote = report.data_state === 'income_only'
    ? 'В периоде есть только доходы.'
    : report.data_state === 'expense_only'
      ? 'В периоде есть только расходы.'
      : '';
  const summary = report.summary
    ? `<div class="metrics-grid report-summary">
        <div class="metric-line income"><span>Доходы<small>${esc(deltaText(report.comparison?.income, currency))}</small></span><strong>${formatMoneyString(report.summary.income, currency)}</strong></div>
        <div class="metric-line expense"><span>Расходы<small>${esc(deltaText(report.comparison?.expense, currency))}</small></span><strong>${formatMoneyString(report.summary.expense, currency)}</strong></div>
        <div class="metric-line"><span>Финрезультат<small>${esc(deltaText(report.comparison?.result, currency))}</small></span><strong>${formatMoneyString(report.summary.result, currency)}</strong></div>
        <div class="metric-line"><span>Операции</span><strong>${report.summary.operation_count}</strong></div>
      </div>`
    : EmptyPanel('Нет операций', 'За этот период в выбранной валюте операций нет.');
  return `
    <section class="screen reports-screen" data-report-kind="${esc(kind)}">
      <div class="report-mode-heading">
        <button class="button text" data-action="reports-close" type="button">Аналитика</button>
        <span class="eyebrow">Отчёты 2.0</span>
      </div>
      <div class="segmented report-kind-selector">${reportButtons}</div>
      <div class="insight-block report-context">
        <span class="eyebrow">${esc(REPORT_LABELS[kind])}</span>
        <h2>${esc(periodLabel(report.period.start_date, report.period.end_date))}</h2>
        <p>${esc(report.workspace.name)} · ${esc(scopeLabel(report))}${report.workspace.read_only ? ' · Только чтение' : ''}</p>
        ${currencyControl}
      </div>
      ${partialNote ? `<p class="caption report-partial-note">${esc(partialNote)}</p>` : ''}
      ${summary}
      <section class="chart-section report-section">
        ${SectionHeader(report.structure_type === 'income' ? 'Структура доходов' : 'Структура расходов', 'Канонические категории выбранной валюты')}
        <div class="detail-stack">${dimensionRows(report.categories, 'category')}</div>
      </section>
      <section class="chart-section report-section">
        ${SectionHeader('Магазины', 'Канонические группы Merchant Intelligence')}
        <div class="detail-stack">${dimensionRows(report.merchants, 'merchant')}</div>
      </section>
      <section class="chart-section report-section">
        ${SectionHeader('Что изменилось', `Сравнение: ${periodLabel(report.comparison_period.start_date, report.comparison_period.end_date)}`)}
        <div class="detail-stack">${observations(report)}</div>
      </section>
      <section class="chart-section report-section">
        ${SectionHeader('Экспорт', report.export_available ? 'Точный выбранный период и пространство' : report.export_reason || 'Для этого отчёта экспорт недоступен')}
        ${report.export_available ? '<button class="button secondary" data-action="report-export" type="button">Открыть экспорт</button>' : '<p class="caption">Экспорт не будет подменён другим набором операций.</p>'}
      </section>
    </section>
  `;
}
