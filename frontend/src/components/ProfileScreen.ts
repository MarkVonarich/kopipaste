import type { ThemeMode, Workspace } from '../types';

type ProfileData = {
  theme: ThemeMode;
  currency: string;
  timezone: string;
  version: string;
  links?: { privacy?: string | null; terms?: string | null };
};

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function ProfileScreen(profile: ProfileData | null, workspaces: Workspace[], activeTheme: ThemeMode): string {
  return `
    <section class="screen">
      <div class="panel detail-grid">
        <div class="detail-row"><span>Валюта</span><strong>${esc(profile?.currency || 'RUB')}</strong></div>
        <div class="detail-row"><span>Часовой пояс</span><strong>${esc(profile?.timezone || '')}</strong></div>
        <div class="detail-row"><span>Пространства</span><strong>${workspaces.filter((item) => item.workspace_id !== 'all').length}</strong></div>
        <div class="detail-row"><span>Версия</span><strong>${esc(profile?.version || '')}</strong></div>
      </div>
      <div class="panel">
        <strong>Тема</strong>
        <div class="segmented" data-action="theme">
          ${(['telegram', 'light', 'dark'] as ThemeMode[]).map((theme) => `<button data-theme="${theme}" class="${activeTheme === theme ? 'active' : ''}">${theme === 'telegram' ? 'Telegram' : theme === 'light' ? 'Светлая' : 'Тёмная'}</button>`).join('')}
        </div>
      </div>
      <div class="panel">
        <strong>Документы</strong>
        ${profile?.links?.privacy ? `<a class="button" href="${esc(profile.links.privacy)}" target="_blank" rel="noreferrer">Privacy</a>` : '<p class="caption">Документ пока недоступен</p>'}
        ${profile?.links?.terms ? `<a class="button" href="${esc(profile.links.terms)}" target="_blank" rel="noreferrer">Terms</a>` : ''}
      </div>
    </section>
  `;
}
