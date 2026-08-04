import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../src/api';

describe('mini app api client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('authenticates with initData and does not send a user id from the browser', async () => {
    window.Telegram = {
      WebApp: {
        initData: 'query_id=1&user=%7B%22id%22%3A42%7D&auth_date=1&hash=abc',
        ready: () => undefined,
        expand: () => undefined,
        onEvent: () => undefined
      }
    };

    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      request_id: 'r1',
      data: { operation: { id: 1 } }
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('crypto', { randomUUID: () => 'uuid-1' });

    await api.createOperation({
      workspace_id: 3,
      type: 'Расходы',
      amount: '216.34',
      category: 'Food',
      description: 'Lunch',
      op_date: '2026-08-04',
      idempotency_key: 'key-1'
    });

    const call = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const [, init] = call;
    expect((init.headers as Headers).get('Authorization')).toContain('tma query_id=1');
    const body = JSON.parse(init.body as string);
    expect(body).not.toHaveProperty('user_id');
    expect(body.idempotency_key).toBe('key-1');
  });
});
