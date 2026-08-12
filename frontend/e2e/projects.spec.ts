import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('项目管理', () => {
  test('渲染项目列表', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects');
    await expect(page.getByTestId('projects-table')).toContainText('待办应用');
    await expect(page.getByTestId('projects-table')).toContainText('博客系统');
  });

  test('新建项目成功后出现在列表', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects');
    await page.getByTestId('create-project-btn').click();
    await page.getByTestId('project-name-input').fill('新项目 X');
    await page.getByTestId('project-workspace-input').fill('ws/x');
    await page.getByRole('button', { name: /创\s*建/ }).click();
    await expect(page.getByTestId('projects-table')).toContainText('新项目 X');
    expect(state.createdProjects).toHaveLength(1);
    expect(state.createdProjects[0]).toEqual({ name: '新项目 X', workspace: 'ws/x' });
  });

  test('新建项目后端报错时提示错误', async ({ page }) => {
    await installApiMocks(page, { projectError: '工作区路径已被占用' });
    await page.goto('/projects');
    await page.getByTestId('create-project-btn').click();
    await page.getByTestId('project-name-input').fill('冲突项目');
    await page.getByTestId('project-workspace-input').fill('ws/dup');
    await page.getByRole('button', { name: /创\s*建/ }).click();
    await expect(page.getByText('工作区路径已被占用')).toBeVisible();
  });

  test('新建项目表单校验：缺少名称时阻止提交', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects');
    await page.getByTestId('create-project-btn').click();
    await page.getByRole('button', { name: /创\s*建/ }).click();
    await expect(page.getByText('请输入项目名称')).toBeVisible();
  });

  test('点击“会话”进入项目会话页', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects');
    await page.getByTestId('projects-table').getByRole('link', { name: /会\s*话/ }).first().click();
    await expect(page).toHaveURL(/\/projects\/p1\/sessions/);
    await expect(page.getByTestId('project-sessions-page')).toBeVisible();
  });

  test('空项目列表显示空状态', async ({ page }) => {
    await installApiMocks(page);
    await page.route('**/api/v1/projects', (r) => r.fulfill({ json: { ok: true, data: [] } }));
    await page.goto('/projects');
    await expect(page.getByText('暂无项目，点击右上角新建')).toBeVisible();
  });
});