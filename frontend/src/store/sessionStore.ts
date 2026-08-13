// 会话 store：详情 + 变更历史 + SSE 订阅 + 门审批操作
import { create } from 'zustand';
import { apiErrorMessage } from './appStore';
import * as api from '../api/endpoints';
import type { ChangeData, SessionEvent, SessionSnapshot } from '../api/types';
import { lastSeq, reduceEvent, snapshotFromEvent } from './sseReducer';

interface ApprovalState {
  sid: string | null;
  hint: string | null;
  open: boolean;
  loading: boolean;
}

interface SessionState {
  snapshots: Record<string, SessionSnapshot>;
  changes: Record<string, ChangeData>;
  events: Record<string, SessionEvent[]>;
  eventErrors: Record<string, string | null>;
  approval: ApprovalState;
  error: string | null;
  loading: Record<string, boolean>;
  fetchSession(sid: string): Promise<SessionSnapshot | null>;
  fetchChanges(sid: string): Promise<ChangeData | null>;
  subscribe(sid: string): () => void;
  unsubscribe(sid: string): void;
  disposeAll(): void;
  handleEvent(sid: string, event: SessionEvent): void;
  openApproval(sid: string, hint: string): void;
  closeApproval(): void;
  approve(): Promise<void>;
  reject(): Promise<void>;
  edit(text: string): Promise<void>;
  respond(text: string): Promise<void>;
  interrupt(sid: string, text: string): Promise<void>;
  stdin(sid: string, text: string): Promise<void>;
  rollback(sid: string, version: string | number): Promise<void>;
  clearSession(sid: string): void;
  resetState(): void;
}

// 模块级 SSE 订阅表（便于测试与页面卸载清理）
const subscriptions = new Map<string, () => void>();

const initialState = {
  snapshots: {} as Record<string, SessionSnapshot>,
  changes: {} as Record<string, ChangeData>,
  events: {} as Record<string, SessionEvent[]>,
  eventErrors: {} as Record<string, string | null>,
  approval: { sid: null, hint: null, open: false, loading: false } as ApprovalState,
  error: null as string | null,
  loading: {} as Record<string, boolean>,
};

