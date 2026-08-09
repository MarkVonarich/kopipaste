import type { CategoryOption, NotificationPreferences, PremiumInfo, ProfileSection, ThemeMode, Workspace } from '../types';
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
};

const SECTIONS: Array<[ProfileSection, string]> = [
  ['user', 'Пользователь'],
  ['appearance', 'Внешний вид'],
  ['workspaces', 'Пространства'],
  ['notifications', 'Уведомления'],
  ['export-data', 'Экспорт и данные'],
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

function panel(section: ProfileSection, active: ProfileSection, body: string): string {
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

export function ProfileScreen(profile: ProfileData | null, workspaces: Workspace[], activeTheme: ThemeMode, activeSection: ProfileSection): string {
  const prefs = profile?.notifications;
  const visibleWorkspaces = profile?.workspaces || workspaces.filter((item) => item.workspace_id !== 'all');
  const activeWorkspace = visibleWorkspaces.find((workspace) => workspace.active);
  return `
    <section class="screen profile-screen">
      ${SectionHeader('Настройки', 'Профиль, данные и внешний вид', `<button class="icon-button secondary" data-action="open-menu" aria-label="Открыть дополнительное меню">${icon('more')}</button>`)}
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
        ${panel('workspaces', activeSection, `
          <div class="settings-list">
            ${visibleWorkspaces.map((workspace) => row(workspace.name, `${workspace.role}${workspace.active ? ' · активно' : ''}`, workspace.kind, workspace.role === 'owner' || workspace.role === 'admin' ? 'profile-workspace-open' : 'profile-active-workspace-set', `data-id="${esc(workspace.workspace_id)}"`)).join('') || '<p class="caption">Нет доступных пространств.</p>'}
          </div>
        `)}
        ${panel('notifications', activeSection, `
          <div class="settings-list">
            ${prefs ? `
              ${notificationBlock('Ежедневные уведомления', 'daily', prefs.daily_notifications?.enabled ?? (prefs.morning_enabled || prefs.evening_enabled), 'Короткие сообщения утром и вечером помогают не забывать записывать операции.', `Утро ${prefs.daily_notifications?.morning_time || prefs.morning_time} · Вечер ${prefs.daily_notifications?.evening_time || prefs.evening_time}`)}
              ${notificationBlock('Планы и контроль', 'plans', prefs.plans_control?.enabled ?? (prefs.limit_alerts_enabled || prefs.budget_alerts_enabled || prefs.goal_notifications_enabled || (prefs.subscription_alerts_enabled ?? true) || (prefs.recurring_spend_alerts_enabled ?? true)), 'Предупреждает о лимитах, бюджетах, целях и важных регулярных расходах.')}
              ${notificationBlock('Отчёты', 'reports', prefs.reports?.enabled ?? (prefs.weekly_reports_enabled || prefs.monthly_reports_enabled), 'Присылает финансовую сводку за неделю и месяц.')}
              ${row('Тихие часы', prefs.quiet_hours?.enabled || prefs.quiet_hours_enabled ? `${prefs.quiet_hours?.start || prefs.quiet_hours_start || '22:30'}–${prefs.quiet_hours?.end || prefs.quiet_hours_end || '08:00'}` : 'Выкл', 'В это время автоматические сообщения не будут вас беспокоить.', 'quiet-hours-open')}
            ` : '<p class="caption">Настройки недоступны.</p>'}
          </div>
        `)}
        ${panel('export-data', activeSection, row('Экспорт', profile?.export?.status || 'Открыть', profile?.export?.privacy_note || 'Экспорт использует существующий flow.', 'export-open'))}
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

export function AdditionalMenu(profile: ProfileData | null, canAddToHome: boolean): string {
  return `
    <div class="form-grid">
      ${canAddToHome ? '<button class="button" data-action="add-to-home">Добавить на главный экран</button>' : ''}
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
