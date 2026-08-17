import type { ApprovalService, IdempotentSessionEventStore, StandardAgent } from '@doai/agent-runtime'
import type { DoAIPlugin } from '@doai/host'
import type { EventScope, JsonValue, MutationMeta } from '@doai/protocol'

import { OrganizationSupervisor, type OrganizationSupervisorOptions } from './supervisor.ts'

export interface OrganizationRunClient {
  run(params: {
    session_id: string
    scope: EventScope
    requirement: string
    budget: number
  }, mutation: MutationMeta): Promise<JsonValue>
  project(events: JsonValue[]): Promise<JsonValue>
  cancel(sessionId: string): Promise<void>
}

export function createOrganizationPlanePlugin(): DoAIPlugin<{
  command: string
  args?: string[]
  cwd: string
  env?: Record<string, string>
  heartbeat_ms?: number
}> {
  return {
    manifest: {
      name: 'organization-plane-python', version: '1.0.0', api_version: '1', dependencies: {},
      requires: ['session.event-store', 'agent.invoke', 'approval.request'],
      provides: ['transport.organization', 'organization.run'],
      permissions: [{ kind: 'process', resources: ['config.command'], reason: 'supervised Python organization plane' }],
      config_schema: {
        type: 'object', additionalProperties: false, required: ['command', 'cwd'],
        properties: {
          command: { type: 'string', minLength: 1 },
          args: { type: 'array', items: { type: 'string' } },
          cwd: { type: 'string', minLength: 1 },
          env: { type: 'object', additionalProperties: { type: 'string' } },
          heartbeat_ms: { type: 'integer', minimum: 0 },
        },
      },
    },
    async apply(ctx, config) {
      const store = ctx.resolve<IdempotentSessionEventStore>('session.event-store')
      const agent = ctx.resolve<StandardAgent>('agent.invoke')
      const approval = ctx.resolve<ApprovalService>('approval.request')
      const hostRequest: OrganizationSupervisorOptions['hostRequest'] = async (method, params, mutation) => {
        if (method === 'session.idempotency.get') {
          const event = await store.findIdempotency(String(params.session_id), String(params.idempotency_key))
          return event === undefined ? null : { event: event as unknown as JsonValue }
        }
        if (method === 'session.append') {
          if (mutation === undefined) throw new Error('session.append requires mutation metadata')
          const event = await store.appendIdempotent(String(params.session_id), mutation, {
            type: String(params.type),
            scope: params.scope as unknown as EventScope,
            payload: params.payload as { [key: string]: JsonValue },
            ignorable: Boolean(params.ignorable),
          })
          return { event: event as unknown as JsonValue }
        }
        if (method === 'agent.invoke') {
          if (mutation === undefined) throw new Error('agent.invoke requires mutation metadata')
          return await agent.invoke({
            session_id: String(params.session_id),
            scope: params.scope as unknown as EventScope,
            input: String(params.input),
            system_prompt: String(params.system_prompt),
            mutation,
          }) as unknown as JsonValue
        }
        if (method === 'approval.request') {
          return await approval.request({
            session_id: String(params.session_id),
            tool: String(params.gate),
            risk: 'write',
            arguments: { summary: params.summary ?? '' },
          }) as unknown as JsonValue
        }
        throw new Error(`organization plane requested unknown Host capability: ${method}`)
      }
      const supervisor = new OrganizationSupervisor({
        command: config.command,
        args: config.args ?? ['-m', 'doai_organization'],
        cwd: config.cwd,
        hostRequest,
        heartbeatMs: config.heartbeat_ms ?? 10_000,
        ...(config.env === undefined ? {} : { env: config.env }),
      })
      ctx.effect(() => () => supervisor.stop(), 'organization plane supervisor')
      await supervisor.start()
      const client: OrganizationRunClient = {
        run: async (params, mutation) => await supervisor.call('organization.run', params as unknown as { [key: string]: JsonValue }, mutation),
        project: async (events) => await supervisor.call('organization.project', { events }),
        cancel: async (sessionId) => await supervisor.cancel(sessionId),
      }
      ctx.provide('transport.organization', supervisor)
      ctx.provide('organization.run', client)
    },
  }
}
