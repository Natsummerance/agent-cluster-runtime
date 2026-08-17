import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import {
  createApprovalPlugin,
  createLocalToolPlugin,
  createModelPlugin,
  createSessionStorePlugin,
  type ModelProvider,
  type StandardAgent,
  type ToolRuntime,
} from '@doai/agent-runtime'
import { DoAIHost, type CapabilityPolicy } from '@doai/host'
import { describe, expect, it } from 'vitest'

import {
  createCodeToolPlugin,
  createCreatorPlugin,
  createMinimalAgentPlugin,
  createPythonRuntimePlugin,
  createTypeScriptRuntimePlugin,
  type CreatorConformanceKit,
} from '../src/index.ts'

const root = resolve(import.meta.dirname, '../../..')
const python = process.platform === 'win32'
  ? resolve(root, '.venv/Scripts/python.exe')
  : resolve(root, '.venv/bin/python')
const policies: Record<string, CapabilityPolicy> = {
  'session.event-store': 'exactly_one', 'model.generate': 'exactly_one',
  'tool.registry': 'exactly_one', 'tool.execute': 'exactly_one',
  'approval.request': 'exactly_one', 'agent.invoke': 'exactly_one',
  'runtime.python': 'optional', 'runtime.typescript': 'optional',
  'creator.conformance': 'optional',
}

describe('v1 preset plugins', () => {
  it.each(['python', 'typescript'] as const)('runs Code-%s through a Host capability', async (language) => {
    const workspace = await mkdtemp(join(tmpdir(), 'doai-code-plugin-'))
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(createApprovalPlugin({ request: async () => ({ approved: true, reason: 'test' }) }))
    host.register(createLocalToolPlugin())
    host.register(language === 'python' ? createPythonRuntimePlugin() : createTypeScriptRuntimePlugin())
    host.register(createCodeToolPlugin(language))
    await host.activate([
      { plugin: `tools-code-${language}` },
      { plugin: `runtime-${language}`, ...(language === 'python' ? { config: { executable: python } } : {}) },
      { plugin: 'tools-local', config: { workspace } },
      { plugin: 'approval-provider' },
    ])

    const tools = host.resolve<ToolRuntime>('tool.execute')
    const result = await tools.execute(`code.${language}`, language === 'python'
      ? { code: 'result = bindings["value"] * 2', bindings: { value: 6 } }
      : { code: '(bindings: any) => bindings.value * 2', bindings: { value: 6 } })
    expect(result).toBe(12)
    await host.dispose()
  })

  it('runs Minimal without registering workspace tools', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-minimal-'))
    const model: ModelProvider = { generate: async () => ({ content: 'minimal answer', tool_calls: [] }) }
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(createSessionStorePlugin())
    host.register(createModelPlugin(model))
    host.register(createMinimalAgentPlugin())
    await host.activate([
      { plugin: 'agent-minimal' }, { plugin: 'model-provider' },
      { plugin: 'session-store-jsonl', config: { root: data } },
    ])

    const result = await host.resolve<StandardAgent>('agent.invoke').invoke({
      session_id: 'minimal', scope: { tenant_id: 't', project_id: 'p' }, input: 'hello', system_prompt: 'minimal',
    })
    expect(result.content).toBe('minimal answer')
    expect(() => host.resolve('tool.execute')).toThrow()
    await host.dispose()
  })

  it('exposes Creator conformance without installing a candidate', async () => {
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(createCreatorPlugin(policies))
    await host.activate([{ plugin: 'agent-creator' }])
    expect(host.resolve<CreatorConformanceKit>('creator.conformance')).toBeDefined()
    await host.dispose()
  })
})
