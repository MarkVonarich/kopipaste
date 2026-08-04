import { defineConfig } from 'vitest/config';
import legacy from '@vitejs/plugin-legacy';

export default defineConfig({
  plugins: [legacy({ targets: ['defaults', 'not IE 11'] })],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/miniapp/api': 'http://127.0.0.1:8080',
      '/miniapp/health': 'http://127.0.0.1:8080'
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false
  },
  test: {
    environment: 'jsdom'
  }
});
