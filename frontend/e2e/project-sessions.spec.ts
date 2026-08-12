import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('项目会话列表', () => {
  test('渲染会话列表与状态', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await expect(page.getByTestId('sessions-table')).toContainText('构建待办事项 Web 应用');
    await expect(page.getByTestId('sessions-table')).toContainText('重构登录模块');
    await expect(page.getByTestId('status-tag').first()).toContainText('运行中');
  });

  test('新建会话成功并显示提示', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByTestId('create-session-btn').click();
    await page.getByTestId('session-goal-input').fill('做一个用户系统');
    await page.getByRole('button', { name: /创\s*建/ }).click();
    await expect(page.getByText(/会话 .+ 已创建/)).toBeVisible();
  });

  test('新建会话缺少目标时阻止提交', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByTestId('create-session-btn').click();
    await page.getByRole('button', { name: /创\s*建/ }).click();
    await expect(page.getByText('请输入会话目标')).toBeVisible();
  });

  test('点击打开进入会话详情', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByRole('link', { name: /打\s*开/ }).first().click();
    await expect(page).toHaveURL(/\/sessions\/s1$/);
    await expect(page.getByTestId('session-detail')).toBeVisible();
  });
});