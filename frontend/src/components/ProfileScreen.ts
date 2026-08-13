import type { CategoryOption, HomePreferences, NotificationPreferences, PremiumInfo, ProfileSection, ThemeMode, VacationMode, Workspace } from '../types';
import { formatMoneyString } from '../money';
import { SectionHeader, esc, icon } from './ui';

export type ProfileData = {
  theme: ThemeMode;
  preferred_name?: string | null;
  display_name?: string;
  currency: string;
  available_currencies?: string[];
  timezone: string;
  timezone_options?: Array<{ label: string; value: string }>;
  version: string;
  help_url?: string;
  links?: { privacy?: string | null; terms?: string | null };
  workspaces?: Workspace[];
  categories?: { expense: CategoryOption[]; income: CategoryOption[] };
  notifications?: NotificationPreferences;
  premium?: PremiumInfo;
  export?: { available: boolean; status: string; presets: string[]; privacy_note: string };
  home_preferences?: HomePreferences;
  vacation_mode?: VacationMode;
};

const SECTIONS: Array<[ProfileSection, string]> = [
  ['user', 'Пользователь'],
  ['appearance', 'Внешний вид'],
  ['home', 'Главная'],
  ['workspaces', 'Пространства'],
  ['notifications', 'Уведомления'],
  ['behaviour', 'Поведение'],
  ['privacy', 'Данные и приватность'],
  ['premium', 'Premium'],
  ['help', 'Помощь'],
  ['legal', 'Правовая информация'],
];

function row(label: string, value = '', description = '', action = '', attrs = ''): string {
  const tag = action ? 'button' : 'div';
  return `
    <${tag} class="settings-row" ${action ? `type="button" data-action="${action}"` : ''} ${attrs}>
      <span>
        <strong>${esc(label)}</strong>
        ${description ? `<small>${esc(description)}</small>` : ''}
      </span>
      <em>${esc(value)}${action ? icon('chevron') : ''}</em>
    </${tag}>
  `;
}

function notificationBlock(label: string, key: string, enabled: boolean, description: string, meta = ''): string {
  return `
    <button class="settings-row switch-row notification-block" type="button" data-action="notification-toggle" data-key="${esc(key)}" role="switch" aria-checked="${enabled ? 'true' : 'false'}">
      <span>
        <strong>${esc(label)}</strong>
        ${meta ? `<small>${esc(meta)}</small>` : ''}
        <small>${esc(description)}</small>
      </span>
      <em><span class="switch-control ${enabled ? 'on' : ''}" aria-hidden="true"></span></em>
    </button>
  `;
}

function panel(section: ProfileSection, active: ProfileSection | null | undefined, body: string): string {
  const title = SECTIONS.find(([key]) => key === section)?.[1] || section;
  const open = section === active;
  return `
    <section class="accordion-section">
      <button class="accordion-trigger" type="button" data-action="profile-section" data-section="${section}" aria-expanded="${open ? 'true' : 'false'}" aria-controls="profile-panel-${section}">
        <span>${esc(title)}</span>${icon('chevron')}
      </button>
      <div class="accordion-panel" id="profile-panel-${section}" ${open ? '' : 'hidden'}>
        ${body}
      </div>
    </section>
  `;
}

