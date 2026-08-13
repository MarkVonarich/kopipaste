import type { HomePreferences, HomeWidgetKey } from '../types';
import { esc } from './ui';

export function HomeSettingsForm(preferences: HomePreferences, order: HomeWidgetKey[], enabled: HomeWidgetKey[], saving = false, error = ''): string {
  const byKey = new Map(preferences.widgets.map((widget) => [widget.key, widget]));
  return `
    <div class="home-settings" data-testid="home-settings-editor">
      <div class="home-widget-editor" data-action="home-widget-editor">
        ${order.map((key) => {
          const widget = byKey.get(key);
          if (!widget) return '';
          const checked = enabled.includes(key);
          return `<div class="home-widget-row" data-home-key="${esc(key)}">
            <label class="home-widget-toggle"><input type="checkbox" data-action="home-widget-toggle" data-key="${esc(key)}" ${checked ? 'checked' : ''} /><span><strong>${esc(widget.title)}</strong><small>${esc(widget.description)}</small></span></label>
          </div>`;
        }).join('')}
      </div>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="button" data-action="home-settings-save" ${saving ? 'disabled' : ''}>Сохранить</button>
    </div>
  `;
}
