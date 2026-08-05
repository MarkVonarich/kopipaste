import type { AppState } from '../types';
import { periodLabel, tabLabel } from '../state';
import { esc } from './ui';

function workspaceLabel(state: AppState): string {
  const workspace = state.boot?.workspaces.find((item) => item.workspace_id === state.workspaceId);
  if (state.workspaceId === 'all') return 'Все пространства';
  return workspace?.name || 'Личное';
}

export function AppShell(state: AppState, toolbar: string, screen: string): string {
  return `
    <main class="app">
      <header class="app-header">
        <div class="brand">
          <span>КопиPaste</span>
          <p>${esc(workspaceLabel(state))}</p>
        </div>
        <div class="screen-title">
          <h1>${esc(tabLabel(state.tab))}</h1>
          <p>${esc(periodLabel(state.period))}</p>
        </div>
        ${toolbar}
      </header>
      ${screen}
    </main>
  `;
}
