import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import {
  createApprovalPlugin,
  createLocalToolPlugin,
  createModelPlugin,
  createSessionStorePlugin,
  createStandardAgentPlugin,
  type IdempotentSessionEventStore,
  type ModelProvider,
} from '@doai/agent-runtime'
import { DoAIHost, type CapabilityPolicy } from '@doai/host'
import type { JsonValue } from '@doai/protocol'
import { describe, expect, it } from 'vitest'

import { createOrganizationPlanePlugin, type OrganizationRunClient } from '../src/index.ts'

const root = resolve(import.meta.dirname, '../../..')
const python = process.platform === 'win32'
  ? resolve(root, '.venv/Scripts/python.exe')
  : resolve(root, '.venv/bin/python')

const policies: Record<string, CapabilityPolicy> = {
  'session.event-store': 'exactly_one',
  'model.generate': 'exactly_one',
  'tool.registry': 'exactly_one',
  'tool.execute': 'exactly_one',
  'approval.request': 'exactly_one',
  'agent.invoke': 'exactly_one',
  'transport.organization': 'optional',
  'organization.run': 'optional',
}

describe('Python Organization Plane Host plugin', () => {
  it('delivers all 12 roles and 7 meetings through the durable Host spine', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-org-plugin-data-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-org-plugin-workspace-'))
    const model: ModelProvider = { generate: async () => ({ content: 'role delivery complete', tool_calls: [] }) }
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(createSessionStorePlugin())
    host.register(createApprovalPlugin({ request: async () => ({ approved: true, reason: 'e2e approval' }) }))
    host.register(createModelPlugin(model))
    host.register(createLocalToolPlugin())
    host.register(createStandardAgentPlugin())
    host.register(createOrganizationPlanePlugin())

    await host.activate([
      { plugin: 'organization-plane-python', config: {
        command: python,
        cwd: root,
        env: { PYTHONPATH: resolve(root, 'src') },
        heartbeat_ms: 0,
      } },
      { plugin: 'agent-standard' },
      { plugin: 'tools-local', config: { workspace } },
      { plugin: 'model-provider' },
      { plugin: 'session-store-jsonl', config: { root: data } },
      { plugin: 'approval-provider' },
    ], {
      permissionGrants: [
        { plugin: 'organization-plane-python', kind: 'process', resource: 'config.command' },
        { plugin: 'tools-local', kind: 'filesystem', resource: 'config.workspace' },
        { plugin: 'tools-local', kind: 'process', resource: 'workspace-child' },
        { plugin: 'model-provider', kind: 'credential', resource: 'model-provider' },
        { plugin: 'session-store-jsonl', kind: 'filesystem', resource: 'config.root' },
      ],
      credentialProbe: async () => true,
    })

    const client = host.resolve<OrganizationRunClient>('organization.run')
    const mutation = { request_id: 'org-e2e-request', idempotency_key: 'org-e2e-run', session_revision: 0 }
    const params = {
      session_id: 'org-e2e',
      scope: { tenant_id: 'tenant', project_id: 'project' },
      requirement: 'Deliver the complete product flow',
      budget: 120_000,
    }
    const first = await client.run(params, mutation)
    const store = host.resolve<IdempotentSessionEventStore>('session.event-store')
    const events = await store.read('org-e2e')
    const beforeReplay = events.length
    const replay = await client.run(params, mutation)

    expect(replay).toEqual(first)
    expect(await store.revision('org-e2e')).toBe(beforeReplay)
    expect(events.filter((event) => event.type === 'agent.completed')).toHaveLength(12)
    expect(events.filter((event) => event.type === 'meeting.completed')).toHaveLength(7)
    expect(events.filter((event) => event.type === 'task.created')).toHaveLength(12)
    const projection = await client.project(events as unknown as JsonValue[])
    expect(projection).toMatchObject({ status: 'completed', evolution_proposals: 1 })
    await host.dispose()
    expect(host.inspect().active).toBe(false)
  }, 60_000)
})
