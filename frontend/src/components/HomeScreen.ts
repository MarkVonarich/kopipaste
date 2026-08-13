import { formatMoneyString, formatWholeMoneyString, subtractMoneyStrings } from '../money';
import type { GlobalFinancialFilters, HomeReminderSummary, Insight, InsightEvidence, Operation } from '../types';
import type { Overview } from '../api';
import { TransactionList } from './TransactionList';
import { EmptyPanel, SectionHeader, esc, icon } from './ui';
import { SpendableCard } from './Forecasting';

function resultLines(overview: Overview | null, fallbackCurrency = 'RUB'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) return formatWholeMoneyString('0.00', fallbackCurrency);
  return currencies.map((currency) => {
    const result = subtractMoneyStrings(totals[currency].income, totals[currency].expense);
    return `${result.startsWith('-') ? '' : '+'}${formatWholeMoneyString(result, currency)}`;
  }).join(' · ');
}

function totalLines(overview: Overview | null, type: 'income' | 'expense', fallbackCurrency = 'RUB'): string {
  const totals = overview?.totals_by_currency || {};
  const currencies = Object.keys(totals);
  if (!currencies.length) return formatWholeMoneyString('0.00', fallbackCurrency);
  return currencies.map((currency) => formatWholeMoneyString(totals[currency][type], currency)).join(' · ');
}

function progressBar(percent?: number): string {
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  return `<div class="progress-line" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}"><span style="width:${value}%"></span></div><small>${value}%</small>`;
}

function compactHomeInsightText(text: string): string {
  return text
    .replace(/На\s+(\d+)%\s+(больше|меньше),\s+чем\s+в\s+прошлом\s+сопоставимом\s+периоде\.?/gi, 'На $1% $2 прошлого периода')
    .replace(/в прошлом сопоставимом периоде/gi, 'прошлого периода')
    .trim();
}

function compactFocusDescription(item: NonNullable<Overview['focus']>): string {
  if (item.kind === 'empty') return item.description || 'Откройте детали.';
  if (item.kind === 'limit') {
    const status = String(item.status || '').toLowerCase();
    const severity = String(item.severity || '').toLowerCase();
    if (status === 'exceeded' || severity === 'critical') return 'Лимит превышен';
    if (status === 'warning' || severity === 'high') return 'Лимит под риском';
    if (status === 'risk' || status === 'attention' || severity === 'medium') return 'Близко к лимиту';
    return 'В пределах лимита';
  }
  return item.description || 'В плане';
}

function reminderCard(reminder: HomeReminderSummary | null | undefined): string {
  const state = reminder?.state || 'empty';
  const title = state === 'empty' ? 'Нет событий' : reminder?.title || 'Напоминание';
  return `<div class="reminder-card ${esc(state)}">
    <span>Напоминания</span><strong>${esc(title)}</strong>
    ${reminder?.amount_text ? `<small>${esc(reminder.amount_text)}</small>` : ''}
    ${reminder?.event_date ? `<small>${esc(reminder.event_date)}</small>` : ''}
    <small>${esc(state === 'empty' ? 'Добавьте в Планах.' : reminder?.status_text || '')}</small>
  </div>`;
}

function inlineInsightFeedback(insight: Insight, detail = false, saving = false): string {
  if (insight.feedback) {
    return `<div class="${detail ? 'insight-feedback' : 'inline-feedback'} resolved-feedback" aria-label="Оценка сохранена"><span>Спасибо</span></div>`;
  }
  if (detail) {
    return `<div class="insight-feedback"><span>Полезно?</span><button class="button secondary" type="button" data-action="insight-feedback" data-feedback="useful" ${saving ? 'disabled' : ''}>👍 Полезно</button><button class="button secondary" type="button" data-action="insight-feedback" data-feedback="not_useful" ${saving ? 'disabled' : ''}>👎 Не полезно</button></div>`;
  }
  return `<div class="inline-feedback"><span>Полезно?</span><button type="button" data-action="home-insight-feedback" data-insight-id="${esc(insight.id)}" data-feedback="useful" aria-label="Полезно" ${saving ? 'disabled' : ''}>👍</button><button type="button" data-action="home-insight-feedback" data-insight-id="${esc(insight.id)}" data-feedback="not_useful" aria-label="Не полезно" ${saving ? 'disabled' : ''}>👎</button></div>`;
}

