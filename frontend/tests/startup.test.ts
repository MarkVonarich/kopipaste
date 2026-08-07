import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const TELEGRAM_LAUNCH_MESSAGE = 'Откройте приложение через кнопку в Telegram-боте';

function installApiMock() {
  const bootstrap = vi.fn(async () => ({
    user: { currency: 'RUB', timezone: 'Europe/Moscow' },
    workspaces: [{ workspace_id: null, name: 'Личное', kind: 'legacy_personal', role: 'owner', read_only: false }],
    theme: 'telegram',
    notifications: {},
    version: 'test'
  }));

  vi.doMock('../src/api', () => ({
    api: {
      bootstrap,
      overview: vi.fn(),
      operations: vi.fn(),
      analytics: vi.fn(),
      plans: vi.fn(),
      profile: vi.fn(),
      track: vi.fn(async () => undefined)
    },
    requestId: () => 'test-request'
  }));

  return { bootstrap };
}

describe('mini app startup guard', () => {
  beforeEach(() => {
    vi.resetModules();
    document.body.innerHTML = '<div id="app">Загрузка КопиPaste…</div>';
    delete window.Telegram;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.doUnmock('../src/api');
    delete window.Telegram;
  });

  it('shows a clear screen and skips bootstrap outside Telegram', async () => {
    const apiMock = installApiMock();

    await import('../src/main');
    await Promise.resolve();

    expect(document.body.textContent).toContain(TELEGRAM_LAUNCH_MESSAGE);
    expect(apiMock.bootstrap).not.toHaveBeenCalled();
  });

  it('shows a clear screen and skips bootstrap when initData is empty', async () => {
    const ready = vi.fn();
    const expand = vi.fn();
    window.Telegram = {
      WebApp: {
        initData: '',
        ready,
        expand,
        onEvent: vi.fn()
      }
    };
    const apiMock = installApiMock();

    await import('../src/main');
    await Promise.resolve();

    expect(ready).toHaveBeenCalledOnce();
    expect(expand).toHaveBeenCalledOnce();
    expect(document.body.textContent).toContain(TELEGRAM_LAUNCH_MESSAGE);
    expect(apiMock.bootstrap).not.toHaveBeenCalled();
  });

  it('allows Main Mini App launch start_param without trusting it for bootstrap', async () => {
    const ready = vi.fn();
    const expand = vi.fn();
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        start_param: 'workspace_999',
        tgWebAppStartParam: 'workspace_999',
        ready,
        expand,
        onEvent: vi.fn()
      }
    } as typeof window.Telegram;
    const apiMock = installApiMock();

    await import('../src/main');
    await Promise.resolve();
    await Promise.resolve();

    expect(ready).toHaveBeenCalled();
    expect(expand).toHaveBeenCalled();
    expect(apiMock.bootstrap).toHaveBeenCalledOnce();
    expect(document.body.textContent).not.toContain(TELEGRAM_LAUNCH_MESSAGE);
  });
});
