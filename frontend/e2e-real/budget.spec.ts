import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, uniqueSuffix } from './helpers/api';

test.describe('真实后端 budget', () => {
  test('预算池：解锁 200/202 审批 + 硬上限 409 budget_pool_exhausted', async ({ request }) => {
    const fixture = await createProject(request, `e2e-budget-${uniqueSuffix()}`);
    // 先跑完一个 deterministic 会话产生聚合用量
    const sid = await startSession(request, fixture, { goal: '待办应用' });
    await waitTerminal(request, sid);
    const budget0 = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      '预算快照',
    );
    expect(budget0.used).toBeGreaterThanOrEqual(1);

    // 自服务解锁 → 200 granted
    const granted = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock`, {
      additional_tokens: 1000,
      reason: '扩容',
    });
    expect(granted.status).toBe(200);
    expect(granted.body.data.status).toBe('granted');
    let budget = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      '预算快照',
    );
    expect(budget.hard_limit_tokens).toBe(1000);

    // 审批模式 → 202 pending（不提额）
    const patched = await api(request, 'PATCH', `/api/v1/projects/${fixture.projectId}`, {
      budget_pool: { unlock_requires_approval: true },
    });
    expect(patched.status).toBe(200);
    const pending = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock`, {
      additional_tokens: 500,
      reason: '例外',
    });
    expect(pending.status).toBe(202);
    expect(pending.body.data.status).toBe('pending');
    const unlockId = pending.body.data.id;
    budget = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      '预算快照',
    );
    expect(budget.hard_limit_tokens).toBe(1000);

    // approve → 200 granted 提额
    const approved = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock/${unlockId}/approve`, {
      decided_by: 'pm',
    });
    expect(approved.status).toBe(200);
    expect(approved.body.data.status).toBe('granted');
    budget = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      '预算快照',
    );
    expect(budget.hard_limit_tokens).toBe(1500);

    // 重复决 → 409 conflict
    const again = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock/${unlockId}/deny`, {});
    expect(again.status).toBe(409);
    expect(again.body.code).toBe('conflict');

    // deny 另一条 pending → 200 denied 不提额
    const pending2 = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock`, {
      additional_tokens: 200,
      reason: '例外2',
    });
    const unlockId2 = pending2.body.data.id;
    const denied = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/budget/unlock/${unlockId2}/deny`, {});
    expect(denied.status).toBe(200);
    expect(denied.body.data.status).toBe('denied');

    // 硬上限 = 1 且已有用量 → 409 budget_pool_exhausted
    const limitPatch = await api(request, 'PATCH', `/api/v1/projects/${fixture.projectId}`, {
      budget_pool: { hard_limit_tokens: 1 },
    });
    expect(limitPatch.status).toBe(200);
    const exhausted = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/sessions`, { goal: '再开一个' });
    expect(exhausted.status).toBe(409);
    expect(exhausted.body.code).toBe('budget_pool_exhausted');

    // 临界态 dashboard 契约
    const dash = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/dashboard`),
      200,
      'dashboard',
    );
    expect(dash.cost.status).toBe('critical');
    expect(dash.cost.limit).toBe(1);
    expect(dash.cost.used).toBe(budget0.used);
  });
});