import { defineConfig } from '@playwright/test';

const browserChannel = process.env.AGENTHUB_PLAYWRIGHT_CHANNEL;

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: 'http://localhost:3000',
    headless: true,
    screenshot: 'only-on-failure',
    ...(browserChannel ? { channel: browserChannel } : {}),
  },
  webServer: {
    command: 'npx next start -p 3000',
    port: 3000,
    timeout: 120000,
    reuseExistingServer: !process.env.CI,
  },
});
