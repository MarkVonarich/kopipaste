import { defineConfig } from 'vitest/config';

export default defineConfig({
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
