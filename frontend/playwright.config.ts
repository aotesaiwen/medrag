import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests', testMatch: '**/*.spec.ts', workers: 1, timeout: 30000,
  reporter: 'list', use: { baseURL: 'http://127.0.0.1:8000', viewport: { width: 1440, height: 960 },
    trace: 'off', screenshot: 'off', video: 'off' },
})
