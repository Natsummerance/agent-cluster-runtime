import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('进化管理', () => {
  test('渲染提案列表', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/evolution');
    await expect(page.getByTestId('proposals-table')).toContainText('引入审批超时自动降级');
    await expect(page.getByTestId('proposals-table')).toContainText('增加阶段产物检查');
  });

  test('生成提案弹窗提交', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/evolution?project_id=p1');
    await page.getByTestId('generate-proposals-btn').click();
    await page.getByTestId('min-evidence-input').fill('3');
    await page.getByTestId('limit-input').fill('5');
    await page.getByRole('dialog').getByRole('button', { name: /生\s*成/ }).click();
    await expect(page.getByText('提案生成完成')).toBeVisible();
  });

  test('应用提案', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/evolution');
    await page.getByTestId('proposals-table').getByRole('button', { name: /应\s*用/ }).first().click();
    await expect(page.getByText('提案已生效')).toBeVisible();
  });

  test('复盘按钮显示复盘结果', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/evolution?project_id=p1');
    await page.getByTestId('retro-btn').click();
    await expect(page.getByTestId('retro-result')).toBeVisible();
    await expect(page.getByTestId('retro-result')).toContainText('复盘完成');
  });
});