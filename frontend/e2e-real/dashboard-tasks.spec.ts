import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, uniqueSuffix } from './helpers/api';

test.describe('真实后端 dashboard-tasks', () => {
  test('dashboard 三轴字段契约 + 任务过滤/指派', async ({ request }) => {
    const fixture = await createProject(request, `e2e-dash-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { goal: '待办应用' });
    await waitTerminal(request, sid);

    const dash = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/dashboard`),
      200,
      'dashboard',
    );
    expect(Object.keys(dash).sort()).toEqual(['cost', 'health', 'progress', 'updated_at']);
    expect(Object.keys(dash.cost).sort()).toEqual(['estimated_usd', 'limit', 'ratio', 'score', 'status', 'used']);
    expect(['ok', 'warn', 'critical']).toContain(dash.cost.status);
    expect(Object.keys(dash.progress).sort()).toEqual(['phases', 'score', 'status']);
    expect(Object.keys(dash.progress.phases).sort()).toEqual(['done', 'total']);
    expect(Object.keys(dash.health).sort()).toEqual(['score', 'sessions', 'status']);
    // health.sessions 为按会话 id 聚合的对象（§10.1）
    expect(Object.keys(dash.health.sessions)).toContain(sid);

    const tasks = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks`),
      200,
      '任务列表',
    );
    expect(tasks.some((t: any) => t.session_id === sid)).toBe(true);

    const done = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?status=completed`),
      200,
      'status=completed',
    );
    expect(done.some((t: any) => t.session_id === sid)).toBe(true);
    const active = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?status=active`),
      200,
      'status=active',
    );
    expect(active.some((t: any) => t.session_id === sid)).toBe(false);

    const qHit = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?q=${encodeURIComponent('待办应用')}`),
      200,
      'q 命中',
    );
    expect(qHit.some((t: any) => t.session_id === sid)).toBe(true);
    const qMiss = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?q=${encodeURIComponent('zzz不存在')}`),
      200,
      'q 未命中',
    );
    expect(qMiss).toEqual([]);

    const assigned = expectOk(
      await api(request, 'PATCH', `/api/v1/projects/${fixture.projectId}/tasks/${sid}`, { assignee: 'alice' }),
      200,
      '指派',
    );
    expect(assigned.assignee).toBe('alice');
    const byAlice = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?assignee=alice`),
      200,
      'assignee=alice',
    );
    expect(byAlice).toHaveLength(1);
    expect(byAlice[0].session_id).toBe(sid);
    const byBob = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/tasks?assignee=bob`),
      200,
      'assignee=bob',
    );
    expect(byBob).toEqual([]);

    // 会话快照同步指派
    const snapshots = expectOk(
      await api(request, 'GET', `/api/v1/projects/${fixture.projectId}/sessions`),
      200,
      '会话快照',
    );
    const snap = snapshots.find((s: any) => s.session_id === sid);
    expect(snap).toBeTruthy();
    expect(snap.assignee).toBe('alice');
    expect(typeof snap.worktree).toBe('boolean');
    expect(typeof snap.merge_conflict).toBe('boolean');
  });
});