export function ProfileScreen(profile: ProfileData | null, workspaces: Workspace[], activeTheme: ThemeMode, activeSection: ProfileSection | null | undefined): string {
  const prefs = profile?.notifications;
  const visibleWorkspaces = profile?.workspaces || workspaces.filter((item) => item.workspace_id !== 'all');
  const activeWorkspace = visibleWorkspaces.find((workspace) => workspace.active);
  const vacation = profile?.vacation_mode;
  const vacationStatus = vacation?.status === 'active'
    ? `Активен до ${vacation.end_date || ''}`
    : vacation?.status === 'scheduled'
      ? 'Запланирован'
      : vacation?.status === 'completed'
        ? 'Завершён'
        : 'Выключен';
  return `
    <section class="screen profile-screen">
      ${SectionHeader('Настройки', 'Профиль, данные и внешний вид')}
      <div class="accordion" data-testid="profile-accordion">
        ${panel('user', activeSection, `
          <div class="settings-list">
            ${row('Как к вам обращаться?', profile?.preferred_name || profile?.display_name || 'Пользователь', 'Используется и в боте', 'profile-name-open')}
            ${row('Валюта', profile?.currency || 'RUB', 'Для новых операций по умолчанию', 'profile-currency-open')}
            ${row('Часовой пояс', profile?.timezone || prefs?.timezone || 'Не выбран', 'Для уведомлений и периодов', 'profile-timezone-open')}
            ${row('Активное пространство', activeWorkspace?.name || 'Личное', 'Для личных действий по умолчанию', 'profile-active-workspace-open')}
          </div>
        `)}
        ${panel('appearance', activeSection, `
          <div class="segmented" data-action="theme" role="tablist" aria-label="Тема">
            ${(['telegram', 'light', 'dark'] as ThemeMode[]).map((theme) => `<button data-theme="${theme}" class="${activeTheme === theme ? 'active' : ''}">${theme === 'telegram' ? 'Telegram' : theme === 'light' ? 'Светлая' : 'Тёмная'}</button>`).join('')}
          </div>
        `)}
        ${panel('home', activeSection, `<div class="settings-list">${row('Настройка главной', `${profile?.home_preferences?.enabled.length ?? 0} виджетов`, 'Выберите порядок и состав главной', 'home-settings-open')}</div>`)}
        ${panel('workspaces', activeSection, `
          <div class="settings-list">
            ${visibleWorkspaces.map((workspace) => row(workspace.name, `${workspace.role}${workspace.active ? ' · активно' : ''}`, workspace.kind, workspace.role === 'owner' || workspace.role === 'admin' ? 'profile-workspace-open' : 'profile-active-workspace-set', `data-id="${esc(workspace.workspace_id)}"`)).join('') || '<p class="caption">Нет доступных пространств.</p>'}
          </div>
        `)}
        ${panel('notifications', activeSection, `
          <div class="settings-list">
            ${vacation?.active ? '<p class="caption vacation-notice">Уведомления временно приостановлены режимом отпуска. Сохранённые настройки не изменены.</p>' : ''}
            ${prefs ? `
              ${notificationBlock('Ежедневные уведомления', 'daily', prefs.daily_notifications?.enabled ?? prefs.evening_enabled, 'Короткое вечернее сообщение помогает не забывать записывать операции.', `Вечер ${prefs.daily_notifications?.evening_time || prefs.evening_time}`)}
              ${notificationBlock('Планы и контроль', 'plans', prefs.plans_control?.enabled ?? (prefs.limit_alerts_enabled || prefs.budget_alerts_enabled || prefs.goal_notifications_enabled || (prefs.subscription_alerts_enabled ?? true) || (prefs.recurring_spend_alerts_enabled ?? true)), 'Предупреждает о лимитах, бюджетах, целях и важных регулярных расходах.')}
              ${notificationBlock('Отчёты', 'reports', prefs.reports?.enabled ?? (prefs.weekly_reports_enabled || prefs.monthly_reports_enabled), 'Присылает финансовую сводку за неделю и месяц.')}
              ${row('Тихие часы', prefs.quiet_hours?.enabled || prefs.quiet_hours_enabled ? `${prefs.quiet_hours?.start || prefs.quiet_hours_start || '22:30'}–${prefs.quiet_hours?.end || prefs.quiet_hours_end || '08:00'}` : 'Выкл', 'В это время автоматические сообщения не будут вас беспокоить.', 'quiet-hours-open')}
            ` : '<p class="caption">Настройки недоступны.</p>'}
          </div>
        `)}
        ${panel('behaviour', activeSection, `<div class="settings-list">${row('Режим отпуска', vacationStatus, 'Приостанавливает автоматические финансовые уведомления, но не ваши напоминания.', 'vacation-open')}</div>`)}
        ${panel('privacy', activeSection, `<div class="settings-list">
          ${row('Экспорт финансовых данных', '', 'Операции за выбранный период в XLSX', 'export-open')}
          ${row('Удалить финансовую историю', '', 'Выберите период и проверьте состав перед удалением', 'privacy-history-open')}
          ${row('Удалить аккаунт и мои данные', '', 'Требует отдельного подтверждения', 'privacy-account-open')}
        </div>`)}
        ${panel('premium', activeSection, row(profile?.premium?.title || 'Premium', profile?.premium?.status || 'info', profile?.premium?.description || 'Информационный раздел.', 'premium-open'))}
        ${panel('help', activeSection, `
          ${profile?.help_url ? `<a class="settings-row" href="${esc(profile.help_url)}" target="_blank" rel="noreferrer"><span><strong>Помощь</strong></span><em>${icon('chevron')}</em></a>` : ''}
          ${row('Версия', profile?.version || '')}
        `)}
        ${panel('legal', activeSection, `
          ${profile?.links?.privacy ? `<a class="settings-row" href="${esc(profile.links.privacy)}" target="_blank" rel="noreferrer"><span><strong>Privacy</strong></span><em>${icon('chevron')}</em></a>` : '<p class="caption">Документ пока недоступен</p>'}
          ${profile?.links?.terms ? `<a class="settings-row" href="${esc(profile.links.terms)}" target="_blank" rel="noreferrer"><span><strong>Terms</strong></span><em>${icon('chevron')}</em></a>` : ''}
        `)}
      </div>
    </section>
  `;
}

