import type { TabKey } from '../types';
import { TAB_ORDER, tabLabel } from '../state';

const ICONS: Record<TabKey, string> = { operations: '≡', analytics: '⌁', home: '●', plans: '◇', profile: '☉' };

export function BottomNavigation(activeTab: TabKey): string {
  return `
    <nav class="nav" aria-label="Основная навигация">
      ${TAB_ORDER.map((tab) => `
        <button data-tab="${tab}" class="${activeTab === tab ? 'active ' : ''}${tab === 'home' ? 'home-tab' : ''}" aria-label="${tabLabel(tab)}">
          <span class="nav-icon">${ICONS[tab]}</span>
          ${tabLabel(tab)}
        </button>
      `).join('')}
    </nav>
  `;
}
