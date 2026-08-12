import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('工作区浏览器', () => {
  test('选择项目后渲染根目录文件树', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/artifacts');
    await expect(page.getByTestId('project-selector')).toBeVisible();
    await page.getByTestId('project-selector').click();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('workspace-tree')).toContainText('README.md');
    await expect(page.getByTestId('workspace-tree')).toContainText('src');
  });

  test('展开目录加载子目录', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/artifacts?project_id=p1');
    await expect(page.getByTestId('workspace-tree')).toContainText('src');
    await page.locator('.ant-tree-switcher').first().click();
    await expect(page.getByTestId('workspace-tree')).toContainText('App.tsx');
  });

  test('点击文件打开预览抽屉', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/artifacts?project_id=p1');
    await page.getByText('README.md').click();
    await expect(page.getByTestId('file-drawer')).toBeVisible();
    await expect(page.getByTestId('file-content')).toContainText('import React');
  });
});