import type { TabKey } from '../types';
import { TAB_ORDER, tabLabel } from '../state';
import { icon } from './ui';

export function BottomNavigation(activeTab: TabKey): string {
  return `
    <nav class="nav" aria-label="Основная навигация">
      ${TAB_ORDER.map((tab) => `
        <button data-tab="${tab}" class="${activeTab === tab ? 'active ' : ''}${tab === 'home' ? 'home-tab' : ''}" aria-label="${tabLabel(tab)}" aria-current="${activeTab === tab ? 'page' : 'false'}">
          <span class="nav-icon">${icon(tab)}</span>
          <span>${tabLabel(tab)}</span>
        </button>
      `).join('')}
    </nav>
  `;
}
