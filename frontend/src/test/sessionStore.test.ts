import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore, getSubscribedSids } from '../store/sessionStore';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

function makeSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    session_id: 's1',
    project_id: 'p1',
    workspace: 'ws',
    goal: '构建待办应用',
    model: 'codex',
    status: 'running',
    pending_hint: null,
    current_phase: '开发',
    current_node: 'dev',
    token: { budget: 1000, used: 100, remaining: 900, over_budget: false },
    phases: ['需求', '开发'],
    transcript_count: 5,
    gate_count: 1,
    health: null,
    error: null,
    exit_code: null,
    ...overrides,
  };
}

const CHANGES = { records: [{ version: 1, ts: 't1', summary: '创建' }], summary: '共 1 条' };

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
  useSessionStore.getState().resetState();
});

afterEach(() => {
  useSessionStore.getState().disposeAll();
});

describe('sessionStore', () => {
  it('fetchSession 写入快照并返回', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: makeSnapshot() }));
    const result = await useSessionStore.getState().fetchSession('s1');
    expect(result?.session_id).toBe('s1');
    expect(useSessionStore.getState().snapshots.s1.status).toBe('running');
    expect(useSessionStore.getState().loading.s1).toBe(false);
  });

  it('fetchSession 失败时记录 error 并返回 null', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: '会话不存在' }, 404));
    const result = await useSessionStore.getState().fetchSession('missing');
    expect(result).toBeNull();
    expect(useSessionStore.getState().error).toBe('会话不存在');
  });

  it('fetchSession 检测到 waiting_approval 自动打开审批弹窗', async () => {
    setFetchImpl(async () =>
      jsonResponse({ ok: true, data: makeSnapshot({ status: 'waiting_approval', pending_hint: '请确认里程碑' }) }),
    );
    await useSessionStore.getState().fetchSession('s1');
    const approval = useSessionStore.getState().approval;
    expect(approval.open).toBe(true);
    expect(approval.sid).toBe('s1');
    expect(approval.hint).toBe('请确认里程碑');
  });

  it('approve 调用端点、刷新快照并关闭弹窗', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/approve')) return jsonResponse({ ok: true, data: {} });
      return jsonResponse({ ok: true, data: makeSnapshot({ status: 'running' }) });
    });
    setFetchImpl(fetchMock);
    useSessionStore.getState().openApproval('s1', 'hint');
    await useSessionStore.getState().approve();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/approve'))).toBe(true);
    expect(useSessionStore.getState().approval.open).toBe(false);
    expect(useSessionStore.getState().snapshots.s1.status).toBe('running');
  });

  it('reject 调用端点并关闭弹窗', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/reject')) return jsonResponse({ ok: true, data: {} });
      return jsonResponse({ ok: true, data: makeSnapshot({ status: 'failed' }) });
    });
    setFetchImpl(fetchMock);
    useSessionStore.getState().openApproval('s1', 'hint');
    await useSessionStore.getState().reject();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/reject'))).toBe(true);
    expect(useSessionStore.getState().approval.open).toBe(false);
  });

  it('edit 传递文本到 /edit', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/edit')) return jsonResponse({ ok: true, data: {} });
      return jsonResponse({ ok: true, data: makeSnapshot() });
    });
    setFetchImpl(fetchMock);
    useSessionStore.getState().openApproval('s1', 'hint');
    await useSessionStore.getState().edit('新提案内容');
    const editCall = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/edit'))!;
    expect(JSON.parse((editCall[1] as RequestInit).body as string)).toEqual({ text: '新提案内容' });
  });

  it('respond 传递文本到 /response', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/response')) return jsonResponse({ ok: true, data: {} });
      return jsonResponse({ ok: true, data: makeSnapshot() });
    });
    setFetchImpl(fetchMock);
    useSessionStore.getState().openApproval('s1', 'hint');
    await useSessionStore.getState().respond('我的回复');
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/response'))!;
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ text: '我的回复' });
  });

  it('interrupt 发送打断指令并刷新快照', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/interrupt')) return jsonResponse({ ok: true, data: {} }, 202);
      return jsonResponse({ ok: true, data: makeSnapshot() });
    });
    setFetchImpl(fetchMock);
    await useSessionStore.getState().interrupt('s1', '改成邮箱登录');
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/interrupt'))!;
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ text: '改成邮箱登录' });
  });

  it('rollback 调用端点并刷新变更与快照', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/rollback')) return jsonResponse({ ok: true, data: {} });
      if (url.endsWith('/changes')) return jsonResponse({ ok: true, data: CHANGES });
      return jsonResponse({ ok: true, data: makeSnapshot() });
    });
    setFetchImpl(fetchMock);
    await useSessionStore.getState().rollback('s1', 2);
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/rollback'))!;
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ version: 2 });
    expect(useSessionStore.getState().changes.s1.records).toHaveLength(1);
  });

  it('fetchChanges 写入变更数据', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: CHANGES }));
    const result = await useSessionStore.getState().fetchChanges('s1');
    expect(result?.summary).toBe('共 1 条');
    expect(useSessionStore.getState().changes.s1.records[0].version).toBe(1);
  });

  it('subscribe 通过 SSE 追加事件并可注销', async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.fn(
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode('data: {"seq":1,"type":"phase_start","data":{"phase":"需求"}}\n\n'));
              controller.enqueue(encoder.encode('data: {"seq":2,"type":"message","data":{"text":"hi"}}\n\n'));
            },
          }),
          { status: 200 },
        ),
    );
    setFetchImpl(fetchMock);
    const stop = useSessionStore.getState().subscribe('s1');
    await new Promise((r) => setTimeout(r, 30));
    expect(getSubscribedSids()).toContain('s1');
    expect(useSessionStore.getState().events.s1).toHaveLength(2);
    expect(useSessionStore.getState().events.s1[0].seq).toBe(1);
    stop();
    expect(getSubscribedSids()).not.toContain('s1');
  });

  it('handleEvent 通过快照事件自动更新快照并打开审批', () => {
    useSessionStore.getState().handleEvent('s1', {
      seq: 1,
      type: 'snapshot',
      data: makeSnapshot({ status: 'waiting_approval', pending_hint: '确认' }) as unknown as Record<string, unknown>,
    });
    const s = useSessionStore.getState();
    expect(s.snapshots.s1.status).toBe('waiting_approval');
    expect(s.approval.open).toBe(true);
  });

  it('handleEvent 对重复 seq 去重', () => {
    const event = { seq: 7, type: 'message', data: { text: 'x' } } as never;
    useSessionStore.getState().handleEvent('s1', event);
    useSessionStore.getState().handleEvent('s1', event);
    expect(useSessionStore.getState().events.s1).toHaveLength(1);
  });

  it('clearSession 清理快照/变更/事件并注销订阅', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: makeSnapshot() }));
    await useSessionStore.getState().fetchSession('s1');
    useSessionStore.getState().clearSession('s1');
    const s = useSessionStore.getState();
    expect(s.snapshots.s1).toBeUndefined();
    expect(getSubscribedSids()).not.toContain('s1');
  });

  it('disposeAll 停止全部 SSE 订阅', async () => {
    const fetchMock = vi.fn(
      async () => new Response(new ReadableStream<Uint8Array>({ start() {} }), { status: 200 }),
    );
    setFetchImpl(fetchMock);
    useSessionStore.getState().subscribe('s1');
    useSessionStore.getState().subscribe('s2');
    expect(getSubscribedSids()).toEqual(['s1', 's2']);
    useSessionStore.getState().disposeAll();
    expect(getSubscribedSids()).toEqual([]);
  });
});