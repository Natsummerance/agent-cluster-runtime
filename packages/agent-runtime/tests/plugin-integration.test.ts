import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { DoAIHost, type CapabilityPolicy, type DoAIPlugin } from '@doai/host'
import { describe, expect, it } from 'vitest'

import {
  createApprovalPlugin,
  createLocalToolPlugin,
  createModelPlugin,
  createSessionStorePlugin,
  createStandardAgentPlugin,
  type ModelProvider,
  type StandardAgent,
} from '../src/index.ts'

const policies: Record<string, CapabilityPolicy> = {
  'session.event-store': 'exactly_one',
  'model.generate': 'exactly_one',
  'tool.registry': 'exactly_one',
  'tool.execute': 'exactly_one',
  'approval.request': 'exactly_one',
  'agent.invoke': 'exactly_one',
}

describe('M2 Host integration', () => {
  it('runs Standard agent only through registered Host capabilities', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-plugin-data-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-plugin-workspace-'))
    await writeFile(join(workspace, 'a.txt'), 'A', 'utf8')
    let modelCalls = 0
    const model: ModelProvider = {
      generate: async () => modelCalls++ === 0
        ? { content: '', tool_calls: [{ id: 'read', name: 'workspace.read', arguments: { path: 'a.txt' } }] }
        : { content: 'done', tool_calls: [] },
    }
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(createSessionStorePlugin())
    host.register(createApprovalPlugin({ request: async () => ({ approved: true, reason: 'test' }) }))
    host.register(createModelPlugin(model))
    host.register(createLocalToolPlugin())
    host.register(createStandardAgentPlugin())
    let intercepted = 0
    const policy: DoAIPlugin = {
      manifest: {
        name: 'tool-audit-policy', version: '1.0.0', api_version: '1', dependencies: {},
        requires: ['tool.execute'], provides: [], permissions: [], config_schema: { type: 'object', additionalProperties: false },
      },
      apply(ctx) {
        ctx.intercept('tool.execute', async (_payload, next) => {
          intercepted += 1
          return await next()
        })
      },
    }
    host.register(policy)

    await host.activate([
      { plugin: 'agent-standard' },
      { plugin: 'tools-local', config: { workspace } },
      { plugin: 'model-provider' },
      { plugin: 'session-store-jsonl', config: { root: data } },
      { plugin: 'approval-provider' },
      { plugin: 'tool-audit-policy' },
    ])
    const agent = host.resolve<StandardAgent>('agent.invoke')
    const result = await agent.invoke({
      session_id: 'integrated',
      scope: { tenant_id: 'tenant', project_id: 'project' },
      input: 'finish',
      system_prompt: 'test',
    })

    expect(result.content).toBe('done')
    expect(intercepted).toBe(1)
    expect(host.inspect().providers).toBe(6)
    await host.dispose()
    expect(host.inspect().providers).toBe(0)
  })
})
