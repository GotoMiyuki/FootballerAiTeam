import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  // Start the demo API + Vite using docs/Frontend_V0.1_运行与验收.md first.
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
