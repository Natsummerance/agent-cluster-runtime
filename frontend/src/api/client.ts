// fetch 封装：baseURL 可配置 + X-Auth-Token + JSON 解包 + 错误处理
import type { ApiEnvelope } from './types';

export const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';

export type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

export interface ApiContext {
  baseUrl: string;
  authToken: string | null;
}

export interface ApiRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  timeoutMs?: number;
  raw?: boolean; // 不做信封解包（SSE 等场景）
}

export class ApiError extends Error {
  status?: number;
  payload?: unknown;
  constructor(message: string, status?: number, payload?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

// 可注入的 fetch 实现（测试时替换）
let fetchImpl: FetchLike = (input, init) => globalThis.fetch(input, init);

export function setFetchImpl(fn: FetchLike | null): void {
  fetchImpl = fn ?? ((input, init) => globalThis.fetch(input, init));
}

export function getFetchImpl(): FetchLike {
  return fetchImpl;
}

let ctx: ApiContext = { baseUrl: DEFAULT_BASE_URL, authToken: null };

export function configureApi(next: Partial<ApiContext>): ApiContext {
  ctx = { ...ctx, ...next };
  if (!ctx.baseUrl) ctx.baseUrl = DEFAULT_BASE_URL;
  return ctx;
}

export function getApiContext(): ApiContext {
  return { ...ctx };
}

export function normalizeBaseUrl(baseUrl: string): string {
  const trimmed = (baseUrl || DEFAULT_BASE_URL).trim().replace(/\/+$/, '');
  return trimmed || DEFAULT_BASE_URL;
}

export function buildUrl(path: string, baseUrl?: string): string {
  const base = normalizeBaseUrl(baseUrl ?? ctx.baseUrl);
  if (/^https?:\/\//.test(path)) return path;
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

function messageFromError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof DOMException && err.name === 'AbortError') {
    return '请求超时或已中断';
  }
  if (err instanceof TypeError || (err instanceof Error && /failed to fetch/i.test(err.message))) {
    return '无法连接到服务器（请确认 agent-cluster serve 已启动）';
  }
  return err instanceof Error ? err.message : String(err);
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { method = 'GET', body, query, timeoutMs = 15000, raw = false } = options;
  const url = new URL(buildUrl(path));
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (ctx.authToken) headers['X-Auth-Token'] = ctx.authToken;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url.toString(), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    let payload: unknown = text;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        // 非 JSON 响应原样返回
      }
    }
    if (!res.ok) {
      const msg =
        payload && typeof payload === 'object' && 'error' in (payload as object)
          ? String((payload as ApiEnvelope<unknown>).error)
          : `HTTP ${res.status}`;
      throw new ApiError(msg, res.status, payload);
    }
    if (raw) return payload as T;
    if (payload && typeof payload === 'object' && 'ok' in (payload as object)) {
      const envelope = payload as ApiEnvelope<unknown>;
      if (envelope.ok) return envelope.data as T;
      throw new ApiError(envelope.error ?? '请求失败', res.status, payload);
    }
    return payload as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(messageFromError(err), undefined, err);
  } finally {
    clearTimeout(timer);
  }
}

// SSE：使用 fetch 流式读取（可携带 X-Auth-Token，支持 ?since 重放）
export interface SseSubscribeOptions {
  since?: number;
  signal?: AbortSignal;
  onError?: (err: unknown) => void;
  /** 会话终态哨兵（session.end）回调；只有收到哨兵才允许置终态（§6.3）。 */
  onTerminal?: (status: string) => void;
  /** 最大重连次数（缺省无限；测试可注入）。 */
  maxRetries?: number;
  /** 无任何字节即中断的窗口（缺省 30s，§6.3）。 */
  stallTimeoutMs?: number;
}

export function parseSseBlock(block: string): { event?: string; id?: string; data: string } | null {
  const lines = block.split('\n');
  let eventType: string | undefined;
  let eventId: string | undefined;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    else if (line.startsWith('event:')) eventType = line.slice(6).trim();
    else if (line.startsWith('id:')) eventId = line.slice(3).trim();
  }
  if (dataLines.length === 0) return null;
  return { event: eventType, id: eventId, data: dataLines.join('\n') };
}

