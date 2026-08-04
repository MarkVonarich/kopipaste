import { formatMoneyString } from '../money';
import type { Operation } from '../types';
import { EmptyState } from './States';

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function operationAmount(op: Operation): string {
  return op.amount_text || formatMoneyString(op.amount, op.currency);
}

function operationKind(op: Operation): 'income' | 'expense' {
  return op.type === 'Доходы' ? 'income' : 'expense';
}

export function TransactionList(items: Operation[], emptyText = 'Операций пока нет.'): string {
  if (!items.length) return EmptyState(emptyText);
  return `
    <div class="operation-list">
      ${items.map((op) => `
        <button class="operation-row" data-action="operation-detail" data-id="${op.id}">
          <span>
            <span class="operation-title">${esc(op.category || op.description || 'Операция')}</span>
            <span class="operation-meta">${esc(op.op_date)}${op.workspace_name ? ` · ${esc(op.workspace_name)}` : ''}${op.description ? ` · ${esc(op.description)}` : ''}</span>
          </span>
          <span class="operation-amount ${operationKind(op)}">${operationKind(op) === 'income' ? '+' : '-'}${esc(operationAmount(op))}</span>
        </button>
      `).join('')}
    </div>
  `;
}
