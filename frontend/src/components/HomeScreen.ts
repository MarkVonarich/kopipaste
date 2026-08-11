import { formatMoneyString, subtractMoneyStrings } from '../money';
import type { GlobalFinancialFilters, HomeReminderSummary, Insight, InsightEvidence, Operation } from '../types';
import type { Overview } from '../api';
import { ActivityCalendarView } from './ActivityCalendar';
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

function heroTitle(filters: GlobalFinancialFilters): string {
  if (filters.category && filters.category !== 'all') return filters.category;
  if (filters.operation_type === 'expense') return 'Расходы за период';
  if (filters.operation_type === 'income') return 'Доходы за период';
  return 'Финансовый результат';
}

function heroSubtitle(filters: GlobalFinancialFilters): string {
  if (filters.category && filters.category !== 'all') {
    if (filters.operation_type === 'expense') return 'Расходы за период';
    if (filters.operation_type === 'income') return 'Доходы за период';
    return 'Доходы / Расходы / результат';
  }
  if (filters.operation_type === 'expense') return 'Сумма расходов без доходов';
  if (filters.operation_type === 'income') return 'Сумма доходов без расходов';
  return 'Доходы − Расходы';
}

function heroAmount(overview: Overview | null, filters: GlobalFinancialFilters, fallbackCurrency: string): string {
  if (filters.operation_type === 'expense') return totalLines(overview, 'expense', fallbackCurrency);
  if (filters.operation_type === 'income') return totalLines(overview, 'income', fallbackCurrency);
  return resultLines(overview, fallbackCurrency);
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

function compactChallengeText(text: string): string {
  return text
    .replace(/Две/g, '2')
    .replace(/две/g, '2')
    .replace(/реальные\s+/gi, '')
    .replace(/операции\s+за\s+сегодня/gi, 'операции сегодня')
    .trim();
}

function compactFocusDescription(item: NonNullable<Overview['focus']>): string {
  if (item.kind === 'empty') {
    if ((item.title || '').toLowerCase().includes('фокус свободен')) return 'Добавьте цель или лимит.';
    return item.description || 'Откройте детали.';
  }
  if (item.kind === 'limit') {
    const status = String(item.status || '').toLowerCase();
    const severity = String(item.severity || '').toLowerCase();
    if (status === 'exceeded' || severity === 'critical') return 'Лимит превышен';
    if (status === 'warning' || severity === 'high') return 'Лимит под риском';
    if (status === 'risk' || status === 'attention' || severity === 'medium') return 'Близко к лимиту';
    return 'В пределах лимита';
  }
  const severity = String(item.severity || '').toLowerCase();
  if (severity === 'critical') return 'Требует внимания';
  if (severity === 'high') return 'Скоро действие';
  if (severity === 'medium') return 'Проверьте план';
  return item.description || 'В плане';
}

function carousel(id: 'challenge' | 'focus' | 'reminder', items: string[], index: number, label: string, action: string, actionAttrs: string[] = []): string {
  if (!items.length) return '';
  const current = Math.max(0, Math.min(index || 0, items.length - 1));
  return `
    <article class="smart-card home-carousel" data-carousel="${id}" data-index="${current}" tabindex="0" role="group" aria-label="${esc(label)}">
      <button class="smart-card-action" data-action="${esc(action)}" type="button" ${actionAttrs[current] || ''}>
        <div class="home-carousel-track">${items[current]}</div>
      </button>
      ${items.length > 1 ? `<div class="carousel-dots" aria-label="${esc(label)}: страницы">
        ${items.map((_item, dot) => `<button type="button" data-action="carousel-dot" data-carousel="${id}" data-index="${dot}" aria-label="${dot + 1}/${items.length}" class="${dot === current ? 'active' : ''}"></button>`).join('')}
      </div>` : ''}
    </article>
  `;
}

function reminderCard(reminder: HomeReminderSummary | null | undefined): string {
  const state = reminder?.state || 'empty';
  const title = state === 'empty' ? 'Нет событий' : reminder?.title || 'Напоминание';
  const amount = reminder?.amount_text ? ` · ${reminder.amount_text}` : '';
  const date = reminder?.event_date || '';
  const status = state === 'empty' ? 'Добавьте в Планах.' : reminder?.status_text || '';
  const label = 'Напоминание';
  const action = state === 'overdue' ? 'Записать оплату' : state === 'upcoming' ? 'Записать сейчас' : 'Все напоминания';
  return `
    <div class="reminder-card ${esc(state)}">
      <span>${esc(label)}</span>
      <strong>${esc(title)}${esc(amount)}</strong>
      ${date ? `<small>${esc(date)}</small>` : ''}
      ${status ? `<small>${esc(status)}</small>` : ''}
      <small class="cta-text">${esc(action)}</small>
    </div>
  `;
}

type HomeIndices = {
  challenge: number;
  focus: number;
  reminder: number;
};

function activityCard(overview: Overview | null): string {
  const activity = overview?.activity;
  const streak = activity?.current_streak || 0;
  const activeDays = activity?.active_days || 0;
  const days = activity?.days_in_period || activity?.days?.length || 0;
  return `
    <section class="hero-activity" data-testid="home-activity-card">
      <span class="eyebrow">${esc(activity?.label || 'Активность')}</span>
      <strong>${streak ? `${streak} дней подряд` : 'Нет серии без пропусков'}</strong>
      <p>${activeDays} активных дней за период${days ? ` из ${days}` : ''}</p>
      ${ActivityCalendarView(activity, true)}
    </section>
  `;
}

function insightEvidenceRow(item: InsightEvidence, fallbackCurrency: string): string {
  const currency = item.currency || fallbackCurrency;
  if (item.kind === 'amount_comparison' || item.kind === 'average_check') {
    return `<div class="insight-evidence-row">
      <span>${esc(item.label)}</span>
      <strong>${esc(formatMoneyString(item.current_amount || '0', currency))}</strong>
      <small>Было ${esc(formatMoneyString(item.previous_amount || '0', currency))}</small>
    </div>`;
  }
  if (item.kind === 'merchant_contribution') {
    return `<div class="insight-evidence-row">
      <span>${esc(item.label)}</span>
      <strong>+${esc(formatMoneyString(item.delta_amount || '0', currency))}</strong>
      <small>${esc(item.share_pct || 0)}% роста${item.current_count !== undefined && item.previous_count !== undefined ? ` · ${esc(item.current_count)} покупок вместо ${esc(item.previous_count)}` : ''}</small>
    </div>`;
  }
  if (item.kind === 'count_comparison') {
    return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(item.current_count || 0)} вместо ${esc(item.previous_count || 0)}</strong></div>`;
  }
  if (item.kind === 'contribution_share') {
    return `<div class="insight-evidence-row"><span>${esc(item.label)}</span><strong>${esc(item.share_pct || 0)}%</strong></div>`;
  }
  if (item.kind === 'limit_pace') {
    return `<div class="insight-evidence-row">
      <span>${esc(item.label)}</span>
      <strong>${esc(formatMoneyString(item.spent_amount || '0', currency))} из ${esc(formatMoneyString(item.limit_amount || '0', currency))}</strong>
      <small>Использовано ${esc(item.used_percent || 0)}% · прошло ${esc(item.period_progress || 0)}% периода</small>
    </div>`;
  }
  return '';
}

export function InsightDetail(insight: Insight, saving = false, error = ''): string {
  return `
    <div class="insight-detail">
      <div class="insight-detail-conclusion ${esc(insight.tone)}">
        <strong>${esc(insight.title)}</strong>
        <p>${esc(insight.summary)}</p>
      </div>
      <div class="insight-periods">
        <span>Текущий период<br><strong>${esc(insight.period.start_date)} — ${esc(insight.period.end_date)}</strong></span>
        <span>Сопоставимый<br><strong>${esc(insight.comparison_period.start_date)} — ${esc(insight.comparison_period.end_date)}</strong></span>
      </div>
      <div class="insight-evidence" aria-label="Почему показан инсайт">
        ${insight.evidence.map((item) => insightEvidenceRow(item, insight.currency)).join('')}
      </div>
      <div class="form-grid insight-actions">
        ${insight.actions.map((action, index) => `<button class="button ${index === 0 ? 'primary' : 'secondary'}" type="button" data-action="insight-action" data-index="${index}" ${saving ? 'disabled' : ''}>${esc(action.label)}</button>`).join('')}
      </div>
      <div class="insight-feedback" aria-label="Оценка инсайта">
        <span>Полезно?</span>
        <button class="button secondary" type="button" data-action="insight-feedback" data-feedback="useful" ${saving || insight.feedback ? 'disabled' : ''}>👍 Полезно</button>
        <button class="button secondary" type="button" data-action="insight-feedback" data-feedback="not_useful" ${saving || insight.feedback ? 'disabled' : ''}>👎 Не полезно</button>
      </div>
      ${insight.feedback ? `<p class="caption">Спасибо, учтём этот выбор.</p>` : ''}
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
    </div>
  `;
}

export function HomeScreen(overview: Overview | null, recent: Operation[], fallbackCurrency: string, canWrite: boolean, filters: GlobalFinancialFilters = { period: 'current_month', operation_type: 'all', category: 'all' }, indices: HomeIndices = { challenge: 0, focus: 0, reminder: 0 }): string {
  const period = overview?.period ? `${overview.period.start_date} — ${overview.period.end_date}` : '';
  const emptyAction = canWrite ? `<button class="button primary" data-action="open-add" data-kind="expense">${icon('expense')}Добавить первую операцию</button>` : '';
  const challenge = overview?.challenge;
  const focus = overview?.focus;
  const insights = overview?.insights?.length ? overview.insights : overview?.insight ? [overview.insight] : [];
  const insight = insights[0];
  const challenges = overview?.challenges?.length ? overview.challenges : challenge ? [challenge] : [];
  const focusItems = overview?.focus_items?.length ? overview.focus_items : focus ? [focus] : [];
  const reminders = overview?.reminders?.length ? overview.reminders : overview?.reminder ? [overview.reminder] : [];
  const homeInsightText = compactHomeInsightText(insight?.summary || overview?.info?.text || 'Показан выбранный период.');
  const challengeCards = challenges.map((item) => `
    <div>
      <span>Челлендж · ${esc(item.period_type === 'week' ? 'Неделя' : item.period_type === 'month' ? 'Месяц' : 'Сегодня')}</span>
      <strong>${esc(item.completed ? 'Готово' : compactChallengeText(item.title))}</strong>
      <small>${esc(`${item.progress}/${item.target}`)}</small>
      <small>${esc(compactChallengeText(item.description))}</small>
      ${progressBar(Math.round((Number(item.progress || 0) / Math.max(1, Number(item.target || 1))) * 100))}
    </div>
  `);
  const focusCards = focusItems.map((item) => `
    <div>
      <span>Фокус</span>
      <strong>${esc(item.title || 'Фокус свободен')}</strong>
      <small>${esc(compactFocusDescription(item))}</small>
      ${item.percent !== undefined ? progressBar(item.percent) : ''}
      ${item.projected_percent ? `<small>Прогноз: ${esc(item.projected_percent)}%</small>` : ''}
    </div>
  `);
  const focusActionAttrs = focusItems.map((item) => `data-mode="${esc(item.target_mode || 'goals')}"`);
  const reminderCards = reminders.map((item) => reminderCard(item));
  const reminderActionAttrs = reminders.map((item) => `${item?.id ? `data-id="${esc(item.id)}"` : ''} data-state="${esc(item?.state || 'empty')}"`);
  return `
    <section class="screen home-screen">
      <div class="home-hero-grid">
        <div class="hero-metric" data-testid="hero-financial-result" aria-label="Доходы − Расходы">
          <span class="eyebrow">${esc(heroTitle(filters))}</span>
          <strong>${esc(heroAmount(overview, filters, fallbackCurrency))}</strong>
          ${period ? `<p>${esc(period)}</p>` : ''}
          <p>${esc(heroSubtitle(filters))}</p>
          <p class="hero-insight">${esc(homeInsightText)}</p>
          ${overview && !overview.aggregation_available ? '<p class="caption">Валюты показаны отдельно. Разные валюты не складываются.</p>' : ''}
        </div>
        ${activityCard(overview)}
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
        ${carousel('challenge', challengeCards, indices.challenge, 'Челленджи', 'home-challenge')}
        ${carousel('focus', focusCards, indices.focus, 'Фокус', 'home-focus', focusActionAttrs)}
        ${carousel('reminder', reminderCards, indices.reminder, 'Напоминания', 'home-reminder', reminderActionAttrs)}
        ${insights.length ? `<div class="insight-stack" aria-label="Инсайты периода">
          ${insights.map((item, index) => `<button class="smart-card insight-card ${index ? 'secondary-insight' : ''} ${esc(item.tone || 'neutral')}" data-action="home-insight" data-insight-id="${esc(item.id)}" type="button">
            <span>${index ? 'Ещё важно' : 'Инсайт периода'}</span>
            <strong>${esc(item.title)}</strong>
            <small>${esc(compactHomeInsightText(item.summary))}</small>
          </button>`).join('')}
        </div>` : ''}
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
