import { defineConfig } from '@playwright/test';

const browserChannel = process.env.AGENTHUB_PLAYWRIGHT_CHANNEL;

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  // Real API fixtures own the fixed loopback port baked into the Next rewrites.
  // Each spec starts/stops its own API; serialize files rather than reuse a listener.
  workers: 1,
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
