import type { OperationsResponse } from '../api';
import { TransactionList } from './TransactionList';
import { EmptyPanel, SectionHeader, esc, icon } from './ui';

export function OperationsScreen(response: OperationsResponse | null, canWrite: boolean, search: string): string {
  const count = response?.items.length || 0;
  return `
    <section class="screen operations-screen">
      <div class="search-field">
        ${icon('search')}
        <input class="input" type="search" data-action="search" placeholder="Поиск по категории или описанию" value="${esc(search)}" aria-label="Поиск операций" />
      </div>
      <div class="quick-actions compact">
        <button class="button primary" data-action="open-add" data-kind="expense" ${canWrite ? '' : 'disabled'}>${icon('expense')}Расход</button>
        <button class="button secondary" data-action="open-add" data-kind="income" ${canWrite ? '' : 'disabled'}>${icon('income')}Доход</button>
      </div>
      ${SectionHeader('Операции', count ? `${count} на экране` : 'История за выбранный период')}
      ${(response?.items || []).length
        ? TransactionList(response?.items || [], 'Список пуст.')
        : EmptyPanel('Нет операций', search ? 'Список пуст. Попробуйте изменить поиск или период.' : 'Список пуст. Запишите первую операцию за этот период.')}
      ${response?.has_more ? '<button class="button secondary" data-action="load-more">Показать ещё</button>' : ''}
    </section>
  `;
}
