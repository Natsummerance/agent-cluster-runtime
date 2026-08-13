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

  test('三轴卡片渲染（成本/进度/健康）', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await expect(page.getByTestId('axis-cost')).toContainText('12000 / 100000');
    await expect(page.getByTestId('axis-cost')).toContainText('正常');
    await expect(page.getByTestId('axis-progress')).toContainText('60%');
    await expect(page.getByTestId('axis-progress')).toContainText('预警');
    await expect(page.getByTestId('axis-health')).toContainText('正常');
  });

  test('关键词筛选触发对应 query 参数', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByTestId('filter-q').fill('登录');
    const [request] = await Promise.all([
      page.waitForRequest(
        (req) => req.url().includes('/projects/p1/tasks') && req.url().includes('q='),
      ),
      page.getByTestId('filter-q').press('Enter'),
    ]);
    expect(new URL(request.url()).searchParams.get('q')).toBe('登录');
  });

  test('指派 Select 触发 PATCH', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByTestId('assignee-select-s1').click();
    const [request] = await Promise.all([
      page.waitForRequest(
        (req) =>
          req.method() === 'PATCH' &&
          /\/projects\/p1\/tasks\/s1$/.test(new URL(req.url()).pathname),
      ),
      page.locator('.ant-select-item-option').filter({ hasText: /^DEV$/ }).click(),
    ]);
    expect(request.postDataJSON()).toEqual({ assignee: 'DEV' });
    await expect(page.getByText(/已指派给 DEV/)).toBeVisible();
  });

  test('运行中会话派生被 409 fork_conflict 拦截', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions');
    await page.getByTestId('fork-s1').click();
    await expect(page.getByText(/fork_conflict/)).toBeVisible();
  });
});