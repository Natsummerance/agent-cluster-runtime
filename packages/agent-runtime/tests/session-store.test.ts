import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { JsonlSessionEventStore, RevisionConflictError } from '../src/index.ts'

const scope = { tenant_id: 'tenant-a', project_id: 'project-a' }

describe('JsonlSessionEventStore', () => {
  it('assigns durable sequence numbers and recovers after restart', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-session-'))
    const first = new JsonlSessionEventStore(root, () => new Date('2026-08-17T00:00:00Z'))
    await first.append('session-1', 0, { type: 'session.created', scope, payload: {}, ignorable: false })
    await first.append('session-1', 1, { type: 'input.received', scope, payload: { content: 'hello' }, ignorable: false })

    const restarted = new JsonlSessionEventStore(root)
    expect(await restarted.revision('session-1')).toBe(2)
    expect((await restarted.read('session-1')).map((event) => event.seq)).toEqual([1, 2])
    await expect(restarted.append('session-1', 1, {
      type: 'agent.started', scope, payload: {}, ignorable: false,
    })).rejects.toBeInstanceOf(RevisionConflictError)
    expect(await restarted.revision('session-1')).toBe(2)
  })

  it('forks a replayable prefix without sharing mutable storage', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-fork-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-17T00:00:00Z'))
    await store.append('source', 0, { type: 'session.created', scope, payload: {}, ignorable: false })
    await store.append('source', 1, { type: 'input.received', scope, payload: { content: 'one' }, ignorable: false })
    await store.append('source', 2, { type: 'input.received', scope, payload: { content: 'two' }, ignorable: false })

    await store.fork('source', 2, 'branch')
    await store.append('source', 3, { type: 'session.closed', scope, payload: {}, ignorable: false })

    const branch = await store.read('branch')
    expect(branch.map((event) => event.type)).toEqual(['session.created', 'input.received', 'session.forked'])
    expect(branch.every((event) => event.session_id === 'branch')).toBe(true)
    expect(await store.revision('branch')).toBe(3)
  })

  it('rejects session identifiers that could escape the store root', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-safe-'))
    const store = new JsonlSessionEventStore(root)
    await expect(store.read('../outside')).rejects.toThrow('invalid session id')
  })

  it('allows exactly one concurrent compare-and-append winner', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-cas-'))
    const store = new JsonlSessionEventStore(root)
    const draft = { type: 'session.created', scope, payload: {}, ignorable: false }

    const results = await Promise.allSettled([
      store.append('race', 0, draft),
      store.append('race', 0, draft),
    ])

    expect(results.filter((result) => result.status === 'fulfilled')).toHaveLength(1)
    expect(results.filter((result) => result.status === 'rejected')).toHaveLength(1)
    expect(await store.revision('race')).toBe(1)
  })
})
