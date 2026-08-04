export function BottomSheet(title: string, body: string): string {
  return `
    <div class="sheet-backdrop" data-action="close-sheet">
      <div class="sheet" data-sheet>
        <h2>${title}</h2>
        ${body}
      </div>
    </div>
  `;
}
