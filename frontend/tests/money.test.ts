import { describe, expect, it } from 'vitest';
import { formatMoneyString, normalizeMoneyText } from '../src/money';

describe('money formatting', () => {
  it('formats decimal money from strings without binary floats', () => {
    expect(normalizeMoneyText('216,3')).toBe('216.30');
    expect(formatMoneyString('216.34', 'RUB')).toBe('216,34 ₽');
    expect(formatMoneyString('1200000.00', 'RUB')).toBe('1 200 000 ₽');
  });

  it('keeps currency buckets separate', () => {
    expect(formatMoneyString('12.50', 'USD')).toBe('12,50 $');
    expect(formatMoneyString('12.50', 'EUR')).toBe('12,50 €');
  });
});