function activityStrip(overview: Overview | null): string {
  const activity = overview?.activity;
  const streak = activity?.current_streak || 0;
  const activeDays = activity?.active_days || 0;
  const message = streak >= 3 ? `${streak} дня подряд · Хороший ритм` : activeDays >= 6 ? `${activeDays} активных дней · Так держать!` : `${activeDays} активных дней за период`;
  return `<button class="home-activity-strip" type="button" data-action="activity-open" data-testid="home-activity-strip">
    <span>${icon('analytics')}<strong>Активность</strong></span><small>${esc(message)}</small>${icon('chevron')}
  </button>`;
}

function insightEvidenceRow(item: InsightEvidence, fallbackCurrency: string): string {
  const currency = item.currency || fallbackCurrency;
  if (item.kind === 'amount_comparison' || item.kind === 'average_check') {
    return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(formatMoneyString(item.current_amount || '0', currency))}</strong><small>Было ${esc(formatMoneyString(item.previous_amount || '0', currency))}</small></div>`;
  }
  if (item.kind === 'merchant_contribution') {
    const count = item.current_count !== undefined && item.previous_count !== undefined
      ? ` · ${esc(item.current_count)} покупок вместо ${esc(item.previous_count)}`
      : '';
    return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>+${esc(formatMoneyString(item.delta_amount || '0', currency))}</strong><small>${esc(item.share_pct || 0)}% изменения${count}</small></div>`;
  }
  if (item.kind === 'count_comparison') return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(item.current_count || 0)} вместо ${esc(item.previous_count || 0)}</strong></div>`;
  if (item.kind === 'contribution_share') return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(item.share_pct || 0)}%</strong></div>`;
  if (item.kind === 'limit_pace') return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(formatMoneyString(item.spent_amount || '0', currency))} из ${esc(formatMoneyString(item.limit_amount || '0', currency))}</strong><small>Использовано ${esc(item.used_percent || 0)}%</small></div>`;
  return '';
}

export function InsightDetail(insight: Insight, saving = false, error = ''): string {
  return `<div class="insight-detail">
    <div class="insight-detail-conclusion ${esc(insight.tone)}"><strong>${esc(insight.title)}</strong><p>${esc(insight.summary)}</p></div>
    <h3>Почему мы это заметили</h3>
    <div class="insight-periods"><span>Текущий период<br><strong>${esc(insight.period.start_date)} — ${esc(insight.period.end_date)}</strong></span><span>Сопоставимый<br><strong>${esc(insight.comparison_period.start_date)} — ${esc(insight.comparison_period.end_date)}</strong></span></div>
    <div class="insight-evidence">${insight.evidence.map((item) => insightEvidenceRow(item, insight.currency)).join('')}</div>
    <div class="form-grid insight-actions">${insight.actions.map((action, index) => `<button class="button ${index === 0 ? 'primary' : 'secondary'}" type="button" data-action="insight-action" data-index="${index}" ${saving ? 'disabled' : ''}>${esc(action.label)}</button>`).join('')}</div>
    ${inlineInsightFeedback(insight, true, saving)}
    ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
  </div>`;
}

type HomeFocusItem = NonNullable<Overview['focus']> & { due_within_one_day?: boolean };

function carouselDots(kind: 'goal' | 'limit' | 'reminder' | 'announcement', total: number, index: number, label: string): string {
  if (total <= 1 && kind !== 'announcement') return '';
  return `<div class="carousel-dots ${kind === 'announcement' ? 'announcement-dots' : 'plan-carousel-dots'}" role="tablist" aria-label="${esc(label)}">
    ${Array.from({ length: total }, (_, dotIndex) => `<button class="${dotIndex === index ? 'active' : ''}" type="button" role="tab" data-action="carousel-dot" data-carousel="${kind}" data-index="${dotIndex}" aria-label="${esc(label)} ${dotIndex + 1} из ${total}" aria-selected="${dotIndex === index}"></button>`).join('')}
  </div>`;
}

function focusStateClass(kind: 'limits' | 'goals', item: HomeFocusItem): string {
  const status = String(item.status || '').toLowerCase();
  const severity = String(item.severity || '').toLowerCase();
  if (severity === 'critical' || status === 'exceeded') return 'is-critical';
  if (kind === 'limits' && (severity === 'high' || severity === 'medium' || ['warning', 'risk', 'attention'].includes(status))) return 'is-warning';
  if (kind === 'goals' && item.due_within_one_day) return 'is-urgent';
  return '';
}

function focusCard(kind: 'limits' | 'goals', overview: Overview | null, index: number): string {
  const source = kind === 'limits' ? overview?.limit_items || [] : overview?.goal_items || [];
  const fallback = kind === 'limits'
    ? { kind: 'empty', title: 'Нет активных лимитов', description: 'Добавьте лимит в Планах.' }
    : { kind: 'empty', title: 'Нет активных целей', description: 'Добавьте цель в Планах.' };
  const currentIndex = Math.max(0, Math.min(index, Math.max(0, source.length - 1)));
  const item = (source.length ? source[currentIndex] : fallback) as HomeFocusItem;
  const carouselKind = kind === 'limits' ? 'limit' : 'goal';
  return `<article class="smart-card plan-home-card ${focusStateClass(kind, item)}" data-carousel="${carouselKind}" data-index="${currentIndex}" tabindex="0">
    <button class="smart-card-action plan-home-card-action" type="button" data-action="home-focus" data-mode="${kind}">
      <span>${kind === 'limits' ? 'Лимиты' : 'Цели'}</span><strong>${esc(item.title)}</strong><small>${esc(compactFocusDescription(item))}</small>
      ${item.percent !== undefined ? progressBar(item.percent) : ''}
    </button>
    ${carouselDots(carouselKind, source.length, currentIndex, kind === 'limits' ? 'Лимит' : 'Цель')}
  </article>`;
}

function reminderWidget(reminders: HomeReminderSummary[], index: number): string {
  const currentIndex = Math.max(0, Math.min(index, Math.max(0, reminders.length - 1)));
  const reminder = reminders[currentIndex];
  const stateClass = reminder?.state === 'overdue' ? 'is-critical' : reminder?.due_within_one_day ? 'is-urgent' : '';
  return `<article class="smart-card plan-home-card ${stateClass}" data-carousel="reminder" data-index="${currentIndex}" tabindex="0">
    <button class="smart-card-action plan-home-card-action" type="button" data-action="home-reminder" ${reminder?.id ? `data-id="${esc(reminder.id)}"` : ''} data-state="${esc(reminder?.state || 'empty')}">${reminderCard(reminder)}</button>
    ${carouselDots('reminder', reminders.length, currentIndex, 'Напоминание')}
  </article>`;
}

function resultComparison(overview: Overview | null): string {
  if (overview && !overview.aggregation_available) return '';
  const metric = overview?.result_comparison;
  if (!metric || metric.pct === null || metric.pct === undefined || metric.state !== 'ok') return '';
  const value = Number(metric.pct);
  return `${value > 0 ? '+' : ''}${Math.round(value)}% к прошлому периоду`;
}

function announcementCard(overview: Overview | null, index: number): string {
  const items = overview?.announcements || [];
  const currentIndex = Math.max(0, Math.min(index, items.length - 1));
  const current = items[currentIndex];
  if (!current) return '';
  return `<section class="announcement-carousel announcement-highlight" aria-label="Новое в КопиPaste">
    <article class="announcement-card compact" data-carousel="announcement" data-index="${currentIndex}" data-announcement-target="${esc(current.action.type)}" tabindex="0">
      <button class="announcement-dismiss" type="button" data-action="announcement-dismiss" data-id="${esc(current.id)}" aria-label="Скрыть">×</button>
      <button class="announcement-card-action" type="button" data-action="announcement-open" data-target="${esc(current.action.type)}">
        <span class="eyebrow">Новое в КопиPaste</span><strong>${esc(current.title)}</strong><p>${esc(current.description)}</p>
      </button>
    </article>
    ${carouselDots('announcement', items.length, currentIndex, 'Новость')}
  </section>`;
}

type HomeIndices = { challenge?: number; focus?: number; goal?: number; limit?: number; reminder?: number; announcement?: number };

export function HomeScreen(overview: Overview | null, recent: Operation[], fallbackCurrency: string, canWrite: boolean, _filters: GlobalFinancialFilters = { period: 'current_month', operation_type: 'all', category: 'all' }, indices: HomeIndices = {}): string {
  const enabled = new Set(overview?.home_preferences?.enabled || ['limits', 'goals', 'reminders', 'insights', 'shopping_list']);
  const insights = overview?.insights?.length ? overview.insights : overview?.insight ? [overview.insight] : [];
  const reminders = (overview?.reminders || [overview?.reminder]).filter(Boolean) as HomeReminderSummary[];
  const announcement = announcementCard(overview, indices.announcement || 0);
  const shopping = overview?.shopping;
  const comparison = resultComparison(overview);
  const emptyAction = canWrite ? `<button class="button primary" data-action="open-add" data-kind="expense">${icon('expense')}Добавить первую операцию</button>` : '';
  return `<section class="screen home-screen">
    ${activityStrip(overview)}
    <div class="home-summary-row" data-testid="home-summary-row">
      <article class="summary-card result-card"><span>Итог</span><strong class="home-primary-amount">${esc(resultLines(overview, fallbackCurrency))}</strong>${comparison ? `<small>${esc(comparison)}</small>` : ''}</article>
      ${SpendableCard(overview?.spendable)}
    </div>
    <section class="home-income-expense" data-testid="income-expense-columns">
      <div class="home-column income" data-testid="income-column"><div class="metric-line income"><span>Доходы</span><strong class="home-rounded-amount">${esc(totalLines(overview, 'income', fallbackCurrency))}</strong></div><button class="button secondary" data-action="open-add" data-kind="income" ${canWrite ? '' : 'disabled'}>${icon('income')}Добавить доход</button></div>
      <div class="home-column expense" data-testid="expense-column"><div class="metric-line expense"><span>Расходы</span><strong class="home-rounded-amount">${esc(totalLines(overview, 'expense', fallbackCurrency))}</strong></div><button class="button primary" data-action="open-add" data-kind="expense" ${canWrite ? '' : 'disabled'}>${icon('expense')}Добавить расход</button></div>
    </section>
    ${announcement}
    <div class="home-plans" data-testid="home-plans">
      ${enabled.has('limits') ? focusCard('limits', overview, indices.limit || 0) : ''}
      ${enabled.has('goals') ? focusCard('goals', overview, indices.goal || 0) : ''}
      ${enabled.has('reminders') ? reminderWidget(reminders, indices.reminder || 0) : ''}
    </div>
    ${enabled.has('insights') && insights.length ? `<section class="home-insights"><h2>Инсайты</h2>${insights.slice(0, 2).map((item, index) => `<article class="smart-card insight-card ${index ? 'secondary-insight' : ''} ${esc(item.tone || 'neutral')}"><button class="smart-card-action" data-action="home-insight" data-insight-id="${esc(item.id)}" type="button"><span>${index ? 'Ещё важно' : 'Главное'}</span><strong>${esc(item.title)}</strong><small>${esc(compactHomeInsightText(item.summary))}</small></button>${inlineInsightFeedback(item)}</article>`).join('')}</section>` : ''}
    ${enabled.has('shopping_list') ? `<button class="smart-card shopping-home-card" data-action="shopping-open" type="button"><span>Список покупок</span><strong>${shopping?.available ? shopping.active_count ? `${shopping.active_count} нужно купить` : 'Список пуст' : 'Выберите пространство'}</strong>${shopping?.available ? (shopping.items || []).filter((item) => !item.completed).slice(0, 3).map((item) => `<small>${esc(item.text)}</small>`).join('') : '<small>Доступен для одного пространства.</small>'}</button>` : ''}
    <section class="recent-home-widget">${SectionHeader('Последние операции', '', `<button class="icon-button" data-action="open-actions" aria-label="Добавить операцию" ${canWrite ? '' : 'disabled'}>${icon('plus')}</button>`)}${recent.length ? TransactionList(recent.slice(0, 3), 'За период операций нет.') : EmptyPanel('Операций пока нет', 'Добавьте первый расход или доход.', emptyAction)}<button class="button text" data-action="go-operations">Все операции</button></section>
  </section>`;
}
