import { beforeEach, describe, expect, it } from 'vitest';
import { initialState, pickInitialWorkspace, TAB_ORDER } from '../src/state';

describe('app state', () => {
  beforeEach(() => localStorage.clear());

  it('opens on Home and keeps Home centered in five-tab navigation', () => {
    expect(initialState().tab).toBe('home');
    expect(TAB_ORDER).toEqual(['operations', 'analytics', 'home', 'plans', 'profile']);
  });

  it('selects the active concrete workspace when no preference is stored', () => {
    expect(pickInitialWorkspace([
      { workspace_id: 'all', name: 'Все', kind: 'all', role: 'viewer' },
      { workspace_id: 7, name: 'Семья', kind: 'group', role: 'member', active: true }
    ], undefined)).toBe(7);
  });

  it('migrates persisted last_30 to current_week global filters', () => {
    localStorage.setItem('finuchet-miniapp-state-v1', JSON.stringify({ period: { period: 'last_30' } }));

    const state = initialState();

    expect(state.globalFilters.period).toBe('current_week');
    expect(state.globalFilters.operation_type).toBe('all');
    expect(state.globalFilters.category).toBe('all');
  });
});
