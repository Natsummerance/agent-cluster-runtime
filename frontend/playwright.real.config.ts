import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e-real',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'uv run agent-cluster serve --port 8765 --auth-token ci',
    url: 'http://127.0.0.1:8765/api/v1/status',
    reuseExistingServer: !!process.env.PW_REUSE_SERVER,
    timeout: 90_000,
  },
});