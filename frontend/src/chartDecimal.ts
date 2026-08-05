const DECIMAL_RE = /^-?\d{1,15}(?:\.\d{1,2})?$/;
const VISUAL_SAFE_ABS_MAX = 9000000000000;

export type VisualDecimalPoint = {
  value: number;
  original: string;
};

export function decimalStringToVisualPoint(value: string): VisualDecimalPoint {
  // Chart.js needs JavaScript numbers for pixels. Financial calculations stay
  // on backend Decimal strings; this adapter only prepares visual coordinates
  // within the safe integer-cent boundary.
  const original = String(value ?? '').trim();
  if (!DECIMAL_RE.test(original)) {
    return { value: 0, original };
  }
  const negative = original.startsWith('-');
  const unsigned = negative ? original.slice(1) : original;
  const [wholeRaw, fractionRaw = ''] = unsigned.split('.');
  const whole = globalThis.parseInt(wholeRaw || '0', 10);
  const fraction = globalThis.parseInt((fractionRaw + '00').slice(0, 2), 10);
  const cents = whole * 100 + fraction;
  const rendered = (negative ? -cents : cents) / 100;
  if (!globalThis.Number.isSafeInteger(cents) || Math.abs(rendered) > VISUAL_SAFE_ABS_MAX) {
    return { value: 0, original };
  }
  return { value: rendered, original };
}
