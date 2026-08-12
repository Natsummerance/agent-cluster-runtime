import { describe, expect, it } from 'vitest';
import { lastSeq, reduceEvent, snapshotFromEvent } from '../store/sseReducer';
import type { SessionEvent } from '../api/types';

const e = (seq: number, type = 'message'): SessionEvent => ({ seq, type, data: { seq } });

describe('SSE 事件 reducer', () => {
  it('追加事件并按 seq 升序排序', () => {
    const state = reduceEvent([], e(3));
    const next = reduceEvent(state, e(1));
    const final = reduceEvent(next, e(2));
    expect(final.map((x) => x.seq)).toEqual([1, 2, 3]);
  });

  it('按 seq 去重，重复事件被忽略', () => {
    const state = reduceEvent([], e(1));
    const next = reduceEvent(state, e(1));
    expect(next).toHaveLength(1);
  });

  it('无 seq 事件追加且排在有 seq 事件之后', () => {
    const a = reduceEvent([], e(5));
    const b = reduceEvent(a, { type: 'ping' } as SessionEvent);
    expect(b).toHaveLength(2);
    expect(b[1].seq).toBeUndefined();
  });

  it('lastSeq 返回当前最大 seq（无事件时 0）', () => {
    expect(lastSeq([])).toBe(0);
    expect(lastSeq([e(3), e(9), e(2)])).toBe(9);
  });

  it('snapshotFromEvent 从携带 session_id 的 data 提取快照', () => {
    const event: SessionEvent = {
      seq: 4,
      type: 'snapshot',
      data: { session_id: 's1', project_id: 'p1', goal: 'g', status: 'running', token: {} },
    };
    const snap = snapshotFromEvent(event);
    expect(snap?.session_id).toBe('s1');
    expect(snap?.status).toBe('running');
  });

  it('snapshotFromEvent 对无 session_id 事件返回 null', () => {
    expect(snapshotFromEvent({ seq: 1, type: 'message', data: { text: 'hi' } })).toBeNull();
    expect(snapshotFromEvent({ seq: 2, type: 'ping' })).toBeNull();
  });
});