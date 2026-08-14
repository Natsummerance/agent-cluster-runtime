import { test, expect } from '@playwright/test';
import { installApiMocks, mockStatus } from './mock-api';

test.describe('连接状态', () => {
  test('后端正常时顶栏显示已连接与服务器地址', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('connection-status')).toContainText('已连接');
    await expect(page.getByTestId('server-url')).toContainText('8765');
  });

  test('后端不可用时显示连接失败横幅', async ({ page }) => {
    await installApiMocks(page, { failAll: true });
    await page.goto('/');
    await expect(page.getByTestId('connection-banner')).toBeVisible();
    await expect(page.getByTestId('connection-banner')).toContainText('连接失败');
  });

  test('连接失败横幅可跳转设置页', async ({ page }) => {
    await installApiMocks(page, { failAll: true });
    await page.goto('/');
    await page.getByTestId('connection-banner').getByText('前往设置').click();
    await expect(page.getByTestId('settings-page')).toBeVisible();
  });

  test('配置认证令牌后请求携带 X-Auth-Token', async ({ page }) => {
    let captured: string | null = null;
    await page.route('**/api/v1/status', async (route) => {
      captured = route.request().headers()['x-auth-token'] ?? null;
      await route.fulfill({ json: { ok: true, data: mockStatus } });
    });
    await page.route('**/api/v1/metrics', (r) => r.fulfill({ json: { ok: true, data: { sessions: 0, active: 0, total_tokens: 0, total_cost: 0, health: null, updated_at: 'x' } } }));
    await page.route('**/api/v1/projects', (r) => r.fulfill({ json: { ok: true, data: [] } }));
    await page.addInitScript(() => {
      localStorage.setItem(
        'doai-workbench',
        JSON.stringify({ state: { serverUrl: 'http://127.0.0.1:8765', authToken: 'e2e-token-123', darkMode: false }, version: 0 }),
      );
    });
    await page.goto('/');
    await expect(page.getByTestId('connection-status')).toContainText('已连接');
    expect(captured).toBe('e2e-token-123');
  });
});