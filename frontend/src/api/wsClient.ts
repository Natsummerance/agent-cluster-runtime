// v0.6 T13.8：产品内 WebSocket 客户端（§6.4）
// 原生 WebSocket 直连 /api/v1/ws；握手失败或帧通道异常自动回退 SSE + REST。
import * as clientApi from './client';
import {
  approveSession,
  cancelSession,
  editSession,
  interruptSession,
  rejectSession,
  respondSession,
  sendSessionStdin,
} from './endpoints';

export type ApprovalDecision = 'approve' | 'reject' | 'edit' | 'response';

export interface WsEventFrame {
  type: 'event';
  seq: number;
  session_id: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface WsSnapshotFrame {
  type: 'snapshot';
  id?: string | number | null;
  payload: { project: string; dashboard: unknown; sessions: unknown[] };
}

export interface WsAckFrame {
  type: 'ack';
  id?: string | number | null;
  payload: { ok?: boolean };
}

export interface WsErrorFrame {
  type: 'error';
  id?: string | number | null;
  payload: { code: string; message: string; fatal?: boolean };
}

export interface WebSocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onclose: ((ev: { code?: number }) => void) | null;
}

export interface WsConnectOptions {
  baseUrl: string;
  authToken?: string | null;
  sessionIds?: string[];
  onEvent?: (event: WsEventFrame) => void;
  onSnapshot?: (snapshot: WsSnapshotFrame) => void;
  onAck?: (ack: WsAckFrame) => void;
  onError?: (err: unknown) => void;
  /** 测试注入：假 socket 类。 */
  WebSocketCtor?: new (url: string) => WebSocketLike;
}

export interface WsClient {
  mode: 'ws' | 'sse';
  close(): void;
  cancel(sessionId: string): Promise<void>;
  approval(sessionId: string, decision: ApprovalDecision, text?: string): Promise<void>;
  interrupt(sessionId: string, text: string): Promise<void>;
  stdin(sessionId: string, text: string): Promise<void>;
}

const WS_OPEN = 1;

function ssePath(sid: string): string {
  return `/api/v1/sessions/${encodeURIComponent(sid)}/events`;
}

function toWsEventFrame(event: Record<string, unknown>): WsEventFrame {
  return {
    type: 'event',
    seq: Number(event.seq ?? 0),
    session_id: String(event.session_id ?? ''),
    event_type: String(event.type ?? ''),
    payload: (event.payload as Record<string, unknown>) ?? {},
  };
}

export function connectWs(options: WsConnectOptions): WsClient {
  const baseUrl = clientApi.normalizeBaseUrl(options.baseUrl);
  const sessionIds = options.sessionIds ?? [];
  let mode: 'ws' | 'sse' = 'ws';
  let socket: WebSocketLike | null = null;
  const sseStops: Array<() => void> = [];
  let closed = false;
  let handshakeDone = false;
  let nextId = 1;

  const startSseFallback = () => {
    if (mode === 'sse') return;
    mode = 'sse';
    if (socket) {
      try {
        socket.close();
      } catch {
        // 忽略关闭异常
      }
      socket = null;
    }
    for (const sid of sessionIds) {
      sseStops.push(
        clientApi.subscribeSse(
          ssePath(sid),
          (event) => options.onEvent?.(toWsEventFrame(event as Record<string, unknown>)),
          { onError: (err) => options.onError?.(err) },
        ),
      );
    }
  };

  const Ctor: new (url: string) => WebSocketLike =
    options.WebSocketCtor ?? (globalThis as unknown as { WebSocket: new (url: string) => WebSocketLike }).WebSocket;
  const wsUrl = new URL(baseUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:'));
  wsUrl.pathname = '/api/v1/ws';
  wsUrl.search = '';
  if (options.authToken) wsUrl.searchParams.set('token', options.authToken);
  if (sessionIds.length === 1) wsUrl.searchParams.set('session_id', sessionIds[0]);
  socket = new Ctor(wsUrl.toString());

  socket.onopen = () => {
    handshakeDone = true;
    if (closed) return;
    socket?.send(
      JSON.stringify({ type: 'subscribe', id: `s${nextId++}`, payload: { session_ids: sessionIds } }),
    );
  };
  socket.onmessage = (ev) => {
    let message: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(String(ev.data));
      if (typeof parsed !== 'object' || parsed === null) throw new Error('非对象帧');
      message = parsed as Record<string, unknown>;
    } catch (err) {
      options.onError?.(err instanceof Error ? err : new Error('WS 帧 JSON 解析失败'));
      return;
    }
    switch (message.type) {
      case 'event':
        options.onEvent?.(message as unknown as WsEventFrame);
        return;
      case 'snapshot':
        options.onSnapshot?.(message as unknown as WsSnapshotFrame);
        return;
      case 'ack':
        options.onAck?.(message as unknown as WsAckFrame);
        return;
      case 'error': {
        const payload = (message.payload ?? {}) as { code?: string; message?: string; fatal?: boolean };
        options.onError?.(new clientApi.ApiError(payload.message ?? 'WS 通道错误', undefined, payload));
        return;
      }
      case 'pong':
        return; // 心跳应答
      default:
        options.onError?.(new Error(`未知 WS 帧类型：${String(message.type)}`));
    }
  };
  socket.onerror = () => {
    if (!handshakeDone) {
      options.onError?.(new Error('WebSocket 握手失败（非 101），回退 SSE + REST'));
      startSseFallback();
    }
  };
  socket.onclose = () => {
    if (closed) return;
    if (!handshakeDone) {
      startSseFallback();
      return;
    }
    // 已建立后的异常断开（帧通道异常）→ 回退 SSE + REST
    options.onError?.(new Error('WebSocket 连接中断，回退 SSE + REST'));
    startSseFallback();
  };

  const sendWs = (message: Record<string, unknown>): boolean => {
    if (socket && socket.readyState === WS_OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  const client: WsClient = {
    mode: 'ws',
    close() {
      if (closed) return;
      closed = true;
      for (const stop of sseStops) {
        try {
          stop();
        } catch {
          // 忽略
        }
      }
      try {
        socket?.close();
      } catch {
        // 忽略
      }
    },
    async cancel(sessionId: string) {
      if (sendWs({ type: 'cancel', id: `c${nextId++}`, payload: { session_id: sessionId } })) return;
      await cancelSession(sessionId);
    },
    async approval(sessionId: string, decision: ApprovalDecision, text?: string) {
      if (
        sendWs({
          type: 'approval',
          id: `a${nextId++}`,
          payload: { session_id: sessionId, decision, text: text ?? '' },
        })
      ) {
        return;
      }
      if (decision === 'approve') await approveSession(sessionId);
      else if (decision === 'reject') await rejectSession(sessionId);
      else if (decision === 'edit') await editSession(sessionId, text ?? '');
      else await respondSession(sessionId, text ?? '');
    },
    async interrupt(sessionId: string, text: string) {
      if (
        sendWs({ type: 'interrupt', id: `i${nextId++}`, payload: { session_id: sessionId, text } })
      ) {
        return;
      }
      await interruptSession(sessionId, text);
    },
    async stdin(sessionId: string, text: string) {
      if (sendWs({ type: 'stdin', id: `n${nextId++}`, payload: { session_id: sessionId, text } })) {
        return;
      }
      await sendSessionStdin(sessionId, text);
    },
  };
  Object.defineProperty(client, 'mode', {
    get: () => mode,
    enumerable: true,
  });
  return client;
}
