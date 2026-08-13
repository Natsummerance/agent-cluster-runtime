import { test, expect } from '@playwright/test';
import WebSocket from 'ws';
import {
  api,
  expectOk,
  createProject,
  startSession,
  waitTerminal,
  uniqueSuffix,
  BASE_URL,
  AUTH_TOKEN,
} from './helpers/api';

interface WsFrame {
  type: string;
  id?: string;
  payload?: any;
}

function connectWs(sessionId: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${BASE_URL}/api/v1/ws?token=${AUTH_TOKEN}&session_id=${sessionId}`);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

function nextFrame(ws: WebSocket, predicate: (frame: WsFrame) => boolean, timeoutMs = 10_000): Promise<WsFrame> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error('WS 等待帧超时'));
    }, timeoutMs);
    const onMessage = (data: any) => {
      const frame = JSON.parse(data.toString()) as WsFrame;
      if (predicate(frame)) {
        cleanup();
        resolve(frame);
      }
    };
    const cleanup = () => {
      clearTimeout(timer);
      ws.off('message', onMessage);
    };
    ws.on('message', onMessage);
  });
}

test.describe('真实后端 ws', () => {
  test('subscribe → snapshot / ping → pong / cancel → ack 全链路', async ({ request }) => {
    const fixture = await createProject(request, `e2e-ws-${uniqueSuffix()}`);
    const sid = await startSession(request, fixture, { yes: false });
    const ws = await connectWs(sid);
    try {
      ws.send(JSON.stringify({ type: 'subscribe', id: 'sub-1', payload: { session_ids: [sid] } }));
      const snapshot = await nextFrame(ws, (f) => f.type === 'snapshot' && f.id === 'sub-1');
      expect(snapshot.payload.project).toBe(fixture.projectId);
      expect(Array.isArray(snapshot.payload.sessions)).toBe(true);
      expect(snapshot.payload.sessions.some((s: any) => s.session_id === sid)).toBe(true);
      expect(snapshot.payload.dashboard).toHaveProperty('cost');

      ws.send(JSON.stringify({ type: 'ping', id: 'ping-1' }));
      const pong = await nextFrame(ws, (f) => f.type === 'pong' && f.id === 'ping-1');
      expect(pong.payload).toEqual({});

      ws.send(JSON.stringify({ type: 'cancel', id: 'cancel-1', payload: { session_id: sid } }));
      const ack = await nextFrame(ws, (f) => f.type === 'ack' && f.id === 'cancel-1');
      expect(ack.payload).toEqual({ ok: true });
    } finally {
      ws.close();
    }

    await waitTerminal(request, sid);
    const audit = expectOk(await api(request, 'GET', `/api/v1/sessions/${sid}/audit`), 200, '审计');
    expect(audit.events.some((e: any) => e.type === 'session.cancel')).toBe(true);
  });
});