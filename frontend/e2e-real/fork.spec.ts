import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, waitForStatus, uniqueSuffix } from './helpers/api';

test.describe('真实后端 fork', () => {
  test('终态派生：血缘字段 + dormant 登记 + 账本不双计', async ({ request }) => {
    const fixture = await createProject(request, `e2e-fork-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { goal: '待办应用' });
    await waitTerminal(request, sid);

    const budgetBefore = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      'fork 前预算',
    );

    const forked = await api(request, 'POST', `/api/v1/sessions/${sid}/fork`, {
      goal: '衍生需求',
      worktree: false,
    });
    expect(forked.status).toBe(200);
    expect(forked.body.data.parent_session_id).toBe(sid);
    expect(forked.body.data.fork_depth).toBe(1);
    const child = forked.body.data.session_id;
    expect(child).not.toBe(sid);

    const budgetAfter = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/budget`),
      200,
      'fork 后预算',
    );
    expect(budgetAfter.used).toBe(budgetBefore.used);

    const live = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/sessions`),
      200,
      '会话列表',
    );
    const childSnap = live.find((s: any) => s.session_id === child);
    expect(childSnap).toBeTruthy();
    expect(childSnap.status).toBe('dormant');

    const childAudit = expectOk(await api(request, 'GET', `/api/v1/sessions/${child}/audit`), 200, '子会话审计');
    expect(
      childAudit.events.some((e: any) => e.type === 'session.start' && e.payload?.forked_from === sid),
    ).toBe(true);

    const parentAudit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '父会话审计');
    expect(
      parentAudit.events.some((e: any) => e.type === 'session.forked' && e.payload?.child_session_id === child),
    ).toBe(true);
  });

  test('active 源 fork → 409 fork_conflict', async ({ request }) => {
    const fixture = await createProject(request, `e2e-fork-active-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { yes: false });
    await waitForStatus(request, sid, 'waiting_approval');

    const res = await api(request, 'POST', `/api/v1/sessions/${sid}/fork`, { goal: 'x', worktree: false });
    expect(res.status).toBe(409);
    expect(res.body.code).toBe('fork_conflict');

    await api(request, 'POST', `/api/v1/sessions/${sid}/cancel`);
  });
});