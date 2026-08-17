import { resolve } from 'node:path'

import type { JsonValue, MutationMeta } from '@doai/protocol'
import { afterEach, describe, expect, it } from 'vitest'

import { OrganizationSupervisor, RpcPeerError } from '../src/index.ts'

const repositoryRoot = resolve(import.meta.dirname, '../../..')
const python = process.platform === 'win32'
  ? resolve(repositoryRoot, '.venv/Scripts/python.exe')
  : resolve(repositoryRoot, '.venv/bin/python')

describe('OrganizationSupervisor stdio E2E', () => {
  let supervisor: OrganizationSupervisor | undefined
  afterEach(async () => { await supervisor?.stop() })

  it('handshakes, serves bidirectional Host calls, and restarts cleanly', async () => {
    const events: Array<Record<string, JsonValue>> = []
    const idempotency = new Map<string, JsonValue>()
    const hostRequest = async (
      method: string,
      params: { [key: string]: JsonValue },
      mutation?: MutationMeta,
    ): Promise<JsonValue> => {
      if (method === 'session.idempotency.get') return idempotency.get(String(params.idempotency_key)) ?? null
      if (method === 'session.append') {
        if (mutation === undefined) throw new Error('mutation required')
        const cached = idempotency.get(mutation.idempotency_key)
        if (cached !== undefined) return cached
        if (mutation.session_revision !== events.length) throw new Error('revision conflict')
        const event = {
          schema_version: '1.0', session_id: params.session_id!, seq: events.length + 1,
          type: params.type!, ts: '2026-08-17T00:00:00Z', scope: params.scope!,
          payload: params.payload!, ignorable: false,
        } satisfies Record<string, JsonValue>
        events.push(event)
        const result = { event } satisfies Record<string, JsonValue>
        idempotency.set(mutation.idempotency_key, result)
        return result
      }
      if (method === 'agent.invoke') return { content: `${String(params.role_id)} completed` }
      if (method === 'approval.request') return { approved: true, reason: 'e2e' }
      throw new Error(`unexpected method: ${method}`)
    }
    supervisor = new OrganizationSupervisor({
      command: python,
      args: ['-m', 'doai_organization'],
      cwd: repositoryRoot,
      env: { PYTHONPATH: resolve(repositoryRoot, 'src') },
      hostRequest,
      heartbeatMs: 0,
      requestTimeoutMs: 20_000,
    })

    const result = await supervisor.call<Record<string, JsonValue>>('organization.run', {
      session_id: 'stdio-e2e',
      scope: { tenant_id: 'tenant', project_id: 'project' },
      requirement: 'Ship through the software company',
      budget: 100_000,
    }, { request_id: 'request-e2e', idempotency_key: 'run-e2e', session_revision: 0 })

    expect((result.event as Record<string, JsonValue>).type).toBe('organization.completed')
    expect(events.filter((event) => event.type === 'meeting.completed')).toHaveLength(7)
    const firstPid = supervisor.inspect().pid
    await supervisor.stop()
    expect(supervisor.inspect().running).toBe(false)
    expect(await supervisor.call('health', {})).toMatchObject({ status: 'ok' })
    expect(supervisor.inspect().pid).not.toBe(firstPid)
  }, 30_000)

  it('returns structured protocol faults', async () => {
    supervisor = new OrganizationSupervisor({
      command: python, args: ['-m', 'doai_organization'], cwd: repositoryRoot,
      env: { PYTHONPATH: resolve(repositoryRoot, 'src') },
      hostRequest: async () => null,
      heartbeatMs: 0,
    })
    await expect(supervisor.call('organization.run', { session_id: 'missing' }))
      .rejects.toMatchObject({ code: 'MUTATION_META_REQUIRED', retryable: false } satisfies Partial<RpcPeerError>)
  })
})
