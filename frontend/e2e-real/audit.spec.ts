import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, waitForStatus, uniqueSuffix } from './helpers/api';

test.describe('真实后端 audit', () => {
  test('审计导出：session.start/end + stdin.applied + 双门审批/变更/成本', async ({ request }) => {
    const fixture = await createProject(request, `e2e-audit-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { goal: '双门流程', twoGates: true, yes: false });
    await waitForStatus(request, sid, 'waiting_approval');

    const injected = await api(request, 'POST', `/api/v1/sessions/${sid}/stdin`, { text: '补充：支持导出 CSV' });
    expect(injected.status).toBe(202);
    expect(injected.body.data.accepted).toBe('补充：支持导出 CSV');

    const approved = await api(request, 'POST', `/api/v1/sessions/${sid}/approve`);
    expect(approved.status).toBe(200);
    expect(approved.body.data.submitted).toBe('accept');

    // 仅一次人工审批：第二道 design_review 门由 deterministic 自动评审放行
    const snap = await waitTerminal(request, sid);
    expect(snap.status).toBe('completed');

    const audit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '审计');
    expect(audit.session_id).toBe(sid);
    expect(audit.goal).toBe('双门流程');
    const eventTypes = audit.events.map((e: any) => e.type);
    expect(eventTypes).toContain('session.start');
    expect(eventTypes).toContain('session.end');
    expect(eventTypes).toContain('stdin.applied');
    const stdinEvent = audit.events.find((e: any) => e.type === 'stdin.applied');
    expect(stdinEvent.payload.text).toBe('补充：支持导出 CSV');

    expect(
      audit.approvals.some((a: any) => a.node === 'gate_req' && a.last_decision === 'accept'),
    ).toBe(true);
    expect(
      audit.approvals.some(
        (a: any) => a.node === 'gate_auto' && a.kind === 'design_review' && a.last_decision === 'accept',
      ),
    ).toBe(true);

    expect(typeof audit.token_summary.used).toBe('number');
    expect(audit.changes.some((c: any) => c.text === '补充：支持导出 CSV')).toBe(true);
    expect(audit.cost.currency).toBe('USD');
    expect(typeof audit.cost.total).toBe('number');
  });
});