import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useProjectStore } from '../store/projectStore';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

const DASHBOARD = {
  cost: { used: 500, limit: 1000, ratio: 0.5, score: 0.5, status: 'ok', estimated_usd: 0.01 },
  progress: { score: 0.6, status: 'warn', phases: { total: 4, done: 2 } },
  health: { score: 0.8, status: 'ok', sessions: {} },
  updated_at: '2026-08-13T00:00:00Z',
};

const TASKS = [
  { session_id: 's1', goal: '待办应用', status: 'active', runtime_status: 'running', assignee: '' },
  { session_id: 's2', goal: '博客系统', status: 'active', runtime_status: 'waiting_approval', assignee: 'alice' },
];

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
  useProjectStore.getState().resetState();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('projectStore', () => {
  it('loadDashboard 写入按项目索引的数据', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: DASHBOARD }));
    const data = await useProjectStore.getState().loadDashboard('p1');
    expect(data?.cost.status).toBe('ok');
    expect(useProjectStore.getState().dashboard.p1.cost.used).toBe(500);
    expect(useProjectStore.getState().loading).toBe(false);
  });

  it('loadDashboard 失败记录 error 并返回 null', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: '项目不存在' }, 404));
    const data = await useProjectStore.getState().loadDashboard('missing');
    expect(data).toBeNull();
    expect(useProjectStore.getState().error).toBe('项目不存在');
  });

  it('loadTasks 携带 filters 查询', async () => {
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => jsonResponse({ ok: true, data: TASKS }),
    );
    setFetchImpl(fetchMock);
    useProjectStore.getState().setFilter({ status: 'running', assignee: 'alice' });
    const tasks = await useProjectStore.getState().loadTasks('p1');
    expect(tasks).toHaveLength(2);
    expect(useProjectStore.getState().tasks.p1[0].session_id).toBe('s1');
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('status=running');
    expect(url).toContain('assignee=alice');
  });

  it('setFilter 合并局部更新', () => {
    useProjectStore.getState().setFilter({ status: 'completed' });
    useProjectStore.getState().setFilter({ q: '登录' });
    expect(useProjectStore.getState().filters).toEqual({ status: 'completed', q: '登录' });
  });

  it('assignTask 调 PATCH 后刷新任务', async () => {
    const patched: { value: { url: string; method: string; body: unknown } | null } = { value: null };
    setFetchImpl(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'PATCH') {
        patched.value = { url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : null };
        return jsonResponse({ ok: true, data: { session_id: 's1', assignee: 'bob' } });
      }
      return jsonResponse({ ok: true, data: TASKS });
    });
    await useProjectStore.getState().assignTask('p1', 's1', 'bob');
    expect(patched.value?.method).toBe('PATCH');
    expect(patched.value?.url).toContain('/api/v1/projects/p1/tasks/s1');
    expect(patched.value?.body).toEqual({ assignee: 'bob' });
  });
});
