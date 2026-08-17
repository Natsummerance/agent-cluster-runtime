import type { JsonValue, MutationMeta, RpcFailure, RpcSuccess, SessionEvent } from './v1.generated';

export interface V1SessionProjection {
  sessionId: string;
  revision: number;
  status: 'idle' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled';
  taskCount: number;
  completedTasks: number;
  approvalCount: number;
  meetingCount: number;
  lastError?: string;
}

export function projectV1Session(events: SessionEvent[]): V1SessionProjection {
  let expected = 1;
  const projection: V1SessionProjection = {
    sessionId: events[0]?.session_id ?? '',
    revision: 0,
    status: 'idle',
    taskCount: 0,
    completedTasks: 0,
    approvalCount: 0,
    meetingCount: 0,
  };
  for (const event of events) {
    if (event.seq !== expected) throw new Error(`event gap: expected ${expected}, got ${event.seq}`);
    if (event.session_id !== projection.sessionId) throw new Error('mixed session ids in one projection');
    expected += 1;
    projection.revision = event.seq;
    if (event.type === 'agent.started' || event.type === 'organization.started') projection.status = 'running';
    else if (event.type === 'approval.requested') projection.status = 'waiting_approval';
    else if (event.type === 'approval.resolved') {
      projection.approvalCount += 1;
      projection.status = 'running';
    } else if (event.type === 'task.created') projection.taskCount += 1;
    else if (event.type === 'task.transitioned' && event.payload.status === 'done') projection.completedTasks += 1;
    else if (event.type === 'meeting.completed') projection.meetingCount += 1;
    else if (event.type === 'agent.completed' || event.type === 'organization.completed') projection.status = 'completed';
    else if (event.type === 'agent.failed' || event.type === 'organization.failed') {
      projection.status = 'failed';
      projection.lastError = String(event.payload.error ?? 'unknown error');
    } else if (event.type === 'organization.transitioned' && event.payload.status === 'cancelled') {
      projection.status = 'cancelled';
    }
  }
  return projection;
}

export class V1ProtocolClient {
  #nextId = 0;

  constructor(
    readonly baseUrl: string,
    readonly fetchImpl: typeof globalThis.fetch = globalThis.fetch,
  ) {}

  async call<Result extends JsonValue>(
    method: string,
    params: { [key: string]: JsonValue },
    mutation?: MutationMeta,
  ): Promise<Result> {
    const response = await this.fetchImpl(new URL('/api/v1/rpc', this.baseUrl), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0', id: `web-${this.#nextId += 1}`, method, params,
        ...(mutation === undefined ? {} : { mutation }),
      }),
    });
    if (!response.ok) throw new Error(`DoAI RPC transport failed with HTTP ${response.status}`);
    const envelope = await response.json() as RpcSuccess | RpcFailure;
    if ('error' in envelope) {
      const error = new Error(envelope.error.message) as Error & { code: string; retryable: boolean };
      error.code = envelope.error.code;
      error.retryable = envelope.error.retryable;
      throw error;
    }
    return envelope.result as Result;
  }
}
