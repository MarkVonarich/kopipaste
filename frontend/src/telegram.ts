export type TelegramThemeParams = Record<string, string | undefined>;

export type TelegramWebApp = {
  initData: string;
  colorScheme?: 'light' | 'dark';
  themeParams?: TelegramThemeParams;
  ready: () => void;
  expand: () => void;
  onEvent: (event: 'themeChanged' | 'backButtonClicked', callback: () => void) => void;
  offEvent?: (event: 'themeChanged' | 'backButtonClicked', callback: () => void) => void;
  BackButton?: {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => void;
    offClick?: (callback: () => void) => void;
  };
  HapticFeedback?: {
    selectionChanged?: () => void;
    impactOccurred?: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void;
    notificationOccurred?: (type: 'error' | 'success' | 'warning') => void;
  };
};

const TELEGRAM_LAUNCH_MESSAGE = 'Откройте приложение через кнопку в Telegram-боте';

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

export function prepareTelegramLaunch(): string | null {
  const tg = getTelegramWebApp();
  if (!tg) return TELEGRAM_LAUNCH_MESSAGE;
  try {
    tg.ready();
    tg.expand();
  } catch {
    return TELEGRAM_LAUNCH_MESSAGE;
  }
  if (!String(tg.initData ?? '').trim()) return TELEGRAM_LAUNCH_MESSAGE;
  return null;
}

export function initTelegramShell(): TelegramWebApp | null {
  const tg = getTelegramWebApp();
  if (!tg) return null;
  tg.ready();
  tg.expand();
  return tg;
}

export function initData(): string {
  return getTelegramWebApp()?.initData ?? '';
}

export function hapticSelection(): void {
  try {
    getTelegramWebApp()?.HapticFeedback?.selectionChanged?.();
  } catch {
    // Haptics are optional; Telegram host differences must not affect UI flow.
  }
}

export function hapticSuccess(): void {
  try {
    getTelegramWebApp()?.HapticFeedback?.notificationOccurred?.('success');
  } catch {
    // Haptics are optional; Telegram host differences must not affect UI flow.
  }
}

export function hapticError(): void {
  try {
    getTelegramWebApp()?.HapticFeedback?.notificationOccurred?.('error');
  } catch {
    // Haptics are optional; Telegram host differences must not affect UI flow.
  }
}

export function hapticDestructive(): void {
  try {
    getTelegramWebApp()?.HapticFeedback?.impactOccurred?.('medium');
  } catch {
    // Haptics are optional; Telegram host differences must not affect UI flow.
  }
}
