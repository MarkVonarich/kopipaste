import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../src/', import.meta.url));
const forbidden = [
  { pattern: 'parseFloat', message: 'Use string/decimal money helpers instead of parseFloat.' },
  { pattern: 'initDataUnsafe', message: 'Do not trust initDataUnsafe for backend authentication.' },
  { pattern: 'console.log', message: 'Do not leave console.log in the Mini App bundle.' }
];

async function files(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const out = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await files(path));
    if (entry.isFile() && /\.(ts|tsx|css)$/.test(entry.name)) out.push(path);
  }
  return out;
}

const failures = [];
for (const file of await files(root)) {
  const text = await readFile(file, 'utf8');
  for (const rule of forbidden) {
    if (text.includes(rule.pattern)) failures.push(`${file}: ${rule.message}`);
  }
}

if (failures.length) {
  for (const failure of failures) process.stderr.write(`${failure}\n`);
  process.exit(1);
}

process.stdout.write('miniapp lint ok\n');
