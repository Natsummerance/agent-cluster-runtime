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
}

export function parseSseBlock(block: string): { event?: string; data: string } | null {
  const lines = block.split('\n');
  let eventType: string | undefined;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    else if (line.startsWith('event:')) eventType = line.slice(6).trim();
  }
  if (dataLines.length === 0) return null;
  return { event: eventType, data: dataLines.join('\n') };
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
  const controller = new AbortController();
  const outer = options.signal;
  const onOuterAbort = () => controller.abort();
  outer?.addEventListener('abort', onOuterAbort);

  let cancelled = false;
  const stop = () => {
    cancelled = true;
    controller.abort();
    outer?.removeEventListener('abort', onOuterAbort);
  };

  (async () => {
    try {
      const res = await fetchImpl(url.toString(), { headers, signal: controller.signal });
      if (!res.ok || !res.body) {
        throw new ApiError(`SSE 连接失败：HTTP ${res.status}`, res.status);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() ?? '';
        for (const block of blocks) {
          if (cancelled) return;
          const parsed = parseSseBlock(block);
          if (!parsed) continue;
          let payload: unknown = parsed.data;
          try {
            payload = JSON.parse(parsed.data);
          } catch {
            // 保持原始文本
          }
          const merged =
            payload && typeof payload === 'object'
              ? { ...(payload as Record<string, unknown>), event: parsed.event ?? (payload as Record<string, unknown>).type }
              : { data: payload, event: parsed.event ?? 'message' };
          onEvent(merged as T);
        }
      }
    } catch (err) {
      if (!cancelled) options.onError?.(err);
    }
  })();

  return stop;
}