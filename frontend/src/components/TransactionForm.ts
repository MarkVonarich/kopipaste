import type { CategoryOption, Operation } from '../types';
import { esc } from './ui';

export function TransactionForm(options: CategoryOption[], params: {
  action: 'create-operation' | 'edit-operation';
  id?: number;
  type: 'Доходы' | 'Расходы';
  operation?: Operation | null;
  saving: boolean;
  error?: string;
}): string {
  const op = params.operation;
  const selected = op?.category || options[0]?.name || '';
  const isExpense = params.type === 'Расходы';
  return `
    ${params.error ? `<div class="error-state inline" data-state="save-error">${esc(params.error)}</div>` : ''}
    <form class="form-grid" data-action="${params.action}" ${params.id ? `data-id="${params.id}"` : ''}>
      <input type="hidden" name="type" value="${params.type}" />
      <label class="field">Сумма
        <input class="input amount-input" name="amount" inputmode="decimal" autocomplete="off" value="${esc(op?.amount || '')}" placeholder="0,00" required ${params.saving ? 'disabled' : ''} />
      </label>
      <label class="field">Категория
        <select class="select" name="category" required ${params.saving ? 'disabled' : ''}>
          ${options.map((item) => `<option value="${esc(item.name)}" ${item.name === selected ? 'selected' : ''}>${esc(item.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field">Описание
        <textarea class="textarea" name="description" maxlength="200" placeholder="${isExpense ? 'Например, кофе у метро' : 'Например, зарплата'}" required ${params.saving ? 'disabled' : ''}>${esc(op?.description || '')}</textarea>
      </label>
      <label class="field">Дата
        <input class="input" name="op_date" type="date" value="${esc(op?.op_date || new Date().toISOString().slice(0, 10))}" required ${params.saving ? 'disabled' : ''} />
      </label>
      <button class="button primary" type="submit" ${params.saving ? 'disabled' : ''}>${params.saving ? 'Сохраняем...' : 'Сохранить'}</button>
    </form>
  `;
}
