import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, waitForStatus, uniqueSuffix } from './helpers/api';

test.describe('真实后端 stdin', () => {
  test('挂起注入 → 变更历史/事件落盘；终态注入 409 session_busy', async ({ request }) => {
    const fixture = await createProject(request, `e2e-stdin-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { yes: false });
    await waitForStatus(request, sid, 'waiting_approval');

    const injected = await api(request, 'POST', `/api/v1/sessions/${sid}/stdin`, { text: '增加导出功能' });
    expect(injected.status).toBe(202);
    expect(injected.body.data.accepted).toBe('增加导出功能');

    const approved = await api(request, 'POST', `/api/v1/sessions/${sid}/approve`);
    expect(approved.status).toBe(200);
    expect(approved.body.data.submitted).toBe('accept');

    const snap = await waitTerminal(request, sid);
    expect(snap.status).toBe('completed');

    const changes = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/changes`), 200, '变更历史');
    expect(changes.records.some((r: any) => r.text === '增加导出功能')).toBe(true);

    const audit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '审计');
    const stdinEvent = audit.events.find((e: any) => e.type === 'stdin.applied');
    expect(stdinEvent).toBeTruthy();
    expect(stdinEvent.payload.text).toBe('增加导出功能');

    const busy = await api(request, 'POST', `/api/v1/sessions/${sid}/stdin`, { text: '再来一条' });
    expect(busy.status).toBe(409);
    expect(busy.body.code).toBe('session_busy');
  });
});