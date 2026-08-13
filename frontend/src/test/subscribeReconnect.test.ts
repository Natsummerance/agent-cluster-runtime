import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { configureApi, setFetchImpl, subscribeSse } from '../api/client';

function frame(body: string, id?: string, eventType?: string): string {
  const lines: string[] = [];
  if (id !== undefined) lines.push(`id: ${id}`);
  if (eventType !== undefined) lines.push(`event: ${eventType}`);
  lines.push(`data: ${body}`);
  return `${lines.join('\n')}\n\n`;
}

// pull 驱动：每次 read 触发一次 pull，按序吐帧后 close 或 error。
// 注意不能用 start() 同步 enqueue 后立刻 error/close：error 会丢弃已入队数据，
// close 后的 done 传递在 fake timers 下也不可靠，pull 语义与真实网络流一致。
function streamOf(chunks: string[], error?: Error): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]));
        i += 1;
      } else if (error) {
        controller.error(error);
      } else {
        controller.close();
      }
    },
  });
}

// 无字节的挂起流；abort 信号触发时让流报错，模拟真实 fetch 中 abort 向 body 的传播。
function stalledStream(init: RequestInit | undefined): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      init?.signal?.addEventListener('abort', () => {
        controller.error(new DOMException('Aborted', 'AbortError'));
      });
    },
  });
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('subscribeSse 自动重连（§6.3）', () => {
  it('正常连接收到事件并携带 id 更新 Last-Event-ID', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response(streamOf([frame('{"seq":1}', '1'), frame('{"seq":2}', '2')]), { status: 200 }));
    setFetchImpl(fetchMock);
    const received: unknown[] = [];
    const stop = subscribeSse('/api/v1/sessions/s1/events', (ev) => received.push(ev));
    await vi.advanceTimersByTimeAsync(0);
    stop();
    expect(received).toHaveLength(2);
  });

  it('流中断后按退避重连且携带 Last-Event-ID', async () => {
    vi.useFakeTimers();
    const calls: (string | undefined)[] = [];
    let n = 0;
    setFetchImpl(async (_input, init) => {
      n += 1;
      calls.push((init?.headers as Record<string, string> | undefined)?.['Last-Event-ID']);
      if (n === 1) {
        return new Response(
          streamOf([frame('{"seq":1}', '1'), frame('{"seq":2}', '2')], new Error('network down')),
          { status: 200 },
        );
      }
      return new Response(streamOf([]), { status: 200 });
    });
    const received: unknown[] = [];
    const errors: unknown[] = [];
    const stop = subscribeSse('/api/v1/sessions/s1/events', (ev) => received.push(ev), { onError: (err) => errors.push(err) });
    await vi.advanceTimersByTimeAsync(0);
    expect(n).toBe(1);
    expect(received.map((x) => (x as { seq: number }).seq)).toEqual([1, 2]);
    expect(errors).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(n).toBe(2);
    expect(calls[1]).toBe('2');
    stop();
  });

  it('重连间隔 1s→2s→4s→8s→15s 封顶', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    setFetchImpl(fetchMock);
    const stop = subscribeSse('/api/v1/sessions/s1/events', () => {}, { onError: () => {} });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    await vi.advanceTimersByTimeAsync(8000);
    expect(fetchMock).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(15000);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    await vi.advanceTimersByTimeAsync(15000);
    expect(fetchMock).toHaveBeenCalledTimes(7);
    stop();
  });

  it('30s 无字节 → abort 并重连', async () => {
    vi.useFakeTimers();
    let n = 0;
    const fetchMock = vi.fn(async (_input, init) => {
      n += 1;
      if (n === 1) {
        return new Response(stalledStream(init), { status: 200 });
      }
      return new Response(streamOf([]), { status: 200 });
    });
    setFetchImpl(fetchMock);
    const stop = subscribeSse('/api/v1/sessions/s1/events', () => {}, { onError: () => {} });
    await vi.advanceTimersByTimeAsync(0);
    expect(n).toBe(1);
    await vi.advanceTimersByTimeAsync(30000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(n).toBe(2);
    stop();
  });

  it('收到 session.end 哨兵 → onTerminal 且不再重连', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () =>
      new Response(
        streamOf([frame('{"type":"session.end","status":"completed","seq":7}', '7', 'session.end')]),
        { status: 200 },
      ),
    );
    setFetchImpl(fetchMock);
    const terminal: string[] = [];
    const received: unknown[] = [];
    const stop = subscribeSse('/api/v1/sessions/s1/events', (ev) => received.push(ev), { onTerminal: (status) => terminal.push(status) });
    await vi.advanceTimersByTimeAsync(0);
    expect(terminal).toEqual(['completed']);
    expect(received).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    stop();
  });

  it('未收哨兵的正常 EOF 视为断线并重连（不静默终态）', async () => {
    vi.useFakeTimers();
    let n = 0;
    const fetchMock = vi.fn(async () => {
      n += 1;
      return new Response(streamOf([frame('{"seq":1}', '1')]), { status: 200 });
    });
    setFetchImpl(fetchMock);
    const terminal: string[] = [];
    const stop = subscribeSse('/api/v1/sessions/s1/events', () => {}, { onTerminal: (status) => terminal.push(status) });
    await vi.advanceTimersByTimeAsync(0);
    expect(n).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(n).toBe(2);
    expect(terminal).toEqual([]);
    stop();
  });
});
