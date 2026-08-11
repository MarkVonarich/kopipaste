import { esc } from './ui';

export function ConfirmDialog(
  operationId: number | string,
  context: string,
  title = 'Удалить операцию?',
  action = 'confirm-delete',
  error = '',
): string {
  return `
    <div class="sheet-backdrop" data-action="close-confirm">
      <div class="sheet confirm" data-sheet role="alertdialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="sheet-handle" aria-hidden="true"></div>
        <h2>${esc(title)}</h2>
        <p class="caption">${esc(context)}</p>
        ${error ? `<p class="error-text" role="alert">${esc(error)}</p>` : ''}
        <div class="actions action-stack">
          <button class="button danger" data-action="${action}" data-id="${operationId}">Удалить</button>
          <button class="button secondary" data-action="cancel-delete">Отмена</button>
        </div>
      </div>
    </div>
  `;
}
