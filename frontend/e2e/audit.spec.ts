import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('审计', () => {
  test('输入会话 ID 查看审计记录', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/audit');
    await page.getByTestId('audit-session-input').locator('input').fill('s1');
    await page.getByTestId('audit-session-input').locator('input').press('Enter');
    await expect(page.getByTestId('audit-card')).toContainText('审计摘要');
    await expect(page.getByTestId('audit-card')).toContainText('s1');
  });

  test('导出审计包', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/audit?session_id=s1');
    await page.getByTestId('audit-export-btn').click();
    await expect(page.getByText(/审计导出成功/)).toBeVisible();
  });
});