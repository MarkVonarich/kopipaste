import type { AppState } from '../types';
import { tabLabel } from '../state';
import { esc, icon } from './ui';

export function AppShell(state: AppState, toolbar: string, screen: string): string {
  return `
    <main class="app">
      <header class="app-header">
        <div class="brand">
          <span>КопиPaste</span>
          <button class="icon-button secondary" data-action="open-menu" type="button" aria-label="Открыть дополнительное меню">${icon('more')}</button>
        </div>
        <div class="screen-title">
          <h1>${esc(tabLabel(state.tab))}</h1>
        </div>
        ${toolbar}
      </header>
      ${screen}
    </main>
  `;
}
