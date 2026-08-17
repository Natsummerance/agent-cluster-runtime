import type { EventScope, JsonValue, MutationMeta, SessionEvent } from '@doai/protocol'

import { projectModelMessages, type ModelMessage, type ToolCall } from './projection.ts'
import type { IdempotentSessionEventStore, SessionEventDraft, SessionEventStore } from './session-store.ts'
import type { ApprovalService, ToolRuntime } from './tools.ts'

export interface ModelResult {
  content: string
  tool_calls: ToolCall[]
}

export interface ModelProvider {
  generate(request: {
    messages: ModelMessage[]
    tools: ReturnType<ToolRuntime['list']>
    signal?: AbortSignal
  }): Promise<ModelResult>
}

export interface AgentInvokeRequest {
  session_id: string
  scope: EventScope
  input: string
  system_prompt: string
  signal?: AbortSignal
  mutation?: MutationMeta
}

export interface AgentInvokeResult {
  content: string
  revision: number
}

export class StandardAgent {
  readonly #store: SessionEventStore
  readonly #model: ModelProvider
  readonly #tools: ToolRuntime
  readonly #approval: ApprovalService
  readonly #maxSteps: number

  constructor(options: {
    store: SessionEventStore
    model: ModelProvider
    tools: ToolRuntime
    approval: ApprovalService
    maxSteps?: number
  }) {
    this.#store = options.store
    this.#model = options.model
    this.#tools = options.tools
    this.#approval = options.approval
    this.#maxSteps = options.maxSteps ?? 32
  }

  async invoke(request: AgentInvokeRequest): Promise<AgentInvokeResult> {
    const idempotentStore = this.#store as Partial<IdempotentSessionEventStore>
    if (request.mutation !== undefined && idempotentStore.findIdempotency !== undefined) {
      const completed = await idempotentStore.findIdempotency(request.session_id, request.mutation.idempotency_key)
      if (completed !== undefined) {
        return { content: String(completed.payload.content ?? ''), revision: completed.seq }
      }
    }
    let events = await this.#store.read(request.session_id, request.mutation?.session_revision)
    let revision = request.mutation?.session_revision ?? events.length
    let eventIndex = 0
    const append = async (draft: SessionEventDraft): Promise<SessionEvent> => {
      const isFinal = draft.type === 'agent.completed'
      const mutation = request.mutation === undefined ? undefined : {
        ...request.mutation,
        idempotency_key: isFinal
          ? request.mutation.idempotency_key
          : `${request.mutation.idempotency_key}:event:${eventIndex}`,
        session_revision: revision,
      }
      eventIndex += 1
      const appended = mutation !== undefined && idempotentStore.appendIdempotent !== undefined
        ? await idempotentStore.appendIdempotent(request.session_id, mutation, draft)
        : await this.#store.append(request.session_id, revision, draft)
      revision = appended.seq
      events.push(appended)
      return appended
    }
    const event = (type: string, payload: { [key: string]: JsonValue } = {}): SessionEventDraft => ({
      type, payload, scope: request.scope, ignorable: false,
    })

    try {
      if ((request.mutation?.session_revision ?? revision) === 0) {
        await append(event('session.created'))
      }
      await append(event('agent.system-prompt', { content: request.system_prompt }))
      await append(event('input.received', { content: request.input }))
      await append(event('agent.started'))

      for (let step = 0; step < this.#maxSteps; step += 1) {
        if (request.signal?.aborted) throw request.signal.reason ?? new Error('agent invocation cancelled')
        const messages = projectModelMessages(events)
        await append(event('model.requested', { step }))
        let response: ModelResult
        try {
          response = await this.#model.generate({
            messages,
            tools: this.#tools.list(),
            ...(request.signal === undefined ? {} : { signal: request.signal }),
          })
        } catch (cause) {
          await append(event('model.failed', { error: cause instanceof Error ? cause.message : String(cause) }))
          throw cause
        }
        await append(event('model.completed', {
          content: response.content,
          tool_calls: response.tool_calls as unknown as JsonValue,
        }))
        if (response.tool_calls.length === 0) {
          await append(event('agent.completed', { content: response.content }))
          return { content: response.content, revision }
        }
        for (const call of response.tool_calls) {
          const definition = this.#tools.get(call.name)
          await append(event('tool.requested', {
            tool_call_id: call.id,
            name: call.name,
            arguments: call.arguments,
          }))
          if (definition.risk !== 'read') {
            await append(event('approval.requested', {
              tool_call_id: call.id, name: call.name, risk: definition.risk, arguments: call.arguments,
            }))
            const decision = await this.#approval.request({
              session_id: request.session_id,
              tool: call.name,
              risk: definition.risk,
              arguments: call.arguments,
            })
            await append(event('approval.resolved', {
              tool_call_id: call.id, approved: decision.approved, reason: decision.reason,
            }))
            if (!decision.approved) throw new Error(decision.reason || `approval denied for ${call.name}`)
          }
          try {
            const output = await this.#tools.execute(call.name, call.arguments, request.signal)
            await append(event('tool.completed', {
              tool_call_id: call.id,
              name: call.name,
              result: typeof output === 'string' ? output : JSON.stringify(output),
            }))
          } catch (cause) {
            await append(event('tool.failed', {
              tool_call_id: call.id,
              name: call.name,
              error: cause instanceof Error ? cause.message : String(cause),
            }))
            throw cause
          }
        }
      }
      throw new Error(`agent exceeded maximum steps: ${this.#maxSteps}`)
    } catch (cause) {
      try {
        await append(event('agent.failed', {
          error: cause instanceof Error ? cause.message : String(cause),
        }))
      } catch {
        // Preserve the original execution error; a revision conflict already proves another writer won.
      }
      throw cause
    }
  }
}
