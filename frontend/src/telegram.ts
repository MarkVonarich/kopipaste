export type TelegramThemeParams = Record<string, string | undefined>;

export type TelegramWebApp = {
  initData: string;
  version?: string;
  platform?: string;
  colorScheme?: 'light' | 'dark';
  themeParams?: TelegramThemeParams;
  ready: () => void;
  expand: () => void;
  onEvent: (event: 'themeChanged' | 'backButtonClicked' | 'homeScreenAdded' | 'homeScreenChecked', callback: (eventData?: { status?: HomeScreenStatus }) => void) => void;
  offEvent?: (event: 'themeChanged' | 'backButtonClicked' | 'homeScreenAdded' | 'homeScreenChecked', callback: (eventData?: { status?: HomeScreenStatus }) => void) => void;
  addToHomeScreen?: () => void;
  checkHomeScreenStatus?: (callback?: (status: HomeScreenStatus) => void) => void;
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

export type HomeScreenStatus = 'unsupported' | 'unknown' | 'added' | 'missed';

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

export function canUseNativeAddToHomeScreen(): boolean {
  return typeof getTelegramWebApp()?.addToHomeScreen === 'function';
}

export function requestAddToHomeScreen(): boolean {
  const tg = getTelegramWebApp();
  if (typeof tg?.addToHomeScreen !== 'function') return false;
  try {
    tg.addToHomeScreen();
    return true;
  } catch {
    return false;
  }
}

export function checkHomeScreenStatus(): Promise<HomeScreenStatus> {
  const tg = getTelegramWebApp();
  const checkStatus = tg?.checkHomeScreenStatus;
  if (typeof checkStatus !== 'function') {
    return Promise.resolve(canUseNativeAddToHomeScreen() ? 'unknown' : 'unsupported');
  }
  return new Promise((resolve) => {
    let settled = false;
    const done = (status: HomeScreenStatus | undefined) => {
      if (settled) return;
      settled = true;
      resolve(status || 'unknown');
    };
    try {
      checkStatus((status) => done(status));
    } catch {
      done('unknown');
    }
    window.setTimeout(() => done('unknown'), 800);
  });
}
