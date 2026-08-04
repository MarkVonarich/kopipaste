import type { OperationsResponse } from '../api';
import { TransactionList } from './TransactionList';

export function OperationsScreen(response: OperationsResponse | null, canWrite: boolean, search: string): string {
  return `
    <section class="screen">
      <input class="input" type="search" data-action="search" placeholder="Поиск" value="${search}" />
      <div class="actions">
        <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite ? '' : 'disabled'}>− Расход</button>
        <button class="button" data-action="open-add" data-kind="income" ${canWrite ? '' : 'disabled'}>+ Доход</button>
      </div>
      ${TransactionList(response?.items || [], 'Список пуст.')}
      ${response?.has_more ? '<button class="button" data-action="load-more">Ещё</button>' : ''}
    </section>
  `;
}
