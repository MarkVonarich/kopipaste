export function LoadingState(label = 'Загрузка...'): string {
  return `<div class="loading" data-state="loading">${label}</div>`;
}

export function EmptyState(label: string): string {
  return `<div class="empty" data-state="empty">${label}</div>`;
}

export function ErrorState(label: string): string {
  return `<div class="error" data-state="error"><p>${label}</p><button class="button" data-action="retry">Повторить</button></div>`;
}

export function AccessDeniedState(label = 'Нет доступа к этому пространству.'): string {
  return `<div class="error" data-state="access-denied">${label}</div>`;
}

export function SaveSuccess(label = 'Сохранено'): string {
  return `<div class="toast" data-state="save-success">${label}</div>`;
}

export function SaveError(label: string): string {
  return `<div class="error" data-state="save-error">${label}</div>`;
}
