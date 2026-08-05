import { EmptyPanel, esc } from './ui';

export function LoadingState(label = 'Загрузка...'): string {
  return `
    <div class="loading skeleton-stack" data-state="loading" aria-live="polite">
      <span>${esc(label)}</span>
      <i></i><i></i><i></i>
    </div>
  `;
}

export function EmptyState(label: string): string {
  return EmptyPanel('Пока пусто', label);
}

export function ErrorState(label: string): string {
  return `<div class="error-state" data-state="error" role="alert"><h2>Что-то не получилось</h2><p>${esc(label)}</p><button class="button secondary" data-action="retry">Повторить</button></div>`;
}

export function AccessDeniedState(label = 'Нет доступа к этому пространству.'): string {
  return `<div class="error-state" data-state="access-denied"><h2>Нет доступа</h2><p>${esc(label)}</p></div>`;
}

export function SaveSuccess(label = 'Сохранено'): string {
  return `<div class="toast" data-state="save-success">${label}</div>`;
}

export function SaveError(label: string): string {
  return `<div class="error-state inline" data-state="save-error">${esc(label)}</div>`;
}