export const useSessionStore = create<SessionState>()((set, get) => ({
  ...initialState,

  async fetchSession(sid: string): Promise<SessionSnapshot | null> {
    set({ loading: { ...get().loading, [sid]: true }, error: null });
    try {
      const snapshot = await api.fetchSession(sid);
      set((state) => ({
        snapshots: { ...state.snapshots, [sid]: snapshot },
        loading: { ...state.loading, [sid]: false },
      }));
      if (snapshot.status === 'waiting_approval' && snapshot.pending_hint) {
        const approval = get().approval;
        if (!approval.open || approval.sid !== sid) {
          get().openApproval(sid, snapshot.pending_hint);
        }
      }
      return snapshot;
    } catch (err) {
      set((state) => ({
        error: apiErrorMessage(err),
        loading: { ...state.loading, [sid]: false },
      }));
      return null;
    }
  },

  async fetchChanges(sid: string): Promise<ChangeData | null> {
    try {
      const changes = await api.fetchSessionChanges(sid);
      set((state) => ({ changes: { ...state.changes, [sid]: changes } }));
      return changes;
    } catch (err) {
      set({ error: apiErrorMessage(err) });
      return null;
    }
  },

  subscribe(sid: string): () => void {
    const existing = subscriptions.get(sid);
    if (existing) existing();
    const current = get().events[sid] ?? [];
    const stop = api.subscribeSessionEvents(
      sid,
      (event) => get().handleEvent(sid, event),
      {
        since: lastSeq(current),
        onError: (err) => {
          set((state) => ({
            eventErrors: { ...state.eventErrors, [sid]: apiErrorMessage(err) },
          }));
        },
      },
    );
    subscriptions.set(sid, stop);
    return () => get().unsubscribe(sid);
  },

  unsubscribe(sid: string) {
    const stop = subscriptions.get(sid);
    if (stop) {
      stop();
      subscriptions.delete(sid);
    }
  },

  disposeAll() {
    for (const stop of subscriptions.values()) stop();
    subscriptions.clear();
  },

  handleEvent(sid: string, event: SessionEvent) {
    const state = get();
    const events = reduceEvent(state.events[sid] ?? [], event);
    const snapshot = snapshotFromEvent(event);
    const snapshots =
      snapshot && snapshot.session_id
        ? { ...state.snapshots, [snapshot.session_id]: snapshot }
        : state.snapshots;
    set({ events: { ...state.events, [sid]: events }, snapshots });
    if (snapshot && snapshot.session_id && snapshot.status === 'waiting_approval' && snapshot.pending_hint) {
      get().openApproval(snapshot.session_id, snapshot.pending_hint);
    }
  },

  openApproval(sid: string, hint: string) {
    set({ approval: { sid, hint, open: true, loading: false } });
  },

  closeApproval() {
    set((state) => ({ approval: { ...state.approval, open: false } }));
  },

  async approve() {
    const { sid } = get().approval;
    if (!sid) return;
    set((state) => ({ approval: { ...state.approval, loading: true } }));
    try {
      await api.approveSession(sid);
      await get().fetchSession(sid);
      get().closeApproval();
    } catch (err) {
      set((state) => ({
        approval: { ...state.approval, loading: false },
        error: apiErrorMessage(err),
      }));
      throw err;
    }
  },

  async reject() {
    const { sid } = get().approval;
    if (!sid) return;
    set((state) => ({ approval: { ...state.approval, loading: true } }));
    try {
      await api.rejectSession(sid);
      await get().fetchSession(sid);
      get().closeApproval();
    } catch (err) {
      set((state) => ({
        approval: { ...state.approval, loading: false },
        error: apiErrorMessage(err),
      }));
      throw err;
    }
  },

  async edit(text: string) {
    const { sid } = get().approval;
    if (!sid) return;
    set((state) => ({ approval: { ...state.approval, loading: true } }));
    try {
      await api.editSession(sid, text);
      await get().fetchSession(sid);
      get().closeApproval();
    } catch (err) {
      set((state) => ({
        approval: { ...state.approval, loading: false },
        error: apiErrorMessage(err),
      }));
      throw err;
    }
  },

  async respond(text: string) {
    const { sid } = get().approval;
    if (!sid) return;
    set((state) => ({ approval: { ...state.approval, loading: true } }));
    try {
      await api.respondSession(sid, text);
      await get().fetchSession(sid);
      get().closeApproval();
    } catch (err) {
      set((state) => ({
        approval: { ...state.approval, loading: false },
        error: apiErrorMessage(err),
      }));
      throw err;
    }
  },

  async interrupt(sid: string, text: string) {
    try {
      await api.interruptSession(sid, text);
      await get().fetchSession(sid);
    } catch (err) {
      set({ error: apiErrorMessage(err) });
      throw err;
    }
  },

  async stdin(sid: string, text: string) {
    try {
      await api.stdinSession(sid, text);
      await get().fetchSession(sid);
    } catch (err) {
      set({ error: apiErrorMessage(err) });
      throw err;
    }
  },

  async rollback(sid: string, version: string | number) {
    try {
      await api.rollbackSession(sid, version);
      await get().fetchChanges(sid);
      await get().fetchSession(sid);
    } catch (err) {
      set({ error: apiErrorMessage(err) });
      throw err;
    }
  },

  clearSession(sid: string) {
    get().unsubscribe(sid);
    set((state) => {
      const snapshots = { ...state.snapshots };
      const changes = { ...state.changes };
      const events = { ...state.events };
      const eventErrors = { ...state.eventErrors };
      delete snapshots[sid];
      delete changes[sid];
      delete events[sid];
      delete eventErrors[sid];
      return { snapshots, changes, events, eventErrors };
    });
  },

  resetState() {
    get().disposeAll();
    set({ ...initialState });
  },
}));

export function getSubscribedSids(): string[] {
  return [...subscriptions.keys()];
}