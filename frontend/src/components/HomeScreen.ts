import { formatMoneyString, subtractMoneyStrings } from '../money';
import type { GlobalFinancialFilters, HomeReminderSummary, Operation } from '../types';
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

function reminderCard(reminder: HomeReminderSummary | null | undefined): string {
  const state = reminder?.state || 'empty';
  const title = reminder?.title || 'Нет запланированных событий';
  const amount = reminder?.amount_text ? ` · ${reminder.amount_text}` : '';
  const date = reminder?.event_date || '';
  const status = reminder?.status_text || 'Добавьте напоминание в боте.';
  const next = reminder?.next_event_date ? `<small>Следующее: ${esc(reminder.next_event_date)}</small>` : '';
  const label = state === 'overdue' ? 'Просрочено' : 'Ближайшее напоминание';
  const action = state === 'overdue' ? 'Записать оплату' : state === 'upcoming' ? 'Записать сейчас' : 'Все напоминания';
  return `
    <button class="smart-card reminder-card ${esc(state)}" data-action="home-reminder" type="button">
      <span>${esc(label)}</span>
      <strong>${esc(title)}${esc(amount)}</strong>
      ${date ? `<small>${esc(date)}</small>` : ''}
      <small>${esc(status)}</small>
      ${next}
      <small class="cta-text">${esc(action)}</small>
    </button>
  `;
}

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

export function HomeScreen(overview: Overview | null, recent: Operation[], fallbackCurrency: string, canWrite: boolean, filters: GlobalFinancialFilters = { period: 'current_month', operation_type: 'all', category: 'all' }): string {
  const period = overview?.period ? `${overview.period.start_date} — ${overview.period.end_date}` : '';
  const emptyAction = canWrite ? `<button class="button primary" data-action="open-add" data-kind="expense">${icon('expense')}Добавить первую операцию</button>` : '';
  const challenge = overview?.challenge;
  const focus = overview?.focus;
  const insight = overview?.insight;
  return `
    <section class="screen home-screen">
      <div class="home-hero-grid">
        <div class="hero-metric" data-testid="hero-financial-result" aria-label="Доходы − Расходы">
          <span class="eyebrow">${esc(heroTitle(filters))}</span>
          <strong>${esc(heroAmount(overview, filters, fallbackCurrency))}</strong>
          ${period ? `<p>${esc(period)}</p>` : ''}
          <p>${esc(heroSubtitle(filters))}</p>
          <div class="currency-lines" aria-label="Финансовый результат по валютам">
            ${Object.keys(overview?.totals_by_currency || {}).map((currency) => {
              const totals = overview?.totals_by_currency[currency];
              const result = filters.operation_type === 'expense'
                ? totals?.expense || '0.00'
                : filters.operation_type === 'income'
                  ? totals?.income || '0.00'
                  : totals ? subtractMoneyStrings(totals.income, totals.expense) : '0.00';
              return `<span>${esc(currency)} · ${esc(formatMoneyString(result, currency))}</span>`;
            }).join('') || `<span>${esc(formatMoneyString('0.00', fallbackCurrency))}</span>`}
          </div>
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
        <button class="smart-card" data-action="home-challenge" type="button" ${challenge ? '' : 'disabled'}>
          <span>Челлендж дня</span>
          <strong>${esc(challenge?.completed ? 'Готово' : challenge?.title || 'Нет задания')}</strong>
          <small>${esc(challenge ? `${challenge.progress}/${challenge.target} · ${challenge.description}` : 'Появится после загрузки')}</small>
        </button>
        <button class="smart-card" data-action="home-focus" data-mode="${esc(focus?.target_mode || 'goals')}" type="button">
          <span>Фокус</span>
          <strong>${esc(focus?.title || 'Фокус свободен')}</strong>
          <small>${esc(focus?.description || 'Цели и лимиты появятся здесь')}</small>
          ${focus?.percent !== undefined ? progressBar(focus.percent) : ''}
          ${focus?.projected_percent ? `<small>Прогноз к концу периода: ${esc(focus.projected_percent)}%</small>` : ''}
        </button>
        <button class="smart-card ${esc(insight?.tone || 'neutral')}" data-action="home-insight" type="button">
          <span>Инсайт периода</span>
          <strong>${esc(insight?.title || overview?.info?.text || 'Период')}</strong>
          <small>${esc(insight?.text || overview?.info?.text || 'Данные обновятся после операций')}</small>
        </button>
        ${reminderCard(overview?.reminder)}
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
