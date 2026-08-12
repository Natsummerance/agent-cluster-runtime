// SSE 事件 reducer：追加、去重、排序、快照提取（纯函数，便于测试）
import type { SessionEvent, SessionSnapshot } from '../api/types';

export function reduceEvent(events: SessionEvent[], event: SessionEvent): SessionEvent[] {
  if (!event) return events;
  if (typeof event.seq === 'number' && events.some((e) => e.seq === event.seq)) {
    return events;
  }
  const next = [...events, event];
  next.sort((a, b) => {
    const sa = typeof a.seq === 'number' ? a.seq : Number.MAX_SAFE_INTEGER;
    const sb = typeof b.seq === 'number' ? b.seq : Number.MAX_SAFE_INTEGER;
    return sa - sb;
  });
  return next;
}

export function lastSeq(events: SessionEvent[]): number {
  return events.reduce((max, e) => {
    const s = typeof e.seq === 'number' ? e.seq : 0;
    return Math.max(max, s);
  }, 0);
}

export function snapshotFromEvent(event: SessionEvent): SessionSnapshot | null {
  const d = event.data;
  if (d && typeof d === 'object' && 'session_id' in d) {
    return d as unknown as SessionSnapshot;
  }
  return null;
}