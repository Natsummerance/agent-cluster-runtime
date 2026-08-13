// T13.8 wsClient：ws 模式成功路径 / SSE 回退 / REST 回退 / close 幂等（假 socket 注入）。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import * as clientApi from '../api/client';
import { connectWs, type WebSocketLike } from '../api/wsClient';

class FakeWebSocket implements WebSocketLike {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code?: number }) => void) | null = null;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
  }
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.restoreAllMocks();
  clientApi.setFetchImpl(null);
  clientApi.configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('wsClient', () => {
  it('连接成功走 ws 模式：subscribe 发出、event/snapshot/ack 帧路由', () => {
    const onEvent = vi.fn();
    const onSnapshot = vi.fn();
    const onAck = vi.fn();
    const client = connectWs({
      baseUrl: 'http://127.0.0.1:8765',
      authToken: 't',
      sessionIds: ['s1'],
      onEvent,
      onSnapshot,
      onAck,
      WebSocketCtor: FakeWebSocket,
    });
    expect(client.mode).toBe('ws');
    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain('/api/v1/ws');
    expect(socket.url).toContain('token=t');
    expect(socket.url).toContain('session_id=s1');

    socket.readyState = 1;
    socket.onopen?.();
    const subscribed = JSON.parse(socket.sent[0]);
    expect(subscribed.type).toBe('subscribe');
    expect(subscribed.payload.session_ids).toEqual(['s1']);

    socket.onmessage?.({ data: JSON.stringify({ type: 'snapshot', id: 's1', payload: { project: 'p', dashboard: {}, sessions: [] } }) });
    expect(onSnapshot).toHaveBeenCalledTimes(1);
    socket.onmessage?.({ data: JSON.stringify({ type: 'event', seq: 1, session_id: 's1', event_type: 'log', payload: { text: 'x' } }) });
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'event', seq: 1, session_id: 's1', event_type: 'log' }),
    );
    socket.onmessage?.({ data: JSON.stringify({ type: 'ack', id: 'c1', payload: { ok: true } }) });
    expect(onAck).toHaveBeenCalledTimes(1);

    // ws 模式下动作走帧通道
    void client.cancel('s1');
    expect(JSON.parse(socket.sent[socket.sent.length - 1]).type).toBe('cancel');
  });

  it('握手失败（onerror 非 101）→ 切换 sse 模式且 subscribeSse 被调用', () => {
    const subscribeSpy = vi.spyOn(clientApi, 'subscribeSse').mockImplementation(() => () => {});
    const onError = vi.fn();
    const client = connectWs({
      baseUrl: 'http://127.0.0.1:8765',
      sessionIds: ['s1'],
      onError,
      WebSocketCtor: FakeWebSocket,
    });
    const socket = FakeWebSocket.instances[0];
    socket.onerror?.(new Event('error'));
    expect(client.mode).toBe('sse');
    expect(subscribeSpy).toHaveBeenCalledTimes(1);
    expect(subscribeSpy.mock.calls[0][0]).toBe('/api/v1/sessions/s1/events');
    expect(onError).toHaveBeenCalled();
  });

  it('建立后异常断开 → 回退 sse + REST 等价端点被调用', async () => {
    vi.spyOn(clientApi, 'subscribeSse').mockImplementation(() => () => {});
    const calls: Array<[string, string]> = [];
    clientApi.setFetchImpl(async (input, init) => {
      const url = String(input);
      calls.push([String(init?.method ?? 'GET'), url]);
      return jsonResponse({ ok: true, data: {} });
    });
    const client = connectWs({
      baseUrl: 'http://127.0.0.1:8765',
      sessionIds: ['s1'],
      WebSocketCtor: FakeWebSocket,
    });
    const socket = FakeWebSocket.instances[0];
    socket.readyState = 1;
    socket.onopen?.();
    socket.onclose?.({ code: 1006 }); // 异常断开
    expect(client.mode).toBe('sse');

    await client.cancel('s1');
    await client.approval('s1', 'approve');
    await client.approval('s1', 'edit', '补充说明');
    await client.interrupt('s1', '增加导出');
    await client.stdin('s1', '继续');
    const paths = calls.map(([, url]) => url);
    expect(paths).toContain('http://127.0.0.1:8765/api/v1/sessions/s1/cancel');
    expect(paths).toContain('http://127.0.0.1:8765/api/v1/sessions/s1/approve');
    expect(paths).toContain('http://127.0.0.1:8765/api/v1/sessions/s1/edit');
    expect(paths).toContain('http://127.0.0.1:8765/api/v1/sessions/s1/interrupt');
    expect(paths).toContain('http://127.0.0.1:8765/api/v1/sessions/s1/stdin');
  });

  it('close() 幂等：重复调用不抛异常，SSE 与 socket 均被关闭', () => {
    const stop = vi.fn();
    vi.spyOn(clientApi, 'subscribeSse').mockImplementation(() => stop);
    const client = connectWs({
      baseUrl: 'http://127.0.0.1:8765',
      sessionIds: ['s1'],
      WebSocketCtor: FakeWebSocket,
    });
    const socket = FakeWebSocket.instances[0];
    socket.onerror?.(new Event('error'));
    expect(client.mode).toBe('sse');
    client.close();
    client.close();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(socket.closed).toBe(true);
  });
});
