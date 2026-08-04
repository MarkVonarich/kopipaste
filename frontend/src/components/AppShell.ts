import type { AppState } from '../types';
import { periodLabel, tabLabel } from '../state';

export function AppShell(state: AppState, toolbar: string, screen: string): string {
  return `
    <main class="app">
      <header class="topbar">
        <div class="brand">
          <h1>КопиPaste</h1>
          <p>${tabLabel(state.tab)} · ${periodLabel(state.period)}</p>
        </div>
      </header>
      ${toolbar}
      ${screen}
    </main>
  `;
}
