export function ConfirmDialog(operationId: number, context: string): string {
  return `
    <div class="sheet-backdrop" data-action="close-confirm">
      <div class="sheet confirm" data-sheet>
        <h2>Удалить операцию?</h2>
        <p class="caption">${context}</p>
        <div class="actions">
          <button class="button danger" data-action="confirm-delete" data-id="${operationId}">Удалить</button>
          <button class="button" data-action="cancel-delete">Отмена</button>
        </div>
      </div>
    </div>
  `;
}
