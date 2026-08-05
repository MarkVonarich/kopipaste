import type { CategoryOption, NotificationPreferences, PremiumInfo, ThemeMode, Workspace } from '../types';
import { SectionHeader, esc, icon } from './ui';

type ProfileData = {
  theme: ThemeMode;
  currency: string;
  timezone: string;
  version: string;
  help_url?: string;
  links?: { privacy?: string | null; terms?: string | null };
  workspaces?: Workspace[];
  categories?: { expense: CategoryOption[]; income: CategoryOption[] };
  notifications?: NotificationPreferences;
  premium?: PremiumInfo;
  export?: { available: boolean; status: string; presets: string[]; privacy_note: string };
};

function settingsRow(label: string, value = '', description = '', action = '', attrs = ''): string {
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

function settingsGroup(title: string, body: string): string {
  return `
    <section class="settings-group">
      <h2>${esc(title)}</h2>
      <div class="settings-list">${body}</div>
    </section>
  `;
}

function toggle(label: string, key: string, enabled: boolean): string {
  return settingsRow(label, enabled ? 'Вкл' : 'Выкл', '', 'notification-toggle', `data-key="${key}"`);
}

export function ProfileScreen(profile: ProfileData | null, workspaces: Workspace[], activeTheme: ThemeMode): string {
  const prefs = profile?.notifications;
  const cats = profile?.categories;
  const visibleWorkspaces = profile?.workspaces || workspaces.filter((item) => item.workspace_id !== 'all');
  return `
    <section class="screen profile-screen">
      ${SectionHeader('Настройки', 'Профиль, данные и внешний вид', `<button class="icon-button secondary" data-action="open-menu" aria-label="Открыть дополнительное меню">${icon('more')}</button>`)}
      ${settingsGroup('Пользователь', `
        ${settingsRow('Валюта', profile?.currency || 'RUB')}
        ${settingsRow('Часовой пояс', profile?.timezone || prefs?.timezone || 'Не выбран')}
        ${settingsRow('Пространства', String(visibleWorkspaces.length))}
      `)}
      <section class="settings-group">
        <h2>Внешний вид</h2>
        <div class="segmented" data-action="theme" role="tablist" aria-label="Тема">
          ${(['telegram', 'light', 'dark'] as ThemeMode[]).map((theme) => `<button data-theme="${theme}" class="${activeTheme === theme ? 'active' : ''}">${theme === 'telegram' ? 'Telegram' : theme === 'light' ? 'Светлая' : 'Тёмная'}</button>`).join('')}
        </div>
      </section>
      ${settingsGroup('Пространства', visibleWorkspaces.map((workspace) => settingsRow(workspace.name, `${workspace.role}${workspace.read_only ? ' · read-only' : ''}`, workspace.kind)).join('') || '<p class="caption">Нет доступных пространств.</p>')}
      <section class="settings-group">
        <h2>Категории и уведомления</h2>
        <p class="caption">Категории используют существующие правила Telegram-бота.</p>
        <div class="chips">
          ${(cats?.expense || []).slice(0, 8).map((cat) => `<span>${esc(cat.name)}</span>`).join('') || '<span>Расходы</span>'}
          ${(cats?.income || []).slice(0, 4).map((cat) => `<span>${esc(cat.name)}</span>`).join('')}
        </div>
        <h2>Уведомления</h2>
        <div class="settings-list">
        ${prefs ? `
          ${toggle('Утро', 'morning', prefs.morning_enabled)}
          ${toggle('Вечер', 'evening', prefs.evening_enabled)}
          ${toggle('Лимиты', 'limits', prefs.limit_alerts_enabled)}
          ${toggle('Цели', 'goals', prefs.goal_notifications_enabled)}
          ${toggle('Челленджи', 'challenges', prefs.challenge_notifications_enabled)}
          ${toggle('Еженедельные отчёты', 'weekly', prefs.weekly_reports_enabled)}
          ${toggle('Ежемесячные отчёты', 'monthly', prefs.monthly_reports_enabled)}
          ${settingsRow('Тихие часы', prefs.quiet_hours_enabled ? `${prefs.quiet_hours_start}–${prefs.quiet_hours_end}` : 'Выкл', '', 'notification-quiet')}
        ` : '<p class="caption">Настройки недоступны.</p>'}
        </div>
      </section>
      ${settingsGroup('Экспорт и данные', `
        ${settingsRow('Экспорт', profile?.export?.status || 'Открыть', profile?.export?.privacy_note || 'Экспорт использует существующий flow.', 'export-open')}
      `)}
      ${settingsGroup('Premium', settingsRow(profile?.premium?.title || 'Premium', profile?.premium?.status || 'info', profile?.premium?.description || 'Информационный раздел.', 'premium-open'))}
      ${settingsGroup('Помощь', `
        ${profile?.help_url ? `<a class="settings-row" href="${esc(profile.help_url)}" target="_blank" rel="noreferrer"><span><strong>Помощь</strong></span><em>${icon('chevron')}</em></a>` : ''}
        ${settingsRow('Версия', profile?.version || '')}
      `)}
      ${settingsGroup('Юридическая информация', `
        ${profile?.links?.privacy ? `<a class="settings-row" href="${esc(profile.links.privacy)}" target="_blank" rel="noreferrer"><span><strong>Privacy</strong></span><em>${icon('chevron')}</em></a>` : '<p class="caption">Документ пока недоступен</p>'}
        ${profile?.links?.terms ? `<a class="settings-row" href="${esc(profile.links.terms)}" target="_blank" rel="noreferrer"><span><strong>Terms</strong></span><em>${icon('chevron')}</em></a>` : ''}
      `)}
    </section>
  `;
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
