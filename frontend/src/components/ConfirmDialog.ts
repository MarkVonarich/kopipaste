export function ConfirmDialog(operationId: number | string, context: string, title = 'Удалить операцию?', action = 'confirm-delete'): string {
  return `
    <div class="sheet-backdrop" data-action="close-confirm">
      <div class="sheet confirm" data-sheet>
        <h2>${title}</h2>
        <p class="caption">${context}</p>
        <div class="actions">
          <button class="button danger" data-action="${action}" data-id="${operationId}">Удалить</button>
          <button class="button" data-action="cancel-delete">Отмена</button>
        </div>
      </div>
    </div>
  `;
}
