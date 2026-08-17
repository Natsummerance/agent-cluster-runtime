import { StandardAgent, ToolRuntime, type ModelProvider, type SessionEventStore } from '@doai/agent-runtime'
import type { CapabilityPolicy, DoAIPlugin } from '@doai/host'
import type { JsonValue } from '@doai/protocol'

import { PythonCodeRuntime, TypeScriptCodeRuntime, type CodeRuntime } from './code-runtime.ts'
import { CreatorConformanceKit } from './creator.ts'

export function createPythonRuntimePlugin(): DoAIPlugin<{ executable: string }> {
  return {
    manifest: {
      name: 'runtime-python', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['runtime.python'],
      permissions: [{ kind: 'process', resources: ['config.executable'], reason: 'isolated Python code process' }],
      config_schema: { type: 'object', additionalProperties: false, required: ['executable'], properties: { executable: { type: 'string', minLength: 1 } } },
    },
    apply(ctx, config) { ctx.provide('runtime.python', new PythonCodeRuntime(config.executable)) },
  }
}

export function createTypeScriptRuntimePlugin(): DoAIPlugin {
  return {
    manifest: {
      name: 'runtime-typescript', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['runtime.typescript'],
      permissions: [{ kind: 'process', resources: ['bundled-node'], reason: 'isolated TypeScript VM process' }],
      config_schema: { type: 'object', additionalProperties: false },
    },
    apply(ctx) { ctx.provide('runtime.typescript', new TypeScriptCodeRuntime()) },
  }
}

export function createCodeToolPlugin(language: 'python' | 'typescript'): DoAIPlugin {
  const capability = `runtime.${language}`
  return {
    manifest: {
      name: `tools-code-${language}`, version: '1.0.0', api_version: '1', dependencies: {},
      requires: ['tool.registry', capability], provides: [], permissions: [],
      config_schema: { type: 'object', additionalProperties: false },
    },
    apply(ctx) {
      const runtime = ctx.resolve<CodeRuntime>(capability)
      ctx.resolve<ToolRuntime>('tool.registry').register({
        name: `code.${language}`,
        description: `Evaluate ${language} code with JSON bindings`,
        risk: 'process',
        input_schema: {
          type: 'object', additionalProperties: false, required: ['code', 'bindings'],
          properties: { code: { type: 'string' }, bindings: { type: 'object' } },
        },
        async execute(args) {
          return await runtime.evaluate({
            code: String(args.code),
            bindings: args.bindings as { [key: string]: JsonValue },
          })
        },
      })
    },
  }
}

export function createMinimalAgentPlugin(): DoAIPlugin<{ max_steps?: number }> {
  return {
    manifest: {
      name: 'agent-minimal', version: '1.0.0', api_version: '1', dependencies: {},
      requires: ['session.event-store', 'model.generate'], provides: ['agent.invoke'], permissions: [],
      config_schema: { type: 'object', additionalProperties: false, properties: { max_steps: { type: 'integer', minimum: 1 } } },
    },
    apply(ctx, config) {
      ctx.provide('agent.invoke', new StandardAgent({
        store: ctx.resolve<SessionEventStore>('session.event-store'),
        model: ctx.resolve<ModelProvider>('model.generate'),
        tools: new ToolRuntime(),
        approval: { request: async () => ({ approved: false, reason: 'Minimal preset has no tools' }) },
        maxSteps: config.max_steps ?? 4,
      }))
    },
  }
}

export function createCreatorPlugin(policies: Record<string, CapabilityPolicy>): DoAIPlugin {
  return {
    manifest: {
      name: 'agent-creator', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['creator.conformance'], permissions: [], config_schema: { type: 'object', additionalProperties: false },
    },
    apply(ctx) { ctx.provide('creator.conformance', new CreatorConformanceKit(policies)) },
  }
}
