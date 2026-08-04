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
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
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
