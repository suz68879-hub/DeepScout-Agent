import { defineConfig, devices } from '@playwright/test';

const e2ePort = Number(process.env.E2E_PORT ?? 3100);

/**
 * P7 E2E：前端以 VITE_E2E=1（.env.e2e + vite --mode e2e）启动，
 * hook 级短路 RTC 调用并以脚本化字幕驱动面试间；API 由 e2e/mocks 拦截。
 */
export default defineConfig({
  testDir: './e2e/tests',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${e2ePort}`,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run e2e:serve -- --host 127.0.0.1 --port ${e2ePort} --strictPort`,
    url: `http://127.0.0.1:${e2ePort}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
