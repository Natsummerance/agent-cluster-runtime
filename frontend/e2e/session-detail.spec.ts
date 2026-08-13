import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('会话详情', () => {
  test('渲染会话目标与状态', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await expect(page.getByTestId('session-detail')).toBeVisible();
    await expect(page.getByTestId('session-goal')).toContainText('构建待办事项 Web 应用');
    await expect(page.getByTestId('status-tag')).toContainText('运行中');
  });

  test('Token 面板显示预算与已用', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await expect(page.getByTestId('token-panel')).toContainText('预算');
    await expect(page.getByTestId('token-panel')).toContainText('12345');
    await expect(page.getByTestId('token-panel')).toContainText('100000');
  });

  test('健康度卡片显示估算准确率', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await expect(page.getByTestId('health-card')).toContainText('0.85');
  });

  test('等待审批会话自动弹出审批窗口并可接受', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s2');
    await expect(page.getByRole('dialog', { name: '审批门（HITL）' })).toBeVisible();
    await expect(page.getByTestId('gate-hint')).toContainText('请确认登录改造方案');
    await page.getByTestId('gate-accept').click();
    await expect(page.getByRole('dialog', { name: '审批门（HITL）' })).toBeHidden();
    expect(state.approved).toContain('s2');
  });

  test('审批窗口可拒绝', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s2');
    await page.getByRole('dialog', { name: '审批门（HITL）' }).waitFor();
    await page.getByTestId('gate-reject').click();
    await expect(page.getByRole('dialog', { name: '审批门（HITL）' })).toBeHidden();
    expect(state.approved).not.toContain('s2');
  });

  test('审批窗口编辑文本提交', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s2');
    await page.getByRole('dialog', { name: '审批门（HITL）' }).waitFor();
    await page.getByTestId('gate-edit-mode').click();
    await page.getByTestId('gate-text-input').fill('改为邮箱+验证码方案');
    await page.getByTestId('gate-text-submit').click();
    await expect(page.getByRole('dialog', { name: '审批门（HITL）' })).toBeHidden();
  });

  test('审批窗口回复文本提交', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s2');
    await page.getByRole('dialog', { name: '审批门（HITL）' }).waitFor();
    await page.getByTestId('gate-response-mode').click();
    await page.getByTestId('gate-text-input').fill('同意，继续推进');
    await page.getByTestId('gate-text-submit').click();
    await expect(page.getByRole('dialog', { name: '审批门（HITL）' })).toBeHidden();
  });

  test('发送打断指令', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await page.getByTestId('interrupt-text').fill('把登录改为邮箱验证');
    await page.getByTestId('interrupt-submit').click();
    await expect(page.getByText('打断指令已发送')).toBeVisible();
    expect(state.interrupted).toContain('把登录改为邮箱验证');
  });

  test('变更历史渲染并可回滚', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await page.getByRole('tab', { name: '变更历史' }).click();
    await expect(page.getByTestId('change-history')).toContainText('共 3 条变更');
    await page.getByTestId('rollback-3').click();
    await page.locator('.ant-popover').getByRole('button', { name: /回\s*滚/ }).click();
    await expect(page.getByText(/已回滚到版本 3/)).toBeVisible();
    expect(state.rolledBack).toContain('3');
  });

  test('SSE 时间线实时追加事件', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await page.getByRole('tab', { name: /时间线/ }).click();
    await expect(page.getByTestId('event-item-1')).toBeVisible();
    await expect(page.getByTestId('event-item-2')).toBeVisible();
    await expect(page.getByTestId('event-item-3')).toBeVisible();
    await expect(page.getByText('会话已启动')).toBeVisible();
  });

  test('实时输入注入成功', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await page.getByTestId('stdin-text').fill('补充：支持导出');
    await page.getByTestId('stdin-submit').click();
    await expect(page.getByText('实时输入已注入')).toBeVisible();
    expect(state.stdin).toContain('补充：支持导出');
  });

  test('已完成会话禁用打断输入', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p2/sessions/s3');
    await expect(page.getByTestId('interrupt-text')).toBeDisabled();
  });

  test('查看审计按钮跳转审计页', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/projects/p1/sessions/s1');
    await page.getByTestId('goto-audit').click();
    await expect(page).toHaveURL(/\/audit\?session_id=s1/);
    await expect(page.getByTestId('audit-page')).toBeVisible();
  });
});