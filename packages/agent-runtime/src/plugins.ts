import type { DoAIPlugin } from '@doai/host'

import { StandardAgent, type ModelProvider } from './agent.ts'
import { EnvironmentCredentialResolver, OpenAICompatibleModelProvider, type CredentialResolver } from './model-openai.ts'
import { JsonlSessionEventStore, type SessionEventStore } from './session-store.ts'
import { LocalExecutionWorld, ToolRuntime, type ApprovalService } from './tools.ts'

const emptyConfig = { type: 'object', additionalProperties: false } as const

export function createSessionStorePlugin(): DoAIPlugin<{ root: string }> {
  return {
    manifest: {
      name: 'session-store-jsonl', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['session.event-store'], permissions: [{ kind: 'filesystem', resources: ['config.root'], reason: 'durable session events' }],
      config_schema: { type: 'object', additionalProperties: false, required: ['root'], properties: { root: { type: 'string', minLength: 1 } } },
    },
    apply(ctx, config) { ctx.provide<SessionEventStore>('session.event-store', new JsonlSessionEventStore(config.root)) },
  }
}

export function createApprovalPlugin(service: ApprovalService): DoAIPlugin {
  return {
    manifest: {
      name: 'approval-provider', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['approval.request'], permissions: [], config_schema: emptyConfig,
    },
    apply(ctx) { ctx.provide('approval.request', service) },
  }
}

export function createModelPlugin(provider: ModelProvider): DoAIPlugin {
  return {
    manifest: {
      name: 'model-provider', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['model.generate'], permissions: [{ kind: 'credential', resources: ['model-provider'], reason: 'model authentication' }],
      config_schema: emptyConfig,
    },
    apply(ctx) { ctx.provide('model.generate', provider) },
  }
}

export function createEnvironmentCredentialPlugin(handles: Record<string, string>): DoAIPlugin {
  return {
    manifest: {
      name: 'credentials-environment', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['credential.resolve'],
      permissions: [{ kind: 'credential', resources: Object.keys(handles), reason: 'resolve opaque handles from process environment' }],
      config_schema: emptyConfig,
    },
    apply(ctx) { ctx.provide('credential.resolve', new EnvironmentCredentialResolver(handles)) },
  }
}

export function createOpenAICompatibleModelPlugin(): DoAIPlugin<{
  base_url: string
  model: string
  credential_handle: string
}> {
  return {
    manifest: {
      name: 'model-openai-compatible', version: '1.0.0', api_version: '1', dependencies: {},
      requires: ['credential.resolve'], provides: ['model.generate'],
      permissions: [
        { kind: 'network', resources: ['config.base_url'], reason: 'model inference API' },
        { kind: 'credential', resources: ['config.credential_handle'], reason: 'model authentication' },
      ],
      config_schema: {
        type: 'object', additionalProperties: false,
        required: ['base_url', 'model', 'credential_handle'],
        properties: {
          base_url: { type: 'string', minLength: 1 },
          model: { type: 'string', minLength: 1 },
          credential_handle: { type: 'string', minLength: 1 },
        },
      },
    },
    apply(ctx, config) {
      ctx.provide('model.generate', new OpenAICompatibleModelProvider({
        baseUrl: config.base_url,
        model: config.model,
        credentialHandle: config.credential_handle,
        credentials: ctx.resolve<CredentialResolver>('credential.resolve'),
      }))
    },
  }
}

export function createLocalToolPlugin(): DoAIPlugin<{ workspace: string }> {
  return {
    manifest: {
      name: 'tools-local', version: '1.0.0', api_version: '1', dependencies: {}, requires: ['approval.request'],
      provides: ['tool.registry', 'tool.execute'],
      permissions: [
        { kind: 'filesystem', resources: ['config.workspace'], reason: 'workspace tools' },
        { kind: 'process', resources: ['workspace-child'], reason: 'structured argv commands' },
      ],
      config_schema: { type: 'object', additionalProperties: false, required: ['workspace'], properties: { workspace: { type: 'string', minLength: 1 } } },
    },
    apply(ctx, config) {
      const approval = ctx.resolve<ApprovalService>('approval.request')
      const runtime = ToolRuntime.withLocalTools(new LocalExecutionWorld(config.workspace), approval, ctx.events)
      ctx.provide('tool.registry', runtime)
      ctx.provide('tool.execute', runtime)
    },
  }
}

export function createStandardAgentPlugin(): DoAIPlugin<{ max_steps?: number }> {
  return {
    manifest: {
      name: 'agent-standard', version: '1.0.0', api_version: '1', dependencies: {},
      requires: ['session.event-store', 'model.generate', 'tool.registry', 'tool.execute', 'approval.request'],
      provides: ['agent.invoke'], permissions: [],
      config_schema: { type: 'object', additionalProperties: false, properties: { max_steps: { type: 'integer', minimum: 1, maximum: 256 } } },
    },
    apply(ctx, config) {
      const tools = ctx.resolve<ToolRuntime>('tool.execute')
      ctx.provide('agent.invoke', new StandardAgent({
        store: ctx.resolve('session.event-store'),
        model: ctx.resolve('model.generate'),
        approval: ctx.resolve('approval.request'),
        tools,
        ...(config.max_steps === undefined ? {} : { maxSteps: config.max_steps }),
      }))
    },
  }
}
