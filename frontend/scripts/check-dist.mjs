import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';

const dist = new URL('../dist/', import.meta.url);
const html = await readFile(new URL('index.html', dist), 'utf8');

const failures = [];
const telegramSdk = 'https://telegram.org/js/telegram-web-app.js?63';
const telegramIndex = html.indexOf(telegramSdk);
const entryIndex = html.indexOf('type="module"');

if (telegramIndex === -1) failures.push('dist/index.html does not include the Telegram WebApp SDK.');
if (entryIndex === -1) failures.push('dist/index.html does not include a module entrypoint.');
if (telegramIndex !== -1 && entryIndex !== -1 && telegramIndex > entryIndex) {
  failures.push('Telegram WebApp SDK must load before the module entrypoint.');
}
if (/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/i.test(html)) {
  failures.push('dist/index.html contains an inline script.');
}
if (/\bnomodule\b/i.test(html)) failures.push('dist/index.html contains a nomodule script.');
if (/vite-legacy|System\.import/i.test(html)) failures.push('dist/index.html contains legacy loader code.');

const assetsDir = new URL('assets/', dist);
let assets = [];
try {
  assets = await readdir(assetsDir);
} catch {
  assets = [];
}
for (const asset of assets) {
  if (/vite-legacy|legacy|polyfills/i.test(asset)) failures.push(`legacy asset generated: ${join('assets', asset)}`);
  if (!/\.(js|css)$/.test(asset)) continue;
  const text = await readFile(new URL(`assets/${asset}`, dist), 'utf8');
  if (/vite-legacy|System\.import/i.test(text)) failures.push(`legacy code generated in ${join('assets', asset)}`);
}

if (failures.length) {
  for (const failure of failures) process.stderr.write(`${failure}\n`);
  process.exit(1);
}

process.stdout.write('miniapp dist check ok\n');
