import { esc } from './ui';

export function BottomSheet(title: string, body: string): string {
  return `
    <div class="sheet-backdrop" data-action="close-sheet">
      <div class="sheet" data-sheet role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="sheet-handle" aria-hidden="true"></div>
        <h2>${esc(title)}</h2>
        ${body}
      </div>
    </div>
  `;
}
