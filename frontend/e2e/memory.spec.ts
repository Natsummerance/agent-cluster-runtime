import { test, expect } from '@playwright/test';
import { installApiMocks } from './mock-api';

test.describe('记忆库', () => {
  test('渲染长期记忆与提案', async ({ page }) => {
    await installApiMocks(page);
    await page.goto('/memory?project_id=p1');
    await expect(page.getByTestId('memory-card')).toContainText('项目使用 React + AntD 技术栈');
    await expect(page.getByTestId('proposals-card')).toContainText('建议引入测试覆盖率门禁');
  });

  test('提升提案为记忆调用 promote 并刷新', async ({ page }) => {
    const { state } = await installApiMocks(page);
    await page.goto('/memory?project_id=p1');
    await page.getByTestId('promote-pro-1').click();
    await expect(page.getByText('提案已提升为长期记忆')).toBeVisible();
    expect(state.promoted).toContain('pro-1');
  });
});