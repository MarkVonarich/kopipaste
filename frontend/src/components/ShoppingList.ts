import type { ShoppingItem } from '../types';
import { esc } from './ui';

export function ShoppingList(items: ShoppingItem[], readOnly: boolean, saving = false, confirmClear = false, error = '', note = '', editingItemId?: number, editingText?: string): string {
  const activeItems = items.filter((item) => !item.completed);
  const completedItems = items.filter((item) => item.completed);
  const completed = completedItems.length;
  const rows = (values: ShoppingItem[]) => values.map((item) => editingItemId === item.id ? `<form class="shopping-item shopping-edit-form" data-action="shopping-edit-save" data-id="${item.id}">
    <input class="input compact" name="text" maxlength="200" value="${esc(editingText ?? item.text)}" autocomplete="off" required aria-label="Название покупки" />
    <div class="shopping-item-actions"><button class="button primary compact" type="submit" ${saving ? 'disabled' : ''}>Сохранить</button><button class="button text compact" type="button" data-action="shopping-edit-cancel" ${saving ? 'disabled' : ''}>Отмена</button></div>
  </form>` : `<div class="shopping-item ${item.completed ? 'completed' : ''}" data-id="${item.id}">
    <button class="shopping-check" type="button" data-action="shopping-toggle" data-id="${item.id}" data-completed="${item.completed}" aria-label="${item.completed ? 'Вернуть' : 'Отметить купленным'}" ${readOnly || saving ? 'disabled' : ''}>${item.completed ? '✓' : ''}</button>
    <span>${esc(item.text)}</span>
    ${readOnly ? '' : `<div class="shopping-item-actions"><button class="icon-button" type="button" data-action="shopping-edit" data-id="${item.id}" aria-label="Изменить" ${saving ? 'disabled' : ''}>✎</button><button class="icon-button shopping-delete" type="button" data-action="shopping-delete" data-id="${item.id}" aria-label="Удалить" ${saving ? 'disabled' : ''}>×</button></div>`}
  </div>`).join('');
  return `<div class="shopping-list" data-testid="shopping-list">
    ${note ? `<p class="caption shopping-scope-note">${esc(note)}</p>` : readOnly ? '<p class="caption">Список доступен только для чтения.</p>' : `<form class="shopping-add" data-action="shopping-add">
      <input class="input" name="text" maxlength="200" placeholder="Что купить?" autocomplete="off" required />
      <button class="button primary" ${saving ? 'disabled' : ''}>Добавить</button>
    </form>`}
    <div class="shopping-items">
      <h3>Нужно купить</h3>
      ${rows(activeItems) || '<p class="caption">Список пока пуст.</p>'}
      ${completedItems.length ? `<h3>Куплено</h3>${rows(completedItems)}` : ''}
    </div>
    ${!readOnly && completed ? (confirmClear
      ? `<div class="confirm-inline"><span>Удалить все отмеченные?</span><button class="button danger" data-action="shopping-clear-confirm" type="button">Удалить</button><button class="button text" data-action="shopping-clear-cancel" type="button">Отмена</button></div>`
      : '<button class="button text" data-action="shopping-clear" type="button">Очистить купленное</button>') : ''}
    ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
  </div>`;
}
