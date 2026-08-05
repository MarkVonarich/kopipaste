import { formatMoneyString } from '../money';
import type { Operation } from '../types';
import { EmptyState } from './States';
import { esc } from './ui';

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
          <span class="operation-mark ${operationKind(op)}" aria-hidden="true">${esc((op.category || op.description || 'О').trim().slice(0, 1).toUpperCase())}</span>
          <span class="operation-copy">
            <span class="operation-title">${esc(op.category || op.description || 'Операция')}</span>
            <span class="operation-meta">${op.description ? `${esc(op.description)} · ` : ''}${esc(op.op_date)}${op.workspace_name ? ` · ${esc(op.workspace_name)}` : ''}</span>
          </span>
          <span class="operation-amount ${operationKind(op)}">${operationKind(op) === 'income' ? '+' : '-'}${esc(operationAmount(op))}</span>
        </button>
      `).join('')}
    </div>
  `;
}
