import { test, expect } from '@playwright/test';
import { api, expectOk, createProject, startSession, waitTerminal, waitForStatus, uniqueSuffix } from './helpers/api';

test.describe('真实后端 gates', () => {
  test('deterministic 自动评审：design_review 门无人值守 accept 并落审计', async ({ request }) => {
    const fixture = await createProject(request, `e2e-gates-auto-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { goal: '架构评审', designOnly: true, yes: false });
    // 不发送任何 approve：能走到终态即证明门被自动放行（deterministic-accept）
    const snap = await waitTerminal(request, sid);
    expect(snap.status).toBe('completed');

    const audit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '审计');
    const approval = audit.approvals.find((a: any) => a.node === 'gate_auto');
    expect(approval).toBeTruthy();
    expect(approval.kind).toBe('design_review');
    expect(approval.last_decision).toBe('accept');
    expect(approval.attempts).toBeGreaterThanOrEqual(1);
    expect(approval.rejections).toBe(0);
    expect(approval.escalated).toBe(false);
  });

  test('auto_review=false 回到人工：等待审批 → approve 后完成', async ({ request }) => {
    const fixture = await createProject(request, `e2e-gates-human-${uniqueSuffix()}`);
    const patched = await api(request, 'PATCH', `/api/v1/projects/${fixture.projectId}`, {
      gate_policy: { auto_review: false },
    });
    expect(patched.status).toBe(200);
    expect(patched.body.data.gate_policy.auto_review).toBe(false);

    const sid = await startSession(request, fixture, { goal: '架构评审', designOnly: true, yes: false });
    const waiting = await waitForStatus(request, sid, 'waiting_approval');
    expect(waiting.current_node).toBe('gate_auto');

    const approved = await api(request, 'POST', `/api/v1/sessions/${sid}/approve`);
    expect(approved.status).toBe(200);
    expect(approved.body.data.submitted).toBe('accept');

    const snap = await waitTerminal(request, sid);
    expect(snap.status).toBe('completed');
    const audit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '终态审计');
    const approval = audit.approvals.find((a: any) => a.node === 'gate_auto');
    expect(approval).toBeTruthy();
    expect(approval.kind).toBe('design_review');
    expect(approval.last_decision).toBe('accept');
  });
});