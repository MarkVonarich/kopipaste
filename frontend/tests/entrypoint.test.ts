import { describe, expect, it } from 'vitest';
import html from '../index.html?raw';

describe('mini app html entrypoint', () => {
  it('loads Telegram SDK before the Vite entrypoint and keeps a visible loading fallback', () => {
    const sdkIndex = html.indexOf('https://telegram.org/js/telegram-web-app.js?63');
    const viteIndex = html.indexOf('/src/main.ts');

    expect(sdkIndex).toBeGreaterThan(-1);
    expect(viteIndex).toBeGreaterThan(-1);
    expect(sdkIndex).toBeLessThan(viteIndex);
    expect(html).toContain('Загрузка КопиPaste…');
  });

  it('uses external scripts only', () => {
    const scriptTags = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];

    expect(scriptTags.length).toBeGreaterThanOrEqual(2);
    for (const [, attributes, body] of scriptTags) {
      expect(attributes).toMatch(/\bsrc=/);
      expect(body.trim()).toBe('');
    }
  });
});
