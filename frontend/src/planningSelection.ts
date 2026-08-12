export function canonicalCategoryKey(value: unknown): string {
  return String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru').replace(/ё/g, 'е');
}

export function togglePlanningCategory(selected: string[], category: string): string[] {
  const key = canonicalCategoryKey(category);
  const existing = selected.findIndex((item) => canonicalCategoryKey(item) === key);
  if (existing >= 0) return selected.filter((_item, index) => index !== existing);
  return [...selected, category];
}

export function dedupePlanningCategories(categories: string[]): string[] {
  const seen = new Set<string>();
  return categories.filter((category) => {
    const key = canonicalCategoryKey(category);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
