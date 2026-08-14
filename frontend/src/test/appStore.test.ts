import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../store/appStore';
import { getApiContext, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

const STATUS = { version: '0.7.2', projects: 1, sessions: 2, active_sessions: 1, uptime: 3600 };
const PROJECTS = [{ id: 'p1', name: 'demo', workspace: 'ws', status: 'active', created_at: '2026-01-01T00:00:00' }];
const METRICS = { sessions: 2, active: 1, total_tokens: 1000, total_cost: 0.01, health: null, updated_at: '2026-01-01T00:00:00' };

beforeEach(() => {
  localStorage.clear();
  useAppStore.getState().resetState();
});

describe('appStore', () => {
  it('refreshStatus 成功时写入状态并标记已连接', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: STATUS }));
    await useAppStore.getState().refreshStatus();
    const s = useAppStore.getState();
    expect(s.status?.version).toBe('0.7.2');
    expect(s.connected).toBe(true);
    expect(s.loading).toBe(false);
  });

  it('refreshStatus 失败时标记未连接并记录错误', async () => {
    setFetchImpl(async () => {
      throw new TypeError('Failed to fetch');
    });
    await useAppStore.getState().refreshStatus();
    const s = useAppStore.getState();
    expect(s.connected).toBe(false);
    expect(s.error).toContain('无法连接到服务器');
  });

  it('refreshProjects 写入项目列表', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: PROJECTS }));
    await useAppStore.getState().refreshProjects();
    expect(useAppStore.getState().projects).toHaveLength(1);
    expect(useAppStore.getState().projects[0].name).toBe('demo');
  });

  it('refreshMetrics 写入指标', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: METRICS }));
    await useAppStore.getState().refreshMetrics();
    expect(useAppStore.getState().metrics?.total_tokens).toBe(1000);
  });

  it('refreshAll 并行刷新三组数据', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/status')) return jsonResponse({ ok: true, data: STATUS });
      if (url.includes('/projects')) return jsonResponse({ ok: true, data: PROJECTS });
      return jsonResponse({ ok: true, data: METRICS });
    });
    setFetchImpl(fetchMock);
    await useAppStore.getState().refreshAll();
    expect(useAppStore.getState().status?.version).toBe('0.7.2');
    expect(useAppStore.getState().projects).toHaveLength(1);
    expect(useAppStore.getState().metrics?.sessions).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('createProject 调用 POST 并刷新项目列表', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      if (init?.method === 'POST') return jsonResponse({ ok: true, data: { ...PROJECTS[0], id: 'p2' } }, 201);
      return jsonResponse({ ok: true, data: PROJECTS });
    });
    setFetchImpl(fetchMock);
    const created = await useAppStore.getState().createProject({ name: 'x', workspace: 'w' });
    expect(created.id).toBe('p2');
    expect(useAppStore.getState().projects).toHaveLength(1);
  });

  it('createProject 失败时抛出错误并写入 error', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: '工作区已存在' }, 400));
    await expect(useAppStore.getState().createProject({ name: 'x', workspace: 'w' })).rejects.toThrow(
      '工作区已存在',
    );
    expect(useAppStore.getState().error).toBe('工作区已存在');
  });

  it('setServerUrl / setAuthToken 更新状态并同步 api 上下文', () => {
    useAppStore.getState().setServerUrl('http://localhost:9999');
    useAppStore.getState().setAuthToken('tok-1');
    const s = useAppStore.getState();
    expect(s.serverUrl).toBe('http://localhost:9999');
    expect(s.authToken).toBe('tok-1');
    expect(getApiContext().authToken).toBe('tok-1');
  });

  it('setDarkMode 切换深色模式', () => {
    useAppStore.getState().setDarkMode(true);
    expect(useAppStore.getState().darkMode).toBe(true);
    useAppStore.getState().setDarkMode(false);
    expect(useAppStore.getState().darkMode).toBe(false);
  });

  it('设置经 persist 写入 localStorage（持久化）', () => {
    useAppStore.getState().setServerUrl('http://persisted:1234');
    useAppStore.getState().setAuthToken('persist-token');
    useAppStore.getState().setDarkMode(true);
    const raw = localStorage.getItem('doai-workbench');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw ?? '{}');
    expect(parsed.state.serverUrl).toBe('http://persisted:1234');
    expect(parsed.state.authToken).toBe('persist-token');
    expect(parsed.state.darkMode).toBe(true);
  });

  it('resetState 恢复初始状态', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: STATUS }));
    await useAppStore.getState().refreshStatus();
    useAppStore.getState().resetState();
    const s = useAppStore.getState();
    expect(s.status).toBeNull();
    expect(s.connected).toBeNull();
    expect(s.projects).toEqual([]);
  });
});