export function PreferredNameForm(profile: ProfileData | null, saving: boolean, error?: string): string {
  return `<form class="form-grid" data-action="profile-name-save">
    <label>Как к вам обращаться?<input class="input" name="preferred_name" maxlength="50" value="${esc(profile?.preferred_name || '')}" autocomplete="name" /></label>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

export function CurrencyForm(profile: ProfileData | null, saving: boolean, error?: string): string {
  const current = profile?.currency || 'RUB';
  const options = profile?.available_currencies?.length ? profile.available_currencies : ['RUB', 'USD', 'EUR'];
  return `<form class="form-grid" data-action="profile-currency-save">
    <label>Валюта<select class="select" name="currency">${options.map((code) => `<option value="${esc(code)}" ${code === current ? 'selected' : ''}>${esc(code)}</option>`).join('')}</select></label>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

export function TimezoneForm(profile: ProfileData | null, saving: boolean, error?: string): string {
  const current = profile?.timezone || profile?.notifications?.timezone || 'Europe/Moscow';
  const options = profile?.timezone_options || [];
  return `<form class="form-grid" data-action="profile-timezone-save">
    <label>Часовой пояс<select class="select" name="timezone_select">
      ${options.map((item) => `<option value="${esc(item.value)}" ${item.value === current ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}
      <option value="custom">Другой IANA</option>
    </select></label>
    <label>IANA<input class="input" name="timezone_custom" placeholder="Europe/Moscow" value="${options.some((item) => item.value === current) ? '' : esc(current)}" /></label>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

export function WorkspaceForm(workspace: Workspace | undefined, saving: boolean, error?: string): string {
  return `<form class="form-grid" data-action="profile-workspace-save" data-id="${esc(workspace?.workspace_id)}">
    <label>Название<input class="input" name="name" maxlength="120" value="${esc(workspace?.name || '')}" /></label>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

export function QuietHoursForm(prefs: NotificationPreferences | undefined, saving: boolean, error?: string): string {
  return `<form class="form-grid" data-action="quiet-hours-save">
    <label class="checkbox-row"><input type="checkbox" name="enabled" ${prefs?.quiet_hours_enabled ? 'checked' : ''} /> Включить тихие часы</label>
    <label>Начало<input class="input" type="time" name="start" value="${esc(prefs?.quiet_hours_start || '22:30')}" /></label>
    <label>Конец<input class="input" type="time" name="end" value="${esc(prefs?.quiet_hours_end || '08:00')}" /></label>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

export function VacationForm(vacation: VacationMode | undefined, saving: boolean, error = ''): string {
  return `<form class="form-grid" data-action="vacation-save">
    <label class="checkbox-row"><input type="checkbox" name="enabled" ${vacation?.enabled ? 'checked' : ''} /> Включить режим отпуска</label>
    <label>Дата начала<input class="input" type="date" name="start_date" value="${esc(vacation?.start_date || '')}" /></label>
    <label>Дата окончания<input class="input" type="date" name="end_date" value="${esc(vacation?.end_date || '')}" /></label>
    <p class="caption">Автоматические финансовые уведомления будут приостановлены. Созданные вами напоминания продолжат приходить.</p>
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    <button class="button primary" ${saving ? 'disabled' : ''}>Сохранить</button>
  </form>`;
}

type HistoryPreview = { period?: string; start_date?: string | null; end_date?: string | null; summary: { operations: number; drafts: number; goals: number; related_records: number } };

export function HistoryDeletionForm(stage: 'select' | 'preview' | 'confirm', period: string, preview: HistoryPreview | undefined, saving: boolean, error = ''): string {
  const labels: Array<[string, string]> = [['today', 'Сегодня'], ['last7', 'Последние 7 дней'], ['this_month', 'Этот месяц'], ['prev_month', 'Прошлый месяц'], ['this_year', 'Этот год'], ['all', 'Вся история']];
  if (stage === 'confirm' && preview) {
    return `<div class="form-grid destructive-panel">
      <strong>Удалить выбранные данные без возможности восстановления?</strong>
      <p class="caption">Операции: ${preview.summary.operations}. Черновики: ${preview.summary.drafts}. Цели: ${preview.summary.goals}.</p>
      <button class="button danger" data-action="privacy-history-delete" ${saving ? 'disabled' : ''}>Удалить навсегда</button>
      <button class="button secondary" data-action="privacy-history-back">Назад</button>
      ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    </div>`;
  }
  return `<div class="form-grid">
    ${stage === 'select' ? `<form class="form-grid" data-action="privacy-history-preview">
      <label>Период<select class="select" name="period">${labels.map(([key, label]) => `<option value="${key}" ${period === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
      <button class="button secondary" ${saving ? 'disabled' : ''}>Показать, что будет удалено</button>
    </form>` : ''}
    ${stage === 'preview' && preview ? `<div class="preview-panel">
      <strong>Будет удалено</strong>
      <div class="detail-row light"><span>Операции</span><strong>${preview.summary.operations}</strong></div>
      <div class="detail-row light"><span>Черновики</span><strong>${preview.summary.drafts}</strong></div>
      <div class="detail-row light"><span>Цели</span><strong>${preview.summary.goals}</strong></div>
      <button class="button danger" data-action="privacy-history-confirm">Продолжить удаление</button>
      <button class="button secondary" data-action="privacy-history-back">Изменить период</button>
    </div>` : ''}
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
  </div>`;
}

type AccountPreview = { summary: { financial_records: number; preferences: number; personal_workspaces: number }; confirmation_text: string; shared_workspace_note: string };

export function AccountDeletionForm(stage: 'account-info' | 'account-preview' | 'account-confirm' | 'deleted', preview: AccountPreview | undefined, saving: boolean, error = '', deletedMessage = ''): string {
  if (stage === 'deleted') {
    return `<div class="success-panel terminal-state"><strong>${esc(deletedMessage || 'Данные удалены. Вы можете закрыть КопиPaste.')}</strong><button class="button primary" data-action="close-miniapp">Закрыть</button></div>`;
  }
  if (stage === 'account-confirm' && preview) {
    return `<form class="form-grid destructive-panel" data-action="privacy-account-delete">
      <strong>Последнее подтверждение</strong>
      <label>Введите УДАЛИТЬ<input class="input" name="confirmation_text" autocomplete="off" /></label>
      <button class="button danger" ${saving ? 'disabled' : ''}>Удалить аккаунт и мои данные</button>
      <button class="button secondary" type="button" data-action="privacy-account-back">Назад</button>
      ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
    </form>`;
  }
  return `<div class="form-grid">
    ${stage === 'account-info' ? `<p class="caption">Личный аккаунт, финансовые данные и настройки будут удалены. Данные других участников общих пространств сохранятся.</p><button class="button danger" data-action="privacy-account-preview" ${saving ? 'disabled' : ''}>Проверить состав удаления</button>` : ''}
    ${stage === 'account-preview' && preview ? `<div class="preview-panel">
      <div class="detail-row light"><span>Финансовые записи</span><strong>${preview.summary.financial_records}</strong></div>
      <div class="detail-row light"><span>Настройки</span><strong>${preview.summary.preferences}</strong></div>
      <div class="detail-row light"><span>Личные пространства</span><strong>${preview.summary.personal_workspaces}</strong></div>
      <p class="caption">${esc(preview.shared_workspace_note)}</p>
      <button class="button danger" data-action="privacy-account-confirm">Продолжить</button>
      <button class="button secondary" data-action="privacy-account-back">Назад</button>
    </div>` : ''}
    ${error ? `<p class="form-error">${esc(error)}</p>` : ''}
  </div>`;
}

function addToHomeUnsupportedText(platform = ''): string {
  const value = platform.toLowerCase();
  if (value.includes('ios')) {
    return 'В этой версии Telegram автоматическое добавление на главный экран недоступно. Обновите Telegram и попробуйте снова.';
  }
  if (value.includes('android')) {
    return 'В этой версии Telegram для Android автоматическое добавление на главный экран недоступно. Обновите Telegram и попробуйте снова.';
  }
  return 'Добавление на главный экран доступно в поддерживаемых мобильных версиях Telegram.';
}

export function AdditionalMenu(profile: ProfileData | null, homeScreenStatus: 'unsupported' | 'unknown' | 'added' | 'missed' | 'pending' = 'unknown', platform = ''): string {
  const canAddToHome = homeScreenStatus === 'unknown' || homeScreenStatus === 'missed';
  return `
    <div class="form-grid">
      ${canAddToHome ? '<button class="button" data-action="add-to-home">Добавить на главный экран</button>' : ''}
      ${homeScreenStatus === 'added' ? '<div class="detail-row"><span>Главный экран</span><strong>Добавлено</strong></div>' : ''}
      ${homeScreenStatus === 'pending' ? '<div class="detail-row"><span>Главный экран</span><strong>Подтвердите добавление в Telegram</strong></div>' : ''}
      ${homeScreenStatus === 'unsupported' ? `<div class="detail-row"><span>Главный экран</span><strong>${esc(addToHomeUnsupportedText(platform))}</strong></div>` : ''}
      <button class="button" data-action="share-app">Поделиться Finuchet</button>
      ${profile?.help_url ? `<a class="button" href="${esc(profile.help_url)}" target="_blank" rel="noreferrer">Помощь</a>` : ''}
      <button class="button" data-action="report-issue">Сообщить о проблеме</button>
      ${profile?.links?.privacy ? `<a class="button" href="${esc(profile.links.privacy)}" target="_blank" rel="noreferrer">Privacy</a>` : ''}
      ${profile?.links?.terms ? `<a class="button" href="${esc(profile.links.terms)}" target="_blank" rel="noreferrer">Terms</a>` : ''}
      <div class="detail-row"><span>Версия</span><strong>${esc(profile?.version || '')}</strong></div>
    </div>
  `;
}

export function InfoPanel(_title: string, body: string): string {
  return `<div class="form-grid"><p class="caption">${esc(body)}</p><button class="button primary" data-action="close-sheet" type="button">Готово</button></div>`;
}

export function ExportForm(draft: Record<string, unknown> | undefined, preview: { period: { start_date: string; end_date: string }; count: number; totals_by_currency: Record<string, { income: string; expense: string; count: number }> } | undefined, sent = false, saving = false, error = ''): string {
  const preset = String(draft?.preset || 'month');
  const totals = preview?.totals_by_currency || {};
  return `
    <form class="form-grid" data-action="export-preview">
      <label class="field">Период<select class="select" name="preset">
        ${[
          ['today', 'Сегодня'],
          ['7', '7 дней'],
          ['14', '14 дней'],
          ['month', 'Этот месяц'],
          ['previous_month', 'Прошлый месяц'],
          ['year', 'Этот год'],
          ['previous_year', 'Прошлый год'],
          ['custom', 'Свой период'],
        ].map(([key, label]) => `<option value="${key}" ${preset === key ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <div class="custom-export-fields" ${preset === 'custom' ? '' : 'hidden'}>
        <label class="field">Дата начала<input class="input" type="date" name="start_date" value="${esc(String(draft?.start_date || ''))}" /></label>
        <label class="field">Дата конца<input class="input" type="date" name="end_date" value="${esc(String(draft?.end_date || ''))}" /></label>
      </div>
      <button class="button secondary" type="submit" ${saving ? 'disabled' : ''}>Показать предпросмотр</button>
    </form>
    ${preview ? `<div class="preview-panel">
      <strong>Экспорт операций</strong>
      <div class="detail-row light"><span>Период</span><strong>${esc(preview.period.start_date)} — ${esc(preview.period.end_date)}</strong></div>
      <div class="detail-row light"><span>Операций</span><strong>${preview.count}</strong></div>
      ${Object.entries(totals).map(([currency, total]) => `<div class="detail-row light"><span>${esc(currency)}</span><strong>Расходы ${formatMoneyString(total.expense, currency)} · Доходы ${formatMoneyString(total.income, currency)}</strong></div>`).join('') || '<p class="caption">За период операций нет.</p>'}
      <button class="button primary" data-action="export-send" type="button" ${saving ? 'disabled' : ''}>Получить XLSX в Telegram</button>
    </div>` : ''}
    ${sent ? '<div class="success-panel"><strong>Готово.</strong><p>Файл отправлен в чат с КопиPaste.</p></div>' : ''}
    ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
  `;
}
