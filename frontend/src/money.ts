const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: '₽',
  USD: '$',
  EUR: '€'
};

export function normalizeMoneyText(value: string | number): string {
  const raw = String(value ?? '0').trim().replace(/\s/g, '').replace(',', '.');
  const negative = raw.startsWith('-');
  const unsigned = negative ? raw.slice(1) : raw;
  const [wholeRaw, fractionRaw = ''] = unsigned.split('.');
  const whole = wholeRaw.replace(/\D/g, '') || '0';
  const fraction = `${fractionRaw.replace(/\D/g, '')}00`.slice(0, 2);
  return `${negative ? '-' : ''}${whole}.${fraction}`;
}

export function formatMoneyString(value: string | number, currency = 'RUB'): string {
  const normalized = normalizeMoneyText(value);
  const negative = normalized.startsWith('-');
  const [wholeRaw, fractionRaw = '00'] = (negative ? normalized.slice(1) : normalized).split('.');
  const whole = wholeRaw.replace(/^0+(?=\d)/, '') || '0';
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const fraction = fractionRaw === '00' ? '' : `,${fractionRaw}`;
  const symbol = CURRENCY_SYMBOLS[currency.toUpperCase()] ?? currency.toUpperCase();
  return `${negative ? '-' : ''}${grouped}${fraction} ${symbol}`.trim();
}

export function subtractMoneyStrings(income: string | number, expense: string | number): string {
  const toCents = (value: string | number) => {
    const normalized = normalizeMoneyText(value);
    const negative = normalized.startsWith('-');
    const [whole, fraction] = (negative ? normalized.slice(1) : normalized).split('.');
    const cents = BigInt(whole || '0') * 100n + BigInt(fraction || '0');
    return negative ? -cents : cents;
  };
  const cents = toCents(income) - toCents(expense);
  const sign = cents < 0 ? '-' : '';
  const abs = cents < 0 ? -cents : cents;
  return `${sign}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
}
