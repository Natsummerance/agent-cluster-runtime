import { test, expect } from '@playwright/test';
import { api, createProject, startSession, waitTerminal, uniqueSuffix } from './helpers/api';

test.describe('真实后端 sessions', () => {
  test('deterministic 会话运行至 completed（快照字段契约）', async ({ request }) => {
    const fixture = await createProject(request, `e2e-sessions-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { goal: '待办应用' });
    const snap = await waitTerminal(request, sid);
    expect(snap.status).toBe('completed');
    expect(snap.session_id).toBe(sid);
    expect(snap.project_id).toBe(fixture.projectId);
    expect(snap.goal).toBe('待办应用');
    expect(snap.model).toBe('deterministic');
    expect(typeof snap.transcript_count).toBe('number');
    expect(typeof snap.gate_count).toBe('number');
    expect(typeof snap.token.used).toBe('number');
    expect(typeof snap.token.remaining).toBe('number');
    expect(snap.token).toHaveProperty('by_phase');
    expect(typeof snap.worktree).toBe('boolean');
    expect(typeof snap.assignee).toBe('string');
  });

  test('恢复已完成会话 409 / 空 goal 400 / 未知会话 404（旧式信封）', async ({ request }) => {
    const fixture = await createProject(request, `e2e-sessions-err-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture);
    await waitTerminal(request, sid);

    const resume = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/sessions`, {
      session_id: sid,
      goal: 'x',
    });
    expect(resume.status).toBe(409);
    expect(resume.body.code).toBe('conflict');

    const empty = await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/sessions`, { goal: '' });
    expect(empty.status).toBe(400);
    expect(empty.body.code).toBe('bad_request');

    const missing = await api(request, 'GET', '/api/v1/sessions/nope');
    expect(missing.status).toBe(404);
    expect(missing.body.ok).toBe(false);
    expect(missing.body.code).toBeUndefined();
    expect(typeof missing.body.error).toBe('string');
  });
});