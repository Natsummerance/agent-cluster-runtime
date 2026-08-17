import { describe, expect, it, vi } from 'vitest';

import { projectV1Session, V1ProtocolClient } from '../api/v1Client';
import type { JsonValue, SessionEvent } from '../api/v1.generated';

const event = (seq: number, type: string, payload: Record<string, JsonValue> = {}): SessionEvent => ({
  schema_version: '1.0', session_id: 's1', seq, type,
  ts: '2026-08-17T00:00:00Z', scope: { tenant_id: 't', project_id: 'p' },
  payload, ignorable: false,
});

describe('v1 generated protocol client', () => {
  it('deterministically projects the canonical event stream', () => {
    const projection = projectV1Session([
      event(1, 'organization.started'), event(2, 'task.created'),
      event(3, 'task.transitioned', { status: 'done' }),
      event(4, 'meeting.completed'), event(5, 'approval.requested'),
      event(6, 'approval.resolved'), event(7, 'organization.completed'),
    ]);
    expect(projection).toMatchObject({
      revision: 7, status: 'completed', taskCount: 1, completedTasks: 1,
      meetingCount: 1, approvalCount: 1,
    });
  });

  it('rejects gaps instead of silently degrading', () => {
    expect(() => projectV1Session([event(1, 'session.created'), event(3, 'agent.started')]))
      .toThrow('event gap');
  });

  it('sends mutation guards and surfaces structured faults', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      jsonrpc: '2.0', id: 'web-1', error: { code: 'REVISION_CONFLICT', message: 'stale', retryable: true },
    }), { status: 200 }));
    const client = new V1ProtocolClient('http://localhost:3000', fetchImpl);
    const error = await client.call('session.append', {}, {
      request_id: 'r', idempotency_key: 'k', session_revision: 2,
    }).catch((reason: unknown) => reason as Error & { code: string; retryable: boolean });

    expect(error).toMatchObject({ message: 'stale', code: 'REVISION_CONFLICT', retryable: true });
    expect(fetchImpl).toHaveBeenCalledWith(expect.any(URL), expect.objectContaining({
      body: expect.stringContaining('"session_revision":2'),
    }));
  });
});