const SSE_RETRY_CAP_MS = 15000;

function sseRetryDelayMs(attempt: number): number {
  // 指数退避 1s→2s→4s→8s→15s 封顶（§6.3）
  return Math.min(SSE_RETRY_CAP_MS, 1000 * 2 ** Math.max(0, attempt - 1));
}

export function subscribeSse<T = unknown>(
  path: string,
  onEvent: (event: T) => void,
  options: SseSubscribeOptions = {},
): () => void {
  const url = new URL(buildUrl(path));
  if (options.since !== undefined) url.searchParams.set('since', String(options.since));
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (ctx.authToken) headers['X-Auth-Token'] = ctx.authToken;
  const outer = options.signal;
  const maxRetries = options.maxRetries ?? Number.POSITIVE_INFINITY;

  let cancelled = false;
  let currentController: AbortController | null = null;
  let lastEventId: string | null = null;
  let attempt = 0;
  const onOuterAbort = () => {
    cancelled = true;
    currentController?.abort();
  };
  outer?.addEventListener('abort', onOuterAbort);

  const stop = () => {
    cancelled = true;
    currentController?.abort();
    outer?.removeEventListener('abort', onOuterAbort);
  };

  async function connect(): Promise<void> {
    if (cancelled) return;
    const controller = new AbortController();
    currentController = controller;
    const requestHeaders: Record<string, string> = { ...headers };
    // 重连续传：Last-Event-ID 优先，URL ?since= 为初始/回退（§6.3）
    if (lastEventId !== null) requestHeaders['Last-Event-ID'] = lastEventId;
    let stallTimer: ReturnType<typeof setTimeout> | null = null;
    const clearStall = () => {
      if (stallTimer !== null) {
        clearTimeout(stallTimer);
        stallTimer = null;
      }
    };
    const armStall = () => {
      clearStall();
      stallTimer = setTimeout(() => controller.abort(), options.stallTimeoutMs ?? 30000);
    };
    try {
      const res = await fetchImpl(url.toString(), {
        headers: requestHeaders,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new ApiError(`SSE 连接失败：HTTP ${res.status}`, res.status);
      }
      attempt = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      armStall();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        armStall();
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() ?? '';
        for (const block of blocks) {
          if (cancelled) return;
          const parsed = parseSseBlock(block);
          if (!parsed) continue;
          if (parsed.id !== undefined && parsed.id !== '') lastEventId = parsed.id;
          let payload: unknown = parsed.data;
          try {
            payload = JSON.parse(parsed.data);
          } catch {
            // 保持原始文本
          }
          let merged: Record<string, unknown>;
          if (payload && typeof payload === 'object') {
            const record = payload as Record<string, unknown>;
            merged = { ...record, event: parsed.event ?? record.type };
          } else {
            merged = { data: payload, event: parsed.event ?? 'message' };
          }
          const eventType =
            typeof merged.event === 'string' && merged.event
              ? merged.event
              : typeof merged.type === 'string'
                ? merged.type
                : '';
          if (eventType === 'session.end') {
            const status =
              typeof merged.status === 'string' && merged.status ? merged.status : 'completed';
            onEvent(merged as T);
            options.onTerminal?.(status);
            clearStall();
            return;
          }
          onEvent(merged as T);
        }
      }
      // 未收哨兵的正常 EOF 视为断线 → 重连，绝不静默终态（§6.3）
      throw new Error('SSE stream ended without terminal sentinel');
    } catch (err) {
      clearStall();
      if (cancelled) return;
      options.onError?.(err);
      if (attempt >= maxRetries) return;
      attempt += 1;
      const delay = sseRetryDelayMs(attempt);
      await new Promise((resolve) => setTimeout(resolve, delay));
      void connect();
    }
  }

  void connect();

  return stop;
}