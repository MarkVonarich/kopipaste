import { esc, icon } from './ui';
import type { CategoryOption } from '../types';

export const categoryIcons = [
  ['operations', 'Список'], ['home', 'Дом'], ['plans', 'Цель'],
  ['profile', 'Человек'], ['analytics', 'Рост'], ['income', 'Плюс'],
  ['expense', 'Минус'], ['empty', 'Покупки'], ['more', 'Прочее'],
] as const;
export const categoryColors = [
  ['#187568', 'Зелёный'], ['#2563EB', 'Синий'], ['#AD3153', 'Малиновый'],
  ['#8854B8', 'Фиолетовый'], ['#8A6116', 'Золотистый'], ['#426877', 'Серо-синий'], ['#59636E', 'Серый'],
] as const;

export function CategoryBadge(category: Pick<CategoryOption, 'icon' | 'color'>): string {
  const selectedIcon = categoryIcons.find(([id]) => id === category.icon)?.[0] || 'operations';
  const color = categoryColors.find(([id]) => id === category.color)?.[0] || '#187568';
  return `<span class="category-badge" style="--category-color:${color}" aria-hidden="true">${icon(selectedIcon)}</span>`;
}

export function CategoryAppearance(category: Partial<CategoryOption>): string {
  return `<div class="category-preview" aria-live="polite">${CategoryBadge(category)}<strong data-category-preview-name>${esc(category.name || 'Новая категория')}</strong></div>
    <fieldset class="appearance-options"><legend>Значок</legend><div class="appearance-grid">
      ${categoryIcons.map(([id, title]) => `<label class="appearance-choice" title="${title}"><input type="radio" name="icon" value="${id}" aria-label="${title}" ${(category.icon || 'operations') === id ? 'checked' : ''} />${icon(id)}</label>`).join('')}
    </div></fieldset>
    <fieldset class="appearance-options"><legend>Цвет</legend><div class="appearance-grid">
      ${categoryColors.map(([id, title]) => `<label class="appearance-choice color-choice" title="${title}" style="--category-color:${id}"><input type="radio" name="color" value="${id}" aria-label="${title}" ${(category.color || '#187568') === id ? 'checked' : ''} /><span aria-hidden="true"></span></label>`).join('')}
    </div></fieldset>`;
}
