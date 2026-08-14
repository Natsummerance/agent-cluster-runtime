import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { configureApi, setFetchImpl } from '../api/client';
import Plans from '../pages/Plans';
import { renderWithIntl } from './renderWithIntl';
import type { Goal, Job, Plan, Schedule } from '../api/types';

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(data), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

function makeHarness() {
  const plans: Plan[] = [
    {
      id: 'p1',
      name: '发布计划',
      mode: 'active',
      goals: ['g1'],
      jobs: ['j1'],
      created_at: '2026-08-13T00:00:00+00:00',
    },
  ];
  const goals: Goal[] = [
    {
      id: 'g1',
      plan_id: 'p1',
      objective: '完成 14.17',
      status: 'active',
      rounds: 0,
      version: 1,
      max_rounds: 3,
    },
  ];
  const jobs: Job[] = [
    { id: 'j1', owner: 'admin', state: 'pending', outcome: '', created_at: '2026-08-13T00:00:00+00:00' },
  ];
  const schedules: Schedule[] = [
    { id: 's1', kind: 'every', every_minutes: 10, state: 'active', created_at: '2026-08-13T00:00:00+00:00' },
  ];
  const lastGets: string[] = [];
  const lastPosts: Array<{ url: string; body: Record<string, unknown> }> = [];

  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (method === 'GET' && url.includes('/api/v1/plans')) {
      lastGets.push(url);
      const match = url.match(/\/api\/v1\/plans\/([^/?]+)/);
      if (match) {
        const plan = plans.find((p) => p.id === match[1]);
        if (!plan) return jsonResponse({ ok: false, error: 'not found' }, 404);
        return jsonResponse({
          ok: true,
          data: {
            plan,
            goals: goals.filter((goal) => (plan.goals ?? []).includes(goal.id)),
            jobs: jobs.filter((job) => (plan.jobs ?? []).includes(job.id)),
          },
        });
      }
      return jsonResponse({ ok: true, data: { plans } });
    }
    if (method === 'POST' && url.endsWith('/api/v1/plans')) {
      const plan: Plan = {
        id: `new-${plans.length + 1}`,
        name: String(body.name ?? ''),
        mode: String(body.mode ?? 'inactive'),
        goals: [],
        jobs: [],
        created_at: '2026-08-13T01:00:00+00:00',
      };
      plans.push(plan);
      lastPosts.push({ url, body });
      return jsonResponse({ ok: true, data: { plan } }, 201);
    }
    const goalMatch = url.match(/\/api\/v1\/plans\/([^/]+)\/goals$/);
    if (method === 'POST' && goalMatch) {
      const plan = plans.find((p) => p.id === goalMatch[1]);
      if (!plan) return jsonResponse({ ok: false, error: 'not found' }, 404);
      const goal: Goal = {
        id: `goal-${goals.length + 1}`,
        plan_id: plan.id,
        objective: String(body.objective ?? ''),
        status: 'active',
        rounds: 0,
        version: 1,
        max_rounds: body.max_rounds ? Number(body.max_rounds) : 5,
      };
      goals.push(goal);
      plan.goals = [...(plan.goals ?? []), goal.id];
      lastPosts.push({ url, body });
      return jsonResponse({ ok: true, data: { goal } }, 201);
    }
    const changeMatch = url.match(/\/api\/v1\/goals\/([^/]+)\/change$/);
    if (method === 'POST' && changeMatch) {
      const goal = goals.find((g) => g.id === changeMatch[1]);
      if (!goal) return jsonResponse({ ok: false, error: 'not found' }, 404);
      if (Number(body.expected_version) !== goal.version) {
        return jsonResponse(
          { ok: false, error: 'goal 版本冲突', code: 'version_conflict' },
          409,
        );
      }
      const updated: Goal = {
        ...goal,
        rounds: body.start_round ? (goal.rounds ?? 0) + 1 : goal.rounds,
        status: typeof body.status === 'string' ? body.status : goal.status,
        version: (goal.version ?? 1) + 1,
      };
      goals.splice(goals.indexOf(goal), 1, updated);
      lastPosts.push({ url, body });
      return jsonResponse({ ok: true, data: { goal: updated } });
    }
    const settleMatch = url.match(/\/api\/v1\/jobs\/([^/]+)\/settle$/);
    if (method === 'POST' && settleMatch) {
      const job = jobs.find((j) => j.id === settleMatch[1]);
      if (!job) return jsonResponse({ ok: false, error: 'not found' }, 404);
      const first = job.state !== 'settled';
      const updated: Job = {
        ...job,
        state: 'settled',
        outcome: String(body.outcome ?? ''),
        settled_at: '2026-08-13T02:00:00+00:00',
      };
      jobs.splice(jobs.indexOf(job), 1, updated);
      lastPosts.push({ url, body });
      return jsonResponse({ ok: true, data: { job: updated, first } });
    }
    if (method === 'GET' && url.includes('/api/v1/schedules')) {
      lastGets.push(url);
      return jsonResponse({ ok: true, data: { schedules } });
    }
    if (method === 'POST' && url.endsWith('/api/v1/schedules')) {
      const kind = String(body.kind ?? '');
      const minutes =
        kind === 'after' ? Number(body.after_minutes) : kind === 'every' ? Number(body.every_minutes) : undefined;
      if (minutes !== undefined && minutes < 5) {
        return jsonResponse(
          { ok: false, error: 'frequency_too_high', code: 'frequency_too_high' },
          400,
        );
      }
      const schedule: Schedule = {
        id: `sched-${schedules.length + 1}`,
        kind,
        at: kind === 'at' ? String(body.at ?? '') : null,
        after_minutes: kind === 'after' ? minutes : null,
        every_minutes: kind === 'every' ? minutes : null,
        state: 'active',
        created_at: '2026-08-13T03:00:00+00:00',
      };
      schedules.push(schedule);
      lastPosts.push({ url, body });
      return jsonResponse({ ok: true, data: { schedule } }, 201);
    }
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return { plans, goals, jobs, schedules, lastGets, lastPosts };
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Plans 页面（计划/目标/任务/排程）', () => {
  it('渲染计划表格与排程表格', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('plans-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('发布计划')).toBeInTheDocument());
    expect(within(screen.getByTestId('plans-table')).getByText('p1')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('schedules-table')).toBeInTheDocument());
    expect(within(screen.getByTestId('schedules-table')).getByText('s1')).toBeInTheDocument();
    expect(
      harness.lastGets.some((url) => url.includes('/api/v1/plans')),
    ).toBe(true);
    expect(
      harness.lastGets.some((url) => url.includes('/api/v1/schedules')),
    ).toBe(true);
  });

  it('新建计划提交 POST /api/v1/plans 并选中', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('add-plan-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-plan-btn'));
    await waitFor(() => expect(screen.getByTestId('create-plan-modal')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('plan-name-input'), '迁移计划');
    fireEvent.mouseDown(screen.getByTestId('plan-mode-select').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('激活'));
    await userEvent.click(
      screen.getByTestId('create-plan-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.plans.some((p) => p.name === '迁移计划')).toBe(true));
    await waitFor(() => expect(within(screen.getByTestId('plans-table')).getByText('迁移计划')).toBeInTheDocument());
    expect(harness.plans.find((p) => p.name === '迁移计划')?.mode).toBe('active');
  });

  it('选中计划后加载目标并创建新目标', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('plan-row-p1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('plan-row-p1'));
    await waitFor(() => expect(screen.getByTestId('goals-table')).toBeInTheDocument());
    expect(within(screen.getByTestId('goals-table')).getByText('完成 14.17')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('add-goal-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-goal-btn'));
    await waitFor(() => expect(screen.getByTestId('create-goal-modal')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('goal-objective-input'), '补充测试');
    await userEvent.type(screen.getByTestId('goal-max-rounds-input'), '2');
    await userEvent.click(
      screen.getByTestId('create-goal-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.goals.some((g) => g.objective === '补充测试')).toBe(true));
    await waitFor(() =>
      expect(within(screen.getByTestId('goals-table')).getByText('补充测试')).toBeInTheDocument(),
    );
  });

  it('开始轮次推进 rounds（CAS expected_version）', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('plan-row-p1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('plan-row-p1'));
    await waitFor(() => expect(screen.getByTestId('start-round-g1')).toBeInTheDocument());
    expect(within(screen.getByTestId('goals-table')).getByText('0 / 3')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('start-round-g1'));
    await waitFor(() => expect(within(screen.getByTestId('goals-table')).getByText('1 / 3')).toBeInTheDocument());
    expect(harness.goals.find((g) => g.id === 'g1')?.version).toBe(2);
    const post = harness.lastPosts.find((p) => p.url.includes('/goals/g1/change'));
    expect(post?.body.start_round).toBe(true);
  });

  it('目标状态变更携带 CAS expected_version 并刷新状态', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('plan-row-p1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('plan-row-p1'));
    await waitFor(() => expect(screen.getByTestId('goal-status-select-g1')).toBeInTheDocument());
    fireEvent.mouseDown(
      screen.getByTestId('goal-status-select-g1').querySelector('.ant-select-selector')!,
    );
    fireEvent.click(await screen.findByTitle('已完成'));
    await waitFor(() => expect(harness.goals.find((g) => g.id === 'g1')?.status).toBe('complete'));
    const post = harness.lastPosts.find((p) => p.url.includes('/goals/g1/change'));
    expect(post?.body).toMatchObject({ expected_version: 1, status: 'complete' });
    await waitFor(() => expect(within(screen.getByTestId('goals-table')).getByText('已完成')).toBeInTheDocument());
  });

  it('结算任务（owner 授权，first-wins）', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('plan-row-p1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('plan-row-p1'));
    await waitFor(() => expect(screen.getByTestId('jobs-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('settle-job-j1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('settle-job-j1'));
    await waitFor(() => expect(screen.getByTestId('settle-job-modal')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('job-outcome-input'), 'ok');
    await userEvent.click(
      screen.getByTestId('settle-job-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.jobs.find((j) => j.id === 'j1')?.state).toBe('settled'));
    await waitFor(() => expect(within(screen.getByTestId('jobs-table')).getByText('已结算')).toBeInTheDocument());
  });

  it('新建排程（every ≥ 5 分钟）并刷新表格', async () => {
    const harness = makeHarness();
    renderWithIntl(<Plans />);
    await waitFor(() => expect(screen.getByTestId('add-schedule-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-schedule-btn'));
    await waitFor(() => expect(screen.getByTestId('create-schedule-modal')).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByTestId('schedule-kind-select').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('间隔（固定分钟）'));
    await waitFor(() => expect(screen.getByTestId('schedule-minutes-input')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('schedule-minutes-input'), '30');
    await userEvent.click(
      screen.getByTestId('create-schedule-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.schedules.some((s) => s.every_minutes === 30)).toBe(true));
    await waitFor(() => expect(within(screen.getByTestId('schedules-table')).getByText('30')).toBeInTheDocument());
  });
});
