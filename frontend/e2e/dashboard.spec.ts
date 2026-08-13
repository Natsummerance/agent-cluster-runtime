import { test, expect } from '@playwright/test';
import { installApiMocks, mockStatus } from './mock-api';

test.describe('仪表盘', () => {
  test('显示后端版本与运行时长', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('status-card')).toContainText(mockStatus.version);
    await expect(page.getByTestId('status-card')).toContainText('1 小时');
  });

  test('显示会话与活跃会话统计', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('status-card')).toContainText('3');
    await expect(page.getByTestId('status-card')).toContainText('1');
  });

  test('显示运行指标（总 Token 与成本）', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('metrics-card')).toContainText('45,678');
    await expect(page.getByTestId('metrics-card')).toContainText('0.42');
  });

  test('快速入口可跳转项目页', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await page.getByTestId('quick-links').getByText('项目管理').click();
    await expect(page).toHaveURL(/\/projects/);
    await expect(page.getByTestId('projects-page')).toBeVisible();
  });

  test('后端断开时仪表盘显示连接告警', async ({ page }) => {
    await installApiMocks(page, { failAll: true });
    await page.goto('/');
    await expect(page.getByTestId('dashboard-connection-alert')).toBeVisible();
    await expect(page.getByTestId('dashboard-connection-alert')).toContainText('请启动 agent-cluster serve');
  });

  test('项目三轴概览汇总块渲染', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('projects-overview')).toContainText('待办应用');
    await expect(page.getByTestId('projects-overview')).toContainText('博客系统');
    await expect(page.getByTestId('projects-overview')).toContainText('预警');
  });
});