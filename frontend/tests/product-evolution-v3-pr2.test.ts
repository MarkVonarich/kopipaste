import { describe, expect, it } from 'vitest';
import { HomeScreen } from '../src/components/HomeScreen';
import { HomeSettingsForm } from '../src/components/HomeSettings';
import { ShoppingList } from '../src/components/ShoppingList';
import mainSource from '../src/main.ts?raw';
import type { HomePreferences } from '../src/types';

const widgets: HomePreferences['widgets'] = [
  { key: 'financial_result', title: 'Финансовый результат', description: 'Итог', layout: 'wide', default_enabled: true, default_order: 0 },
  { key: 'goals', title: 'Цели', description: 'Цели', layout: 'compact', default_enabled: true, default_order: 1 },
  { key: 'limits', title: 'Лимиты', description: 'Лимиты', layout: 'compact', default_enabled: true, default_order: 2 },
  { key: 'shopping_list', title: 'Список покупок', description: 'Покупки', layout: 'compact', default_enabled: true, default_order: 3 },
  { key: 'whats_new', title: 'Новое в КопиPaste', description: 'Новости', layout: 'wide', default_enabled: true, default_order: 4 },
];

const base: any = {
  period: { key: 'current_month', start_date: '2026-08-01', end_date: '2026-08-11' },
  workspace_scope: 10,
  aggregation_available: true,
  totals_by_currency: {},
  recent_operations: [],
  home_widgets: widgets,
};

