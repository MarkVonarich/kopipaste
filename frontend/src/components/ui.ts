import type { TabKey } from '../types';

export function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function icon(name: TabKey | 'plus' | 'search' | 'more' | 'chevron' | 'expense' | 'income' | 'empty' | 'grip'): string {
  const common = 'viewBox="0 0 24 24" aria-hidden="true" focusable="false"';
  const paths: Record<string, string> = {
    operations: '<path d="M5 7h14M5 12h14M5 17h9" />',
    analytics: '<path d="M5 19V9m7 10V5m7 14v-7" /><path d="M4 19h17" />',
    home: '<path d="M4 11.5 12 5l8 6.5V20h-5v-5H9v5H4z" />',
    plans: '<path d="M5 12h14" /><path d="M12 5v14" /><circle cx="12" cy="12" r="7" />',
    profile: '<circle cx="12" cy="8" r="4" /><path d="M5 20c1.4-3.2 4-5 7-5s5.6 1.8 7 5" />',
    plus: '<path d="M12 5v14M5 12h14" />',
    search: '<circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" />',
    more: '<circle cx="6" cy="12" r="1.3" /><circle cx="12" cy="12" r="1.3" /><circle cx="18" cy="12" r="1.3" />',
    chevron: '<path d="m9 6 6 6-6 6" />',
    expense: '<path d="M5 12h14" />',
    income: '<path d="M12 5v14M5 12h14" />',
    empty: '<path d="M6 7h12v12H6z" /><path d="M9 4h6v3H9z" />',
    grip: '<circle cx="9" cy="7" r="1" /><circle cx="15" cy="7" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="9" cy="17" r="1" /><circle cx="15" cy="17" r="1" />',
  };
  return `<svg class="ui-icon" ${common}>${paths[name] || paths.empty}</svg>`;
}

export function SectionHeader(title: string, subtitle = '', action = ''): string {
  return `
    <div class="section-heading">
      <div>
        <h2>${esc(title)}</h2>
        ${subtitle ? `<p>${esc(subtitle)}</p>` : ''}
      </div>
      ${action}
    </div>
  `;
}

export function Chip(label: string, tone: 'neutral' | 'accent' | 'income' | 'expense' | 'warning' = 'neutral'): string {
  return `<span class="chip ${tone}">${esc(label)}</span>`;
}

export function ProgressBar(percent: number, label: string, tone = ''): string {
  const width = Math.min(100, Math.max(0, percent));
  return `
    <div class="progress-block">
      <div class="progress-meta"><span>${esc(label)}</span><strong>${Math.round(percent)}%</strong></div>
      <div class="progress ${tone}" aria-label="${esc(label)} ${Math.round(percent)}%"><span style="width:${width}%"></span></div>
    </div>
  `;
}

export function EmptyPanel(title: string, body: string, action = ''): string {
  return `
    <div class="empty-state" data-state="empty">
      <span class="empty-mark">${icon('empty')}</span>
      <h2>${esc(title)}</h2>
      <p>${esc(body)}</p>
      ${action}
    </div>
  `;
}
