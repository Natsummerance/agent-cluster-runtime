import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest';
import * as api from '../api/endpoints';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

type Call = [string | URL | Request, RequestInit | undefined];

function callAt(fetchMock: Mock, index = 0): Call {
  return fetchMock.mock.calls[index] as unknown as Call;
}

describe('api endpoints 路由', () => {
  beforeEach(() => {
    configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
  });

  it('fetchStatus 请求 GET /api/v1/status', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { version: '1' } }));
    setFetchImpl(fetchMock);
    await api.fetchStatus();
    const [input, init] = callAt(fetchMock);
    expect(String(input)).toBe('http://127.0.0.1:8765/api/v1/status');
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('fetchMetrics 请求 GET /api/v1/metrics', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.fetchMetrics();
    expect(String(callAt(fetchMock)[0])).toContain('/api/v1/metrics');
  });

  it('createProject 请求 POST /api/v1/projects 并携带 body', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { id: 'p1' } }, 201));
    setFetchImpl(fetchMock);
    await api.createProject({ name: 'n', workspace: 'w' });
    const [input, init] = callAt(fetchMock);
    expect(String(input)).toContain('/api/v1/projects');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe(JSON.stringify({ name: 'n', workspace: 'w' }));
  });

  it('createSession 请求项目子路径并序列化全部可选字段', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: {} }, 201));
    setFetchImpl(fetchMock);
    await api.createSession('p1', { goal: 'g', model: 'codex', budget: 1000, deterministic: true, yes: false });
    const [input, init] = callAt(fetchMock);
    expect(String(input)).toBe('http://127.0.0.1:8765/api/v1/projects/p1/sessions');
    const body = JSON.parse(init?.body as string);
    expect(body).toMatchObject({ goal: 'g', model: 'codex', budget: 1000, deterministic: true, yes: false });
  });

  it('会话审批/拒绝/编辑/回复/打断/回滚使用正确端点', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.approveSession('s1');
    await api.rejectSession('s1');
    await api.editSession('s1', 'text');
    await api.respondSession('s1', 'answer');
    await api.interruptSession('s1', 'stop');
    await api.rollbackSession('s1', 3);
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls).toEqual([
      'http://127.0.0.1:8765/api/v1/sessions/s1/approve',
      'http://127.0.0.1:8765/api/v1/sessions/s1/reject',
      'http://127.0.0.1:8765/api/v1/sessions/s1/edit',
      'http://127.0.0.1:8765/api/v1/sessions/s1/response',
      'http://127.0.0.1:8765/api/v1/sessions/s1/interrupt',
      'http://127.0.0.1:8765/api/v1/sessions/s1/rollback',
    ]);
    const rollbackBody = JSON.parse((fetchMock.mock.calls[5][1] as RequestInit).body as string);
    expect(rollbackBody).toEqual({ version: 3 });
    const interruptBody = JSON.parse((fetchMock.mock.calls[4][1] as RequestInit).body as string);
    expect(interruptBody).toEqual({ text: 'stop' });
  });

  it('fetchWorkspaceTree 携带 path query 且 encode', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { path: '', entries: [] } }));
    setFetchImpl(fetchMock);
    await api.fetchWorkspaceTree('p1', 'src/组件');
    const [input] = callAt(fetchMock);
    const url = String(input);
    expect(url).toContain('/api/v1/projects/p1/workspace/tree');
    expect(url).toContain(encodeURIComponent('src/组件'));
  });

  it('fetchMemory 请求项目记忆端点', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { items: [], proposals: [] } }));
    setFetchImpl(fetchMock);
    await api.fetchMemory('p1');
    expect(String(callAt(fetchMock)[0])).toBe('http://127.0.0.1:8765/api/v1/projects/p1/memory');
  });

  it('promoteMemory 请求全局 memory promote 端点', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.promoteMemory('m1');
    const [input, init] = callAt(fetchMock);
    expect(String(input)).toBe('http://127.0.0.1:8765/api/v1/memory/m1/promote');
    expect(init?.method).toBe('POST');
  });

  it('fetchEvolutionProposals 解包 data.proposals 并携带 project_id query', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, data: { proposals: [{ id: 'e1' }] } }),
    );
    setFetchImpl(fetchMock);
    const list = await api.fetchEvolutionProposals('p1');
    const [input] = callAt(fetchMock);
    const url = String(input);
    expect(url).toContain('/api/v1/evolution/proposals');
    expect(url).toContain('project_id=p1');
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe('e1');
  });

  it('generateEvolutionProposals 与 retro 使用 POST', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.generateEvolutionProposals({ project_id: 'p1', min_evidence: 3, limit: 5 });
    await api.runEvolutionRetro({ session_id: 's1' });
    const calls = fetchMock.mock.calls.map((c) => [String(c[0]), (c[1] as RequestInit).method] as const);
    expect(calls[0][0]).toContain('/api/v1/evolution/generate');
    expect(calls[0][1]).toBe('POST');
    expect(calls[1][0]).toContain('/api/v1/evolution/retro');
    expect(calls[1][1]).toBe('POST');
  });

  it('fetchSessionAudit 与 export 使用 /audit 端点', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.fetchSessionAudit('s1');
    await api.exportSessionAudit('s1');
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls[0]).toContain('/api/v1/sessions/s1/audit');
    expect(urls[1]).toContain('/api/v1/sessions/s1/audit/export');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('POST');
  });

  it('applyEvolutionProposal / rollbackEvolutionProposal 使用提案子路径', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => jsonResponse({ ok: true, data: {} }));
    setFetchImpl(fetchMock);
    await api.applyEvolutionProposal('e1');
    await api.rollbackEvolutionProposal('e1');
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls[0]).toBe('http://127.0.0.1:8765/api/v1/evolution/proposals/e1/apply');
    expect(urls[1]).toBe('http://127.0.0.1:8765/api/v1/evolution/proposals/e1/rollback');
  });
  it('fetchPlugins/fetchSkills/fetchMcp 解包 data.<key> 信封', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) =>
      jsonResponse({ ok: true, data: { plugins: [1], skills: [2], mcp: [3] } }),
    );
    setFetchImpl(fetchMock);
    const [plugins, skills, mcp] = await Promise.all([api.fetchPlugins(), api.fetchSkills(), api.fetchMcp()]);
    expect(plugins).toEqual([1]);
    expect(skills).toEqual([2]);
    expect(mcp).toEqual([3]);
  });
});