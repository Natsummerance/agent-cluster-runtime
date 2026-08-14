import { test, expect } from '@playwright/test';
import { installApiMocks, mockStatus } from './mock-api';

test.describe('设置', () => {
  test('保存服务器地址与令牌并持久化', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/settings');
    await page.getByTestId('server-url-input').fill('http://127.0.0.1:9000');
    await page.getByTestId('auth-token-input').fill('token-e2e');
    await page.getByTestId('save-settings-btn').click();
    await expect(page.getByText('设置已保存')).toBeVisible();
    const raw = await page.evaluate(() => localStorage.getItem('doai-workbench'));
    expect(raw).toContain('http://127.0.0.1:9000');
    expect(raw).toContain('token-e2e');
    await page.reload();
    await expect(page.getByTestId('server-url-input')).toHaveValue('http://127.0.0.1:9000');
    await expect(page.getByTestId('auth-token-input')).toHaveValue('token-e2e');
  });

  test('测试连接成功显示成功提示', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/settings');
    await page.getByTestId('test-connection-btn').click();
    await expect(page.getByTestId('settings-conn-ok')).toBeVisible();
    await expect(page.getByTestId('settings-conn-ok')).toContainText(mockStatus.version);
  });

  test('测试连接失败显示错误', async ({ page }) => {
    await installApiMocks(page, { failStatus: true });
    await page.goto('/settings');
    await page.getByTestId('test-connection-btn').click();
    await expect(page.getByTestId('settings-conn-error')).toBeVisible();
  });

  test('深色模式切换并持久化', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/settings');
    await page.getByTestId('settings-dark-switch').click();
    const raw = await page.evaluate(() => localStorage.getItem('doai-workbench'));
    expect(raw).toContain('"darkMode":true');
    await page.reload();
    await expect(page.getByTestId('settings-page')).toBeVisible();
    const after = await page.evaluate(() => localStorage.getItem('doai-workbench'));
    expect(after).toContain('"darkMode":true');
  });

  test('切换语言为英文后界面显示英文文案', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/settings');
    await page.getByTestId('settings-language-select').click();
    await page.locator('.ant-select-item-option', { hasText: 'en-US' }).click();
    await expect(page.getByText('Server address')).toBeVisible();
    await expect(page.getByText('Language')).toBeVisible();
    const raw = await page.evaluate(() => localStorage.getItem('doai-workbench'));
    expect(raw).toContain('"locale":"en-US"');
  });

  test('非法地址校验阻止保存', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/settings');
    await page.getByTestId('server-url-input').fill('not-a-url');
    await page.getByTestId('save-settings-btn').click();
    await expect(page.getByText(/地址需以 http/)).toBeVisible();
  });
});