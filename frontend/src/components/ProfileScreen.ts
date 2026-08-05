import type { CategoryOption, NotificationPreferences, PremiumInfo, ThemeMode, Workspace } from '../types';

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

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toggle(label: string, key: string, enabled: boolean): string {
  return `
    <button class="detail-row toggle-button" data-action="notification-toggle" data-key="${key}">
      <span>${esc(label)}</span><strong>${enabled ? 'Вкл' : 'Выкл'}</strong>
    </button>
  `;
}

export function ProfileScreen(profile: ProfileData | null, workspaces: Workspace[], activeTheme: ThemeMode): string {
  const prefs = profile?.notifications;
  const cats = profile?.categories;
  const visibleWorkspaces = profile?.workspaces || workspaces.filter((item) => item.workspace_id !== 'all');
  return `
    <section class="screen">
      <div class="section-header">
        <strong>Профиль</strong>
        <button class="icon-button secondary" data-action="open-menu" aria-label="Меню">⋯</button>
      </div>
      <div class="panel detail-grid">
        <div class="detail-row"><span>Валюта</span><strong>${esc(profile?.currency || 'RUB')}</strong></div>
        <div class="detail-row"><span>Часовой пояс</span><strong>${esc(profile?.timezone || prefs?.timezone || '')}</strong></div>
        <div class="detail-row"><span>Пространства</span><strong>${visibleWorkspaces.length}</strong></div>
        <div class="detail-row"><span>Версия</span><strong>${esc(profile?.version || '')}</strong></div>
      </div>
      <div class="panel">
        <strong>Тема</strong>
        <div class="segmented" data-action="theme">
          ${(['telegram', 'light', 'dark'] as ThemeMode[]).map((theme) => `<button data-theme="${theme}" class="${activeTheme === theme ? 'active' : ''}">${theme === 'telegram' ? 'Telegram' : theme === 'light' ? 'Светлая' : 'Тёмная'}</button>`).join('')}
        </div>
      </div>
      <div class="panel">
        <strong>Пространства и участники</strong>
        ${visibleWorkspaces.map((workspace) => `<div class="detail-row"><span>${esc(workspace.name)}</span><strong>${esc(workspace.role)}${workspace.read_only ? ' · read-only' : ''}</strong></div>`).join('') || '<p class="caption">Нет доступных пространств.</p>'}
      </div>
      <div class="panel">
        <strong>Категории</strong>
        <p class="caption">Используются существующие категории и защищённые правила Telegram-бота.</p>
        <div class="chips">
          ${(cats?.expense || []).slice(0, 8).map((cat) => `<span>${esc(cat.name)}</span>`).join('') || '<span>Расходы</span>'}
          ${(cats?.income || []).slice(0, 4).map((cat) => `<span>${esc(cat.name)}</span>`).join('')}
        </div>
      </div>
      <div class="panel">
        <strong>Уведомления</strong>
        ${prefs ? `
          ${toggle('Утро', 'morning', prefs.morning_enabled)}
          ${toggle('Вечер', 'evening', prefs.evening_enabled)}
          ${toggle('Лимиты', 'limits', prefs.limit_alerts_enabled)}
          ${toggle('Цели', 'goals', prefs.goal_notifications_enabled)}
          ${toggle('Челленджи', 'challenges', prefs.challenge_notifications_enabled)}
          ${toggle('Еженедельные отчёты', 'weekly', prefs.weekly_reports_enabled)}
          ${toggle('Ежемесячные отчёты', 'monthly', prefs.monthly_reports_enabled)}
          <button class="detail-row toggle-button" data-action="notification-quiet"><span>Тихие часы</span><strong>${prefs.quiet_hours_enabled ? `${esc(prefs.quiet_hours_start)}–${esc(prefs.quiet_hours_end)}` : 'Выкл'}</strong></button>
        ` : '<p class="caption">Настройки недоступны.</p>'}
      </div>
      <div class="panel">
        <strong>Premium</strong>
        <p class="caption">${esc(profile?.premium?.description || 'Информационный раздел.')}</p>
        <button class="button" data-action="premium-open">Подробнее</button>
      </div>
      <div class="panel">
        <strong>Экспорт и данные</strong>
        <p class="caption">${esc(profile?.export?.privacy_note || 'Экспорт использует существующий flow.')}</p>
        <button class="button" data-action="export-open">Открыть экспорт</button>
      </div>
      <div class="panel">
        <strong>Помощь и документы</strong>
        ${profile?.help_url ? `<a class="button" href="${esc(profile.help_url)}" target="_blank" rel="noreferrer">Помощь</a>` : ''}
        ${profile?.links?.privacy ? `<a class="button" href="${esc(profile.links.privacy)}" target="_blank" rel="noreferrer">Privacy</a>` : '<p class="caption">Документ пока недоступен</p>'}
        ${profile?.links?.terms ? `<a class="button" href="${esc(profile.links.terms)}" target="_blank" rel="noreferrer">Terms</a>` : ''}
      </div>
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
