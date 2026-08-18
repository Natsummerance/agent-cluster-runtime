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
  ToolRuntime,
} from '@doai/agent-runtime'
import { DoAIHost, type CapabilityPolicy, type DoAIPlugin } from '@doai/host'
import { describe, expect, it } from 'vitest'

import {
  createCodeToolPlugin,
  createCreatorPlugin,
  createMinimalAgentPlugin,
  createPythonRuntimePlugin,
  createTypeScriptRuntimePlugin,
  type CodeRuntime,
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
    ], {
      permissionGrants: [
        { plugin: `runtime-${language}`, kind: 'process', resource: language === 'python' ? 'config.executable' : 'bundled-node' },
        { plugin: 'tools-local', kind: 'filesystem', resource: 'config.workspace' },
        { plugin: 'tools-local', kind: 'process', resource: 'workspace-child' },
      ],
    })

    const tools = host.resolve<ToolRuntime>('tool.execute')
    const result = await tools.execute(`code.${language}`, language === 'python'
      ? { code: 'result = bindings["value"] * 2', bindings: { value: 6 } }
      : { code: '(bindings: any) => bindings.value * 2', bindings: { value: 6 } })
    expect(result).toBe(12)
    await host.dispose()
  })

  it('returns a shared ToolRuntime to baseline across 100 Code plugin load/unload cycles', async () => {
    const sharedTools = new ToolRuntime()
    sharedTools.register({
      name: 'baseline', description: 'baseline', risk: 'read',
      input_schema: { type: 'object', additionalProperties: false },
      async execute() { return 'baseline' },
    })
    const baseline = sharedTools.list().length
    const registry: DoAIPlugin = {
      manifest: {
        name: 'shared-tool-registry', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
        provides: ['tool.registry'], permissions: [], config_schema: { type: 'object', additionalProperties: false },
      },
      apply(ctx) { ctx.provide('tool.registry', sharedTools) },
    }
    const codeRuntime: CodeRuntime = {
      language: 'typescript',
      async evaluate(request) { return request.bindings.value ?? null },
    }
    const runtime: DoAIPlugin = {
      manifest: {
        name: 'fast-typescript-runtime', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
        provides: ['runtime.typescript'], permissions: [], config_schema: { type: 'object', additionalProperties: false },
      },
      apply(ctx) { ctx.provide('runtime.typescript', codeRuntime) },
    }
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(registry)
    host.register(runtime)
    host.register(createCodeToolPlugin('typescript'))

    for (let cycle = 0; cycle < 100; cycle += 1) {
      await host.activate([
        { plugin: 'shared-tool-registry' },
        { plugin: 'fast-typescript-runtime' },
        { plugin: 'tools-code-typescript' },
      ])
      expect(sharedTools.list()).toHaveLength(baseline + 1)
      expect(sharedTools.get('code.typescript').name).toBe('code.typescript')
      expect(await sharedTools.execute('code.typescript', { code: '', bindings: { value: cycle } })).toBe(cycle)

      await host.deactivate()
      expect(sharedTools.list()).toHaveLength(baseline)
      expect(() => sharedTools.get('code.typescript')).toThrow('unknown tool: code.typescript')
      expect(await sharedTools.execute('baseline', {})).toBe('baseline')
    }

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
    ], {
      permissionGrants: [
        { plugin: 'model-provider', kind: 'credential', resource: 'model-provider' },
        { plugin: 'session-store-jsonl', kind: 'filesystem', resource: 'config.root' },
      ],
      credentialProbe: async () => true,
    })

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