describe('Product Evolution v3 PR2', () => {
  it('renders only enabled widgets in saved order without placeholders', () => {
    const html = HomeScreen({
      ...base,
      home_preferences: { order: ['limits', 'goals', 'financial_result', 'shopping_list', 'whats_new'], enabled: ['limits', 'financial_result'] },
      limit_items: [{ kind: 'limit', title: 'Продукты', description: 'Норма', percent: 40 }],
    }, [], 'RUB', true);
    expect(html.indexOf('data-widget="limits"')).toBeLessThan(html.indexOf('data-widget="financial_result"'));
    expect(html).not.toContain('data-widget="goals"');
    expect(html).not.toContain('data-widget="shopping_list"');
  });

  it('supports an intentionally empty Home', () => {
    const html = HomeScreen({ ...base, home_preferences: { order: widgets.map((item) => item.key), enabled: [] } }, [], 'RUB', true);
    expect(html).toContain('Главная настроена минимально');
    expect(html).toContain('data-action="home-settings-open"');
  });

  it('keeps goals and limits as separate widgets', () => {
    const html = HomeScreen({
      ...base,
      home_preferences: { order: ['goals', 'limits'], enabled: ['goals', 'limits'] },
      goal_items: [{ kind: 'goal', title: 'Отпуск', description: 'В плане', percent: 20 }],
      limit_items: [{ kind: 'limit', title: 'Кафе', description: 'Норма', percent: 60 }],
    }, [], 'RUB', true);
    expect(html).toContain('data-widget="goals"');
    expect(html).toContain('data-widget="limits"');
    expect(html).toContain('Отпуск');
    expect(html).toContain('Кафе');
  });

  it('shows concrete-workspace selection for both Goals and Limits in all scope', () => {
    const html = HomeScreen({
      ...base,
      workspace_scope: 'all',
      home_preferences: { order: ['goals', 'limits'], enabled: ['goals', 'limits'] },
      goal_items: [{ kind: 'empty', title: 'Выберите пространство', description: 'Цели доступны для одного пространства.', target_mode: 'goals' }],
      limit_items: [{ kind: 'empty', title: 'Выберите пространство', description: 'Лимиты доступны для одного пространства.', target_mode: 'limits' }],
    }, [], 'RUB', false);

    expect(html.match(/Выберите пространство/g)).toHaveLength(2);
    expect(html).toContain('Цели доступны для одного пространства.');
    expect(html).toContain('Лимиты доступны для одного пространства.');
    expect(html).not.toContain('Нет активных лимитов');
  });

  it('keeps the genuine empty Limits state for a concrete workspace', () => {
    const html = HomeScreen({
      ...base,
      workspace_scope: 10,
      home_preferences: { order: ['limits'], enabled: ['limits'] },
      limit_items: [],
    }, [], 'RUB', true);

    expect(html).toContain('Нет активных лимитов');
    expect(html).toContain('Добавьте лимит в Планах.');
  });

  it('keeps the genuine empty Goals state for a concrete workspace', () => {
    const html = HomeScreen({
      ...base,
      workspace_scope: 10,
      home_preferences: { order: ['goals'], enabled: ['goals'] },
      goal_items: [],
    }, [], 'RUB', true);

    expect(html).toContain('Нет активных целей');
    expect(html).toContain('Добавьте цель в Планах.');
  });

  it('renders mobile-safe drag handles and keyboard fallback controls', () => {
    const preferences = { widgets, order: widgets.map((item) => item.key), enabled: widgets.map((item) => item.key) };
    const html = HomeSettingsForm(preferences, preferences.order, preferences.enabled);
    expect(html).toContain('data-action="home-drag"');
    expect(html).toContain('data-direction="up"');
    expect(html).toContain('data-direction="down"');
    expect(html).toContain('data-action="home-widget-toggle"');
  });

  it('renders announcements with dismiss, typed action and manual navigation only', () => {
    const html = HomeScreen({
      ...base,
      home_preferences: { order: ['whats_new'], enabled: ['whats_new'] },
      announcements: [{ id: 'home-v1', family: 'home', kind: 'feature', released_on: '2026-08-11', title: 'Настройте главную под себя', description: 'Описание', action: { type: 'OPEN_HOME_SETTINGS', label: 'Настроить' } }],
    }, [], 'RUB', true);
    expect(html).toContain('data-action="announcement-dismiss"');
    expect(html).toContain('data-target="OPEN_HOME_SETTINGS"');
    expect(html).not.toContain('autoplay');
  });

  it('renders shopping write and read-only states', () => {
    const item = { id: 1, workspace_id: 10, text: 'Молоко', completed: false, created_at: '2026-08-11', updated_at: '2026-08-11' };
    expect(ShoppingList([item], false)).toContain('data-action="shopping-add"');
    const readOnly = ShoppingList([item], true);
    expect(readOnly).toContain('только для чтения');
    expect(readOnly).not.toContain('data-action="shopping-delete"');
  });

  it('separates active and completed shopping items and escapes user text', () => {
    const html = ShoppingList([
      { id: 1, workspace_id: 10, text: '<script>alert(1)</script>', completed: false, created_at: '2026-08-11', updated_at: '2026-08-11' },
      { id: 2, workspace_id: 10, text: 'Хлеб', completed: true, created_at: '2026-08-11', updated_at: '2026-08-11' },
    ], false);
    expect(html).toContain('Нужно купить');
    expect(html).toContain('Куплено');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
  });

  it('uses inline shopping edit controls without a browser prompt', () => {
    const item = { id: 1, workspace_id: 10, text: '<b>Молоко</b>', completed: false, created_at: '2026-08-11', updated_at: '2026-08-11' };
    const html = ShoppingList([item], false, false, false, '', '', 1, item.text);

    expect(html).toContain('data-action="shopping-edit-save"');
    expect(html).toContain('maxlength="200"');
    expect(html).toContain('&lt;b&gt;Молоко&lt;/b&gt;');
    expect(mainSource).not.toContain('window.prompt');
  });

  it('shows a concrete-workspace note instead of generic read-only copy', () => {
    const html = ShoppingList([], true, false, false, '', 'Выберите одно пространство для списка покупок.');

    expect(html).toContain('Выберите одно пространство для списка покупок.');
    expect(html).not.toContain('Список доступен только для чтения.');
  });
});
