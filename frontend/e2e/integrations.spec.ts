import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('集成', () => {
  test('插件列表渲染', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/integrations');
    await expect(page.getByTestId('plugins-table')).toContainText('codex-hooks');
    await expect(page.getByTestId('plugins-table')).toContainText('docker-sandbox');
  });

  test('技能标签页渲染', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/integrations');
    await page.getByTestId('tab-skills').click();
    await expect(page.getByTestId('skills-table')).toContainText('frontend');
  });

  test('MCP 占位 note 兼容显示', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/integrations');
    await page.getByTestId('tab-mcp').click();
    await expect(page.getByTestId('mcp-note')).toContainText('占位');
  });
});