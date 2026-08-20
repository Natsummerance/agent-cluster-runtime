import { describe, expect, it, vi, type Mock } from 'vitest';
import {
  ApiError,
  apiRequest,
  buildUrl,
  configureApi,
  getApiContext,
  normalizeBaseUrl,
  parseSseBlock,
  setFetchImpl,
  subscribeSse,
} from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Call = [string | URL | Request, RequestInit | undefined];

function callAt(fetchMock: Mock, index = 0): Call {
  return fetchMock.mock.calls[index] as unknown as Call;
}

describe('api client', () => {
  it('解包成功响应：ok=true 时返回 data', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: { version: '0.8.0' } }));
    const data = await apiRequest<{ version: string }>('/api/v1/status');
    expect(data).toEqual({ version: '0.8.0' });
  });

  it('ok=false 时抛出 ApiError 并携带后端 error 信息', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: '会话不存在' }, 404));
    const err = await apiRequest('/api/v1/sessions/x').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('会话不存在');
    expect((err as ApiError).status).toBe(404);
  });

  it('HTTP 500 且非信封响应时抛出 HTTP 状态错误', async () => {
    setFetchImpl(async () => new Response('boom', { status: 500 }));
    const err = await apiRequest('/api/v1/metrics').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('HTTP 500');
  });

  it('网络层失败映射为“无法连接到服务器”', async () => {
    setFetchImpl(async () => {
      throw new TypeError('Failed to fetch');
    });
    const err = await apiRequest('/api/v1/status').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toContain('无法连接到服务器');
  });

  it('超时/中断错误映射为“请求超时或已中断”', async () => {
    setFetchImpl(async () => {
      throw new DOMException('The operation was aborted', 'AbortError');
    });
    const err = await apiRequest('/api/v1/status', { timeoutMs: 50 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('请求超时或已中断');
  });

  it('配置 authToken 后发送 X-Auth-Token 请求头', async () => {
    configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: 'secret-abc' });
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) =>
      jsonResponse({ ok: true, data: null }),
    );
    setFetchImpl(fetchMock);
    await apiRequest('/api/v1/status');
    const [, init] = callAt(fetchMock);
    expect((init?.headers as Record<string, string>)['X-Auth-Token']).toBe('secret-abc');
  });

  it('query 参数被正确拼接到 URL，undefined 被忽略', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: null }));
    setFetchImpl(fetchMock);
    await apiRequest('/api/v1/projects/p1/workspace/tree', {
      query: { path: 'src/app', filter: undefined, limit: 3 },
    });
    const [input] = callAt(fetchMock);
    const url = String(input);
    expect(url).toContain('path=src%2Fapp');
    expect(url).toContain('limit=3');
    expect(url).not.toContain('filter');
  });

  it('POST body 以 JSON 序列化并携带 Content-Type', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { id: '1' } }));
    setFetchImpl(fetchMock);
    await apiRequest('/api/v1/projects', { method: 'POST', body: { name: 'x', workspace: 'w' } });
    const [, init] = callAt(fetchMock);
    expect(init?.method).toBe('POST');
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(init?.body).toBe(JSON.stringify({ name: 'x', workspace: 'w' }));
  });

  it('非信封 JSON 响应原样返回', async () => {
    setFetchImpl(async () => jsonResponse({ note: '占位' }, 200));
    const data = await apiRequest<{ note: string }>('/api/v1/plugins');
    expect(data).toEqual({ note: '占位' });
  });

  it('raw 模式跳过信封解包', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: 'x' }, 200));
    const data = await apiRequest<{ ok: boolean }>('/api/v1/status', { raw: true });
    expect(data.ok).toBe(false);
  });

  it('normalizeBaseUrl 去除尾部斜杠并回退默认地址', () => {
    expect(normalizeBaseUrl('http://a:1/')).toBe('http://a:1');
    expect(normalizeBaseUrl('')).toBe('http://127.0.0.1:8765');
    expect(buildUrl('/api/v1/status', 'http://localhost:9/')).toBe('http://localhost:9/api/v1/status');
  });

  it('configureApi 与 getApiContext 同步上下文', () => {
    configureApi({ baseUrl: 'http://1.2.3.4:9999/', authToken: 'tok' });
    const ctx = getApiContext();
    expect(ctx.baseUrl).toBe('http://1.2.3.4:9999/');
    expect(ctx.authToken).toBe('tok');
  });

  it('parseSseBlock 解析 data 与 event 字段', () => {
    const block = 'event: snapshot\ndata: {"seq":1,"type":"snapshot"}\n\n';
    const parsed = parseSseBlock(block);
    expect(parsed).toEqual({ event: 'snapshot', data: '{"seq":1,"type":"snapshot"}' });
    expect(parseSseBlock('retry: 1000')).toBeNull();
  });

  it('subscribeSse 流式解析事件并在结束时停止', async () => {
    configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"seq":1,"type":"session_start"}\n\n'));
        controller.enqueue(encoder.encode('data: {"seq":2,"type":"phase_start"}\n\n'));
        controller.close();
      },
    });
    setFetchImpl(async () => new Response(body, { status: 200 }));
    const received: unknown[] = [];
    const stop = subscribeSse('/api/v1/sessions/s1/events', (e) => received.push(e));
    await new Promise((r) => setTimeout(r, 30));
    stop();
    expect(received).toHaveLength(2);
    expect((received[0] as { seq: number }).seq).toBe(1);
    expect((received[1] as { seq: number }).seq).toBe(2);
  });
});